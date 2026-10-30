"""信号表读数口径的对照检查：电台真实采样 + 合成突发，跑三代算法。

复刻的是 radio.c 的 RADIO_SignalDbm()（逐行对齐），不是另写一套。
这一格的历史就是本项目最典型的历史：同一个量，固件、主界面、PC 工具各算一遍。
"""
import statistics
import sys

FLOOR_OLD, FLOOR_NEW, CEIL = -125, -139, -53
WIN, DECAY10 = 8, 20
STALE_TICKS = 200                    # 2 s @ 10 ms tick：多久没有效读数就转 --
TICK_PER_MS = 0.1
CONFIRM10 = 60                       # 上冲需下一帧确认，容差 6 dB（0.1 dB 单位）
FRAME_MS = 100                      # $D 在 RUN 页是 10 Hz ⇒ 一帧 100 ms = 10 个 tick
FRAME_TICKS = FRAME_MS // 10              # 与固件同单位：10 ms/tick


def clamp(v, floor):
    return min(CEIL, max(floor, v))


def gen1(rssi_raw, corr=-6):
    return rssi_raw / 2 - 160 + corr


def old_mean(seq, corr=-6):
    """最早那版：单次瞬时值，只有下钳 -125，没有封底字符串之前的形态。"""
    return ["%d" % round(clamp(gen1(r, corr), FLOOR_OLD)) if r else "--" for r in seq]


def avg_marker(seq, corr=-6):
    """上一轮我加的：8 次平均 + 平均 ≤ -125 就印封底话。"""
    out, w = [], []
    for r in seq:
        if r:
            w.append(gen1(r, corr))
        wm = w[-WIN:]
        if not wm:
            out.append("--")
            continue
        m = sum(wm) / len(wm)
        out.append("<%d" % FLOOR_OLD if m <= FLOOR_OLD else "%d" % round(m))
    return out


def peak_hold(seq, corr=-6):
    """这一版：逐行照搬 radio.c 的 RADIO_SignalDbm()（含"上冲需下一帧确认"）。"""
    out, w = [], []
    hold, have, tick, t = None, False, 0, 0
    pend, pend_ok = None, False
    last_ok = 0
    for r in seq:
        t += FRAME_TICKS
        if r != 0:
            v = gen1(r, corr)
            w.append(v)
            if len(w) > WIN:
                w = w[-WIN:]
            last_ok = t
            if not have:
                hold, have, tick = v * 10, True, t
                out.append("%d" % round(clamp(v, FLOOR_NEW)))
                continue
            gap = min(t - tick, 6000)          # tick
            tick = t
            d = hold - gap * DECAY10 // 100
            d = max(d, FLOOR_NEW * 10)
            floor10 = statistics.median(w) * 10      # 中位数，一帧毛刺抬不起来
            d = max(d, floor10)
            if d < v * 10:                       # 想上冲
                if pend_ok and abs(v * 10 - pend) <= CONFIRM10:
                    d = max(d, pend, v * 10)     # 连续两帧都报这个电平 → 采信
                    pend_ok = False
                else:
                    pend, pend_ok = v * 10, True # 先记着，下一帧再说
            else:
                pend_ok = False
            hold = d
            out.append("%d" % round(clamp(d / 10.0, FLOOR_NEW)))
        else:
            if not have:
                out.append("--")
                continue
            if t - last_ok > STALE_TICKS:
                have, w, pend_ok = False, [], False
                out.append("--")
                continue
            gap = min(t - tick, 6000)          # tick
            tick = t
            d = max(hold - gap * DECAY10 // 100, FLOOR_NEW * 10)
            hold = d
            out.append("%d" % round(clamp(d / 10.0, FLOOR_NEW)))
    return out


def show(title, seq, corr=-6):
    print("\n%s" % title)
    print("  原始 REG_67: %s" % " ".join(str(x) for x in seq[:16]))
    for label, f in (("最早(瞬时+下钳-125)", lambda s: old_mean(s, corr)),
                     ("上一轮(8次平均+封底)", lambda s: avg_marker(s, corr)),
                     ("这一版(峰值保持)     ", lambda s: peak_hold(s, corr))):
        r = f(seq)
        print("  %s → %s" % (label, " ".join(r[:16])))


if __name__ == "__main__":
    idle = [176] * 8 + [175, 178, 176, 175]
    burst = [76] * 6 + [183] * 6 + [76] * 4
    drop = [183] * 4 + [0] * 30          # 载波走完后检波器连续没有输出

    show("A. 静默本底（实测 437.8 MHz 的取值，原始 175~178）", idle)
    show("B. 突发载波（实测同位置 76 ↔ 183，一跳 53 dB）", burst)
    show("C. 载波走掉后检波器持续没有输出 —— 专门看会不会冻住", drop)
    # 单帧残留：改频后第一次读会拿到上一个频点的值（scanner.c 为此"读两次丢一次"）
    residue = [76] * 6 + [300] + [76] * 8        # 中间那一帧是假峰 -10 dBm
    show("D. 单帧假峰（改频后的寄存器残留，300 → 约 -10 dBm）", residue)
    print("\n  C 段判据：上一轮的平均会把 0 当 -170 一起算；这一版必须在连续 0")
    print("  之后落到 \"--\"，而不是把 -78 挂在屏幕上不动。")
