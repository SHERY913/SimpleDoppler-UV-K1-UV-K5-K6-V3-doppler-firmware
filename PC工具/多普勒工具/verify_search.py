#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""搜索与手动频点回归。

盖住的失败模式（都是真机上报过的）：
  1. 模糊匹配退化回连续子串 —— 搜 "RS 90"（带空格）又找不到 RS90S 了；
  2. 分词各命中一处 —— "rs 90" 命中 TDRS（名字里有 rs、NORAD 里有 90）；
  3. 列表把名字砍成第一段 —— "LUCA (RS90S)" 显示成 "LUCA"，行在眼前也认不出；
  4. 无频点星偷偷用 145.800 占位值算过境 / 写表 / 回填输入框；
  5. 写电台的闸门退回 `if not self.sat.known` —— 手动频点的星永远写不进去；
  6. 默认视图闸门失效 —— 白名单读不到时列表要么空、要么把整份缓存摆出来。

不需要 GUI、不需要电台：匹配与显示名是纯函数，落表走 doppler_core.build_table。
"""
import io
import os
import re
import struct
import sys
from datetime import datetime, timedelta, timezone

sys.stdout.reconfigure(encoding="utf-8")

import doppler_core as core
import doppler_tool as T
import sat_freq

FAILS = []


def check(ok, msg):
    print(("  ✓ " if ok else "  ✗ ") + msg)
    if not ok:
        FAILS.append(msg)


def sat(norad, name):
    s = core.Sat(norad, name, 145.800, "未知", "")
    s.known = norad in sat_freq.FREQ
    return s


def known_sat(norad):
    """按载入时的同一条路径填出来：freq_mhz / 转发器都取频点表里的真值。"""
    return sat_freq.fill(core.Sat(norad, "ISS", 145.800, "未知", ""))


LUCA = sat(67287, "LUCA (RS90S)")
# NORAD 里带 "90" 的诱饵：匹配一旦退化回"每个词各命中一个键就行"，
# 它就会因为名字里的 "rs" + 编号里的 "90" 混进 "rs 90" 的结果里。
TDRS = sat(49013, "TDRS 11")
AO91 = sat(43017, "AO-91 (Fox-1B)")
RS44 = sat(44387, "AO-95 / RS-44")
ISS = sat(25544, "ISS")
POOL = [LUCA, TDRS, AO91, RS44, ISS]

print("== 1 模糊匹配：大小写、空格、连字符、别名段、NORAD ==")
for q in ["rs90", "RS90", "RS 90", "rs-90", "rs 90s", "luca", "67287", "6728"]:
    check(T.sat_match(LUCA, q), f"LUCA (RS90S) 搜 {q!r} 命中")
check(T.sat_match(AO91, "fox"), "AO-91 (Fox-1B) 搜发射名 fox 命中")
check(T.sat_match(AO91, "ao 91"), "AO-91 搜 'ao 91' 命中")
check(T.sat_match(RS44, "rs44"), "'AO-95 / RS-44' 搜 rs44 命中（斜杠分段）")
check(T.sat_match(RS44, "ao-95"), "'AO-95 / RS-44' 搜 ao-95 命中")
check(not T.sat_match(LUCA, "rs44"), "RS90S 不会被 rs44 误命中")
check(not T.sat_match(LUCA, "qmr"), "RS90S 不会被无关串命中")

print()
print("== 2 分词必须落在同一个键里（别把 NORAD 当名字凑数） ==")
hits = [s.name for s in POOL if T.sat_match(s, "rs 90")]
check(hits == ["LUCA (RS90S)"], f"'rs 90' 只命中 RS90S，实际 {hits}")
hits = [s.name for s in POOL if T.sat_match(s, "90")]
check(len(hits) <= 3, f"单串 '90' 命中数不爆（{len(hits)} 行：{hits}）")

print()
print("== 3 中文名与 NORAD 查询 ==")
cn = sat_freq.CNNAME.get(25544)
check(bool(cn), f"ISS 有中文名（{cn}）")
if cn:
    head = re.split(r"[。，,；;]", cn)[0]
    check(T.sat_match(ISS, head[:4]), f"搜中文名片段 {head[:4]!r} 命中 ISS")
check(T.sat_match(ISS, "25544"), "搜 NORAD 25544 命中 ISS")
check(not T.sat_match(ISS, "25545"), "NORAD 差一位不误命中")

print()
print("== 4 空查询 = 全部放行（由默认视图闸门另管） ==")
check(all(T.sat_match(s, "") and T.sat_match(s, "   ") for s in POOL),
      "空/空白查询不筛掉任何一行")

print()
print("== 5 列表名字不砍短 ==")
lbl = T.Tool._label(None, LUCA, set())
check("RS90S" in lbl, f"LUCA (RS90S) 的列表行保留编号：{lbl!r}")
lbl2 = T.Tool._label(None, sat(1, "TEVEL2-9 TEVEL2-9 TEVEL2-9 TEVEL2-9 EXTRA-LONG-NAME"), set())
check(T.wide_len(lbl2) <= 26, f"超长名字裁尾巴且不撑破列宽：{lbl2!r} 宽 {T.wide_len(lbl2)}")

print()
print("== 6 默认视图闸门：白名单 ∪ 频点表，pending 星不显示但能搜到 ==")
base = T.amateur_base_norads()
check(set(sat_freq.FREQ) <= base, "频点表里的星一定在默认视图")
check(len(base) >= 106, f"名单读到了（{len(base)} 颗；读不到白名单时退回 106 颗也算过）")
check(67287 in base, "RS90S 已拍板进表，默认视图里可见")
check(T.sat_match(LUCA, "rs90"), "按编号搜得到")
# 闸门仍然关着：审计判 drop 的星（非业余段/已失效）不许混进默认视图
check(23802 not in base, "审计判 drop 的 POLAR 不进默认视图（闸门没被放宽）")

print()
print("== 7 手动频点：回填只认操作员填过的，不拿 145.800 占位值 ==")


class Stub(object):
    def __init__(self, freq):
        self.cfg = {"freq": freq}


check(T.Tool._manual_freq(Stub({}), 67287) is None, "没填过 → None（框留空）")
check(T.Tool._manual_freq(Stub({"67287": {"down": 437.18, "up": 0.0}}), 67287)
      == (437.18, 0.0), "填过 437.180 仅接收 → (437.18, 0.0)")
check(T.Tool._manual_freq(Stub({"67287": {"down": "abc"}}), 67287) is None,
      "配置里的坏值 → None，不猜")
check(T.Tool._manual_freq(Stub({"67287": {"down": 0}}), 67287) is None,
      "下行 0 → None（0 落表就是废表）")

print()
print("== 7b 两个框的回填：无频点星不许拿占位值当默认 ==")


class Box(object):
    def __init__(self):
        self.text = None

    def delete(self, _a, _b):
        self.text = ""

    def insert(self, _i, v):
        self.text = v


class FillStub(object):
    """只为驱动 _fill_custom_defaults：两个输入框换成能记值的假盒子。"""

    _manual_freq = T.Tool._manual_freq

    def __init__(self, s, freq):
        self.sat = s
        self.cfg = {"freq": freq}
        self.ent_custom_down = Box()
        self.ent_custom_up = Box()

    def _trs(self):
        return []

    def _custom_bad(self, _bad):
        pass


f = FillStub(LUCA, {})
T.Tool._fill_custom_defaults(f, 0)
check(f.ent_custom_down.text == "" and f.ent_custom_up.text == "",
      f"无频点且没填过 → 两个框都空（实际 {f.ent_custom_down.text!r}）")

f = FillStub(LUCA, {"67287": {"down": 437.18, "up": 0.0}})
T.Tool._fill_custom_defaults(f, 0)
check(f.ent_custom_down.text == "437.1800" and f.ent_custom_up.text == "",
      f"填过就回填（实际 {f.ent_custom_down.text!r} / {f.ent_custom_up.text!r}）")

f = FillStub(known_sat(25544), {})
T.Tool._fill_custom_defaults(f, 0)
check(f.ent_custom_down.text == f"{sat_freq.FREQ[25544][0]:.4f}",
      f"有频点的星照旧回填标称下行（实际 {f.ent_custom_down.text!r}）")

print()
print("== 8 手动频点落表：freq10 对、up10=0 是合法的仅接收 ==")
tr = sat_freq.make_custom_transponder("437.180", "", 0.0)
check(tr is not None and tr.freq10 == 43_718_000 and tr.up10 == 0,
      "437.180 / 空 → freq10=43,718,000 up10=0")


def fake_pass(name, n=5, **plan):
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


if tr:
    table = core.build_table([fake_pass("RS90S", freq10=tr.freq10, up10=tr.up10,
                                        ctcss10=tr.ctcss10)],
                             tr.freq10, 10.0, tz_hours=8, name="RS90S",
                             up10=tr.up10, ctcss10=tr.ctcss10)
    off = core.DPH_HDR                      # 第 0 条：紧跟 16 B 表头
    fields = struct.unpack_from("<16sIIIHHHHHBB", table, off)
    f10, up10 = fields[1], fields[3]        # name, freq10, data_off, up10, ...
    check(f10 == 43_718_000, f"条目 freq10 读回 {f10:,}")
    check(up10 == 0, f"条目 up10 读回 {up10}（0 = 仅接收，固件保留自己的上行）")

print()
print("== 10 并池：联网那 96 颗不许把本地全量缓存盖掉 ==")
ONLINE = [sat(43017, "AO-91 (Fox-1B)")]
ONLINE[0].tle = ("AO-91 (Fox-1B)", "1 43017U", "2 43017 x")   # 联网那份带星历
CACHE = {
    "43017": ("AO-91 (Fox-1B) CACHED", "1 43017U 17073A   26288.50000000  .00000000  00000-0  00000-0 0  9999",
              "2 43017  97.6000 200.0000 0010000 100.0000 260.0000 14.90000000 10000"),
    "67287": ("LUCA (RS90S)", "1 67287U 25001A   26288.50000000  .00000000  00000-0  00000-0 0  9999",
              "2 67287  97.5000 210.0000 0012000 110.0000 250.0000 15.10000000 10000"),
}
POOL2 = T.merge_cached(ONLINE, CACHE)
check(any(s.norad == 67287 for s in POOL2), "联网刷新后池子里仍有 67287 (RS90S)")
check(len(POOL2) == 2, f"只补缓存独有条目（池子 {len(POOL2)} 颗）")
same = [s for s in POOL2 if s.norad == 43017][0]
check(same.tle[0] == "AO-91 (Fox-1B)",
      "同一个 NORAD 用联网那份，缓存不覆盖（名字仍是联网的）")
luca2 = [s for s in POOL2 if s.norad == 67287][0]
check(T.sat_match(luca2, "rs 90"), "并进来的 RS90S 仍被 'rs 90' 命中")

print()
print("== 9 源码结构守卫：占位频率与旧闸门都不许回来 ==")
SRC = io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "doppler_tool.py"), encoding="utf-8").read()
check("merge_cached(sats, self._tle_cache())" in SRC,
      "联网分支真的把缓存并进池子（只定义不调用 = 没修）")
check("if not self.sat.known" not in SRC,
      "write_radio 不再用 sat.known 一刀切拦掉手动频点的星")
check("freq = 145.8e6" not in SRC,
      "过境计算与详情里不再拿 145.8 MHz 顶替未知频点")
check("if tr_now is None and not sat_now.known" in SRC,
      "无频点且没填下行 → 明说要先填，不假装算过")
check('先填下行频点' in SRC, "提示文案在")

print()
if FAILS:
    print(f"失败 {len(FAILS)} 条")
    sys.exit(1)
print("搜索与手动频点：全部通过")
sys.exit(0)
