# -*- coding: utf-8 -*-
"""被动实验：RSSI(REG_67) 与 AGC 增益寄存器(REG_13) 的相关性。

目的：回答"过境页/主界面的 dBm 到底能不能做准"。
BK4819_GetRSSI_dBm() = REG_67/2 - 160，上游把 `- BK4819_GetRxGain_dB()`
注释掉了。若 RSSI 是"输入折算"的（芯片内部已按当前 AGC 增益补偿），则增益
大幅变化时 RSSI 应保持平稳；若 RSSI 是"检波点原始值"，则增益每降一档，
RSSI 应跟着跳同样的 dB 数——后者就必须把增益加回去才能做绝对读数。

只读，不写任何寄存器。用法：
    python hw_rssi_probe.py                     # 默认 COM7、20 秒
    python hw_rssi_probe.py --port COM7 --seconds 20
    python hw_rssi_probe.py 30                  # 位置参数也认（老写法）
"""
import os, sys, time, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # 上一级就是工具根，项目搬家/改名不再断

import serial
import radio_proto as rp


def parse_args(argv):
    """以前这里只有 `float(argv[1])`，而 文档/项目交接-9.md 的"真机作业"写的是
    `--port COM7 --seconds 20` —— 照文档抄命令的人第一条就崩。以文档为准。"""
    port, dur = "COM7", 20.0
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--port":
            port = argv[i + 1]; i += 2
        elif a.startswith("--port="):
            port = a.split("=", 1)[1]; i += 1
        elif a == "--seconds":
            dur = float(argv[i + 1]); i += 2
        elif a.startswith("--seconds="):
            dur = float(a.split("=", 1)[1]); i += 1
        elif a.isdigit() or a.replace('.', '', 1).isdigit():
            dur = float(a); i += 1                      # 老写法：位置参数
        else:
            print("不认识的参数：%s（用 --port COMx / --seconds N）" % a)
            raise SystemExit(2)
    return port, dur


PORT, DUR = parse_args(sys.argv[1:])
BAUD = 38400


def ask(ser, cmd, payload=b"", timeout=0.8, reply_id=None):
    ser.reset_input_buffer()
    ser.write(rp.request_bytes(cmd, payload))
    ser.flush()
    buf = bytearray()
    end = time.time() + timeout
    while time.time() < end:
        chunk = ser.read(256)
        if chunk:
            buf += chunk
        replies, rest = rp.parse_replies(buf)
        for r in replies:
            if r is not None and r.cmd == (reply_id if reply_id is not None else cmd + 1):
                return r
        buf = bytearray(rest)
    return None


def read_reg(ser, reg):
    r = ask(ser, 0x0601, bytes([reg]), reply_id=0x0602)
    if r is None or len(r.payload) < 3:
        return None
    # 回包 data = { uint8 reg; uint16 value; } → payload[0] 是回显的寄存器地址
    return r.payload[1] | (r.payload[2] << 8)


def gain_db(v):
    """与 driver/bk4819.c: BK4819_GetRxGain_dB() 同口径。
    pga:3(b0-2) mixer:2(b3-4) lna:3(b5-7) lnaS:2(b8-9)"""
    if v is None:
        return None
    ls = [-28, -24, -19, 0]
    ln = [-24, -19, -14, -9, -6, -4, -2, 0]
    mx = [-8, -6, -3, 0]
    pg = [-33, -27, -21, -15, -9, -6, -3, 0]
    return ls[(v >> 8) & 3] + ln[(v >> 5) & 7] + mx[(v >> 3) & 3] + pg[v & 7]


# 与 App/misc.c 的 dBmCorrTable[7] 同表；频段划分照 App/frequencies.c 的
# frequencyBandTable（宽频版：BAND1 从 BX4819_band1_lower 起）。rx10 单位 10 Hz。
DBM_CORR = [-15, -16, -10, -4, -7, -6, -1]
BANDS = [(10800000, 13700000, 1), (13700000, 17400000, 2), (17400000, 35000000, 3),
         (35000000, 40000000, 4), (40000000, 47000000, 5), (47000000, 600000000, 6)]


def band_corr(rx10):
    """屏幕口径相对裸算要加的那个修正量。"""
    if rx10 is None:
        return 0
    for lo, hi, idx in BANDS:
        if lo <= rx10 < hi:
            return DBM_CORR[idx]
    return DBM_CORR[0]          # 低于 108 MHz 落 BAND1


with serial.Serial(PORT, BAUD, timeout=0.2) as ser:
    print("读当前接收频点…")
    f = ask(ser, 0x0770)
    rx10 = int.from_bytes(f.payload[:4], "little") if (f and len(f.payload) >= 4) else None
    if rx10:
        print(f"  0x0770 rx10 = {rx10} → {rx10 * 10 / 1e6:.5f} MHz")
    print(f"\n采样 {DUR:.0f}s（RSSI / REG_13 / 增益 dB）…\n")
    rows = []
    t0 = time.time()
    while time.time() - t0 < DUR:
        r67 = read_reg(ser, 0x67)
        r7e = read_reg(ser, 0x7E)
        if r67 is None or r7e is None:
            continue
        rssi_raw = r67 & 0x1FF
        # 与固件同尺：dBmCorrTable[band] 那一步以前这里没加，于是"工具算的 dBm"
        # 和"屏幕显示的 dBm"差一档（VHF 差 10 dB），两边各自都"对"，合起来又
        # 是一个说不清的数。现在两列都给：裸的用于对寄存器，折的用于对屏幕。
        dbm = rssi_raw / 2.0 - 160.0
        dbm_disp = dbm + band_corr(rx10) if rx10 else dbm
        agc_str = (r7e >> 5) & 0x7F
        gi = (r7e >> 12) & 0x7
        ga = 0x14 if gi & 0x4 else 0x10 + (gi & 0x3)
        rgain = read_reg(ser, ga)
        g = gain_db(rgain)
        rows.append((rssi_raw, dbm, agc_str, gi, ga, g, dbm_disp))
        print(f"  REG_67={rssi_raw:4d}  {dbm:7.1f} dBm(裸) {dbm_disp:7.1f} dBm(屏幕口径)"
              f" | REG_7E agcStr={agc_str:3d} idx={gi}"
              f" | REG_{ga:02X}=0x{rgain:04X} 增益 {g:+4d} dB")
        time.sleep(0.3)

if not rows:
    print("没有样本：电台没在接收，或串口被占用。")
    sys.exit(2)

dbms = [r[1] for r in rows]
gs = [r[5] for r in rows]
ags = [r[2] for r in rows]
print(f"\n样本 {len(rows)} 条")
print(f"  dBm 范围 {min(dbms):.1f} … {max(dbms):.1f}（跨度 {max(dbms) - min(dbms):.1f} dB，"
      f"抖动 σ={statistics.pstdev(dbms):.2f} dB）")
print(f"  增益范围 {min(gs):+d} … {max(gs):+d}（跨度 {max(gs) - min(gs)} dB，档位数 {len(set(gs))}）")
print(f"  REG_7E agcStr 范围 {min(ags)} … {max(ags)}")
if len(set(gs)) > 1:
    r = statistics.correlation(gs, dbms)
    print(f"  增益-读数 相关系数 r = {r:+.3f}")
    pairs = {}
    for _raw, dbm, _a, _i, _ga, g in rows:
        pairs.setdefault(g, []).append(dbm)
    print("\n  按增益档聚合（增益 → 平均 dBm / n）：")
    for g in sorted(pairs):
        v = pairs[g]
        print(f"    {g:+4d} dB → {sum(v) / len(v):7.1f} dBm  (n={len(v)})")
else:
    print("  增益全程没变（AGC 稳住了）：只能测抖动，测不到增益补偿是否成立。")
if len(set(ags)) > 1:
    print(f"  agcStr-读数 相关系数 r = {statistics.correlation(ags, dbms):+.3f}")
