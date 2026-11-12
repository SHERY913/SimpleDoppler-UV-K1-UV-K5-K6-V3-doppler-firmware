#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""卫星频点的单一数据源 → 安卓 App 的 Freqs.kt（生成 / 校验）。

为什么要有这个脚本
------------------
以前频点写在三个地方，各写一份、各自漂移：
  * 安卓 `doppler-ng/app/src/main/assets/freqs.json`（由 build_catalog.py 生成）
  * PC 工具 `sat_freq.py` 的 FREQ（21 颗，只有下行）
  * 交接文档里的表格
结果是同一颗星在手机上能通、在电脑上有另一个数，PO-101 的上行甚至是错的
（写 435.100，真值是 437.500）。屏幕上的数字必须等于机器里真正的数字，
所以改成一个 JSON 当唯一真源，两边都从它来。

用法
----
    python gen_sat_freq.py --write   # 调用 build_catalog.py 重新生成 assets/freqs.json
    python gen_sat_freq.py --check   # 只校验三处是否一致，不写文件（CI / verify_all 用）

`--check` 的判定：
  1. JSON 里 down <= 0 的条目算错；
  2. assets/freqs.json 里的 manual 条目、下行/上行/哑音/SSTV/直通 必须与 JSON 完全一致；
  3. sat_freq.py 的 FREQ 下行必须与 JSON 一致（PC 侧只记下行，不比对上行）。
"""

import argparse
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.abspath(os.path.join(HERE, "..", "..", ".."))          # …/default-workspace
JSON_PATH = os.path.join(WS, "多普勒", "数据", "sat_freq.json")
ALLOW_PATH = os.path.join(WS, "多普勒", "数据", "amateur_allow.json")
TOOLS_DIR = os.path.join(WS, "多普勒", "tools")
# 白名单的读取/匹配只实现一次（多普勒/tools/allowlist.py），三个生成器 + 守卫共用。
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)
import allowlist as _allow  # noqa: E402
# App 侧现在读的是打包进 assets 的 freqs.json（由 doppler-ng/tools/build_catalog.py
# 从 数据/sat_freq.json 的 manual 条目 + 在线目录合成），不再是手写 Kotlin 字面量。
ASSETS_PATH = os.path.join(
    WS, "doppler-ng", "app", "src", "main", "assets", "freqs.json",
)
GEN_CATALOG = os.path.join(WS, "doppler-ng", "tools", "build_catalog.py")
# App 的转发器表：和 PC 工具同一份真源（数据/sat_freq.json 的 transponders），
# 不再让 App 从 SatNOGS 的原始 tx 数组里挑频点 —— 那份 ISS 有 6 条重复频点，
# 而且根本没有 SSTV 437.550。
APP_TRANS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))), "doppler-ng", "app", "src",
    "main", "assets", "transponders.json")
PY_PATH = os.path.join(HERE, "sat_freq.py")


def load_json_raw():
    with io.open(JSON_PATH, encoding="utf-8") as f:
        return json.load(f)


def filter_sats(doc, allow):
    """按业余白名单过滤 sats。

    返回 (kept, dropped)。dropped 里每项是 (norad, name, 记进剔除清单的那一行)。
    规则在 多普勒/tools/allowlist.py 里：NORAD 优先，NORAD 缺失按名字精确匹配，
    都不中就剔 —— 这台机器上出现过"名单缺失就静默全放行"，所以名单缺失时
    _allow.load() 直接 SystemExit，不会走到这里。
    """
    kept, dropped = {}, []
    for k, e in (doc.get("sats") or {}).items():
        norad = k if str(k).isdigit() else e.get("norad")
        name = e.get("name")
        if allow.match(norad, name):
            kept[str(k)] = e
        else:
            dropped.append((norad, name, _allow.dropped_line(norad, name)))
    return kept, dropped


def _dump_json(doc, path, indent):
    """按文件现有换行写回：sat_freq.json 是 CRLF，别一改就整文件重排。"""
    crlf = b"\r\n" in open(path, "rb").read(4096)
    txt = json.dumps(doc, ensure_ascii=False, indent=indent)
    if crlf:
        txt = txt.replace("\n", "\r\n")
    with io.open(path, "w", encoding="utf-8", newline="") as f:
        f.write(txt + ("\r\n" if crlf else "\n"))


def prune_json(doc, sats, allow):
    """把 数据/sat_freq.json 物理裁剪到名单内，并打上 _unverified。

    返回 (剔除行数, 打标条数)。剔除清单写到 多普勒/tools/_dropped_from_source.txt。
    """
    out = {}
    for k, e in (doc.get("sats") or {}).items():
        if str(k) not in sats:
            continue
        if allow.is_unverified(k):
            e["_unverified"] = True
        else:
            e.pop("_unverified", None)
        out[k] = e
    doc["sats"] = out
    doc["_allow"] = ("只保留 数据/amateur_allow.json 里的业余卫星；"
                     "剔除清单见 tools/_dropped_from_source.txt")
    _dump_json(doc, JSON_PATH, 2)
    return len(sats)


def fmhz(v):
    """Kotlin 里的 MHz 字面量：固定三位小数，避免 437.8 与 437.800 这种无意义 diff。"""
    return "%.3f" % float(v)


def kt_escape(s):
    return s.replace("\\", "\\\\").replace('"', '\\"')


def render_kt(sats):
    lines = [
        "package com.f4hwn.doppler",
        "",
        "/** 下行/上行频率（MHz）与哑音，按 NORAD 编号。没有上行的卫星 upMhz = 0。 */",
        "data class SatFreq(",
        "    val downMhz: Double,",
        "    val upMhz: Double = 0.0,",
        "    val tone: Double = 0.0,",
        '    val mode: String = "FM",',
        '    val blurb: String = "",',
        "    /** SSTV 图像下发频率（MHz）。0 表示这颗星没有 SSTV，选「SSTV 收听」时退回 downMhz。 */",
        "    val sstvMhz: Double = 0.0,",
        "    /** 语音直通（不走中继）：接收频率（MHz）。0 表示这颗星没有直通频点。 */",
        "    val voiceRxMhz: Double = 0.0,",
        "    /** 语音直通的发射频率（MHz）。 */",
        "    val voiceTxMhz: Double = 0.0",
        ")",
        "",
        "/**",
        " * 频点表 —— **这个文件由脚本生成，不要手改**。",
        " *",
        " * 真源：`多普勒/数据/sat_freq.json`",
        " * 生成：`多普勒/PC工具/多普勒工具/gen_sat_freq.py --write`",
        " * 校验：同目录 `verify_three_way.py`（App / PC 工具 / JSON 三处必须一致）",
        " *",
        " * 表里只放**有出处**的频点：AMSAT 频率表 / IARU 协调结果 / 卫星团队公告。",
        " * 拿不准的一律不写 —— 列表是按 tles.json 建的，写了频点但没有星历的星",
        " * 永远显示不出来，只会被校验脚本记成死数据。",
        " */",
        "object Freqs {",
        "    val table: Map<String, SatFreq> = mapOf(",
    ]
    for norad in sorted(sats, key=lambda k: (not sats[k].get("pinned"), k)):
        e = sats[norad]
        parts = [fmhz(e["down"])]
        if e.get("up") is not None:
            parts.append(fmhz(e.get("up") or 0.0))
        if e.get("tone") is not None:
            parts.append("%.1f" % float(e.get("tone") or 0.0))
        if e.get("mode") is not None:
            parts.append('"%s"' % kt_escape(e.get("mode") or "FM"))
        parts.append('"%s"' % kt_escape(e.get("blurb") or ""))
        extra = []
        if float(e.get("sstv") or 0) > 0:
            extra.append("sstvMhz = " + fmhz(e["sstv"]))
        if float(e.get("voice_rx") or 0) > 0:
            extra.append("voiceRxMhz = " + fmhz(e["voice_rx"]))
        if float(e.get("voice_tx") or 0) > 0:
            extra.append("voiceTxMhz = " + fmhz(e["voice_tx"]))
        head = '        "%s" to SatFreq(%s' % (norad, ", ".join(parts))
        if extra:
            lines.append(head + ",")
            for i, x in enumerate(extra):
                # mapOf 的每一项都要有逗号，最后一项也不例外（追加新条目时不至于漏）。
                lines.append("            " + x + ("," if i < len(extra) - 1 else "),"))
        else:
            lines.append(head + "),")
    lines += [
        "    )",
        "",
        "    fun of(norad: String): SatFreq = table[norad] ?: SatFreq(0.0)",
        "}",
        "",
    ]
    return "\n".join(lines)


RE_KT_ENTRY = re.compile(
    r'"(\d+)" to SatFreq\(\s*([0-9.]+)\s*(?:,\s*([0-9.]+))?\s*(?:,\s*([0-9.]+))?\s*'
    r'(?:,\s*"([^"]*)")?\s*(?:,\s*"((?:[^"\\]|\\.)*)")?\s*(.*?)\)\s*,?\s*(?=\n\s*(?:"\d+" to SatFreq|\]\s*$|\)))',
    re.S,
)


def parse_kt(path):
    """把 Freqs.kt 解析成 {norad: dict}。只认生成器会写出的形态。"""
    txt = io.open(path, encoding="utf-8").read()
    out = {}
    for m in RE_KT_ENTRY.finditer(txt):
        norad, down, up, tone, mode, _blurb, tail = m.groups()
        e = {"down": float(down)}
        if up:
            e["up"] = float(up)
        if tone:
            e["tone"] = float(tone)
        if mode:
            e["mode"] = mode
        if tail:
            for key, pat in (("sstv", r"sstvMhz\s*=\s*([0-9.]+)"),
                             ("voice_rx", r"voiceRxMhz\s*=\s*([0-9.]+)"),
                             ("voice_tx", r"voiceTxMhz\s*=\s*([0-9.]+)")):
                mm = re.search(pat, tail)
                if mm:
                    e[key] = float(mm.group(1))
        out[norad] = e
    return out


def parse_py(path):
    """从 sat_freq.py 的 FREQ 字典里取 norad -> 下行。"""
    txt = io.open(path, encoding="utf-8").read()
    body = txt.split("FREQ = {", 1)[1].split("\n}", 1)[0]
    out = {}
    for m in re.finditer(r"^\s*(\d+):\s*\(\s*([0-9.]+)", body, re.M):
        out[m.group(1)] = float(m.group(2))
    return out


def patch_manual_assets(sats, allow=None):
    """没有网络时（CelesTrak 403 / SatNOGS 超时）只同步 manual 那 98 颗。

    全量目录来自在线 TLE + SatNOGS，抓不到就动不了；但 manual 条目是本地
    真源覆盖的字段，可以逐颗改。--check 比的就是这几个字段，App 也只读它们。
    返回改动条数。
    """
    with io.open(ASSETS_PATH, encoding="utf-8") as f:
        doc = json.load(f)
    assets = doc.get("sats") or {}
    changed = 0
    for n, e in sats.items():
        a = assets.get(n)
        if not a or a.get("source") != "manual":
            continue
        before = json.dumps(a, sort_keys=True, ensure_ascii=False)
        a["down"] = float(e["down"])
        a["up"] = float(e.get("up") or 0.0)
        a["tone"] = float(e.get("tone") or 0.0)
        a["mode"] = e.get("mode") or "FM"
        a["blurb"] = (e.get("blurb") or "")[:220]
        for key in ("sstv", "voice_rx", "voice_tx"):
            if float(e.get(key) or 0.0) > 0:
                a[key] = float(e[key])
            else:
                a.pop(key, None)
        # 本地值没在 AMSAT 页上核对过的，App 侧要同样带上标记，否则 --check 会红。
        if allow is not None and allow.is_unverified(n):
            a["_unverified"] = True
        else:
            a.pop("_unverified", None)
        if json.dumps(a, sort_keys=True, ensure_ascii=False) != before:
            changed += 1
    doc["sats"] = assets
    with io.open(ASSETS_PATH, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print("  离线只同步 manual 条目：改动 %d 颗 / 共 %d 颗" % (changed, len(assets)))
    return changed


def write_app_transponders(allow):
    """把 数据/sat_freq.json 里人工校对的转发器表抄给 App。

    为什么不让 App 自己读 SatNOGS 的 tx：那份是原始抓取，ISS 有 6 条，
    混着重复频点和 "FM · 145.800" 这种没意义的标签，而且没有 SSTV 437.550。
    App 和 PC 必须是同一份真源，否则两边写进电台的频率会不一样。

    只写名单内的 NORAD（`ts.allowlist` 的规则）；真源已经裁剪过一遍，
    这里再挡一次是为了"有人手改 JSON 又忘了跑 --write"时也不会漏进 App。
    """
    with io.open(JSON_PATH, encoding="utf-8") as f:
        sats = json.load(f).get("sats") or {}
    out = {}
    for norad, e in sats.items():
        if not allow.match(norad, e.get("name")):
            continue
        trs = e.get("transponders") or []
        if not trs:
            continue
        rows = []
        for t in trs:
            rows.append({
                "name": t.get("name") or "转发器",
                "down": float(t.get("down") or 0.0),
                "up": float(t.get("up") or 0.0),
                "tone": float(t.get("tone") or 0.0),
                "desc": t.get("note") or "",
            })
        out[str(norad)] = rows
    doc = {"_note": "由 数据/sat_freq.json 生成，别手改。生成器：gen_sat_freq.py --app-trans",
           "count": len(out), "sats": out}
    with io.open(APP_TRANS, "w", encoding="utf-8") as f:
        f.write(json.dumps(doc, ensure_ascii=False, indent=1))
    return len(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="重新生成 Freqs.kt")
    ap.add_argument("--check", action="store_true", help="只校验，不写文件")
    ap.add_argument("--app-trans", action="store_true",
                    help="把转发器表抄给 App（assets/transponders.json）")
    args = ap.parse_args()
    if not (args.write or args.check):
        args.check = True

    if "--app-trans" in sys.argv:
        n = write_app_transponders(_allow.load(ALLOW_PATH))
        print("App 转发器表已写：%d 颗 → %s" % (n, APP_TRANS))
        return 0

    allow = _allow.load(ALLOW_PATH)
    doc = load_json_raw()
    sats, dropped = filter_sats(doc, allow)
    bad = []
    for n, e in sats.items():
        if float(e.get("down") or 0) <= 0:
            bad.append(f"{n} 的 down 不是正数")
        d, u = float(e.get("down") or 0), float(e.get("up") or 0)
        if d and not (29.0 <= d <= 1300.0):
            bad.append(f"{n} 的下行 {d} MHz 不在 29~1300 MHz 内")
        if u and not (29.0 <= u <= 1300.0):
            bad.append(f"{n} 的上行 {u} MHz 不在 29~1300 MHz 内")
        # transponders 是写表和界面用的真源：第 0 条必须与老字段完全一致，
        # 否则「界面默认那条」与「老字段算出来的表」会分叉。
        tr = e.get("transponders") or []
        if not tr:
            bad.append(f"{n} 没有 transponders")
            continue
        for i, t in enumerate(tr):
            if not str(t.get("name") or "").strip():
                bad.append(f"{n} 的 transponders[{i}] 没有名字")
            td = float(t.get("down") or 0)
            tu = float(t.get("up") or 0)
            if not (29.0 <= td <= 1300.0):
                bad.append(f"{n} 的 transponders[{i}] 下行 {td} MHz 不在 29~1300 MHz 内")
            if tu and not (29.0 <= tu <= 1300.0):
                bad.append(f"{n} 的 transponders[{i}] 上行 {tu} MHz 不在 29~1300 MHz 内")
        t0 = tr[0]
        for key, ref in (("down", d), ("up", u), ("tone", float(e.get("tone") or 0))):
            if abs(float(t0.get(key) or 0) - ref) > 1e-6:
                bad.append(f"{n} 的 transponders[0].{key}={t0.get(key)} 与老字段 {ref} 不一致")
    # exe 读不到 JSON，用的是 sat_freq.py 里的内嵌副本；两边必须逐条一致，
    # 否则 exe 的下拉会悄悄退回「一颗星一条」而 --check 还是绿的。
    # --write 会紧接着重生这块，所以它过期不算"JSON 自身有问题"。
    import sat_freq as _sf
    emb = getattr(_sf, "EMBEDDED_TRANS", {})
    stale_embedded = []
    for n, e in sats.items():
        rows = [t for t in (e.get("transponders") or [])
                if float(t.get("down") or 0) > 0]
        want = [(t.get("name"), round(float(t.get("down") or 0), 6),
                 round(float(t.get("up") or 0), 6), round(float(t.get("tone") or 0), 6))
                for t in rows]
        got = [(nm, round(d, 6), round(u, 6), round(t, 6))
               for (nm, d, u, t) in emb.get(int(n), [])]
        if want != got:
            stale_embedded.append(
                f"{n} 的内嵌转发器表与 JSON 不一致（{len(got)} 条 vs {len(want)} 条）")
    if not args.write:
        bad.extend(stale_embedded)

    if bad:
        print("JSON 自身有问题：")
        for b in bad:
            print("  - " + b)
        return 2

    if dropped:
        # 名单是唯一真源：真源里还留着非业余星，说明有人绕过了生成链。
        # --write 会把它们物理剔掉；--check 必须报红，不能装作没看见。
        _allow.record_dropped("sat_freq.json",
                              [ln for (_n, _nm, ln) in dropped])
        print("名单外条目：%d 颗（%s）"
              % (len(dropped), "已剔除写盘" if args.write else "--check 报红，跑 --write"))
        for _n, _nm, ln in dropped[:8]:
            print("  - " + ln)
        if len(dropped) > 8:
            print("  - ……共 %d 条，全量见 tools/_dropped_from_source.txt" % len(dropped))

    if args.write:
        if dropped:
            n_keep = prune_json(doc, sats, allow)
            print("  真源已裁剪：%d 颗（剔除 %d 颗）" % (n_keep, len(dropped)))
        else:
            # 名单没变也要把 _unverified 标记补齐（181 颗 missing_no_amsat）
            prune_json(doc, sats, allow)
        # App 侧的频点表已改由 build_catalog.py 生成（它会合并 manual + 在线目录），
        # 这里再写一份 Kotlin 字面量只会制造第三份副本。
        import subprocess
        cache = os.path.join(os.path.dirname(GEN_CATALOG), os.pardir, ".cache")
        cmd = [sys.executable, GEN_CATALOG, "--no-tles"]
        # build_catalog 默认只读 .cache；新检出的仓库里它不存在，
        # 于是 --write 直接 FileNotFoundError。缺缓存时自己补 --fetch。
        if not (os.path.exists(os.path.join(cache, "active.txt"))
                and os.path.exists(os.path.join(cache, "sn.json"))):
            print("  本地没有 .cache（TLE / SatNOGS），先联网抓一次")
            cmd.append("--fetch")
        rc = subprocess.call(cmd, cwd=os.path.dirname(GEN_CATALOG))
        if rc != 0:
            # CelesTrak 对部分网络直接 403、SatNOGS 全量 3.9 MB 常超时；全量目录
            # 抓不到时不能连 manual 的修正也一起丢掉 —— 那 98 颗是本地真源，
            # App 读的就是它们，离线同步即可让 --check 变绿。
            print("  build_catalog.py 失败（rc=%d），改为离线同步 manual 条目" % rc)
            patch_manual_assets(sats, allow)
            rc = 0
        print("已调用 build_catalog.py（rc=%d）：%s" % (rc, ASSETS_PATH))
        # assets 落盘后顺手重生 sat_freq.py 的内嵌转发器表（exe 读不到 JSON，
        # 只有这份副本）。用函数直调，别再开一个 subprocess；两边不一致时
        # 下面的 --check 会逐颗报红。
        n_app = write_app_transponders(allow)
        print("  已同步 App 转发器表：%d 颗" % n_app)
        import embed_transponders
        n_sat, n_line = embed_transponders.write_embedded()
        print("  内嵌转发器表已同步：%d 颗卫星 / %d 行" % (n_sat, n_line))
        return rc

    if dropped:
        print("名单外的星还留在 数据/sat_freq.json 里：%d 颗 —— 跑 --write 剔掉" % len(dropped))
        return 1

    # ── 校验 ────────────────────────────────────────────────────────────
    problems = []
    if not os.path.exists(ASSETS_PATH):
        problems.append("缺文件：" + ASSETS_PATH)
        assets = {}
    else:
        with io.open(ASSETS_PATH, encoding="utf-8") as f:
            assets = json.load(f).get("sats") or {}
    if not assets:
        problems.append("assets/freqs.json 一条都没读出来（文件坏了？）")
    # build_catalog 会丢掉"有频点但没有 TLE"的星（算不出过境，留着也没用），
    # 这是设计行为。所以缺条目要分两种：有 TLE 却没进表 = 真不一致；
    # 没 TLE = 生成器按规则跳过，只提示。
    tle_norad = set()
    tles_txt = os.path.join(os.path.dirname(ASSETS_PATH), "tles.txt")
    if os.path.exists(tles_txt):
        with io.open(tles_txt, encoding="utf-8", errors="replace") as f:
            for ln in f:
                if ln.startswith("1 "):
                    tle_norad.add(ln[2:7].strip())
    skipped_no_tle = []
    for n in sorted(set(sats) - set(assets), key=int):
        if n.lstrip("0") in {x.lstrip("0") for x in tle_norad} or n in tle_norad:
            problems.append(f"JSON 有 {n}（{sats[n].get('name')}），assets/freqs.json 里没有 —— 跑 --write")
        else:
            skipped_no_tle.append(f"{n} {sats[n].get('name')}")
    # 反向不做集合差：assets 是 1000+ 颗的全量表，JSON 只是人工维护的子集。
    for n in sorted(set(assets) & set(sats), key=int):
        a, b = assets[n], sats[n]
        if a.get("source") not in (None, "manual"):
            problems.append(f"{n}（{b.get('name')}）在 assets 里被在线目录覆盖了（source={a.get('source')}）")
        for key in ("down", "up", "tone", "sstv", "voice_rx", "voice_tx"):
            av = float(a.get(key) or 0.0)
            bv = float(b.get(key) or 0.0)
            if abs(av - bv) > 1e-6:
                problems.append(f"{n}（{b.get('name')}）的 {key}：assets={av} JSON={bv}")
        # 181 颗本地值在 AMSAT 页上查不到，星对象要带 _unverified；两边不一致
        # 说明有人手改了其中一边，界面上"未核对"的标记就不可信了。
        if bool(a.get("_unverified", False)) != bool(b.get("_unverified", False)):
            problems.append(
                f"{n}（{b.get('name')}）的 _unverified：assets={a.get('_unverified')} "
                f"JSON={b.get('_unverified')}")

    # App 目录必须整体落在名单内 —— 名单是唯一真源，assets 里混着非业余星
    # 说明 freqs.json 是旧批次，跑 --write 重生成。
    stale = []
    for n in assets:
        if not allow.match(n, assets[n].get("name")):
            stale.append(n)
    if stale:
        stale.sort(key=lambda x: int(x) if x.isdigit() else 0)
        problems.append(
            "assets/freqs.json 里还有 %d 颗名单外的星：%s%s"
            % (len(stale), "、".join(stale[:8]), "……" if len(stale) > 8 else ""))

    # App 的转发器表（assets/transponders.json）：App 和 PC 必须同一份真源，
    # 否则两边选出来的频点会不一样。逐颗逐条比下行，条数不同直接报。
    if not os.path.exists(APP_TRANS):
        problems.append("缺文件：" + APP_TRANS + "（跑 --app-trans）")
    else:
        with io.open(APP_TRANS, encoding="utf-8") as f:
            app_tr = json.load(f).get("sats") or {}
        for n, e in sats.items():
            want = e.get("transponders") or []
            got = app_tr.get(str(n)) or []
            if len(got) != len(want):
                problems.append("%s App 转发器 %d 条，JSON %d 条" % (n, len(got), len(want)))
                continue
            for i, (a, b) in enumerate(zip(got, want)):
                if abs(float(a.get("down", 0)) - float(b.get("down", 0))) > 1e-6:
                    problems.append("%s App 转发器[%d] 下行 %s ≠ %s" % (n, i, a.get("down"), b.get("down")))

    py = parse_py(PY_PATH)
    for n in sorted(set(py) & set(sats), key=int):
        if abs(py[n] - float(sats[n]["down"])) > 1e-6:
            problems.append(
                f"{n}（{sats[n].get('name')}）的下行：sat_freq.py={py[n]} JSON={sats[n]['down']}"
            )

    if problems:
        print("三处频点不一致：")
        for p in problems:
            print("  - " + p)
        return 1
    if skipped_no_tle:
        print("  · 没有 TLE 被生成器跳过（正常）：" + "、".join(skipped_no_tle))
    print(f"频点一致：JSON {len(sats)} 颗 / assets/freqs.json {len(assets)} 颗"
          f"（交集 {len(set(assets) & set(sats))} 颗）/ sat_freq.py 交集 {len(set(py) & set(sats))} 颗")
    return 0


if __name__ == "__main__":
    sys.exit(main())
