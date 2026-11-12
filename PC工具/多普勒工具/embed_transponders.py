# -*- coding: utf-8 -*-
"""把 数据/sat_freq.json 的 transponders 压成 Python 字面量塞进 sat_freq.py。

为什么需要：PyInstaller 单文件 exe 里 __file__ 指向解包目录，读不到仓库里的
数据/sat_freq.json（spec 的 datas 只带 sat_pics）。没有这段内嵌表，exe 里
每颗星只剩一条兜底转发器，ISS 下拉就只有 1 条而不是 4 条（2026-11-08 实测）。

JSON 仍是唯一真源：exe 用内嵌副本，gen_sat_freq.py --check 逐颗比对两者，
不一致就报红，内嵌副本不会悄悄漂移。

用法：
    python embed_transponders.py            # 只校验（默认）
    python embed_transponders.py --write    # 重新生成内嵌块
    python embed_transponders.py --check    # 只校验，rc=1 表示块过期
"""
import argparse
import io
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
JSON_PATH = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "数据", "sat_freq.json"))
PY_PATH = os.path.join(HERE, "sat_freq.py")
TOOLS_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "tools"))
# 白名单只有一份，规则只写一遍（多普勒/tools/allowlist.py）。
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)
import allowlist as _allow  # noqa: E402
BEGIN = "# --- EMBEDDED_TRANS begin（gen: PC工具/多普勒工具/embed_transponders.py，别手改）---"
END = "# --- EMBEDDED_TRANS end ---"


def fmt_num(v):
    v = float(v or 0.0)
    return repr(round(v, 6))


def build_block(doc, allow=None):
    """按 norad 升序生成字面量块；down<=0 的条目跳过（没有下行就没法写表）。

    只收名单内的 NORAD：exe 读的是这份内嵌副本，它要是还带着非业余星，
    PC 工具的下拉就和 App 的列表不是同一批了。
    """
    allow = allow or _allow.load()
    lines = [BEGIN, "EMBEDDED_TRANS = {"]
    for key in sorted(doc["sats"], key=lambda k: int(k)):
        entry = doc["sats"][key]
        if not allow.match(key, entry.get("name")):
            continue
        rows = [t for t in (entry.get("transponders") or [])
                if float(t.get("down") or 0) > 0]
        if not rows:
            continue
        items = []
        for t in rows:
            name = (t.get("name") or "").replace('"', "'")
            items.append('("%s", %s, %s, %s)' % (
                name, fmt_num(t.get("down")), fmt_num(t.get("up")),
                fmt_num(t.get("tone"))))
        lines.append("    %d: [%s]," % (int(key), ", ".join(items)))
    lines.append("}")
    lines.append(END)
    return "\n".join(lines)


def dropped_entries(doc, allow):
    """名单外/无 NORAD 的条目，写成 _dropped_from_source.txt 的一行一条。"""
    out = []
    for key, entry in (doc.get("sats") or {}).items():
        norad = key if str(key).isdigit() else entry.get("norad")
        if not allow.match(norad, entry.get("name")):
            out.append(_allow.dropped_line(norad, entry.get("name")))
    return out


def load_json(json_path=None):
    return json.load(io.open(json_path or JSON_PATH, encoding="utf-8"))


def write_embedded(json_path=None, py_path=None, allow=None):
    """把 JSON 重生成进 sat_freq.py；返回 (卫星数, 行数)。gen_sat_freq --write 也调它。"""
    py_path = py_path or PY_PATH
    allow = allow or _allow.load()
    doc = load_json(json_path)
    block = build_block(doc, allow)
    _allow.record_dropped("transponders.json", dropped_entries(doc, allow))
    src = io.open(py_path, encoding="utf-8").read()
    if BEGIN in src or "# --- EMBEDDED_TRANS begin" in src:
        start = src.index("# --- EMBEDDED_TRANS begin")
        end = src.index(END, start) + len(END)
        src = src[:start] + block + src[end:]
    else:
        anchor = "_JSON_PATH = os.path.join("
        assert anchor in src, "sat_freq.py 里找不到 EMBEDDED_TRANS 块和 _JSON_PATH 锚点"
        src = src.replace(anchor, block + "\n\n\n" + anchor, 1)
    io.open(py_path, "w", encoding="utf-8", newline="\n").write(src)
    n_sat = sum(1 for line in block.splitlines() if line.startswith("    "))
    return n_sat, len(block.splitlines())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="重新生成 sat_freq.py 的内嵌块")
    ap.add_argument("--check", action="store_true", help="只校验内嵌块是否与 JSON 一致")
    args = ap.parse_args()
    if not (args.write or args.check):
        args.check = True

    doc = load_json()
    allow = _allow.load()
    want = build_block(doc, allow)
    src = io.open(PY_PATH, encoding="utf-8").read()
    if args.write:
        n_sat, n_line = write_embedded(allow=allow)
        print("已写入内嵌转发器表：%d 颗卫星 / %d 行" % (n_sat, n_line))
        return 0

    # --check：块必须逐字等于按当前 JSON 生成的结果（连块头注释一起比）
    if BEGIN in src:
        start = src.index(BEGIN)
        end = src.index(END, start) + len(END)
        have = src[start:end]
    else:
        have = None
    if have is None:
        print("sat_freq.py 里没有内嵌转发器块")
        return 1
    if have != want:
        print("内嵌转发器表与 数据/sat_freq.json 不一致，跑 embed_transponders.py --write")
        return 1
    n_sat = sum(1 for line in want.splitlines() if line.startswith("    "))
    print("内嵌转发器表一致：%d 颗卫星 / %d 行" % (n_sat, len(want.splitlines())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
