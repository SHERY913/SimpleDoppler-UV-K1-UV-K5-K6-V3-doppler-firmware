#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""转发器选择回归：写表写的是「界面上选中的那条」，不是每颗星写死的那一条。

覆盖 PC-1 想抓的两个失败模式：
  * ISS 选 SSTV，表头里却是 437.800（旧行为：FREQ 里只有中继一条）；
  * 队列混着不同频点，表头/条目退回到统一的默认频率。

不需要 GUI、不需要电台：只喂 build_table 假过境，读回条目里的 freq10/up10/ctcss10。
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
    """最小可用过境：时间、时长、样点、步长、最高仰角，外加写表计划。"""
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
    """第 i 条条目的 (freq10, up10, ctcss10)。条目从 DPH_HDR=16 起，每条 40 B。"""
    off = core.DPH_HDR + i * core.DPH_ENTRY
    f = struct.unpack_from("<16sIIIHHHHHBB", table, off)
    return f[1], f[3], f[8]


def iss(norad=25544):
    s = core.Sat(norad, "ISS (ZARYA)", 0.0, "", "")
    return sat_freq.fill(s)


print("== ISS 转发器表 ==")
s = iss()
trs = list(getattr(s, "transponders", []))
check(len(trs) == 4, f"ISS 有 4 条转发器（实际 {len(trs)}）")
if len(trs) == 4:
    check([t.name for t in trs] == ["FM 中继", "SSTV", "语音直通", "APRS"],
          "顺序：FM 中继 / SSTV / 语音直通 / APRS")
    check(abs(trs[1].down_mhz - 437.55) < 1e-9, "SSTV 下行 437.550 MHz")
    check(abs(trs[3].down_mhz - 437.825) < 1e-9, "APRS 437.825 MHz")
    check(trs[1].label == "437.5500 MHz · SSTV", f"下拉文案 = {trs[1].label}")
    check(trs[1].up10 == 0 and trs[1].ctcss10 == 0, "SSTV 没有上行/哑音")
    check(trs[0].up10 == 14_599_000 and trs[0].ctcss10 == 670,
          "中继上行 145.990 MHz / 67.0 Hz")

print("== 写表：选哪条写哪条 ==")
if len(trs) == 4:
    t_sstv = core.build_table([fake_pass("ISS", freq10=trs[1].freq10,
                                         up10=trs[1].up10,
                                         ctcss10=trs[1].ctcss10)],
                              trs[1].freq10, 10.0, tz_hours=8, name="ISS")
    f, u, c = entry_fields(t_sstv)
    check(f == 43_755_000, f"选 SSTV → 表头 freq10 = {f:,}（要 43,755,000）")
    check(u == 0 and c == 0, f"选 SSTV → 上行/哑音 = {u}/{c}（要 0/0）")

    t_rpt = core.build_table([fake_pass("ISS", freq10=trs[0].freq10,
                                        up10=trs[0].up10,
                                        ctcss10=trs[0].ctcss10)],
                             trs[0].freq10, 10.0, tz_hours=8, name="ISS")
    f, u, c = entry_fields(t_rpt)
    check(f == 43_780_000, f"选默认中继 → 表头 freq10 = {f:,}（要 43,780,000）")
    check(u == 14_599_000 and c == 670, f"选默认中继 → 上行/哑音 = {u}/{c}")

print("== 混合队列：每段带自己的频率 ==")
if len(trs) == 4:
    a = fake_pass("ISS", freq10=trs[0].freq10)
    b = fake_pass("ISS", freq10=trs[1].freq10)
    b.aos = a.aos + timedelta(hours=2)
    t = core.build_table([a, b], trs[0].freq10, 10.0, tz_hours=8, name="ISS")
    check(entry_fields(t, 0)[0] == 43_780_000 and entry_fields(t, 1)[0] == 43_755_000,
          "同表两段各写 43,780,000 / 43,755,000")

print("== 每颗星都能选，默认那条 == 老字段（98 颗） ==")
bad = []
for norad in sorted(sat_freq.FREQ):
    s = sat_freq.fill(core.Sat(norad, str(norad), 0.0, "", ""))
    rows = list(getattr(s, "transponders", []))
    if not rows:
        bad.append(f"{norad} 没有 transponders")
        continue
    t0 = rows[0]
    if t0.freq10 != s.freq10 \
            or t0.up10 != int(round((getattr(s, "up_mhz", 0.0) or 0.0) * 1e6 / 10)) \
            or abs(t0.tone_hz - (getattr(s, "up_tone", 0.0) or 0.0)) > 1e-6:
        bad.append(f"{norad} {t0.down_mhz}/{t0.up_mhz}/{t0.tone_hz} vs "
                   f"{s.freq_mhz}/{getattr(s, 'up_mhz', 0)}/{getattr(s, 'up_tone', 0)}")
check(not bad, "fill() 后 transponders[0] == 老字段（{} 颗）".format(len(sat_freq.FREQ)))
for b in bad[:5]:
    print("    " + b)

check(abs(sat_freq.SSTV[25544] - 437.55) < 1e-9, "sat_freq.SSTV[ISS] = 437.550")
check(abs(sat_freq.FREQ[25544][3] - 437.825) < 1e-9, "FREQ[ISS].aprs = 437.825")

print()
if FAILS:
    print(f"失败 {len(FAILS)} 条")
    sys.exit(1)
print("转发器层：全部通过")
sys.exit(0)
