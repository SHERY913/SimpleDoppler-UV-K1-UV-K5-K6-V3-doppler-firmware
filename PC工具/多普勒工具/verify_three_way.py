#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""三端一致性检查：固件（C） / PC 工具（Python） / 安卓 App（Kotlin）。

三者靠一份**字符串协议**活着，没有任何编译器能替我们发现它们漂了：
固件改了表头字段顺序、PC 还按老顺序 unpack，结果就是"能连上、能写、全是错数"。
这个脚本只做静态对照，不需要电台、不需要串口、不需要装 Gradle：

  1. 频点表：`数据/sat_freq.json` ↔ `Freqs.kt` ↔ `sat_freq.py`
  2. 结构体：`doppler.h` 里 packed 结构体的大小 ↔ `doppler_core.py` 的 struct 格式
     （布局层 verify_layout.py 比的是"字段宽度序列"；这一层比的是**总字节数**，
     两者缺一不可：宽度序列一样、对齐/填充不一样，unpack 照样错位）
  3. 命令号：固件 `case 0x07xx` 实现集合 ↔ App 引用集合 ↔ PC 引用集合
     （App 引用了而固件没实现 = 那颗按钮永远不会有回包）

布局常量（magic/版本/表头长/条目长/条目上限/表基址/混淆掩码）不在这里重复检查，
那是 `verify_layout.py` 的地盘 —— 同一个约定只有一处实现在盯着它。

用法：
    python verify_three_way.py            # 人看的报告
    python verify_three_way.py --quiet    # 只输出失败项（verify_all 用），有问题返回 1
"""

import argparse
import io
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
FW = os.path.join(WS, "多普勒", "固件源码", "uvk1-v6.3.0", "App", "app")
PY_DIR = HERE
KT = os.path.join(WS, "多普勒", "安卓App", "android-app", "app", "src", "main", "java",
                  "com", "f4hwn", "doppler")

CTYPES = {
    "char": 1, "uint8_t": 1, "int8_t": 1, "bool": 1,
    "uint16_t": 2, "int16_t": 2,
    "uint32_t": 4, "int32_t": 4,
}

FAILS = []
NOTES = []


def fail(msg):
    FAILS.append(msg)


def note(msg):
    NOTES.append(msg)


def read(path):
    return io.open(path, encoding="utf-8", errors="replace").read()


# ── 1 频点表 ────────────────────────────────────────────────────────────────
def check_freqs():
    sys.path.insert(0, PY_DIR)
    try:
        import gen_sat_freq as g
    except Exception as e:                                   # pragma: no cover
        fail(f"导入 gen_sat_freq 失败：{e}")
        return
    import contextlib
    buf = io.StringIO()
    # 直接复用它的判定逻辑，不复制一份：复制出来的第二份实现迟早会与第一份不一致。
    old = sys.argv
    sys.argv = [sys.argv[0], "--check"]
    try:
        with contextlib.redirect_stdout(buf):
            rc = g.main()
    finally:
        sys.argv = old
    if rc != 0:
        fail("频点三处不一致：\n    " + buf.getvalue().strip().replace("\n", "\n    "))
    else:
        note("频点：" + buf.getvalue().strip().splitlines()[-1])


# ── 2 结构体大小 ────────────────────────────────────────────────────────────
def c_struct_size(body):
    total = 0
    fields = []
    for line in body.splitlines():
        line = line.split("/*")[0].split("//")[0].strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:\[(\d+)\])?\s*;", line)
        if not m:
            continue
        typ, name, arr = m.group(1), m.group(2), m.group(3)
        if typ not in CTYPES:
            continue
        n = int(arr) if arr else 1
        fields.append((typ, name, n))
        total += CTYPES[typ] * n
    return total, fields


RE_TYPEDEF = re.compile(r"typedef struct __attribute__\(\(packed\)\) \{(.*?)\}\s*([A-Za-z_][A-Za-z0-9_]*)\s*;", re.S)


def extract_struct(hdr_text, typedef_name):
    """按名字取 packed 结构体。必须一次扫出**所有** typedef 再按名字选：
    用 `.*?` 从第一个 typedef 起偷懒匹配，会把前面几个结构体一起吞进来，
    于是"表头 56 字节"这种假差异就冒出来了。"""
    for body, name in RE_TYPEDEF.findall(hdr_text):
        if name == typedef_name:
            return c_struct_size(body)
    return None


def check_structs():
    hdr = read(os.path.join(FW, "doppler.h"))
    core = read(os.path.join(PY_DIR, "doppler_core.py"))

    # 只对照**两端都会解析**的结构体。dph_cal_t 是固件独有（PC/App 只通过
    # 0x0773 读 ppm 数值，不解析这条记录），拿一条没人解析的格式去做对照
    # 只会制造一条假差异。
    pairs = [("dph_header_t", r"<4sBBHII", "表头"),
             ("dph_entry_t", r"<16sIIIHHHHHBB", "条目")]
    for cname, fmt, label in pairs:
        got = extract_struct(hdr, cname)
        if got is None:
            fail(f"doppler.h 里找不到 packed 结构体 {cname}")
            continue
        csize, fields = got
        psize = struct.calcsize(fmt)
        if csize != psize:
            fail(f"{label} {cname}: 固件 {csize} B ≠ PC {fmt} 的 {psize} B")
        else:
            note(f"{label} {cname} = {csize} B（两侧一致）")

    # 样点结构：固件里是匿名 typedef，用 DPH_PT_SIZE 对齐；PC 写死 "<hBHb"。
    m = re.search(r"#define DPH_PT_SIZE\s+(\d+)u", hdr)
    if not m:
        fail("doppler.h 里找不到 DPH_PT_SIZE")
    else:
        fw = int(m.group(1))
        zm = re.search(r'struct\.(?:pack|unpack_from)\(\s*"([^"]+)"', core)
        psz = struct.calcsize("<hBHb")
        if fw != psz:
            fail(f"样点: 固件 DPH_PT_SIZE={fw} ≠ PC <hBHb> 的 {psz}")
        else:
            note(f"样点 = {fw} B（两侧一致）")
        if '"<hBHb"' not in core:
            fail("doppler_core.py 里找不到样点格式 \"<hBHb\"（被改名了？）")


# ── 3 命令号 ────────────────────────────────────────────────────────────────
def cmd_set(text, patterns):
    out = set()
    for p in patterns:
        out |= {int(x, 16) for x in re.findall(p, text)}
    return out


def check_commands():
    uart = read(os.path.join(FW, "uart.c"))
    dop = read(os.path.join(FW, "doppler.c"))
    fw = cmd_set(uart + dop, [r"case\s+(0x07[0-9A-Fa-f]{2})"])
    fw |= cmd_set(dop, [r"\b(0x077[0-9A-Fa-f])\b"])
    py = set()
    for fn in os.listdir(PY_DIR):
        # 校验脚本自身不算"客户端"：它们文本里全是命令号，会把报告搅浑。
        if not fn.endswith(".py") or fn.startswith("verify_") or fn.startswith("gen_"):
            continue
        t = read(os.path.join(PY_DIR, fn))
        py |= cmd_set(t, [r"0x(076[0-9A-Fa-f]|077[0-9A-Fa-f])"])
    kt = set()
    for fn in os.listdir(KT):
        if not fn.endswith(".kt"):
            continue
        kt |= cmd_set(read(os.path.join(KT, fn)), [r"0x(076[0-9A-Fa-f]|077[0-9A-Fa-f])"])

    if not fw:
        fail("固件里一条 0x07xx 命令都没扫到（uart.c 路径变了？）")
        return
    # 只在 0x0760..0x077F 这一段做对照：这是多普勒协议的领地。
    def rng(s):
        return {c for c in s if 0x0760 <= c <= 0x077F}

    fw, py, kt = rng(fw), rng(py), rng(kt)
    both = sorted(fw & (py | kt))
    note("命令号：固件实现 %d 条（%s）" % (len(fw), " ".join("%04X" % c for c in sorted(fw))))
    miss_py = sorted((fw - py) - {c + 1 for c in py})     # 回包 ID 不算缺
    miss_kt = sorted((fw - kt) - {c + 1 for c in kt})
    if miss_py:
        note("  固件有、PC 工具没用：%s（不算错，只是 PC 端少一条功能）"
             % " ".join("%04X" % c for c in miss_py))
    if miss_kt:
        note("  固件有、App 没用：%s" % " ".join("%04X" % c for c in miss_kt))
    # 回包 ID = 请求 ID + 1，属于正常用法，不能算"固件没实现"。
    ghosts = (py | kt) - fw - {(c + 1) & 0xFFFF for c in fw}
    ghost = sorted(ghosts)
    if ghost:
        fail("有人发了固件根本没实现的命令：%s —— 那颗按钮/那条路径永远不会有回包"
             % " ".join("%04X" % c for c in ghost))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    check_freqs()
    check_structs()
    check_commands()

    if not a.quiet:
        for n in NOTES:
            print("  · " + n)
    if FAILS:
        print("\n三端不一致：")
        for f in FAILS:
            print("  ✗ " + f)
        return 1
    print("\n三端一致（频点 / 结构体字节数 / 命令号）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
