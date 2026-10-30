#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""卫星频点的单一数据源 → 安卓 App 的 Freqs.kt（生成 / 校验）。

为什么要有这个脚本
------------------
以前频点写在三个地方，各写一份、各自漂移：
  * 安卓 `app/src/main/java/com/f4hwn/doppler/Freqs.kt`（11 颗，带上行/哑音）
  * PC 工具 `sat_freq.py` 的 FREQ（21 颗，只有下行）
  * 交接文档里的表格
结果是同一颗星在手机上能通、在电脑上有另一个数，PO-101 的上行甚至是错的
（写 435.100，真值是 437.500）。屏幕上的数字必须等于机器里真正的数字，
所以改成一个 JSON 当唯一真源，两边都从它来。

用法
----
    python gen_sat_freq.py --write   # 重新生成 Freqs.kt（改完 JSON 就跑它）
    python gen_sat_freq.py --check   # 只校验三处是否一致，不写文件（CI / verify_all 用）

`--check` 的判定：
  1. JSON 里 down <= 0 的条目算错；
  2. Freqs.kt 里的条目集合、下行/上行/哑音/SSTV/直通 必须与 JSON 完全一致；
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
KT_PATH = os.path.join(
    WS, "多普勒", "安卓App", "android-app", "app", "src", "main", "java",
    "com", "f4hwn", "doppler", "Freqs.kt",
)
PY_PATH = os.path.join(HERE, "sat_freq.py")


def load_json():
    with io.open(JSON_PATH, encoding="utf-8") as f:
        d = json.load(f)
    return d["sats"]


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="重新生成 Freqs.kt")
    ap.add_argument("--check", action="store_true", help="只校验，不写文件")
    args = ap.parse_args()
    if not (args.write or args.check):
        args.check = True

    sats = load_json()
    bad = []
    for n, e in sats.items():
        if float(e.get("down") or 0) <= 0:
            bad.append(f"{n} 的 down 不是正数")
        d, u = float(e.get("down") or 0), float(e.get("up") or 0)
        if d and not (29.0 <= d <= 1300.0):
            bad.append(f"{n} 的下行 {d} MHz 不在 29~1300 MHz 内")
        if u and not (29.0 <= u <= 1300.0):
            bad.append(f"{n} 的上行 {u} MHz 不在 29~1300 MHz 内")
    if bad:
        print("JSON 自身有问题：")
        for b in bad:
            print("  - " + b)
        return 2

    if args.write:
        kt = render_kt(sats)
        io.open(KT_PATH, "w", encoding="utf-8", newline="\n").write(kt)
        print("已生成 " + KT_PATH + f"（{len(sats)} 颗）")
        return 0

    # ── 校验 ────────────────────────────────────────────────────────────
    problems = []
    kt = parse_kt(KT_PATH)
    if not kt:
        problems.append("Freqs.kt 一条都没解析出来（文件被手改过？）")
    for n in sorted(set(kt) - set(sats), key=int):
        problems.append(f"Freqs.kt 有 {n}，JSON 里没有")
    for n in sorted(set(sats) - set(kt), key=int):
        problems.append(f"JSON 有 {n}（{sats[n].get('name')}），Freqs.kt 里没有 —— 跑 --write")
    for n in sorted(set(kt) & set(sats), key=int):
        a, b = kt[n], sats[n]
        for key in ("down", "up", "tone", "sstv", "voice_rx", "voice_tx"):
            av = float(a.get(key) or 0.0)
            bv = float(b.get(key) or 0.0)
            if abs(av - bv) > 1e-6:
                problems.append(f"{n}（{b.get('name')}）的 {key}：Freqs.kt={av} JSON={bv}")

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
    print(f"频点一致：JSON {len(sats)} 颗 / Freqs.kt {len(kt)} 颗 / sat_freq.py 交集 {len(set(py) & set(sats))} 颗")
    return 0


if __name__ == "__main__":
    sys.exit(main())
