#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AMSAT 频率页体检（只读，不改任何数据文件）→ tools/_amsat_freqs_report.md

为什么需要
----------
`数据/sat_freq.json` 只有 95 颗有频点，白名单 `数据/amateur_allow.json` 有 222 颗，
差的那 127 颗不是"不想填"，是**填的时候没有权威值**。AMSAT 官网把在役星按模式分
六页列了频率，抓一次就能知道：哪些颗现在就能补、哪些缺星历（补了也算不出过境）、
哪些本地已有的值已经和 AMSAT 不一致了。

用法
----
    python tools/fetch_amsat_freqs.py                 # 联网抓六页 + 出报告
    python tools/fetch_amsat_freqs.py --offline       # 用上次抓下来的 HTML（tools/_amsat_pages/）
    python tools/fetch_amsat_freqs.py --timeout 20    # 单次请求超时，默认 25 s
    python tools/fetch_amsat_freqs.py --json          # 同时写 _amsat_freqs_report.json

它**只写报告**：`数据/sat_freq.json`、`数据/amateur_allow.json`、`sat_freq.py`
一个字节都不动。合不合、合哪几颗，由操作员逐颗拍板（白名单里 pending 的规矩）。

退出码
------
    0  六页都抓到并解析成功（或 --offline 全命中缓存）
    2  部分页失败：报告已写，但明细里标了哪几页没抓到
    1  一页都没拿到 / 星历缓存读不到 —— 报告不写
"""

import argparse
import io
import json
import os
import re
import sys
import urllib.request
from html.parser import HTMLParser

if hasattr(sys.stdout, "write"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)                                    # 多普勒/
TOOL = os.path.join(REPO, "PC工具", "多普勒工具")
FREQ_JSON = os.path.join(REPO, "数据", "sat_freq.json")
ALLOW_JSON = os.path.join(REPO, "数据", "amateur_allow.json")
TLE_CACHE = os.path.join(TOOL, "tle_cache.json")
PAGE_DIR = os.path.join(HERE, "_amsat_pages")
REPORT_MD = os.path.join(HERE, "_amsat_freqs_report.md")
REPORT_JSON = os.path.join(HERE, "_amsat_freqs_report.json")

# AMSAT 按模式分页；六页一起才是"在役 + 待入轨"的全集。
PAGES = [
    ("live-fm-satellites", "FM 中继"),
    ("live-linear-satellites", "线性"),
    ("live-digipeater-satellites", "数字中继"),
    ("image-transmitting-satellites", "图像/SSTV"),
    ("live-miscellaneous-mode-satellites", "其他模式"),
    ("upcoming-satellites", "待入轨"),
]
BASE = "https://www.amsat.org/%s/"

# IARU 业余业务里实际用于卫星的频段（含 QO-100 的微波段）。
# 400-401 MHz 那一组不在内：AMSAT 也把它们列在"其他模式"页里，但那是
# 非业余的遥测/教育载荷频率，写进我们的表等于把业余名单开例外。
AMATEUR_BANDS = [(28.0, 29.8), (50.0, 54.0), (144.0, 148.0), (222.0, 225.0),
                 (420.0, 450.0), (902.0, 928.0), (1240.0, 1300.0),
                 (2300.0, 2450.0), (3300.0, 3500.0), (5650.0, 5925.0),
                 (10450.0, 10500.0)]

MHz = re.compile(r"(\d{1,5}(?:\.\d{1,6})?)\s*MHz")
_NAME_SEP = re.compile(r"[()/,]|·|\s+(?=[A-Za-z]{2,}|\d)")
_NONKEY = re.compile(r"[^0-9a-z\u4e00-\u9fff]")
# 本地值和 AMSAT 值差多少算"漂移"：15 kHz 是转发器标称值的常见舍入差，
# 再大就不是四舍五入的问题了（审计那一轮用的也是 15 kHz 这条线）。
DRIFT_KHZ = 15.0


def norm(text):
    return _NONKEY.sub("", str(text or "").lower())


def tokens(text):
    """一颗星的可搜索别名集合：整串 + 每个分段（括号里的发射名/OSCAR 编号都要）。"""
    out = {norm(text)}
    for part in _NAME_SEP.split(str(text or "")):
        p = norm(part)
        if len(p) >= 3:
            out.add(p)
    return {k for k in out if k}


def in_amateur(freq):
    return any(lo <= freq <= hi for lo, hi in AMATEUR_BANDS)


class Rows(HTMLParser):
    """抓 <table> 里的行；AMSAT 这六页都是三/四列的表。"""

    def __init__(self):
        HTMLParser.__init__(self)
        self.rows = []
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = ""

    def handle_endtag(self, tag):
        if tag == "tr" and self._row is not None:
            if any(x.strip() for x in self._row):
                self.rows.append([re.sub(r"\s+", " ", x).strip() for x in self._row])
            self._row = None
        elif tag in ("td", "th") and self._row is not None and self._cell is not None:
            self._row.append(self._cell)
            self._cell = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell += data


def fetch(slug, timeout, use_cache_only=False):
    """返回 (html, 来源说明)。抓失败退回上次缓存，缓存也没有就 (None, ...)。"""
    path = os.path.join(PAGE_DIR, slug + ".html")
    url = BASE % slug
    if not use_cache_only:
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                text = r.read().decode("utf-8", "replace")
            os.makedirs(PAGE_DIR, exist_ok=True)
            with io.open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            return text, "联网 200"
        except Exception as e:
            net_err = str(e)[:60]
    else:
        net_err = "--offline"
    if os.path.exists(path):
        with io.open(path, encoding="utf-8") as f:
            return f.read(), "缓存页（%s）" % net_err
    return None, "缺（%s）" % net_err


def parse_entries(slug, tag, html):
    """一页 → [{'page','mode','name','freqs','note'}...]，跳过表头与空行。"""
    p = Rows()
    p.feed(html)
    out = []
    for r in p.rows:
        name = r[0].strip()
        if not name or name.lower().startswith("satellite"):
            continue
        freqs = [float(x) for x in MHz.findall(" ".join(r[1:]))]
        if not freqs:
            continue
        out.append({"page": slug, "mode": tag, "name": name,
                    "freqs": sorted(set(freqs)),
                    "note": re.sub(r"\s+", " ", " ".join(r[1:]))[:120]})
    return out


def load_tle_names():
    """{归一化别名: [(norad, 原始名)]} —— 名字到编号的对齐表。"""
    try:
        with io.open(TLE_CACHE, encoding="utf-8") as f:
            cache = json.load(f)
    except Exception:
        return None
    idx = {}
    for norad, v in cache.items():
        if not str(norad).isdigit():
            continue
        name = v[0] if isinstance(v, (list, tuple)) and v else ""
        if not isinstance(name, str) or not name:
            continue
        for k in tokens(name):
            idx.setdefault(k, []).append((int(norad), name))
    return idx


def bind(entry, idx):
    """挑命中数最多的那个别名 token 做绑定；唯一命中才认。"""
    best, best_hits = None, 0
    for tok in sorted(tokens(entry["name"]), key=len, reverse=True):
        cands = idx.get(tok, [])
        uniq = sorted({n for n, _ in cands})
        if len(uniq) == 1:
            return uniq[0], cands[0][1], tok
        if len(uniq) > best_hits:
            best, best_hits = (uniq, cands, tok), len(uniq)
    if best:
        return None, best[1], best[2]          # 多个 NORAD 抢同一个别名
    return None, None, None


def main(argv=None):
    ap = argparse.ArgumentParser(description="AMSAT 频率页体检（只报告，不改数据）")
    ap.add_argument("--offline", action="store_true", help="只用上次抓下来的 HTML")
    ap.add_argument("--timeout", type=float, default=25.0)
    ap.add_argument("--json", action="store_true", help="同时写 _amsat_freqs_report.json")
    a = ap.parse_args(argv)

    with io.open(FREQ_JSON, encoding="utf-8") as f:
        local = json.load(f).get("sats") or {}
    local_by_norad = {int(k): v for k, v in local.items()}
    try:
        with io.open(ALLOW_JSON, encoding="utf-8") as f:
            allow = json.load(f).get("keep") or []
        allow_norads = {int(e["norad"]) for e in allow if e.get("norad")}
    except Exception:
        allow_norads = set()

    idx = load_tle_names()
    if idx is None:
        print("读不到星历缓存：%s\n先跑 PC工具/多普勒工具/refresh_tle.py" % TLE_CACHE)
        return 1

    entries, status = [], []
    for slug, tag in PAGES:
        html, how = fetch(slug, a.timeout, a.offline)
        got = parse_entries(slug, tag, html) if html else []
        status.append({"page": slug, "mode": tag, "http": how, "rows": len(got)})
        entries += got
        print("%-34s %-12s %6s 条" % (slug, how, len(got)))
    if not any(s["rows"] for s in status):
        print("六页一条频率都没解析出来 —— 报告不写。")
        return 1

    # 同一颗星可能同时出现在两页（ISS 的 FM 和 SSTV），按归一化全名合一次。
    merged = {}
    for e in entries:
        k = norm(e["name"])
        if k in merged:
            merged[k]["freqs"] = sorted(set(merged[k]["freqs"]) | set(e["freqs"]))
            merged[k]["mode"] += "+" + e["mode"]
        else:
            merged[k] = dict(e)

    addable, drift, same, no_tle, non_am, ambiguous = [], [], [], [], [], []
    for e in sorted(merged.values(), key=lambda x: x["name"]):
        norad, tle_name, tok = bind(e, idx)
        amateur = [f for f in e["freqs"] if in_amateur(f)]
        if norad is None and tok:
            ambiguous.append({**e, "why": "别名对上了 %d 个 NORAD" % len(idx.get(tok, []))})
            continue
        if norad is None:
            no_tle.append(e)
            continue
        if not amateur:
            non_am.append({**e, "norad": norad, "tle": tle_name})
            continue
        row = {**e, "norad": norad, "tle": tle_name, "amateur": amateur}
        have = local_by_norad.get(norad)
        if not have:
            addable.append(row)
            continue
        cur = float(have.get("down") or 0.0)
        near = min(amateur, key=lambda f: abs(f - cur)) if cur else 0.0
        if cur and abs(near - cur) * 1000.0 > DRIFT_KHZ:
            row.update(local_down=cur, amsat=near)
            drift.append(row)
        else:
            row.update(local_down=cur, amsat=near)
            same.append(row)

    lines = ["# AMSAT 频率页体检（生成：%s）" % time_str(), ""]
    lines += ["`tools/fetch_amsat_freqs.py` 的产物。**只报告，不改表**：",
              "`数据/sat_freq.json` / `数据/amateur_allow.json` / `sat_freq.py` 一字节未动。",
              ""]
    lines += ["## 抓取状态", "", "| 页面 | 模式 | 来源 | 解析出的频率行 |",
              "|---|---|---|---|"]
    for s in status:
        lines.append("| %s | %s | %s | %d |" % (s["page"], s["mode"], s["http"], s["rows"]))
    lines += ["", "去重后星数：%d；星历缓存别名数：%d；本地频点表：%d 颗；白名单：%d 颗"
              % (len(merged), len(idx), len(local_by_norad), len(allow_norads)), ""]

    def table(rows, cols, head):
        out = [head, "|" + "|".join(c[0] for c in cols) + "|",
               "|" + "|".join("---" for _ in cols) + "|"]
        for r in rows:
            out.append("|" + "|".join(str(c[1](r)) for c in cols) + "|")
        return out + [""]

    lines += ["## 一、能补：有权威频率 + 本地有星历 + 落在业余段（%d 颗）" % len(addable), ""]
    if addable:
        lines += table(addable, [
            ("星", lambda r: r["name"]),
            ("NORAD", lambda r: r["norad"]),
            ("星历里的名字", lambda r: r["tle"]),
            ("模式", lambda r: r["mode"]),
            ("业余频点 (MHz)", lambda r: " / ".join("%.3f" % f for f in r["amateur"])),
            ("在白名单?", lambda r: "是" if r["norad"] in allow_norads else "否（pending）"),
        ], "")
    else:
        lines += ["（无）", ""]

    lines += ["## 二、已有频点且与 AMSAT 一致（%d 颗）" % len(same), "",
              "不用动。", ""]

    lines += ["## 三、已有频点但与 AMSAT 不一致（%d 颗，阈值 %.0f kHz）" % (len(drift), DRIFT_KHZ), ""]
    if drift:
        lines += table(drift, [
            ("星", lambda r: r["name"]),
            ("NORAD", lambda r: r["norad"]),
            ("本地下行", lambda r: "%.3f" % r["local_down"]),
            ("AMSAT", lambda r: "%.3f" % r["amsat"]),
            ("差 (kHz)", lambda r: "%.0f" % ((r["amsat"] - r["local_down"]) * 1000)),
        ], "")
    else:
        lines += ["（无）", ""]

    lines += ["## 四、有频率但本地没星历（%d 颗）—— 补了也算不出过境" % len(no_tle), ""]
    if no_tle:
        lines += table(no_tle, [
            ("星", lambda r: r["name"]),
            ("模式", lambda r: r["mode"]),
            ("频点 (MHz)", lambda r: " / ".join("%.3f" % f for f in r["freqs"])),
        ], "")
    else:
        lines += ["（无）", ""]

    lines += ["## 五、频率不在业余段（%d 颗）—— 要进表得先决定要不要开例外" % len(non_am), ""]
    if non_am:
        lines += table(non_am, [
            ("星", lambda r: r["name"]),
            ("NORAD", lambda r: r["norad"]),
            ("频点 (MHz)", lambda r: " / ".join("%.3f" % f for f in r["freqs"])),
        ], "")
    else:
        lines += ["（无）", ""]

    lines += ["## 六、别名撞多个 NORAD，没敢绑（%d 颗）" % len(ambiguous), ""]
    if ambiguous:
        lines += table(ambiguous, [
            ("星", lambda r: r["name"]),
            ("原因", lambda r: r["why"]),
        ], "")
    else:
        lines += ["（无）", ""]

    lines += ["## 怎么用这份报告", "",
              "1. 第一节逐颗核对 AMSAT 页面原文，确认没抓错行；",
              "2. 认可的把 `数据/amateur_allow.json` 的 `pending_add` 挪进 `keep`（或直接加进 `keep`）；",
              "3. 频点写进 `数据/sat_freq.json`（真源），再 `python PC工具/多普勒工具/embed_transponders.py --write`",
              "   重生成 `sat_freq.py` 里的内嵌块，然后 `bash PC工具/多普勒工具/verify_all.sh --fast`；",
              "4. 第三节的漂移是**现有表可能要改**的信号，优先级高于补新星。", ""]

    with io.open(REPORT_MD, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    if a.json:
        with io.open(REPORT_JSON, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"status": status, "addable": addable, "drift": drift,
                       "same": same, "no_tle": no_tle, "non_amateur": non_am,
                       "ambiguous": ambiguous}, f, ensure_ascii=False, indent=1)

    print("\n能补 %d 颗 · 一致 %d 颗 · 漂移 %d 颗 · 缺星历 %d 颗 · 非业余段 %d 颗 · 歧义 %d 颗"
          % (len(addable), len(same), len(drift), len(no_tle), len(non_am), len(ambiguous)))
    print("报告：%s" % REPORT_MD)
    bad_pages = [s for s in status if not s["rows"]]
    return 2 if bad_pages else 0


def time_str():
    import time
    return time.strftime("%Y-%m-%d %H:%M:%S")


if __name__ == "__main__":
    sys.exit(main())
