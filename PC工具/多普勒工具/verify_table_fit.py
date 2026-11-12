#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""表区容量与归属（离线）：64 KiB 到底装不装得下、5 s 栅格到底准不准、擦之前到底认不认得出别人。

为什么要单独一层：把表区从 496 KiB 砍到 64 KiB，是"用采样密度换空间"。
这个交换必须同时钉住三件事，缺一条就是自欺欺人 ——

  1. 精度：10 s 一个点（下限 5 s）、固件线性插值，和 1 s 真值的偏差有多少？
     437.8 MHz 上多普勒二阶导约 0.5 Hz/s²，粗估 |f''|h²/8 ≈ 6 Hz。
     这里用**固件那段整数代码的等价实现**（DPH_Q1000 的
     t_ms*1000/step_ms + a+(b-a)*frac/1000 + int16 量化）去比，
     而不是另写一份漂亮的浮点插值。
  2. 容量：条目区必须正好填满表头扇区（102 条 × 40 B + 16 B == 4,096），
     一趟满长过境必须装得下，而"这张表几趟"按**真实字节**算 ——
     `table_used()` / `fit_keep()` 给出的趟数与字节数要自洽
     （10 s 与 5 s 两档分别实测），并且 build_table 真能拼出一张
     validate_table 认可的表。
  3. 归属：表区里躺的不是我们的东西时，PC 端必须在**擦之前**说不；
     反过来，**表区自己的每一个扇区都必须被 guard() 放行** —— 上一版只测了
     "拦得住"，没测"放得行"，于是 FLASH_VOICE_PACK 盖住表区、写表按钮全线
     静默失效，九层回归照样全绿。正向用例是这一层必须有的那一半。
     这条用合成扇区直接喂 classify_region/occupied_reason，不连真机。

另外把固件 doppler.h 里的常量读回来，用同一套整数公式重算一遍容量不等式 ——
verify_layout.py 比的是"三端数字一样"，这里比的是"这个数字真的装得下"。

    python verify_table_fit.py          # 默认用缓存 TLE 里的 ISS

退出码：0 全过；1 有硬错误。
"""
import datetime as dt
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import doppler_core as core          # noqa: E402
from sgp4.api import Satrec, WGS72   # noqa: E402

ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
FW_H = os.path.join(ROOT, "固件源码", "uvk1-v6.3.0", "App", "app", "doppler.h")
MB_H = os.path.join(ROOT, "固件源码", "uvk1-v6.3.0", "App", "driver", "mb_flash.h")

FREQ10 = 43_780_000                  # 437.800 MHz，ISS 下行
LAT, LON, ALT = 23.1869, 113.4192, 800.0

# 精度上限：NFM 带宽 2.5 kHz，留 25 倍余量。这条线的意义是"插值误差必须
# 小到听不出来"，而不是"越小越好"—— 真要比绝对小，得先修 ppm 校准。
ERR_MAX_HZ = 100.0

TLE_CACHE = os.path.join(HERE, "tle_cache.json")


def load_sat(norad=25544):
    """从本地 tle_cache.json 造 Satrec —— 不联网，结论才可复现。

    core.find_sat() 返回的是目录条目（class Sat，只有频率和简介），
    SGP4 要的是 Satrec；这两者别混。"""
    with open(TLE_CACHE, encoding="utf-8") as f:
        cache = json.load(f)
    row = cache.get(str(norad))
    if not row:
        raise SystemExit(f"tle_cache.json 里没有 {norad}（先跑 refresh_tle.py）")
    return Satrec.twoline2rv(row[1], row[2], WGS72)


fails = []
unverified = []          # 真镜像对照里"本地没有这个文件"的项
verified_real = []       # 真镜像对照里真跑过的文件


def ok(msg):
    print(f"  \u2713 {msg}")


def bad(msg):
    print(f"  \u2717 {msg}")
    fails.append(msg)


def hdr(msg):
    print(f"\n== {msg} ==")


# ---------------------------------------------------------------- 固件等价插值
def fw_offset_at(samples_hz, t_ms, step_ms):
    """doppler.c DPH_OffsetAt 的等价实现（含 int16 量化与 C 的截断方向）。

    samples_hz 是已经量化成 int16 的表内样点。"""
    if len(samples_hz) < 2:
        return 0
    q = (t_ms * 1000) // step_ms          # DPH_Q1000：64 位中间量，不溢出
    idx = q // 1000
    frac = q % 1000
    if idx >= len(samples_hz) - 1:
        idx = len(samples_hz) - 2
    a = samples_hz[idx]
    b = samples_hz[idx + 1]
    # C 的整数除法向零截断；Python // 向下取整，负差值时必须掰回来
    d = (b - a) * frac
    d = int(d / 1000)
    return a + d


def check_accuracy():
    hdr("10 s 栅格 vs 1 s 真值：固件那段整数插值到底偏多少")
    sat, gnd = load_sat(), core.Ground(LAT, LON, ALT)
    passes = core.find_passes(sat, gnd, FREQ10 / 10.0, days=2, min_el=0.0,
                             step=10.0, max_passes=1)
    if not passes:
        bad("两天内没有 ISS 过境，无法评估精度（TLE 太旧？）")
        return
    p = passes[0]

    # 真值：1 s 一个点的浮点多普勒（这一步是 SGP4 本身，不是插值）
    t0 = p.aos
    dur = p.duration
    truth = []
    for s in range(0, dur + 1):
        _, _, _, rate = core.look(sat, gnd, t0 + dt.timedelta(seconds=s))
        truth.append(core.doppler_hz(FREQ10 / 10.0, rate))

    worst, worst_at = 0.0, 0
    for step in (1.0, 5.0, 10.0):
        step_ms = int(step * 1000)
        q = [max(-32768, min(32767, int(round(truth[i]))))
             for i in range(0, len(truth), int(step))] if step == 1.0 else None
        # 表样点：与 _measure 一致，从 AOS 起每 step 一点
        q = []
        for s in range(0, dur + int(step), int(step)):
            t = t0 + dt.timedelta(seconds=s)
            _, _, _, rate = core.look(sat, gnd, t)
            hz = core.doppler_hz(FREQ10 / 10.0, rate)
            q.append(max(-32768, min(32767, int(round(hz)))))

        e = 0.0
        at = 0
        for s in range(0, dur + 1):
            got = fw_offset_at(q, s * 1000, step_ms)
            d = abs(got - truth[s])
            if d > e:
                e, at = d, s
        tag = f"{step:>4g} s 栅格 · {len(q):>3d} 点 · 最大偏差 {e:7.1f} Hz（在 AOS+{at}s）"
        if step == 10.0:
            worst, worst_at = e, at
            (ok if e <= ERR_MAX_HZ else bad)(
                f"{tag} ≤ {ERR_MAX_HZ:g} Hz" if e <= ERR_MAX_HZ
                else f"{tag} 超过 {ERR_MAX_HZ:g} Hz")
        else:
            print(f"  · {tag}")

    print(f"  · 这趟最高仰角 {p.max_el:.1f}°、摆幅 "
          f"±{(p.span[1] - p.span[0]) / 2:.0f} Hz；"
          f"10 s 栅格误差占摆幅 {worst / ((p.span[1] - p.span[0]) / 2) * 100:.2f}%")

    # 参照系：未做 ppm 校准时晶振 ±76 ppm 在 437.8 MHz 上是多少
    ppm76 = 437.8e6 * 76e-6
    if worst * 50 < ppm76:
        ok(f"插值误差比 76 ppm 失谐（{ppm76 / 1000:.0f} kHz）小 "
           f"{ppm76 / worst:.0f} 倍——10 s 栅格不是新的精度瓶颈")
    else:
        bad("插值误差和 ppm 失谐同量级，10 s 栅格太粗")


def check_capacity():
    hdr("容量：条目区必须正好填满表头扇区，字节数按真实表算（不再有固定趟数）")
    # 1. 条目区 = 表头 + PASS_MAX 条 40 B 条目，必须**正好**等于样点区起点。
    #    多一条就越界写进样点区，少一条那个扇区就白扔。
    need = core.DPH_HDR + core.DPH_PASS_MAX * core.DPH_ENTRY
    (ok if need == core.DPH_SAMPLE_OFF else bad)(
        f"{core.DPH_PASS_MAX} 条 × {core.DPH_ENTRY} B + {core.DPH_HDR} B 表头 = {need:,} B "
        f"== 样点区起点 0x{core.DPH_SAMPLE_OFF:X}"
        if need == core.DPH_SAMPLE_OFF else
        f"条目区对不上：{need:,} B ≠ 样点区起点 {core.DPH_SAMPLE_OFF:,} B")

    # 2. 哪怕只有一趟、哪怕是最密的栅格、最长时长，也必须装得下。
    pts = core.DPH_DUR_S_MAX * 1000 // core.DPH_STEP_MS_MIN + 1
    per = core.DPH_ENTRY + pts * core.DPH_PT
    (ok if core.DPH_SAMPLE_OFF + per <= core.DPH_REGION else bad)(
        f"最坏的一趟（{core.DPH_DUR_S_MAX} s @ {core.DPH_STEP_MS_MIN / 1000:g} s，"
        f"{pts} 点）{per:,} B，加 4 KiB 样点偏移 = {core.DPH_SAMPLE_OFF + per:,} B "
        f"≤ {core.DPH_REGION:,} B")

    # 3. 真实数据：先数清楚同一片 64 KiB 在 10 s / 5 s 两档各能装几趟。
    #    上一版这里断言"30 条最坏情况塞得下"，等于把条数上限钉死在最坏步长上，
    #    10 s 档永远只用到一半 —— 现在按 table_used()/fit_keep() 的**真实字节**说话。
    sat, gnd = load_sat(), core.Ground(LAT, LON, ALT)
    for step_s in (core.DPH_STEP_S_DEFAULT, core.DPH_STEP_MS_MIN / 1000.0):
        ps = core.find_passes(sat, gnd, FREQ10 / 10.0, days=10, min_el=0.0,
                              step=step_s, max_passes=core.DPH_PASS_MAX)
        if not ps:
            bad(f"{step_s:g} s 档十天内一趟过境都没排出来")
            continue
        keep = core.fit_keep(ps)
        used = core.table_used(ps[:keep])
        if keep <= 0:
            bad(f"{step_s:g} s 档连一趟都装不下")
            continue
        ok(f"{step_s:g} s 档：{len(ps)} 趟候选，64 KiB 装得下 {keep} 趟"
           f"（{used:,} B / {core.DPH_REGION:,} B，{used / core.DPH_REGION * 100:.0f}%）")
        # 多 1 趟就必须判超
        if keep < len(ps):
            over = core.table_used(ps[:keep + 1])
            (ok if over > core.DPH_REGION else bad)(
                f"{step_s:g} s 档：再多 1 趟就是 {over:,} B > {core.DPH_REGION:,} B，"
                f"fit_keep 不会多留")

    # 4. 真表也得装得下：先按 fit_keep 裁到装得下的长度再构表
    passes = core.find_passes(sat, gnd, FREQ10 / 10.0, days=10, min_el=0.0,
                              step=core.DPH_STEP_S_DEFAULT,
                              max_passes=core.DPH_PASS_MAX)
    if not passes:
        bad("十天内一趟过境都没排出来，find_passes 或 TLE 缓存有问题")
        return
    passes = passes[:max(1, core.fit_keep(passes))]
    try:
        table = core.build_table(passes, FREQ10, core.DPH_STEP_S_DEFAULT,
                                 tz_hours=8)
    except Exception as exc:                     # noqa: BLE001
        bad(f"build_table 在 {len(passes)} 条真实过境上失败：{exc}")
        return
    (ok if len(table) <= core.DPH_REGION else bad)(
        f"{len(passes)} 条真实 ISS 过境拼出 {len(table):,} B（{len(table) / core.DPH_REGION * 100:.1f}% "
        f"表区），表尾 0x{core.DPH_BASE + len(table):06X} ≤ "
        f"0x{core.DPH_BASE + core.DPH_REGION:06X}")

    errs = core.validate_table(table)
    (ok if not errs else bad)(
        "validate_table 认可这张表" if not errs else f"validate_table 报错：{errs[:3]}")

    want = core.DPH_REGION // core.DPH_SECTOR
    plan = core.erase_plan(len(table))
    (ok if len(plan) == want else bad)(
        f"这张表要 {core.table_sectors(core.DPH_SAMPLE_OFF + len(table))} 个数据扇区，"
        f"erase_plan 仍按整块擦 {len(plan)} 个（= 表区全部 {want} 个）"
        if len(plan) == want else
        f"erase_plan 只擦 {len(plan)} 个扇区，≠ 表区整块 {want} 个")
    tiny = core.erase_plan(1)
    (ok if len(tiny) == want else bad)(
        f"哪怕只有 1 B 的表，erase_plan 也擦满 {want} 个扇区（不留上一轮旧样点）"
        if len(tiny) == want else f"1 B 的表只擦 {len(tiny)} 个扇区，会留旧样点")
    if all(a >= core.DPH_BASE and a + 4096 <= core.DPH_BASE + core.DPH_REGION
           for a in plan):
        ok(f"erase_plan 最远擦到 0x{plan[-1] + 4096:06X}，没出表区边")
    else:
        bad(f"erase_plan 越界：{['0x%06X' % a for a in plan]}")


def check_gates():
    hdr("闸门：太密的表、太长的过境、太多条，必须当场拒")
    sat, gnd = load_sat(), core.Ground(LAT, LON, ALT)
    passes = core.find_passes(sat, gnd, FREQ10 / 10.0, days=2, min_el=0.0,
                              step=core.DPH_STEP_S_DEFAULT, max_passes=2)
    if not passes:
        bad("没有过境可用于闸门检查")
        return

    for label, kw in (("1 s 采样间隔", dict(step_s=1.0)),
                      ("2 s 采样间隔", dict(step_s=2.0)),
                      ("4.9 s 采样间隔", dict(step_s=4.9))):
        try:
            core.build_table(passes, FREQ10, kw["step_s"], tz_hours=8)
            bad(f"{label}竟然通过了 build_table")
        except ValueError as exc:
            ok(f"{label}被拒：{str(exc)[:34]}…")

    # 正向：下限本身必须放行，而且放行之后 validate_table 也要认
    # （上一版的 validate_table 把下限 10000 同时当上界用，等价于只允许 10 s）
    try:
        t5 = core.build_table(passes, FREQ10, core.DPH_STEP_MS_MIN / 1000.0,
                              tz_hours=8)
        errs5 = core.validate_table(t5)
        if errs5:
            bad(f"{core.DPH_STEP_MS_MIN / 1000:g} s 的表构建成功但 validate_table 拒：{errs5[:2]}")
        else:
            ok(f"{core.DPH_STEP_MS_MIN / 1000:g} s（下限）既能构建也被 validate_table 认可"
               f"（{len(t5):,} B）")
    except Exception as exc:                     # noqa: BLE001
        bad(f"{core.DPH_STEP_MS_MIN / 1000:g} s（下限）竟然被 build_table 拒：{exc}")

    p = passes[0]
    p.samples = list(p.samples) + [(0, 0, 0, 0, 0)] * 10   # 撑时长即可
    real_los = p.los
    p.los = p.aos + dt.timedelta(seconds=core.DPH_DUR_S_MAX + 60)
    try:
        core.build_table([p], FREQ10, core.DPH_STEP_S_DEFAULT, tz_hours=8)
        bad(f"时长 {core.DPH_DUR_S_MAX + 60} s 的过境竟然通过")
    except ValueError as exc:
        ok(f"超长过境被拒：{str(exc)[:34]}…")
    p.los = real_los

    many = [passes[i % len(passes)] for i in range(core.DPH_PASS_MAX + 1)]
    try:
        core.build_table(many, FREQ10, core.DPH_STEP_S_DEFAULT, tz_hours=8)
        bad(f"{len(many)} 条过境竟然通过（上限 {core.DPH_PASS_MAX}）")
    except ValueError as exc:
        ok(f"{len(many)} 条被拒：{str(exc)[:34]}…")

    # 本地日偏移只有 4 bit（条目 flags 的 [3:0]）。这里以前 `& 0x0F` 静默折回：
    # 跨到第 16 天那趟在电台上显示成"表头那天"，倒计时跟着错，而构表和
    # validate_table 双双放行。
    # 下面全部用**字面量 15/16/17**，不从 core.DPH_DAY_MAX 推导：这条要钉的是
    # 格式的物理上限，用被测常量推导期望的话，把上限改成 100 时这条会跟着
    # 一起放宽 —— 那就又是一条永绿的绿线。
    DAY_BITS = 15                              # 4 bit 能表达的最大值，改不了的物理事实
    if core.DPH_DAY_MAX != DAY_BITS:
        bad(f"PC 的 DPH_DAY_MAX = {core.DPH_DAY_MAX}，而条目的日偏移只有 4 bit，"
            f"最大 {DAY_BITS} —— 常量放宽了，写出来的表会被折回")
    else:
        ok(f"DPH_DAY_MAX = {DAY_BITS}，正好是 4 bit 的物理上限")

    def spread(n):
        """把同一条真实过境按整天往后挪 n 份，本地时间-日期偏移正好 0..n-1。"""
        b = passes[0]
        out = []
        for k in range(n):
            q = core.Pass()
            shift = dt.timedelta(days=k)
            q.aos, q.los, q.tca = b.aos + shift, b.los + shift, b.tca + shift
            q.max_el, q.aos_el, q.name = b.max_el, b.aos_el, b.name
            q.samples = list(b.samples)
            out.append(q)
        return out

    import struct as _st
    try:
        core.build_table(spread(DAY_BITS + 2), FREQ10, core.DPH_STEP_S_DEFAULT, tz_hours=8)
        bad(f"跨 {DAY_BITS + 2} 个本地日的表竟然通过 —— 最后一趟的日期会被折回第 0 天")
    except ValueError as exc:
        ok(f"跨 {DAY_BITS + 2} 个本地日被拒：{str(exc)[:30]}…")
    edge = None
    try:
        edge = core.build_table(spread(DAY_BITS + 1), FREQ10,
                                core.DPH_STEP_S_DEFAULT, tz_hours=8)
    except ValueError as exc:
        bad(f"边界内（{DAY_BITS + 1} 个本地日）的表反而构不出来：{str(exc)[:40]}…"
            " —— 闸门收得太紧或这道检查的样例有问题")
    if edge is not None:
        day_flags = [_st.unpack_from("<16sIIIHHHHHBB", edge,
                                     core.DPH_HDR + i * core.DPH_ENTRY)[9]
                     for i in range(DAY_BITS + 1)]
        if max(day_flags) == DAY_BITS and len(set(day_flags)) == DAY_BITS + 1:
            ok(f"边界内照常写：{len(day_flags)} 趟的日偏移 0..{max(day_flags)} 各不相同")
        else:
            bad(f"{DAY_BITS + 1} 个本地日的表读回来的日偏移是 {day_flags} —— 闸门或排版有问题")

    # 同一条闸门的**另一半**：`day_idx < 0`（队列倒着排）。`>15` 那支一直有用例，
    # 这支只有实现没有测试 —— 删掉它的后果是 `struct.pack("<…B", 负数)` 当场抛
    # 一句 nobody 看得懂的格式化错误（变异验过：`error 'B' format requires
    # 0 <= number <= 255`），而不是§一说好的那句人话；哪天再把 `& 0x0F` 加回来，
    # 就退回"静默折回成错误日期"。两种都不能靠人记住，所以这条必须有用例。
    # 表头取的是 passes[0]，所以把两条前后颠倒就得到负偏移。
    two = spread(2)
    try:
        core.build_table([two[1], two[0]], FREQ10, core.DPH_STEP_S_DEFAULT, tz_hours=8)
        bad("倒着排的表竟然通过 —— 后一趟的日偏移是负数，会被折回成一个错误日期")
    except ValueError as exc:
        ok(f"倒序被拒（日偏移为负）：{str(exc)[:34]}…")
    except Exception as exc:                           # noqa: BLE001
        bad(f"倒序被拦下了，但不是人话错误：{type(exc).__name__} {str(exc)[:40]}")


def check_ownership():
    hdr("归属：表区里不是我们的东西时，擦之前就得说不")
    region = core.DPH_REGION

    blob = b"\xff" * region
    cl = core.classify_region(blob)
    (ok if core.occupied_reason(cl) is None else bad)(
        "整块全 FF（新片）→ 放行")

    blob = b"DPT1" + b"\x05\x02\x10\x00" + b"\x00\x50\x1d\x00" + b"\x61\x35\x00\x00"
    blob = blob + b"\x00" * (region - len(blob))
    cl = core.classify_region(blob)
    (ok if core.occupied_reason(cl) is None else bad)(
        "表头是 DPT1 → 放行（这是我们自己的表，照常重写）")

    blob = b"FMP3" + b"\x00" * (region - 4)
    cl = core.classify_region(blob)
    r = core.occupied_reason(cl)
    (ok if r else bad)("表头被别的东西占着 → 拒绝" if r else "表头是 FMP3 却放行了！")
    if r:
        print(f"      → 给出的拒绝理由：{r}")

    blob = bytearray(b"\xff" * region)          # 表头空，但第 2 个扇区有东西
    cl = core.classify_region(bytes(blob))
    (ok if core.occupied_reason(cl) is None else bad)("表头空 + 其余扇区全 FF → 放行")

    blob[4096 * 2 + 100] = 0x42
    cl = core.classify_region(bytes(blob))
    r = core.occupied_reason(cl)
    (ok if r else bad)("表头空但第 2 扇区有数据 → 拒绝（旧代码在这里会直接擦掉）")
    if r:
        print(f"      → 给出的拒绝理由：{r}")

    # 真镜像：把镜像里表区那 64 KiB 原样喂给闸门。
    # 这是整套检查里最"实战"的一条：不是合成数据，是真的字节。
    #
    # 两种来源：
    #   ① 原厂 / 汉化整片镜像（全满.bin、无多系统.bin、汉化-02-external-flash.bin）
    #      —— 串口 dump 产不出来，只能从外部拿到；本地没有就记"未验证"，
    #      不许再像 2026-11-08 之前那样 print 一句"跳过"就算过（假绿）。
    #   ② 自己手台的 dump（dump_region.py 产出的 handset-table-*.bin，裸 64 KiB）
    #      —— 表头是 DPT1，属于"我们自己的表"，闸门必须放行；这条本机就能复跑。
    # 加载器两种格式都吃：文件比 DPH_BASE 短 → 整个文件就是表区；否则按整片镜像
    # 定位到 DPH_BASE。
    fixtures = os.path.join(ROOT, "verify", "fixtures")
    legacy = os.path.join(ROOT, "临时")

    def load_region(path):
        with open(path, "rb") as f:
            data = f.read(core.DPH_BASE + core.DPH_REGION)
        if len(data) < core.DPH_BASE + core.DPH_REGION:
            return data if len(data) == core.DPH_REGION else None
        return data[core.DPH_BASE:core.DPH_BASE + core.DPH_REGION]

    cases = [
        ("全满.bin", False, "原厂镜像（表区是语音包尾巴）"),
        ("无多系统.bin", False, "原厂单系统镜像（同上）"),
        ("汉化-02-external-flash.bin", True, "白头佬汉化镜像（表区真的空着）"),
    ]
    for p in sorted(glob.glob(os.path.join(fixtures, "handset-table-*.bin"))):
        cases.append((os.path.basename(p), True, ""))

    for fname, expect_pass, what in cases:
        if fname.startswith("handset-table-"):
            path = os.path.join(fixtures, fname)
            what = "手台自 dump 表区 %s" % fname
        else:
            path = next((q for q in (os.path.join(fixtures, fname),
                                     os.path.join(legacy, fname))
                         if os.path.exists(q)), os.path.join(fixtures, fname))
        if not os.path.exists(path):
            unverified.append("%s（%s 未验证：本地没有这个文件）" % (what, fname))
            continue
        blob = load_region(path)
        if blob is None:
            bad(f"{fname} 既不是 64 KiB 裸表区，也不够一整个镜像")
            continue
        verified_real.append(fname)
        cl = core.classify_region(blob)
        r = core.occupied_reason(cl)
        if expect_pass:
            (ok if r is None else bad)(
                f"{what} → 放行" if r is None else f"{what} 被误判成占用：{r}")
        else:
            (ok if r else bad)(
                f"{what} → 拒绝擦除" if r else f"{what} 竟然放行了！会被擦掉 {core.DPH_REGION} B 真数据")
            if r:
                print(f"      → {r}")

    print(f"  · 真镜像对照：跑过 {len(verified_real)} 条"
          f"（{'、'.join(verified_real) or '无'}），未验证 {len(unverified)} 条")
    for u in unverified:
        print(f"  · 未验证：{u}")

    # guard() 的**两半**都要测：拦得住禁区，也要放得行表区。
    # 上一版只有前一半，于是"表区落在自己声明的禁区里"这种致命错误全绿通过。
    for name, addr in (("13 个 FAP1 应用区", 0x102000),
                       ("app 槽声明区中段", 0x140000),
                       ("A-law 语音包头", 0x11C000),
                       ("表区上方语音尾", 0x180000),
                       ("汉化字库", 0x1C0000)):
        try:
            core.guard(addr, 16)
            bad(f"guard() 放过了{name} 0x{addr:06X}")
        except Exception:                        # noqa: BLE001
            ok(f"guard() 拦住{name} 0x{addr:06X}")

    n_sect = core.DPH_REGION // core.DPH_SECTOR
    blocked = []
    for i in range(n_sect):
        a = core.DPH_BASE + i * core.DPH_SECTOR
        try:
            core.guard(a, core.DPH_SECTOR)
        except Exception as exc:                 # noqa: BLE001
            blocked.append((a, str(exc)))
    if blocked:
        bad(f"guard() 拒了表区自己的 {len(blocked)}/{n_sect} 个扇区"
            f"（首个 0x{blocked[0][0]:06X}：{blocked[0][1]}）—— "
            f"写表按钮会全线静默失效")
    else:
        ok(f"guard() 放行表区全部 {n_sect} 个扇区"
           f"（0x{core.DPH_BASE:06X}..0x{core.DPH_BASE + core.DPH_REGION:06X}）")

    # erase_plan 的每个扇区同样必须过 guard —— 这是写表线程真正会走的路径
    plan = core.erase_plan(core.DPH_REGION)
    try:
        for a in plan:
            core.guard(a, core.DPH_SECTOR)
        ok(f"erase_plan 的 {len(plan)} 个扇区全部通过 guard()")
    except Exception as exc:                     # noqa: BLE001
        bad(f"erase_plan 里有扇区被 guard() 拒：{exc}")


# ------------------------------------------------------------- 固件 .h 复核
def read_defines(path):
    txt = open(path, "r", encoding="utf-8", errors="replace").read()
    out = {}
    for m in re.finditer(r"^#define\s+([A-Z0-9_]+)\s+\(?([^)\n/]+?)\)?\s*(?:/\*|//|$)",
                         txt, re.M):
        val = m.group(2).strip().rstrip("uU")
        try:
            out[m.group(1)] = int(val, 0)
        except ValueError:
            pass
    return out


def check_firmware():
    hdr("固件 doppler.h ↔ PC：同一套整数公式重算容量不等式")
    h = read_defines(FW_H)
    need = ["DPH_BASE_ADDR", "DPH_REGION_SIZE", "DPH_SAMPLE_OFF", "DPH_HDR_SIZE",
            "DPH_ENTRY_SIZE", "DPH_PT_SIZE", "DPH_PASS_MAX", "DPH_STEP_MS_MIN",
            "DPH_DUR_S_MAX"]
    miss = [k for k in need if k not in h]
    if miss:
        bad(f"doppler.h 里读不到：{miss}")
        return

    for k, v in (("DPH_BASE_ADDR", core.DPH_BASE), ("DPH_REGION_SIZE", core.DPH_REGION),
                 ("DPH_SAMPLE_OFF", core.DPH_SAMPLE_OFF),
                 ("DPH_HDR_SIZE", core.DPH_HDR),
                 ("DPH_ENTRY_SIZE", core.DPH_ENTRY),
                 ("DPH_PT_SIZE", core.DPH_PT),
                 ("DPH_PASS_MAX", core.DPH_PASS_MAX),
                 ("DPH_STEP_MS_MIN", core.DPH_STEP_MS_MIN),
                 ("DPH_DUR_S_MAX", core.DPH_DUR_S_MAX)):
        if h.get(k) == v:
            ok(f"{k} = {v}（.h 与 PC 一致）")
        else:
            bad(f"{k}: .h = {h.get(k)} / PC = {v}")

    base, region = h["DPH_BASE_ADDR"], h["DPH_REGION_SIZE"]
    if base % 4096 == 0 and region % 4096 == 0 and h["DPH_SAMPLE_OFF"] % 4096 == 0:
        ok(f"三者都按 4 KiB 扇区对齐（base 0x{base:06X} / region {region // 1024} KiB"
           f" / sample_off 0x{h['DPH_SAMPLE_OFF']:X}）")
    else:
        bad("有地址不是 4 KiB 对齐，扇区擦写会跨到邻居")
    if h["DPH_SAMPLE_OFF"] < h["DPH_HDR_SIZE"]:
        bad("样点区压到表头上了")
    else:
        ok(f"样点区 0x{h['DPH_SAMPLE_OFF']:X} 在表头（{h['DPH_HDR_SIZE']} B）之后，"
           f"中间 {h['DPH_SAMPLE_OFF'] - h['DPH_HDR_SIZE']} B 空着")

    # 编译期那两条断言（FIX(CAP-BY-BYTES)）用同一套整数重算：
    #   1. 条目区必须正好填满表头扇区；
    #   2. 一趟满长过境必须装得下。
    entry_area = h["DPH_HDR_SIZE"] + h["DPH_PASS_MAX"] * h["DPH_ENTRY_SIZE"]
    if entry_area == h["DPH_SAMPLE_OFF"]:
        ok(f"编译期断言 1 重算：{h['DPH_PASS_MAX']}×{h['DPH_ENTRY_SIZE']}"
           f"+{h['DPH_HDR_SIZE']} = {entry_area:,} B == 样点区起点 "
           f"0x{h['DPH_SAMPLE_OFF']:X}（条目区不多不少）")
    else:
        bad(f"编译期断言 1 重算：{entry_area:,} B ≠ 样点区起点 "
            f"{h['DPH_SAMPLE_OFF']:,} B，固件根本编不过")

    one = (h["DPH_SAMPLE_OFF"] + h["DPH_ENTRY_SIZE"] +
           (h["DPH_DUR_S_MAX"] * 1000 // h["DPH_STEP_MS_MIN"] + 1) * h["DPH_PT_SIZE"])
    if one <= region:
        ok(f"编译期断言 2 重算：最坏一趟 {one:,} B ≤ 表区 {region:,} B")
    else:
        bad(f"编译期断言 2 重算：最坏一趟 {one:,} B > 表区 {region:,} B，固件根本编不过")

    mb = read_defines(MB_H)
    sonic = mb.get("MB_SONIC_RESERVE_BASE")
    if sonic is None:
        bad("mb_flash.h 里读不到 MB_SONIC_RESERVE_BASE")
    elif base + region <= sonic:
        ok(f"表区末尾 0x{base + region:06X} ≤ Sonic 保留区 0x{sonic:06X}")
    else:
        bad(f"表区末尾 0x{base + region:06X} 越进 Sonic 保留区 0x{sonic:06X}")

    # 下沿：必须整块落在 48 个 app 槽的声明区之后。
    # app 槽从 APP_REGION_BASE 起，48 × 8 KiB 正好排到 0x162000，
    # 表区从这里开始 = 不多不少吃掉一个字节的槽位。
    overlay = read_defines(os.path.join(ROOT, "固件源码", "uvk1-v6.3.0", "App",
                                        "apps", "app_overlay.h"))
    app_base = overlay.get("APP_REGION_BASE")
    app_stride = overlay.get("APP_SLOT_STRIDE")
    app_count = overlay.get("APP_SLOT_COUNT")
    if None in (app_base, app_stride, app_count):
        bad("app_overlay.h 里读不到 APP_REGION_BASE / APP_SLOT_STRIDE / APP_SLOT_COUNT")
    else:
        slots_end = app_base + app_stride * app_count
        if base == slots_end:
            ok(f"表区起点 0x{base:06X} 正好等于 app 槽声明区末尾"
               f"（0x{app_base:06X} + {app_count} × {app_stride // 1024} KiB）")
        elif base > slots_end:
            ok(f"表区起点 0x{base:06X} 在 app 槽声明区末尾 0x{slots_end:06X} 之后，"
               f"中间 {base - slots_end:,} B 空着")
        else:
            bad(f"表区起点 0x{base:06X} 压进 app 槽声明区（末尾 0x{slots_end:06X}）——"
                f"会把第 {48 - (base - app_base) // app_stride + 1} 槽吃掉")

    # 上沿：汉化字库 0x180000 之前必须留出缓冲。
    # 实测 02-external-flash.bin：40 个 FAP1 应用占 0x102000..0x151A5C，
    # 0x162000..0x180000 非 FF 字节数 0/122,880；大字体 16x16 @0x180000..0x1C0000、
    # 小字体 8x8 @0x1C0000..0x1D0000、拼音检索表 @0x1D0000..0x1D6300。
    CN_ASSET_BASE = h.get("DPH_CN_ASSET_BASE", 0x180000)
    tail = base + region
    if tail <= CN_ASSET_BASE:
        ok(f"表区末尾 0x{tail:06X} ≤ 汉化字库起点 0x{CN_ASSET_BASE:06X}，"
           f"中间留 {CN_ASSET_BASE - tail:,} B 缓冲")
    else:
        bad(f"表区末尾 0x{tail:06X} 越过汉化字库起点 0x{CN_ASSET_BASE:06X}，"
            f"汉化包一刷就重合 {tail - CN_ASSET_BASE:,} B")

    # 解锁令牌：PC 发的和固件认的必须是同一个数，否则 0x0776 永远回 Credits=0
    tok = h.get("DPH_UNLOCK_TOKEN")
    if tok is None:
        bad("doppler.h 里读不到 DPH_UNLOCK_TOKEN（受控清地那条通道没了）")
    else:
        try:
            import doppler_tool as tool
            if tool.UNLOCK_TOKEN == tok:
                ok(f"解锁令牌 0x{tok:08X}：PC 与固件一致")
            else:
                bad(f"解锁令牌不一致：PC 0x{tool.UNLOCK_TOKEN:08X} / .h 0x{tok:08X}")
        except Exception as exc:                 # noqa: BLE001
            bad(f"读不到 doppler_tool.UNLOCK_TOKEN：{exc}")

    # 原厂镜像里这块地原本躺着语音包尾巴（0x162000..0x196510）。
    # 那不是 bug 而是设计取舍：表区挑的是汉化包的空位，
    # 代价是第一台有语音的机器首次写表会被归属闸门拒，必须人手确认一次。
    VOICE_END = 0x196510
    if base < VOICE_END:
        ok(f"已知取舍：原厂语音包尾 0x{VOICE_END:06X} 覆盖表区，"
           f"首次写表会被归属闸门拒（这是设计意图，不是漏检）")


def main():
    strict = "--strict" in sys.argv[1:]
    print("多普勒表区：容量 / 精度 / 归属（离线，不需要电台）")
    print(f"表区 0x{core.DPH_BASE:06X}..0x{core.DPH_BASE + core.DPH_REGION:06X}，"
          f"{core.DPH_REGION // 1024} KiB，{core.DPH_PASS_MAX} 条，"
          f"采样间隔 ≥ {core.DPH_STEP_MS_MIN // 1000} s")
    check_firmware()
    check_capacity()
    check_accuracy()
    check_gates()
    check_ownership()

    print("\n" + "=" * 34)
    if fails:
        print(f"有 {len(fails)} 项硬错误，见上")
        return 1
    if strict and unverified:
        print(f"有 {len(unverified)} 条真镜像对照没跑（--strict）")
        return 1
    print("全部通过" + (f"（{len(unverified)} 条真镜像对照未验证）" if unverified else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
