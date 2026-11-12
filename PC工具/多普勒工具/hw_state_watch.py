# -*- coding: utf-8 -*-
"""真机 State/Phase 电子见证：3 Hz 轮询 0x076A，只在变化时打一行。

为什么不是 0x0772：phase 有了，但 B1 里最要命的两条（⑦ 载波关不掉、⑮ READY
不许发射）判的是 **State 位图**里的 bit5/bit6 —— 0x076A 的第二个字节就是它。

位定义（固件源码/uvk1-v6.3.0/App/app/doppler.c 的 DOPPLER_ExtState）：
    0x01 ext_ctrl 接管中        0x02 在过境（RUN 或 EXT+手机自报）
    0x04 table_ok 表可用        0x08 auto_armed 武装
    0x10 ext_page 第二套逻辑页  0x20 tx_hold（PTT 按下）
    0x40 FUNCTION_TRANSMIT（芯片真在发）  0x80 串口刷写中

判据口径（B1 卡片）：
    · 「载波关不掉」= 松手后 0x40 位持续不落
    · 「READY 不许发射」= 在 READY 相位下按 PTT，0x20 可以有、**0x40 必须一直是 0**

只读：0x076A 只读 RAM 变量，不写 PLL、不写 VFO、不动接管。

用法：
    python hw_state_watch.py                 # 一直跑到 Ctrl-C
    python hw_state_watch.py --secs 600      # 跑 10 分钟
    python hw_state_watch.py --csv out.csv   # 默认自动落 verify/state-<时间戳>.csv
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import radio_proto as rp

PHASE = {0: "OFF", 1: "READY", 2: "RUN", 3: "DONE", 4: "EMPTY",
         5: "EXT", 6: "PRE", 7: "POST"}
BITS = ((0x01, "ext"), (0x02, "run"), (0x04, "tbl"), (0x08, "arm"),
        (0x10, "page"), (0x20, "ptt"), (0x40, "TX!"), (0x80, "flash"))


def ask(ser, cmd, timeout=0.8):
    ser.reset_input_buffer()
    ser.write(rp.request_bytes(cmd))
    ser.flush()
    buf = bytearray()
    end = time.time() + timeout
    while time.time() < end:
        chunk = ser.read(256)
        if chunk:
            buf += chunk
        replies, rest = rp.parse_replies(buf)
        for r in replies:
            if r is not None and r.cmd == cmd + 1:
                return r
        buf = bytearray(rest)
    return None


def resolve_port(explicit):
    if explicit:
        return explicit
    try:
        import find_port
        for c in find_port.candidates():
            if "COM" in c["device"].upper():
                return c["device"]
    except Exception:
        pass
    return "COM7"


def bits_str(f):
    return "|".join(name for mask, name in BITS if f & mask) or "-"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port")
    ap.add_argument("--secs", type=float, default=0)
    ap.add_argument("--csv")
    ap.add_argument("--rate", type=float, default=3.0)
    a = ap.parse_args()

    import serial

    port = resolve_port(a.port)

    def open_port():
        s = serial.Serial(port, 38400, timeout=0.4, write_timeout=1.5)
        s.reset_input_buffer()
        return s

    ser = open_port()

    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    path = a.csv or os.path.join(
        here, "verify", time.strftime("state-%Y%m%d-%H%M%S.csv"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fh = open(path, "w", newline="", encoding="utf-8")
    w = csv.writer(fh)
    w.writerow(["t", "proto", "state_hex", "bits", "phase", "phase_name"])

    t0 = time.time()
    last = None
    print(f"# 端口 {port} · CSV {path}")
    print(f"{'t(s)':>7}  {'state':>4} {'bits':<22} phase")
    try:
        while True:
            if a.secs and time.time() - t0 >= a.secs:
                break
            try:
                r = ask(ser, rp.CMD_SELFTEST)
                now = time.time() - t0
            except (serial.SerialException, OSError) as exc:
                # 手台拔插、USB 休眠、另一个进程抢口：都只是暂时，重开继续记。
                # B1 那种"人在按、我在记"的场合不能因为一次写超时就丢整段日志。
                print(f"# 串口异常 {type(exc).__name__}: {exc} —— 1 秒后重开")
                try:
                    ser.close()
                except Exception:
                    pass
                time.sleep(1.0)
                try:
                    ser = open_port()
                    print("# 串口已重开")
                except Exception as exc2:
                    print(f"# 重开失败 {type(exc2).__name__}: {exc2}")
                    time.sleep(2.0)
                continue
            if r is None:
                time.sleep(1.0 / a.rate)
                continue
            proto, state, phase = r.payload[0], r.payload[1], r.payload[2]
            key = (state, phase)
            if key != last:
                last = key
                print(f"{now:7.2f}  0x{state:02X} {bits_str(state):<22} "
                      f"{phase} {PHASE.get(phase, '?')}")
                fh.flush()
            w.writerow([f"{now:.2f}", proto, f"0x{state:02X}",
                        bits_str(state), phase, PHASE.get(phase, "?")])
            time.sleep(1.0 / a.rate)
    except KeyboardInterrupt:
        pass
    finally:
        fh.close()
        ser.close()
        print(f"# 结束，CSV: {path}")


if __name__ == "__main__":
    main()
