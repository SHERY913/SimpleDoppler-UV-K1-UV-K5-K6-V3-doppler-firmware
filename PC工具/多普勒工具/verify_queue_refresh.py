#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""待写队列的频率刷新回归。

盖住的失败模式（操作员实测撞到的）：
  1. 队列里的 freq10/up10/ctcss10 是入队那一刻的快照。之后改「下行 MHz」、
     换转发器、换星，队列不变，write_radio 又优先用队列 —— 六条过境全写成
     "ISS 437.800"，反复重写一个字都不变；
  2. 找不到对应卫星的条目被静默写成 0 Hz（废表），或者被悄悄改写；
  3. 转发器名换了写法（"FM 中继" / "fm  中继"）就匹配不上，退化成星级默认，
     写进去的是错的频点；
  4. 源码把"只补缺字段"的旧逻辑请回来（`if not hasattr(q, "freq10")`）——
     带旧值的条目一个都不会被纠正，等于没修。

不需要 GUI、不需要电台：refresh_entry_freqs 是模块级纯函数，假对象直接调。
"""
import io
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

import doppler_tool as T

FAILS = []


def check(ok, msg):
    print(("  ✓ " if ok else "  ✗ ") + msg)
    if not ok:
        FAILS.append(msg)


class FakeTr:
    """最小转发器：名字 + 三个 MHz/Hz 字段，另带 freq10/up10/ctcss10 属性。"""

    def __init__(self, name, down_mhz, up_mhz=0.0, tone_hz=0.0):
        self.name = name
        self.down_mhz = down_mhz
        self.up_mhz = up_mhz
        self.tone_hz = tone_hz

    @property
    def freq10(self):
        return int(round(self.down_mhz * 1e5))

    @property
    def up10(self):
        return int(round(self.up_mhz * 1e5))

    @property
    def ctcss10(self):
        return int(round(self.tone_hz * 10))

    @property
    def label(self):
        return f"{self.down_mhz:.4f} MHz · {self.name}"


class FakeSat:
    def __init__(self, norad, name, freq_mhz, up_mhz=0.0, up_tone=0.0,
                 mode="FM", trans=(), known=True):
        self.norad = norad
        self.name = name
        self.freq_mhz = freq_mhz
        self.up_mhz = up_mhz
        self.up_tone = up_tone
        self.mode = mode
        self.known = known
        self.transponders = list(trans)

    @property
    def freq10(self):
        return int(round(self.freq_mhz * 1e5))


class FakeEntry:
    """一条待写队列条目（过境对象的最小替身）。"""

    def __init__(self, name, norad, trans_name, freq10, up10=0, ctcss10=0, aos=None):
        self.name = name
        self.norad = norad
        self.trans_name = trans_name
        self.freq10 = freq10
        self.up10 = up10
        self.ctcss10 = ctcss10
        self.aos = aos


# ------------------------------------------------------------------ 1 主 bug
print("== 1 改完频率再写入：队列里的旧快照必须被重取 ==")
tr_a = FakeTr("FM 中继", 437.800, 145.990, 67.0)
SAT_A = FakeSat(25544, "ISS (ZARYA)", 437.800, 145.990, 67.0,
                mode="FM / SSTV", trans=[tr_a, FakeTr("SSTV", 437.550, 0.0, 0.0)])
e = FakeEntry("ISS (ZARYA)", 25544, "FM 中继", 43780000, 14599000, 670)
check(e.freq10 == 43780000, "入队时是旧值 437.800（43,780,000）")

tr_a.down_mhz = 436.210          # 操作员在界面里换了频点
changed, missing = T.refresh_entry_freqs([SAT_A], [e])
check(e.freq10 == 43621000,
      f"改频率后重取成 43,621,000（旧代码永远停在 43,780,000，实际 {e.freq10:,}）")
check(e.up10 == 14599000, f"上行跟着重取：{e.up10:,}")
check(e.ctcss10 == 670, f"哑音跟着重取：{e.ctcss10}（0.1 Hz 单位）")
check(changed == 1, f"changed 报 1 段（实际 {changed}）")
check(not missing, f"没有 missing（实际 {missing}）")
check(e.name == "ISS (ZARYA)", f"名字按星刷新：{e.name!r}")

# 再跑一次：值没变 → changed 必须归零（否则日志会天天喊"刷新了 N 段"）
changed2, _ = T.refresh_entry_freqs([SAT_A], [e])
check(changed2 == 0, f"同一个值重跑 changed=0（实际 {changed2}）")

print()
print("== 2 找不到星：missing 非空、原值一个字节都不动 ==")
e2 = FakeEntry("ISS (ZARYA)", 99999, "FM 中继", 43780000, 14599000, 670)
changed3, missing3 = T.refresh_entry_freqs([SAT_A], [e2], current_sat=None)
check(bool(missing3), f"missing 非空（实际 {missing3}）")
check(e2.freq10 == 43780000 and e2.up10 == 14599000 and e2.ctcss10 == 670,
      "找不到星时沿用原值（不写 0，也不乱改）")
check(changed3 == 0, f"changed=0（实际 {changed3}）")
check(all(str(m).strip() for m in missing3), f"missing 里每条都有描述：{missing3}")

print()
print("== 3 转发器名对不上：落到星级默认 ==")
SAT_B = FakeSat(43017, "AO-91 (Fox-1B)", 145.960, 435.250, 67.0,
                trans=[FakeTr("FM", 145.960, 435.250, 67.0)])
e3 = FakeEntry("AO-91", 43017, "早已删掉的转发器", 43780000, 14599000, 670)
changed4, missing4 = T.refresh_entry_freqs([SAT_B], [e3])
check(e3.freq10 == 14596000, f"用星级 freq_mhz：{e3.freq10:,}")
check(e3.up10 == 43525000, f"用星级 up_mhz：{e3.up10:,}")
check(e3.ctcss10 == 670, f"用星级 up_tone：{e3.ctcss10}")
check(not missing4 and changed4 == 1, f"不报 missing，算 1 段变化（{changed4}/{missing4}）")

print()
print("== 4 转发器名写法不同（大小写 / 多空格）也要命中，不许退化到星级默认 ==")
e4 = FakeEntry("ISS (ZARYA)", 25544, "  fm  中继 ", 43780000, 14599000, 670)
T.refresh_entry_freqs([SAT_A], [e4])
check(e4.freq10 == 43621000,
      f"'  fm  中继 ' 仍命中 'FM 中继' → 436.210（实际 {e4.freq10:,}）")

print()
print("== 5 容错：字符串 norad / None / 缺字段都不许抛 ==")
ok5 = True
try:
    e5 = FakeEntry("ISS (ZARYA)", "25544", None, 43780000)   # 字符串 norad
    T.refresh_entry_freqs([SAT_A, None], [e5, None])
    ok5 = ok5 and e5.freq10 == 43780000                        # trans_name 空 → 星级默认
    e6 = FakeEntry("ISS (ZARYA)", None, "FM 中继", 43780000, aos="2026-11-13")  # norad 缺失
    _c, m6 = T.refresh_entry_freqs([SAT_A], [e6], current_sat=SAT_A)
    ok5 = ok5 and not m6 and e6.freq10 == 43621000              # 靠名字兜到 current_sat
    e7 = FakeEntry("X", 25544, "FM 中继", None)                 # freq10 是 None
    T.refresh_entry_freqs([SAT_A], [e7])
    ok5 = ok5 and e7.freq10 == 43621000
    T.refresh_entry_freqs(None, None)                           # 全空
    T.refresh_entry_freqs([], [])
except Exception as ex:                                        # noqa: BLE001
    ok5 = False
    print(f"    异常：{type(ex).__name__}: {ex}")
check(ok5, "怪输入（字符串 norad / None / 缺字段 / 空入参）都不抛异常")

print()
print("== 6 源码结构守卫：调用必须在 write_radio 里，旧补丁逻辑不许回来 ==")
SRC = io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "doppler_tool.py"), encoding="utf-8").read()
m = re.search(r"\n    def write_radio\(self\):([\s\S]*?)\n    def ", SRC)
body = m.group(1) if m else ""
# 注释里写着"已经在上面重取过了"不算数：先剥掉注释行，只看真代码。
body_nc = "\n".join(l for l in body.splitlines() if not l.lstrip().startswith("#"))
call_m = re.search(r"=\s*refresh_entry_freqs\((.*)$", body_nc, re.M)
check(bool(call_m) and "_all_sats()" in call_m.group(1),
      "write_radio 里真调用了 refresh_entry_freqs(self._all_sats(), sel, self.sat)"
      "（只定义不调用、只在注释里提过、或只看当前这颗星都算没修）")
check("if not hasattr(q, \"freq10\")" not in SRC,
      "旧的『只给缺字段的条目补值』逻辑已删除（否则带旧快照的条目永远不纠正）")
check(SRC.count("def refresh_entry_freqs(") == 1,
      f"refresh_entry_freqs 只有一个定义（实际 {SRC.count('def refresh_entry_freqs(')} 个）")

print()
if FAILS:
    print(f"失败 {len(FAILS)} 条")
    sys.exit(1)
print("全部通过")
sys.exit(0)
