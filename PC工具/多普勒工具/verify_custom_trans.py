#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自定义标称频率回归：下拉最后一项「自定义…」算出来的那条要真的能落表。

覆盖的失败模式：
  * 输入 437.550 却在表头写成 437.800（退回老字段）或 0（up10=0 = 仅接收）；
  * 用户打错一个字符，上行被静默当成 0，发射频点悄悄没了；
  * tone 不跟着该星走（自定义 SSTV 却带上了中继的 67 Hz）。

不需要 GUI、不需要电台：直接喂 make_custom_transponder，再用 doppler_core
自己的 build_table 写一遍表，读回条目里的 freq10/up10/ctcss10。
"""
import struct
import sys
from datetime import datetime, timezone, timedelta

sys.stdout.reconfigure(encoding="utf-8")

import doppler_core as core
import sat_freq

FAILS = []


def check(ok, msg):
    print(("  ✓ " if ok else "  ✗ ") + msg)
    if not ok:
        FAILS.append(msg)


def fake_pass(name, n=5, **plan):
    """最小可用过境（与 verify_transponder.py 同一份形状）。"""
    p = type("P", (), {})()
    p.name = name
    p.aos = datetime(2026, 11, 8, 12, 0, 0, tzinfo=timezone.utc)
    p.duration = n * 10
    p.samples = [(p.aos + timedelta(seconds=10 * i), 0.0, 30, 500, 0)
                 for i in range(n)]
    p.step_ms = 10000
    p.max_el = 45.0
    for k, v in plan.items():
        setattr(p, k, v)
    return p


def entry_fields(table, i=0):
    """第 i 条条目的 (freq10, up10, ctcss10)。16 B 表头 + 40 B/条。"""
    off = core.DPH_HDR + i * core.DPH_ENTRY
    f = struct.unpack_from("<16sIIIHHHHHBB", table, off)
    return f[1], f[3], f[8]


mk = sat_freq.make_custom_transponder
# doppler_core 里同一条 MHz → 10 Hz 换算，别在测试里再写一遍。
ref_f10 = core.Sat(0, "_ref", 437.55, "", "").freq10

print("== 437.55 + 空上行（仅接收） ==")
tr = mk("437.55", "", 0.0)
check(tr is not None, "make_custom_transponder('437.55','',0) 不是 None")
if tr:
    check(abs(tr.down_mhz - 437.55) < 1e-9, f"down_mhz = {tr.down_mhz:g}（要 437.55）")
    check(tr.up_mhz == 0.0, f"up_mhz = {tr.up_mhz:g}（空上行 → 0.0 = 仅接收）")
    check(tr.freq10 == ref_f10, f"freq10 = {tr.freq10:,}（core 同一条换算 {ref_f10:,}）")
    check(tr.freq10 == 43_755_000, f"freq10 就是 43,755,000（实际 {tr.freq10:,}）")
    check(tr.up10 == 0, f"up10 = {tr.up10}（写表契约：0 = 仅接收）")
    check(tr.name == "自定义" and tr.note == "标称",
          f"name/note = {tr.name!r}/{tr.note!r}")

print("== 437.55 + 145.8（有上行） ==")
tr2 = mk("437.55", "145.8", 0.0)
check(tr2 is not None and abs(tr2.up_mhz - 145.8) < 1e-9,
      f"up_mhz = {tr2.up_mhz:g}（要 145.8）" if tr2 else "None")
if tr2:
    check(tr2.up10 == 14_580_000, f"up10 = {tr2.up10:,}")

print("== 亚音原样带出（该星主转发器的 tone） ==")
iss = sat_freq.fill(core.Sat(25544, "ISS (ZARYA)", 0.0, "", ""))
base_tone = iss.transponders[0].tone_hz
tr3 = mk("437.55", "", base_tone)
check(tr3 is not None and tr3.tone_hz == base_tone,
      f"tone_hz = {tr3.tone_hz:g}（ISS 中继 {base_tone:g} Hz）" if tr3 else "None")
if tr3:
    check(tr3.ctcss10 == 670, f"ctcss10 = {tr3.ctcss10}（67.0 Hz → 670）")

print("== 给不出频率一律 None（调用方保留上一个有效值） ==")
for bad in ("abc", "0", "20000", "", "  "):
    check(mk(bad, "", 67.0) is None, f"下行 {bad!r} → None")
check(mk("437.55", "abc", 67.0) is None, "上行 'abc' → None（不静默当 0）")
check(mk("437.55", "0", 67.0) is None, "上行 '0' → None")

print("== 真正落表：build_table 读回来是自定义那条 ==")
if tr:
    table = core.build_table([fake_pass("ISS", freq10=tr.freq10, up10=tr.up10,
                                        ctcss10=tr.ctcss10)],
                             tr.freq10, 10.0, tz_hours=8, name="ISS",
                             up10=tr.up10, ctcss10=tr.ctcss10)
    f, u, c = entry_fields(table)
    check(f == 43_755_000, f"表头 freq10 = {f:,}（要 43,755,000）")
    check(u == 0 and c == 0, f"表头 up10/ctcss10 = {u}/{c}（仅接收，无哑音）")

print()
if FAILS:
    print(f"失败 {len(FAILS)} 条")
    sys.exit(1)
print("自定义标称频率：全部通过")
sys.exit(0)
