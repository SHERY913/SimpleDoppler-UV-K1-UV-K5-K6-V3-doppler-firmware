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
F_PROTO = HERE / "radio_proto.py"
F_LAYOUT = FW / "doppler_layout.h"
F_MB_H = FW.parent / "driver" / "mb_flash.h"   # 外部 flash 布局的权威表
F_ST7565 = FW.parent / "driver" / "st7565.h"   # LCD 的物理尺寸（夹紧的参照）
F_EDITOR = HERE / "layout_editor.html"
F_CLEAR = HERE / "clear_table.py"              # 也照着表头标识决定放不放行擦除
F_DETAIL_KT = KT / "ui" / "DetailScreen.kt"    # App 的擦除闸门
F_DOC = HERE / "多普勒使用说明.md"
F_FLASH_MAP = ROOT / "文档" / "历史" / "外部flash空间地图-实测.md"
F_DELIV = ROOT / "交付" / "README.md"

BAD: list[str] = []
WARN: list[str] = []
OK: list[str] = []


def read(p: Path) -> str:
    if not p.exists():
        BAD.append(f"找不到文件：{p.relative_to(ROOT)}")
        return ""
    return p.read_text(encoding="utf-8", errors="replace")


def check(label: str, got: dict[str, object]) -> None:
    """got: 端名 -> 该端的值。全部相等才绿；**声明了却解析不到的那一端也判红**。
    原来只要求"至少两端有值"，于是三端里掉了一端照样绿（屏幕上写 2 端一致，
    读的人以为比过了）—— 而"某一端把常量改了名/删了"正是这条要抓的事。"""
    vals = {k: v for k, v in got.items() if v is not None}
    if len(vals) < 2:
        BAD.append(f"{label}：只有 {list(vals)} 一端有值，其余端解析不到 —— 检查本身失效了")
        return
    if len(vals) < len(got):
        miss = "、".join(k for k, v in got.items() if v is None)
        BAD.append(f"{label}：{miss} 解析不到 —— 这条当下只比了 {len(vals)} 端，"
                   f"掉的那一端改了也不会有人知道")
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
    proto_py = read(F_PROTO)

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
    m = re.search(r"^MASK = \[(.*?)\]", proto_py, re.S | re.M)
    masks["协议库 radio_proto.py"] = hex_list(m.group(1)) if m else None
    check("协议混淆掩码（应各 16 字节）", masks)
    for k, v in masks.items():
        if v is not None and len(v) != 16:
            BAD.append(f"{k} 的掩码有 {len(v)} 个字节，不是 16")

    # ── 2. 表结构：表头 / 条目 / 样点 ──
    cdef = c_defines(dop_h, ["DPH_BASE_ADDR", "DPH_CAL_ADDR", "DPH_REGION_SIZE",
                             "DPH_HDR_SIZE", "DPH_ENTRY_SIZE", "DPH_PT_SIZE",
                             "DPH_VERSION", "DPH_VERSION_MIN", "DPH_ENTRY_MAX",
                             "DPH_PASS_MAX", "DPH_SAMPLE_OFF", "DPH_STEP_MS_MIN",
                             "DPH_DUR_S_MAX", "DPH_MAGIC"])
    mb_h = read(F_MB_H)
    sonic = c_defines(mb_h, ["MB_SONIC_RESERVE_BASE"]).get("MB_SONIC_RESERVE_BASE")
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
              "DPH_SAMPLE_OFF", "DPH_REGION", "DPH_PT_SIZE", "DPH_PASS_MAX",
              "DPH_STEP_MS_MIN", "DPH_DUR_S_MAX", "DPH_DAY_MAX"]:
        m = re.search(rf"^{n} = (\S+)", core, re.M)
        if m:
            pydef[n] = int(m.group(1).split("#")[0], 0)
    ktdef = {}
    for cn, key in (("BASE", "DPH_BASE_ADDR"), ("SAMPLE_OFF", None), ("HDR", "DPH_HDR_SIZE"),
                    ("ENTRY", "DPH_ENTRY_SIZE"), ("ENTRY_MAX", "DPH_ENTRY_MAX"),
                    ("PASS_MAX", None), ("MAX_TABLE_BYTES", None),
                    ("STEP_MS_MIN", None), ("DUR_S_MAX", None), ("DAY_MAX", None)):
        m = re.search(rf"const val {cn} = (\S+)", pass_kt)
        if m:
            ktdef[cn] = int(m.group(1).rstrip("L"), 0)

    check("表区起始地址", {"固件 DPH_BASE_ADDR": cdef.get("DPH_BASE_ADDR"),
                          "PC DPH_BASE": pydef.get("DPH_BASE"),
                          "App BASE": ktdef.get("BASE")})
    # 表区容量同时是**擦除上限**：PC 的 erase_plan 与 App 的 sectors() 都从它推导，
    # 所以这一项不只是"能写多少"，而是"最多敢擦多大"。三端必须一个数。
    check("表区容量（= 擦除上限）",
          {"固件 DPH_REGION_SIZE": cdef.get("DPH_REGION_SIZE"),
           "PC DPH_REGION": pydef.get("DPH_REGION"),
           "App MAX_TABLE_BYTES": ktdef.get("MAX_TABLE_BYTES")})
    check("一次最多写几趟",
          {"固件 DPH_PASS_MAX": cdef.get("DPH_PASS_MAX"),
           "PC DPH_PASS_MAX": pydef.get("DPH_PASS_MAX"),
           "App PASS_MAX": ktdef.get("PASS_MAX")})
    # 下面三条以前是"抓到了却没比"或者"根本没抓"。样点区起点这条有现成的账：
    # App 在 1.5 版里是 0x3000，固件那轮改成 0x1000，差出来的 8 KiB 每次写表
    # 都被填零盖出去 —— 三端比过这条，就不会等到新固件把它判成 BAD LAYOUT 才发现。
    check("样点区起点（条目区的物理边界）",
          {"固件 DPH_SAMPLE_OFF": cdef.get("DPH_SAMPLE_OFF"),
           "PC DPH_SAMPLE_OFF": pydef.get("DPH_SAMPLE_OFF"),
           "App SAMPLE_OFF": ktdef.get("SAMPLE_OFF")})
    check("采样间隔下限 ms",
          {"固件 DPH_STEP_MS_MIN": cdef.get("DPH_STEP_MS_MIN"),
           "PC DPH_STEP_MS_MIN": pydef.get("DPH_STEP_MS_MIN"),
           "App STEP_MS_MIN": ktdef.get("STEP_MS_MIN")})
    check("单趟时长上限 s",
          {"固件 DPH_DUR_S_MAX": cdef.get("DPH_DUR_S_MAX"),
           "PC DPH_DUR_S_MAX": pydef.get("DPH_DUR_S_MAX"),
           "App DUR_S_MAX": ktdef.get("DUR_S_MAX")})
    # 固件不参与这条：4 bit 折回后的 +0 与合法的 +0 在 flash 里是同一串字节，
    # 电台读不出差别，只有写表的一方拦得住（见 doppler.h 的 flags 注释）。
    check("一张表覆盖到表头之后第几天",
          {"PC DPH_DAY_MAX": pydef.get("DPH_DAY_MAX"),
           "App DAY_MAX": ktdef.get("DAY_MAX")})
    # 擦除不能越进 Sonic 4K 互操作保留区：固件里是 _Static_assert，
    # 这里再算一遍，防的是"改了 C 的常量、忘了改 Python/Kotlin"这种半拉子。
    if sonic is None:
        BAD.append("解析不到 MB_SONIC_RESERVE_BASE —— 布局表被改过？本检查需要更新")
    else:
        ends = {"固件": (cdef.get("DPH_BASE_ADDR") or 0) + (cdef.get("DPH_REGION_SIZE") or 0),
                "PC":   (pydef.get("DPH_BASE") or 0) + (pydef.get("DPH_REGION") or 0),
                "App":  (ktdef.get("BASE") or 0) + (ktdef.get("MAX_TABLE_BYTES") or 0)}
        over = {k: v for k, v in ends.items() if v > sonic}
        if over:
            BAD.append("表区末尾越进 Sonic 4K 保留区 (0x%06X)：%s"
                       % (sonic, "; ".join(f"{k} 到 0x{v:06X}" for k, v in over.items()))
                       + " —— 擦除会踩不可恢复的地盘")
        else:
            OK.append("表区末尾 0x%06X 三端一致且不越进 Sonic 4K 保留区 (0x%06X)"
                      % (ends["固件"], sonic))
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
    # FIX(永绿): 原来只比 len({...}）> 1。三端正则全都失配时三个值都是 None，
    # {None} 的长度是 1 → 走 else → 打印"条目上限 = None（三端一致）"并且算通过。
    # 本文件第 14 行自己写的原则是"解析不到 = 报错，绝不静默通过"，
    # 这条漏了；常量一改名字或换个写法，这一层就悄悄变成一条什么都不看的绿线。
    if c_max is None or p_max is None or k_max is None:
        BAD.append(f"条目上限解析失效：固件 {c_max} / PC {p_max} / App {k_max} "
                   f"—— 常量大概被改名或换了写法，这条检查本身需要更新")
    elif len({c_max, p_max, k_max}) > 1:
        WARN.append(f"条目上限三端不同口径：固件 {c_max} / PC {p_max} / App {k_max}。"
                    f"固件 hdr.count 是 u8，实际最大 255，行为一致但常量撒谎——"
                    f"要么把固件改成 255，要么在 .h 里注明")
    else:
        OK.append(f"条目上限 = {c_max}（三端一致）")

    if cdef.get("DPH_VERSION_MIN") and cdef.get("DPH_VERSION_MIN") != cdef.get("DPH_VERSION"):
        WARN.append(f"固件 DPH_VERSION_MIN={cdef['DPH_VERSION_MIN']} 与当前 {cdef['DPH_VERSION']} 不同："
                    f"旧版表仍可读，但 PC/App 只会写新版本，跨度内的兼容要人工确认")

    # ── 3b. 表头标识：它是"这块地是不是我的"的唯一判据 ──
    #   固件一个常量，PC 七个文件、App 两个文件各自抄了一遍字符串（共 20 多处）。
    #   这一条以前没有任何一层比过 —— 和 SAMPLE_OFF 完全同一种"三端各写一份"，
    #   只是它管的是擦除放行：认不出自己的表 → 该放行的拒擦；认错了别人的表 →
    #   该拒的放行（语音包没了）。两种都只在按下"写表"之后才看得见。
    def magic_of(txt: str) -> object:
        s = set(re.findall(r'''["'](DPT\d)["']''', txt))
        return None if not s else (next(iter(s)) if len(s) == 1 else " / ".join(sorted(s)))

    check("表头标识 DPT1（擦除放行的判据）",
          {"固件 DPH_MAGIC": cdef.get("DPH_MAGIC"),
           "PC doppler_core.py": magic_of(core),
           "PC doppler_tool.py": magic_of(tool),
           "PC clear_table.py": magic_of(read(F_CLEAR)),
           "App PassCalc.kt": magic_of(pass_kt),
           "App DetailScreen.kt": magic_of(read(F_DETAIL_KT))})

    # ── 3c. 校准记录在外部 flash 的哪一扇区：文档必须跟着常量走 ──
    #   本轮 §三.3 修的那条错（文档写 0x171000，而它正落在表区 16 个扇区里）
    #   没有任何一层能抓到 —— 照着地图去"清校准"的人会先把整张过境表擦掉。
    #   判据：凡同时提到"校准"和一个外置 flash 地址（0x100000..0x1FFFFF）的行，
    #   那个地址就必须是 DPH_CAL_ADDR；一份文档里一条都找不到也判红（说明说法变了，
    #   这条检查瞎了）。0x200000 是 flash 末尾的区间上界，不算说法，排除在外。
    for label, txt in (("多普勒使用说明", read(F_DOC)),
                       ("外部flash空间地图", read(F_FLASH_MAP)),
                       ("交付README", read(F_DELIV))):
        said = []
        for ln in txt.splitlines():
            if "校准" not in ln:
                continue
            said += [int(x, 16) for x in re.findall(r"0x([0-9A-Fa-f]{6})", ln)
                     if 0x00100000 <= int(x, 16) < 0x00200000]
        if not said:
            BAD.append(f"{label} 里找不到任何一处带地址的校准说法 —— DPH_CAL_ADDR 这条"
                       f"没有对照，检查本身失效了")
        elif set(said) != {cdef.get("DPH_CAL_ADDR")}:
            BAD.append(f"{label} 写的校准地址是 "
                       + ", ".join(f"0x{v:06X}" for v in sorted(set(said)))
                       + f"，而固件 DPH_CAL_ADDR = 0x{cdef.get('DPH_CAL_ADDR') or 0:06X}"
                       + " —— 照着文档去擦的那个扇区不是校准记录所在")
        else:
            OK.append(f"{label} 的校准扇区说法 = 0x{cdef['DPH_CAL_ADDR']:06X}（与固件一致）")

    # ── 4. 0x076C 推帧：名字起始偏移 + 最短合法帧长 ──
    #   这两个数错一处就整帧被静默丢掉（App 里就写过这个坑：短名星算出来 24 字节，
    #   固件 len<28 直接 return）。比逐字段宽度稳，又正好抓这类漂移。
    c_off = c_min = None
    if "void DOPPLER_ExtPush" in dop_c:
        body = dop_c.split("void DOPPLER_ExtPush", 1)[1].split("\n}\n", 1)[0]
        m = re.search(r"p\[(\d+)u?\s*\+\s*k\]", body)
        c_off = int(m.group(1)) if m else None
        m = re.search(r"\|\|\s*len\s*<\s*(\d+)u?", body)
        c_min = int(m.group(1)) if m else None
    k_off = k_min = None
    if "fun pushExt" in sess_kt:
        body = sess_kt.split("fun pushExt", 1)[1].split("val t = Thread", 1)[0]
        m = re.search(r"size\s*=\s*(\d+)\s*\+\s*nb\.size", body)
        k_off = int(m.group(1)) if m else None
        m = re.search(r"maxOf\((\d+),\s*size\)", body)
        k_min = int(m.group(1)) if m else None
    check("0x076C 名字字段起始偏移", {"固件 p[21+k]": c_off, "App 21+nb.size": k_off})
    # 固件下限现在按真实布局取 21，App 仍补零到 28（兼容只认 28 的旧固件），
    # 所以这里要的是"App 发出去的长度 ≥ 固件下限"，不是两端数字相等。
    if c_min is None or k_min is None:
        BAD.append("0x076C 最短帧长解析失效（固件或 App 一侧没抓到数字）")
    elif k_min < c_min:
        # 这是真漂移，不是告警：短名星（"ISS" 24 字节）会被静默丢掉整帧。
        # 以前这里 append 到一个从没定义过的 DRIFT 上，真出事时抛 NameError，
        # 检查自己先崩 —— 而"崩了"和"过了"在人眼里长得一样。
        BAD.append(f"0x076C 最短帧长：App 只保证 {k_min} 字节，固件下限 {c_min} —— "
                   f"短名星（ISS）会被静默丢弃")
    else:
        OK.append(f"0x076C 最短帧长：固件下限 {c_min} ≤ App 补零后 {k_min}")

    # ── 5. 命令 ID：App 发的每一条，固件必须认 ──
    fw_cmd = {int(x, 16) for x in re.findall(r"case (0x07[0-9A-F]{2}):", uart)}
    # 只认真的发出去的那些：`ask(0x076A, …)`。以前把 RadioLink.kt 里所有 0x07xx
    # 字面量都算进来，于是注释、范围判断（cmd in 0x0760..0x076F）全被当成命令，
    # 报出"App 会发、但固件没有分支"。要校的是调用点，不是源码文本。
    kt_cmd = {int("0x" + x, 16) for x in re.findall(r"ask\(\s*0x(07[0-9A-F]{2})", link_kt)}
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

    # ── 9. 台站原点：只能有一个来源，而且必须是用户设的 ──
    #   43.83/87.62 是乌鲁木齐的坐标，当年直接写在 MainScreen.kt 的一行 remember 里，
    #   界面上没有任何入口能改：别人装上这个 App，过境时刻、仰角、斜距、多普勒样点
    #   全按乌鲁木齐算，还会把这张错表写进电台外部 flash，而界面一路报「成功」。
    hard = re.findall(r"Station\s*\(\s*\d+", main_kt)
    if hard:
        BAD.append("App 里又出现了硬编码台站坐标：" + ", ".join(hard) +
                   " —— 原点必须走 Geo.StationStore.load()，未设时挡住写表与控频")
    elif "StationStore.load(ctx)" not in main_kt:
        BAD.append("App 没有从 Geo.StationStore 读台站（MainScreen.kt 里找不到 load(ctx)）")
    else:
        un = [k for k in ("writeTable", "liveTune") if k in main_kt]
        gated = "Geo.StationStore.isUnset(station)" in main_kt
        OK.append(f"台站原点单一来源（Geo.StationStore）；未设时的闸门：{'有' if gated else '没有'}"
                  f"（挡在 {'/'.join(un)} 前面）")
        if not gated:
            BAD.append("App 没在台站未设时挡住计算/写表 —— 又拿默认坐标蒙混")

    # ── 10. 表驱动排版的页表与占位符 ──
    #   App/app/doppler_layout.h 是 layout_editor.html 导出的产物，页名在排版器
    #   的 PAGES 里，占位符的**实现**只在 doppler.c 的 DPH_Expand 里、**预览**在
    #   排版器的 SAMPLE 里。三处各写一遍，而漏的地方一个都不会报错：
    #     漏页       → 用排版器重新导出时整页被丢掉（新增的信息页就这么白加）
    #     漏实现     → 屏幕上原样打出 "$%" 而不是日期
    #     漏预览     → 排版器里那行是空白，没人看得出该填什么
    #   注意 App 侧 UiLayout.kt 不参与这条：它是 App 自己的按钮/天空页布局
    #   （PAGE_RADIO / PAGE_SKY），不镜像固件这套页表，固件加页不需要动它。
    lay = read(F_LAYOUT)
    ed = read(F_EDITOR)
    lay_items = [(int(p), txt) for p, txt in re.findall(
        r"\{\s*(\d+)\s*,\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*\"([^\"]*)\"\s*\}", lay)]
    if not lay_items:
        BAD.append("doppler_layout.h 解析不到任何条目 —— 排版表格式变了，本检查需要更新")
    lay_pages = {p for p, _t in lay_items}
    lay_ph = {t[i + 1] for _p, t in lay_items for i in range(len(t) - 1) if t[i] == "$"}

    m = re.search(r"PAGES\s*=\s*\[(.*?)\]", ed, re.S)
    ed_pages = re.findall(r"'([^']+)'", m.group(1)) if m else []
    m = re.search(r"const SAMPLE=\{(.*?)\};", ed, re.S)
    ed_ph = set(re.findall(r"\$([ -~])", m.group(1))) if m else set()
    fw_ph = set(re.findall(r"case '(.)':", dop_c))

    if not ed_pages:
        BAD.append("layout_editor.html 解析不到 PAGES —— 本检查失效")
    else:
        over = sorted(p for p in lay_pages if p >= len(ed_pages))
        if over:
            BAD.append(
                f"固件排版表用到 page {over}，但排版器 PAGES 只登记了 {len(ed_pages)} 页"
                f"（{', '.join(ed_pages)}）—— 在 layout_editor.html 里重新导出 doppler_layout.h"
                f" 会把这些页整页丢掉。该改：PC 工具 layout_editor.html 的 PAGES 与 "
                f"defaultModel()，补上对应页名与行（本轮固件新增的是 page 7 = PRE-LISTEN "
                f"与 page 8 = POST-LISTEN）。")
        else:
            OK.append(f"排版页口径：固件用 page {sorted(lay_pages)}，排版器登记 {len(ed_pages)} 页"
                      f"（{', '.join(ed_pages)}）")

    # 排版器的 defaultModel 必须与固件那份**逐条相等**：一个是"人打开编辑器看到的
    # 默认"，一个是"机器真正画的那张表"。上一轮加信息页时只动了固件，
    # defaultModel 里 page 6 还是三行旧文案、RUN 页少了 $O Hz、READY 页还写着
    # 死的 "M  START"（固件早就换成 $! 了）、空表页还是 "NO DATA"（换成 $@ 了）
    # —— 谁点一次「恢复默认布局」再导出，那些修复就无声无息地被顶掉。
    # 所以这条不只比页号，比到每一条的字体、坐标和文案。
    m = re.search(r"function defaultModel\(\)\s*\{.*?return \[(.*?)\n  \];", ed, re.S)
    fw_full = [(int(p), int(f), int(al), int(x), int(y), t) for p, f, al, x, y, t
               in re.findall(r"\{\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,"
                             r"\s*\"([^\"]*)\"\s*\}", lay)]
    if not m:
        BAD.append("layout_editor.html 解析不到 defaultModel() 的数组 —— 本检查需要更新")
    elif not fw_full:
        BAD.append("doppler_layout.h 解析不到完整条目 —— 本检查需要更新")
    else:
        F = {"tiny": 0, "small": 1, "bold": 2, "big": 3, "bar": 4}
        A = {"left": 0, "centre": 1, "center": 1, "right": 2}
        ed_full = []
        for pg, rows in re.findall(r"/\*\s*(\d+).*?\*/\s*\[(.*?)\]", m.group(1), re.S):
            for r in re.findall(r"\{text:\s*'(.*?)',\s*font:\s*'(\w+)',\s*x:\s*(\d+),"
                                r"\s*y:\s*(\d+),\s*align:\s*'(\w+)'\}", rows):
                ed_full.append((int(pg), F[r[1]], A[r[4]], int(r[2]), int(r[3]), r[0]))
        if len(ed_full) * 2 < len(fw_full):
            BAD.append(f"排版器 defaultModel 只解析到 {len(ed_full)} 条，固件有 {len(fw_full)} 条 "
                       "—— 解析式失效，这条检查别看空气")
        elif ed_full == fw_full:
            OK.append(f"排版器 defaultModel 与固件排版表逐条一致（{len(fw_full)} 条 / "
                      f"{len(lay_pages)} 页）—— 编辑器里导出不会偷偷改掉固件的版面")
        else:
            only_fw = [it for it in fw_full if it not in ed_full]
            only_ed = [it for it in ed_full if it not in fw_full]
            BAD.append(
                "排版器 defaultModel 与固件 doppler_layout.h 不是同一张表："
                f"固件多 {len(only_fw)} 条 / 编辑器多 {len(only_ed)} 条。"
                " 固件侧：" + "; ".join(f"p{p} {t}@({x},{y})" for p, _f, _a, x, y, t in only_fw[:6])
                + " 编辑器侧：" + "; ".join(f"p{p} {t}@({x},{y})"
                                            for p, _f, _a, x, y, t in only_ed[:6])
                + " —— 在 layout_editor.html 里把默认布局改成与固件一致（文案真值以导出为准）")

    # ── 5b. 进度条项只能排在固件真会画它的页上 ──
    #   DPH_Progress 只在几个相位里动手，其余相位第一句就 return —— 排在那些页上的
    #   框永远是空的。而排版器的预览无条件画一条 60% 的假条（layout_editor.html 的
    #   drawBar），于是"编辑器里看着有、电台上没有"。本轮新增的 page 7（预听）正挨着
    #   RUN 页，是最容易被顺手拖进去一个条的一页。
    #   允许的页号不写死：从固件现推（DPH_Progress 点名的相位 ×
    #   UI_DisplayDoppler 里"该相位画第几页"的映射），两个解析任一失配就判红。
    prog = (dop_c.split("static void DPH_Progress", 1)[1].split("\n}\n", 1)[0]
            if "static void DPH_Progress" in dop_c else "")
    bar_ph = sorted(set(re.findall(r"phase [!=]= (DPH_[A-Z]+)", prog)))
    page_of = dict(re.findall(r"case (DPH_[A-Z]+):\s*DPH_DrawPage\((\d+)\)", dop_c))
    bars = sorted({p for p, f, _a, _x, _y, _t in fw_full if f == 4})
    ph_names = "、".join(p.replace("DPH_", "") for p in bar_ph)
    if not bar_ph:
        BAD.append("解析不到 DPH_Progress 里的相位清单（那个函数改了名或换了写法）"
                   " —— 进度条页码这条看不见，判红")
    elif not page_of:
        BAD.append("解析不到 UI_DisplayDoppler 的 case → DPH_DrawPage 映射"
                   " —— 同上，这条看不见，判红")
    elif not bars:
        WARN.append("版面里没有任何进度条项（font 4）—— 这条没有可对照的对象")
    else:
        ok_pages = sorted({int(page_of[p]) for p in bar_ph if p in page_of})
        bad_pages = [p for p in bars if p not in ok_pages]
        if bad_pages:
            BAD.append(f"排版表把进度条放在 page {bad_pages}，而固件只在 page {ok_pages} 画它"
                       f"（相位 {ph_names}） —— 编辑器预览会画一条 60% 的假条，"
                       f"电台上是永远空着的框")
        else:
            OK.append(f"进度条项只在固件会画它的页上：page {bars} ⊆ {ok_pages}"
                      f"（相位 {ph_names}）")

    #   同一个函数的另外两个夹紧：`x0 > LCD_WIDTH-89` 挪到那个值、`y > FRAME_LINES*8-7`
    #   同理。超出之后固件是**整条搬回来**，编辑器是**逐像素裁掉**（plot() 判界），
    #   于是排得太靠右的条在预览里"右边出屏"、在电台上"整条左移"，两边都看不出
    #   自己错在哪。夹紧的数不写死：两个被减数从 DPH_Progress 正文里抓，
    #   LCD_WIDTH / FRAME_LINES 从 driver/st7565.h 抓，任一处抓不到就判红。
    st7565 = read(F_ST7565)
    lcd_w = re.search(r"#define\s+LCD_WIDTH\s+(\d+)", st7565)
    fr_l = re.search(r"#define\s+FRAME_LINES\s+(\d+)", st7565)
    bar_w = re.search(r"LCD_WIDTH\s*-\s*(\d+)u?", prog)
    bar_h = re.search(r"FRAME_LINES\s*\*\s*8u?\s*-\s*(\d+)u?", prog)
    BARS = [(p, x, y) for p, f, _a, x, y, _t in fw_full if f == 4]
    if not bar_ph or not page_of:
        BAD.append("进度条坐标这条没对照（上面的相位/页号解析失效）")
    elif not (lcd_w and fr_l and bar_w and bar_h):
        BAD.append("进度条夹紧解析不到：LCD_WIDTH=%s FRAME_LINES=%s 宽减数=%s 高减数=%s"
                   " —— DPH_Progress 或 st7565.h 改了写法，这条看不见，判红"
                   % (bool(lcd_w), bool(fr_l), bool(bar_w), bool(bar_h)))
    else:
        x_max = int(lcd_w.group(1)) - int(bar_w.group(1))
        y_max = int(fr_l.group(1)) * 8 - int(bar_h.group(1))
        off = [(p, x, y) for p, x, y in BARS if x > x_max or y > y_max]
        if off:
            BAD.append("进度条项超出固件不夹就画不下的范围："
                       + "; ".join(f"page {p} 的 (x={x}, y={y})" for p, x, y in off)
                       + f" —— 固件会把整条夹到 x≤{x_max} / y≤{y_max}，编辑器只是把出屏的"
                         "像素裁掉，两边画出来的不是同一个位置")
        else:
            OK.append(f"进度条项坐标都在固件的夹紧范围内（{len(BARS)} 个，"
                      f"x ≤ {x_max}、y ≤ {y_max}，两个数从固件现推）")

    if not fw_ph:
        BAD.append("doppler.c 里解析不到任何 `case 'x':` —— 占位符实现变了，本检查需要更新")
    else:
        miss_impl = sorted(lay_ph - fw_ph)
        if miss_impl:
            BAD.append("布局用了 $" + " $".join(miss_impl) +
                       "，但 doppler.c 的 DPH_Expand 没有对应 case —— 屏幕上会原样打出 \"$\" + 那个字")
        else:
            OK.append(f"布局占位符全部有实现（{len(lay_ph)} 种，固件共 {len(fw_ph)} 种）")

    if not ed_ph:
        BAD.append("layout_editor.html 解析不到 SAMPLE 表 —— 本检查失效")
    else:
        miss_prev = sorted(lay_ph - ed_ph)
        if miss_prev:
            BAD.append("布局用了 $" + " $".join(miss_prev) +
                       "，但排版器的 SAMPLE 表没登记 —— 编辑器里那几行预览是空白。"
                       "该改：PC 工具 layout_editor.html 的 SAMPLE（顺便补上面那行占位符说明）")
        else:
            OK.append(f"排版器 SAMPLE 覆盖布局里全部 {len(lay_ph)} 种占位符")

    # ── 写频合必须同步 last_freq ──────────────────────────────────────────
    # last_freq 是"芯片现在停在哪一个频点"的唯一缓存，DPH_Tune() 一开头就拿它
    # 做 `freq == last_freq -> return` 的早退。任何一处真写 BK4819 频合的地方
    # 漏了同步，下一次"松手回接收频率"就会被这行早退整段吞掉：屏幕 $F 读的是
    # pRX、照样写着正确的下行数字，芯片却停在上行频点上 —— 屏对、硬件错，
    # 台子上表现为"按完 PTT 就听不见卫星了"，而且只有再按一次 M 才恢复。
    # 2026-11-01 就是这么漏的（DPH_TxApply 少了一行 last_freq = up），本轮补上。
    #
    # 判定方式：把 doppler.c 按"顶格 }"切成函数块（内层 if/for 的右括号都有缩进，
    # 不会误切）；块里出现真调用 BK4819_SetFrequency( 的，块内必须有一条真赋值
    # `last_freq =`（同步、清零、先存原值都算）。注释不算 —— 否则一段解释这份
    # 缓存的注释就能让检查永远绿，那是会骗人的检查。
    spans = []
    prev = 0
    for m in re.finditer(r"(?m)^\}\s*$", dop_c):
        spans.append((prev, m.start()))
        prev = m.start()
    spans.append((prev, len(dop_c)))

    def _strip_comments(text: str) -> str:
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        text = re.sub(r"/\*[^\n]*$", "", text, flags=re.M)
        return re.sub(r"//[^\n]*", "", text)

    miss = []
    ncall = 0
    for m in re.finditer(r"(?m)^(?!\s*\*)(?!\s*/\*).*?\bBK4819_SetFrequency\s*\(", dop_c):
        ncall += 1
        line = dop_c.count("\n", 0, m.start()) + 1
        span = next((sp for sp in spans if sp[0] <= m.start() < sp[1]), None)
        if span is None:
            miss.append("第 %d 行（找不到所属函数块，本检查失效）" % line)
            continue
        if not re.search(r"\blast_freq\s*=", _strip_comments(dop_c[span[0]:span[1]])):
            miss.append("第 %d 行" % line)

    if ncall == 0:
        BAD.append("doppler.c 里一处 BK4819_SetFrequency( 真调用都解析不到 —— "
                   "写频入口改名了，本检查需要更新（解析不到就是失败，别静默通过）")
    elif miss:
        BAD.append("这些地方写了 BK4819 频合、但所在函数没见过 last_freq = 赋值："
                   + "、".join(miss)
                   + " —— 漏同步会让下一次 DPH_Tune() 的 freq == last_freq 早退"
                     "吞掉松手后的恢复，芯片停在上行频点上而屏幕照旧显示下行频率")
    else:
        OK.append("每处 BK4819_SetFrequency( 真调用所在函数都有一条 last_freq = 赋值"
                  "（共 %d 处）" % ncall)

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
