#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""三端口径静态一致性检查（不需要编译器、不需要电台、不需要模拟器）。

为什么要有这个：本项目反复出现同一类 bug —— 固件、App、PC 工具各自把同一个
约定写了一遍（表结构、flash 地址、协议帧、混淆掩码、状态位），其中一端改了
另一端没跟上，而且**失败方式通常是静默的**：屏幕数字全对、芯片跑的是别的。
这类漂移是纯静态可查的，不该靠人记住。

跑法：python 多普勒工具/verify_layout.py
退出码：0 一致，1 有漂移或有约定解析不到。

设计原则（上一版 verify_data.py 就是栽在这条上）：
  解析不到 = 报错，绝不静默通过。一个会骗人的检查比没有检查更坏。
"""

from __future__ import annotations

import re
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent                       # 多普勒/
FW = ROOT / "固件源码" / "uvk1-v6.3.0" / "App" / "app"
KT = ROOT / "安卓App" / "android-app" / "app" / "src" / "main" / "java" / "com" / "f4hwn" / "doppler"

F_DOP_H = FW / "doppler.h"
F_DOP_C = FW / "doppler.c"
F_UART = FW / "uart.c"
F_CORE = HERE / "doppler_core.py"
F_TOOL = HERE / "doppler_tool.py"
F_PROBE = HERE / "probe_fw.py"
F_PASS_KT = KT / "PassCalc.kt"
F_LINK_KT = KT / "RadioLink.kt"
F_SESS_KT = KT / "RadioSession.kt"
F_MAIN_KT = KT / "MainScreen.kt"
F_FAKE = HERE / "fakeradio.py"
F_RADIO = HERE / "verify_radio.py"

BAD: list[str] = []
WARN: list[str] = []
OK: list[str] = []


def read(p: Path) -> str:
    if not p.exists():
        BAD.append(f"找不到文件：{p.relative_to(ROOT)}")
        return ""
    return p.read_text(encoding="utf-8", errors="replace")


def check(label: str, got: dict[str, object]) -> None:
    """got: 端名 -> 该端的值。全部相等才绿。"""
    vals = {k: v for k, v in got.items() if v is not None}
    if len(vals) < 2:
        BAD.append(f"{label}：只有 {list(vals)} 一端有值，其余端解析不到 —— 检查本身失效了")
        return
    uniq = set(map(repr, vals.values()))
    if len(uniq) == 1:
        OK.append(f"{label} = {next(iter(vals.values()))!r}（{len(vals)} 端一致）")
    else:
        BAD.append(f"{label} 三端不一致：" + "; ".join(f"{k}={v!r}" for k, v in vals.items()))


# ── C 侧解析 ────────────────────────────────────────────────────────
C_SIZE = {"char": 1, "int8_t": 1, "uint8_t": 1, "int16_t": 2, "uint16_t": 2,
          "int32_t": 4, "uint32_t": 4, "int64_t": 8}


def c_defines(txt: str, names: list[str]) -> dict[str, int | str]:
    out: dict[str, int | str] = {}
    for n in names:
        m = re.search(rf"#define\s+{n}\s+(\"+[^\"]*\"|0[xX][0-9A-Fa-f]+u?|\d+u?)", txt)
        if not m:
            continue
        raw = m.group(1)
        out[n] = raw.strip('"') if raw.startswith('"') else int(raw.rstrip("u"), 0)
    return out


def c_struct(txt: str, typedef: str) -> list[tuple[str, int]] | None:
    """packed struct 的字段宽度序列（C 编译器按 packed 布局，无填充）。"""
    m = re.search(rf"typedef struct __attribute__\(\(packed\)\) \{{([^{{}}]*)\}} {typedef};",
                  txt, re.S)
    if not m:
        return None
    fields: list[tuple[str, int]] = []
    for fm in re.finditer(r"(char|int\d+_t|uint\d+_t)\s+(\w+)(?:\[(\d+)\])?\s*;", m.group(1)):
        typ, name, arr = fm.group(1), fm.group(2), fm.group(3)
        n = int(arr) if arr else 1
        fields.append((name, C_SIZE[typ] * n))
    return fields or None


def fmt_widths(fmt: str) -> list[tuple[str, int]] | None:
    """把 struct 格式串拆成 (字段类型, 宽度)。只支持本项目用到的字符集。"""
    if not fmt.startswith("<"):
        return None
    out: list[tuple[str, int]] = []
    for cnt, ch in re.findall(r"(\d*)([bBhHiIcs])", fmt[1:]):
        n = int(cnt) if cnt else 1
        out.append((ch, {"b": 1, "B": 1, "h": 2, "H": 2, "i": 4, "I": 4}[ch] if ch not in "cs"
                        else n))
    return out or None


def kt_write_widths(seq: str) -> list[int] | None:
    """Kotlin 逐字段写入的宽度序列：pad16→16、le32→4、le16→2、字面串→串长、其余→1。"""
    out: list[int] = []
    for m in re.finditer(r"\.write\(([^;]*?)\)\s*(//[^\n]*)?$", seq, re.M):
        arg = m.group(1)
        if "pad16(" in arg:
            out.append(16)
        elif "le32(" in arg:
            out.append(4)
        elif "le16(" in arg:
            out.append(2)
        else:
            lit = re.search(r'"([^"]+)"', arg)
            out.append(len(lit.group(1)) if lit else 1)
    return out or None


def main() -> int:
    dop_h, dop_c, uart = read(F_DOP_H), read(F_DOP_C), read(F_UART)
    core, tool, probe = read(F_CORE), read(F_TOOL), read(F_PROBE)
    pass_kt, link_kt, sess_kt = read(F_PASS_KT), read(F_LINK_KT), read(F_SESS_KT)
    main_kt = read(F_MAIN_KT)
    fake_py, radio_py = read(F_FAKE), read(F_RADIO)

    # ── 1. 混淆掩码（同一张表在项目里有 4 份拷贝）──
    def hex_list(body: str) -> list[int] | None:
        nums = re.findall(r"0[xX]([0-9A-Fa-f]{2})", body)
        return [int(x, 16) for x in nums] or None

    masks = {}
    m = re.search(r"Obfuscation\[16\]\s*=\s*\{(.*?)\};", uart, re.S)
    masks["固件 uart.c"] = hex_list(m.group(1)) if m else None
    m = re.search(r"MASK = intArrayOf\((.*?)\)", link_kt, re.S)
    masks["App RadioLink.kt"] = hex_list(m.group(1)) if m else None
    m = re.search(r"^MASK = bytes\(\[(.*?)\]\)", tool, re.S | re.M)
    masks["PC doppler_tool.py"] = hex_list(m.group(1)) if m else None
    m = re.search(r"^MASK = \[(.*?)\]", probe, re.S | re.M)
    masks["PC probe_fw.py"] = hex_list(m.group(1)) if m else None
    #   假电台是这一张表的第 5 份拷贝。既然已经复制了，就必须一起钉住，
    #   否则多出来的那份会悄悄变成"第 5 种说法"。
    m = re.search(r"^MASK = \[(.*?)\]", fake_py, re.S | re.M)
    masks["假电台 fakeradio.py"] = hex_list(m.group(1)) if m else None
    check("协议混淆掩码（应各 16 字节）", masks)
    for k, v in masks.items():
        if v is not None and len(v) != 16:
            BAD.append(f"{k} 的掩码有 {len(v)} 个字节，不是 16")

    # ── 2. 表结构：表头 / 条目 / 样点 ──
    cdef = c_defines(dop_h, ["DPH_BASE_ADDR", "DPH_CAL_ADDR", "DPH_REGION_SIZE",
                             "DPH_HDR_SIZE", "DPH_ENTRY_SIZE", "DPH_PT_SIZE",
                             "DPH_VERSION", "DPH_VERSION_MIN", "DPH_ENTRY_MAX"])
    for label, typedef, py_fmt, kt_lo, kt_hi in (
        ("表头", "dph_header_t", "<4sBBHII", "val head = ByteArrayOutputStream()", "val body ="),
        ("条目", "dph_entry_t", "<16sIIIHHHHHBB", "val e = ByteArrayOutputStream()", "entries.write"),
    ):
        cfields = c_struct(dop_h, typedef)
        cw = [w for _n, w in cfields] if cfields else None
        pw = None
        if py_fmt in core:
            pw = [w for _t, w in (fmt_widths(py_fmt) or [])]
        km = None
        if kt_lo in pass_kt:
            seg = pass_kt.split(kt_lo, 1)[1].split(kt_hi, 1)[0]
            km = kt_write_widths(seg)
        check(f"{label}结构字段宽度序列", {"固件 .h": cw, "PC core.py": pw, "App PassCalc.kt": km})
        for end, val in (("固件 .h", cw), ("PC core.py", pw), ("App PassCalc.kt", km)):
            if val is None:
                BAD.append(f"{label}结构在 {end} 解析不到 —— 结构大概被改过，本检查需要更新")
        if cfields:
            check(f"{label}总长（按 .h 字段名 {len(cfields)} 个）",
                  {"固件 .h 累加": sum(cw or []),
                   "PC core.py 累加": sum(pw or []),
                   "App 累加": sum(km or [])})

    # 样点：固件从裸字节手工取（DPH_PT_SIZE），PC 用 struct 格式串，App 逐字段写
    c_smp = cdef.get("DPH_PT_SIZE")
    py_smp = struct.calcsize("<hBHb") if "<hBHb" in core else None
    kt_smp = None
    if "for (pt in p.points)" in pass_kt:
        s = pass_kt.split("for (pt in p.points)", 1)[1].split("}", 1)[0]
        w = kt_write_widths(s)
        kt_smp = sum(w) if w else None
    check("样点字节数", {"固件 DPH_PT_SIZE": c_smp, "PC '<hBHb'": py_smp, "App 累加": kt_smp})

    # ── 3. flash 地址与尺寸常量 ──
    pydef = {}
    for n in ["DPH_BASE", "DPH_VERSION", "DPH_HDR", "DPH_ENTRY", "DPH_ENTRY_MAX",
              "DPH_SAMPLE_OFF", "DPH_REGION", "DPH_PT_SIZE"]:
        m = re.search(rf"^{n} = (\S+)", core, re.M)
        if m:
            pydef[n] = int(m.group(1).split("#")[0], 0)
    ktdef = {}
    for cn, key in (("BASE", "DPH_BASE_ADDR"), ("SAMPLE_OFF", None), ("HDR", "DPH_HDR_SIZE"),
                    ("ENTRY", "DPH_ENTRY_SIZE"), ("ENTRY_MAX", "DPH_ENTRY_MAX")):
        m = re.search(rf"const val {cn} = (\S+)", pass_kt)
        if m:
            ktdef[cn] = int(m.group(1).rstrip("L"), 0)

    check("表区起始地址", {"固件 DPH_BASE_ADDR": cdef.get("DPH_BASE_ADDR"),
                          "PC DPH_BASE": pydef.get("DPH_BASE"),
                          "App BASE": ktdef.get("BASE")})
    check("表区容量", {"固件 DPH_REGION_SIZE": cdef.get("DPH_REGION_SIZE"),
                       "PC DPH_REGION": pydef.get("DPH_REGION")})
    check("表头长度", {"固件": cdef.get("DPH_HDR_SIZE"), "PC": pydef.get("DPH_HDR"),
                       "App": ktdef.get("HDR")})
    check("条目长度", {"固件": cdef.get("DPH_ENTRY_SIZE"), "PC": pydef.get("DPH_ENTRY"),
                       "App": ktdef.get("ENTRY")})
    check("表格式版本", {"固件 DPH_VERSION": cdef.get("DPH_VERSION"),
                         "PC DPH_VERSION": pydef.get("DPH_VERSION"),
                         "App 写死的字面量": (lambda m: int(m.group(1)) if m else None)(
                             re.search(r"head\.write\((\d+)\)\s*//\s*version", pass_kt))})

    # 条数上限：count 是 u8，固件常量写 256 但那条校验已被自标恒假，故只告警不判红
    c_max, p_max, k_max = cdef.get("DPH_ENTRY_MAX"), pydef.get("DPH_ENTRY_MAX"), ktdef.get("ENTRY_MAX")
    if len({c_max, p_max, k_max}) > 1:
        WARN.append(f"条目上限三端不同口径：固件 {c_max} / PC {p_max} / App {k_max}。"
                    f"固件 hdr.count 是 u8，实际最大 255，行为一致但常量撒谎——"
                    f"要么把固件改成 255，要么在 .h 里注明")
    else:
        OK.append(f"条目上限 = {c_max}（三端一致）")

    if cdef.get("DPH_VERSION_MIN") and cdef.get("DPH_VERSION_MIN") != cdef.get("DPH_VERSION"):
        WARN.append(f"固件 DPH_VERSION_MIN={cdef['DPH_VERSION_MIN']} 与当前 {cdef['DPH_VERSION']} 不同："
                    f"旧版表仍可读，但 PC/App 只会写新版本，跨度内的兼容要人工确认")

    # ── 4. 0x076C 推帧：名字起始偏移 + 最短合法帧长 ──
    #   这两个数错一处就整帧被静默丢掉（App 里就写过这个坑：短名星算出来 24 字节，
    #   固件 len<28 直接 return）。比逐字段宽度稳，又正好抓这类漂移。
    c_off = c_min = None
    if "void DOPPLER_ExtPush" in dop_c:
        body = dop_c.split("void DOPPLER_ExtPush", 1)[1].split("\n}\n", 1)[0]
        m = re.search(r"p\[(\d+)u?\s*\+\s*k\]", body)
        c_off = int(m.group(1)) if m else None
        m = re.search(r"len\s*<\s*(\d+)u?", body)
        c_min = int(m.group(1)) if m else None
    k_off = k_min = None
    if "fun pushExt" in sess_kt:
        body = sess_kt.split("fun pushExt", 1)[1].split("val t = Thread", 1)[0]
        m = re.search(r"size\s*=\s*(\d+)\s*\+\s*nb\.size", body)
        k_off = int(m.group(1)) if m else None
        m = re.search(r"maxOf\((\d+),\s*size\)", body)
        k_min = int(m.group(1)) if m else None
    check("0x076C 名字字段起始偏移", {"固件 p[21+k]": c_off, "App 21+nb.size": k_off})
    check("0x076C 最短合法帧长", {"固件 len<28 丢弃": c_min, "App maxOf(28,size)": k_min})

    # ── 5. 命令 ID：App 发的每一条，固件必须认 ──
    fw_cmd = {int(x, 16) for x in re.findall(r"case (0x07[0-9A-F]{2}):", uart)}
    kt_cmd = {int("0x" + x, 16) for x in re.findall(r"0x(07[0-9A-F]{2})", link_kt)}
    if not fw_cmd or not kt_cmd:
        BAD.append(f"命令 ID 解析失效：固件 {len(fw_cmd)} 条 / App {len(kt_cmd)} 条，"
                   f"至少一端一个都没抓到 —— 本检查不可信")
    unknown = {c for c in kt_cmd if c % 2 == 0 and c not in fw_cmd}
    if unknown:
        BAD.append("App 会发、但固件没有分支的命令：" +
                   ", ".join(hex(c) for c in sorted(unknown)))
    else:
        OK.append(f"App 发出的命令 ID 全部在固件分支里（固件共 {len(fw_cmd)} 条 0x07xx）")
    unused = sorted(c for c in fw_cmd if c % 2 == 0 and c not in kt_cmd
                    and 0x0760 <= c <= 0x076E)
    if unused:
        WARN.append("多普勒命令族里固件有分支、App 从不调用：" +
                    ", ".join(hex(c) for c in unused) + "（不一定是错，但要确认是没做完还是留白）")

    # ── 6. 状态位：固件报的每一位，App 至少要知道 ──
    fw_bits = set()
    if "uint8_t DOPPLER_ExtState" in dop_c:
        body = dop_c.split("uint8_t DOPPLER_ExtState", 1)[1].split("\n}\n", 1)[0]
        fw_bits = {int(x, 16) for x in re.findall(r"f \|= (0x[0-9A-F]{2})", body)}
    kt_bits = {int(x, 16) for x in re.findall(r"st and (0x[0-9A-F]{2})", sess_kt)}
    missing = sorted(fw_bits - kt_bits)
    if missing:
        WARN.append("固件报出但 App 从不解析的状态位：" + ", ".join(hex(b) for b in missing) +
                    " —— 例如 0x80（正在刷写，PTT 被故意吞掉）没被解析的话，"
                    "写表那 35 秒里 App 无法告诉操作员'按了没反应'是正常现象")
    else:
        OK.append("固件报出的状态位 App 全部有解析")

    # ── 7. 自动交还时长：全项目只能有一处真值 ──
    #   这个数复发过：App 弹窗写"15 s"、固件留着 1500 tick 的死常量，而真正
    #   生效的是 doppler.c 那句 `> 500u`（tick 是 10 ms，所以 5 s）。操作员
    #   只会按屏幕上那个数去等，屏幕上说 15 秒、电台 5 秒就收回控制权，
    #   等于让他多等 10 秒再发现这次过境已经废了。
    def sec(txt: str, pat: str, unit: float = 1.0) -> float | None:
        m = re.search(pat, txt)
        return None if not m else round(float(m.group(1)) * unit, 2)

    claims = {
        "固件 silent 阈值": sec(dop_c, r"ext_ctrl_tick\)\s*>\s*(\d+)u?", 0.01),
        "App 接管弹窗文案": sec(main_kt, r"断链\s*(\d+)\s*s"),
        "App releaseControl 注释": sec(sess_kt, r"只认\s*(\d+)\s*秒内"),
        "App 看门狗注释": sec(sess_kt, r"看门狗会在\s*(\d+)\s*秒后"),
        #   同一个时长在项目里出现过第三种说法（1.2 s，是 5 Hz 推帧时代的老阈值），
        #   代码改到 5 s 之后没人回头改注释 —— 这类漂移就是这条要抓的。
        "App 推帧心跳注释": sec(sess_kt, r"(\d+(?:\.\d+)?)\s*秒不来固件就认为手机死"),
        "假电台默认 watchdog": sec(fake_py, r"--watchdog-ms[^\n]*?default=([\d.]+)", 0.001),
        "时序层全量模式": sec(radio_py, r"if a\.quick else ([\d.]+)", 0.001),
    }
    check("断链后自动交还时长（秒）", claims)

    #   死常量单独判红：它不参与判断，只负责让下一个改代码的人抄错。
    hits = [ln.strip() for ln in dop_c.splitlines() if "DPH_EXT_TIMEOUT_TICKS" in ln]
    code = [ln for ln in hits if not ln.startswith(("*", "//", "/*"))]
    if code and all(ln.startswith("#define") for ln in code):
        BAD.append(f"固件死常量 DPH_EXT_TIMEOUT_TICKS 还留着（{code[0]}），"
                   f"用它那个分支已经删掉了 —— 注释和常量都在说 15 s，代码是 5 s")
    elif code:
        OK.append(f"DPH_EXT_TIMEOUT_TICKS 仍被 {len(code)} 处代码引用")

    # ── 8. 多普勒补偿的符号（上下行必须反号）──
    #   这类错误编译通过、跑起来也"看着在动"，只有对着一份权威实现比符号才看得出来。
    #   起因：在线推帧的上行曾写成 upHz + upDopp（与下行的 + 号同形），而固件自己那套
    #   是 up10 - 补偿、离线表存的是标称值 —— 只有在线这一条路反了，145.99 MHz 上
    #   AOS/LOS 两端差到 7 kHz，FM 静噪开不了，现象是"手机控频一发就没人应"。
    def sign(txt: str, pat: str) -> str | None:
        m = re.search(pat, txt)
        return None if not m else m.group(1)

    kt_rx = sign(main_kt, r"rxHz\s*([+-])\s*dop\b")
    kt_tx = sign(main_kt, r"upHz\s*([+-])\s*upDopp")
    fw_tx = None
    #   只切定义那一处：文件开头还有个前向声明，从它切就什么都切不到。
    m = re.search(r"static uint32_t DPH_Tx10WithOff\(int32_t off_hz\)\s*\{(.*?)\n\}",
                  dop_c, re.S)
    if m:
        g = re.search(r"up10\s*([+-])", m.group(1))
        fw_tx = g.group(1) if g else None
    if kt_rx is None or kt_tx is None or fw_tx is None:
        BAD.append(f"符号检查解析失效：App 下行 {kt_rx} / App 上行 {kt_tx} / 固件上行 {fw_tx}"
                   f" —— 有一端没匹配到，本检查不可信")
    else:
        if kt_rx != "+":
            BAD.append(f"App 下行补偿符号应为 +（rxHz + dop），实得 rxHz {kt_rx} dop")
        if kt_tx != fw_tx:
            BAD.append(f"App 在线推帧的上行是 upHz {kt_tx} upDopp，固件是 up10 {fw_tx} 补偿 "
                       f"—— 两端反号，电台会朝错误的频点发射")
        if kt_rx == "+" and kt_tx == fw_tx:
            OK.append(f"多普勒补偿符号：App 下行 {kt_rx} / App 上行 {kt_tx} = 固件上行 {fw_tx}")

    # ── 输出 ──
    for line in OK:
        print(f"  ✓ {line}")
    for line in WARN:
        print(f"  ! {line}")
    for line in BAD:
        print(f"  ✗ {line}")
    print(f"\n一致 {len(OK)} · 告警 {len(WARN)} · 漂移 {len(BAD)}")
    print("结果：" + ("FAIL" if BAD else ("WARN" if WARN else "OK")))
    return 1 if BAD else (2 if WARN else 0)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
