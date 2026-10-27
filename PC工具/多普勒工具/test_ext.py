#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第二套逻辑联调：PC 直接推帧给电台，验证电台会不会切到手机推送页。

流程：
  1) 0x076E=1  允许电台切到手机推送页
  2) 0x0768=1  接管频率
  3) 每 300 ms 推一帧 0x076C（数值在动），同时照多普勒写 REG_38/39
  4) 24 秒后 0x0768=0 交还，电台应显示 73! 页 3 秒再退出

用法：
  python test_ext.py             # 全程跑一遍
  python test_ext.py --hold     # 推完不交还，停在接管状态（Ctrl-C 结束）
  python test_ext.py --release  # 只交还，用于收尾

电台必须先进多普勒页（F 再按 0），否则 phase=OFF，切不了第二套页。
"""
import argparse
import math
import time

import serial

MASK = [0x16, 0x6C, 0x14, 0xE6, 0x2E, 0x91, 0x0D, 0x40,
        0x21, 0x35, 0xD5, 0x40, 0x13, 0x03, 0xE9, 0x80]


def crc16(data):
    crc = 0
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def frame(cmd, pl=b""):
    msg = bytes([cmd & 0xFF, cmd >> 8, len(pl) & 0xFF, (len(pl) >> 8) & 0xFF]) + pl
    if len(msg) % 2:
        msg += bytes([0])
    body = msg + crc16(msg).to_bytes(2, "little")
    body = bytes(b ^ MASK[i % 16] for i, b in enumerate(body))
    return bytes([0xAB, 0xCD]) + len(msg).to_bytes(2, "little") + body + bytes([0xDC, 0xBA])


class Radio:
    def __init__(self, port, baud=38400):
        self.s = serial.Serial(port, baud, timeout=0.15)
        time.sleep(0.2)

    def close(self):
        try:
            self.s.close()
        except Exception:
            pass

    def ask(self, cmd, pl=b"", wait=0.6):
        self.s.reset_input_buffer()
        self.s.write(frame(cmd, pl))
        self.s.flush()
        end = time.time() + wait
        buf = b""
        while time.time() < end:
            c = self.s.read(64)
            if c:
                buf += c
                break
        return self.decode(buf)

    def decode(self, buf):
        if len(buf) < 8 or buf[0] != 0xAB or buf[1] != 0xCD:
            return None
        n = int.from_bytes(buf[2:4], "little")
        raw = bytes(b ^ MASK[i % 16] for i, b in enumerate(buf[4:4 + n + 2]))
        cmd = raw[0] | raw[1] << 8
        plen = raw[2] | raw[3] << 8
        return cmd, raw[4:4 + plen]

    def state(self):
        r = self.ask(0x076A, b"", 0.8)
        if not r:
            return None
        _, p = r
        if len(p) < 3:
            return None
        proto, st = p[0], p[1]
        # phase 名表补上 EMPTY：固件里 4 = EMPTY，以前缺这一项，phase=4 会被
        # 显示成字符串 "4"，后面的 EXT(5) 也跟着错位（报告 第六部分·回归项 5）。
        names = ["OFF", "READY", "RUN", "DONE", "EMPTY", "EXT"]
        # 位映射按固件 doppler.c 的 DOPPLER_ExtState() 逐位对齐：
        # 0x01 接管 / 0x02 过境中 / 0x04 有表 / 0x08 武装 /
        # 0x10 过境页 / 0x20 PTT / 0x40 发射中 / 0x80 刷写中。
        # 以前把 0x04 当 audio、0x08 当 table、0x20 当 armed，三项全错。
        return {
            "proto": proto,
            "state": st,
            "phase": names[p[2]] if p[2] < len(names) else str(p[2]),
            "ext": bool(st & 0x01),
            "pass": bool(st & 0x02),
            "table": bool(st & 0x04),
            "armed": bool(st & 0x08),
            "page": bool(st & 0x10),
            "ptt": bool(st & 0x20),
            "tx": bool(st & 0x40),
            "flash": bool(st & 0x80),
        }

    def set_rx(self, hz):
        f = int(hz / 10)
        self.ask(0x0602, bytes([0x38, f & 0xFF, (f >> 8) & 0xFF]), 0.05)
        self.ask(0x0602, bytes([0x39, (f >> 16) & 0xFF, (f >> 24) & 0xFF]), 0.05)

    def push(self, rx10, tx10, dopp, dist, remain, el01, rate01, prog, flags, name):
        nb = name.encode("ascii", "replace")[:8]
        pl = (rx10.to_bytes(4, "little") + tx10.to_bytes(4, "little")
              + int(dopp).to_bytes(2, "little", signed=True)
              + int(dist).to_bytes(2, "little")
              + int(remain).to_bytes(2, "little", signed=True)
              + int(el01).to_bytes(2, "little", signed=True)
              + int(rate01).to_bytes(2, "little", signed=True)
              + bytes([prog & 0xFF, flags & 0xFF, len(nb)]) + nb)
        pl += bytes(28 - len(pl)) if len(pl) < 28 else b""
        return self.ask(0x076C, pl, 0.25)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM7")
    ap.add_argument("--hold", action="store_true")
    ap.add_argument("--release", action="store_true")
    ap.add_argument("--seconds", type=float, default=24.0)
    a = ap.parse_args()

    r = Radio(a.port)
    try:
        st = r.state()
        print("[起始状态]", st or "没回包")
        if st is None:
            print("电台没应答，先确认它能被 0x076A 问到。")
            return 2

        if a.release:
            r.ask(0x0768, bytes([0]), 0.3)
            time.sleep(0.6)
            print("[交还后]", r.state())
            return 0

        if st["phase"] == "OFF":
            print("提示：电台现在不在多普勒页（phase=OFF）。")
            print("      先按 F 再按 0 进多普勒页，再重跑本脚本，否则切不到第二套页。")

        print("[开新页开关] 0x076E=1 ->", r.ask(0x076E, bytes([1]), 0.6))
        print("[接管]       0x0768=1 ->", r.ask(0x0768, bytes([1]), 0.6))
        time.sleep(0.4)
        print("[接管后状态]", r.state())

        base = 437_800_000
        t0 = time.time()
        n = 0
        while time.time() - t0 < a.seconds:
            t = time.time() - t0
            # 人造一段过境：远离 -> 靠近 -> 远离
            el = 40.0 * math.sin(math.pi * min(t / 12.0, 1.0))
            dopp = -2400 * math.cos(math.pi * min(t / 12.0, 1.0))
            dist = int(12000 - 8000 * min(t / 12.0, 1.0))
            rate = -2.0 + 4.0 * min(t / 12.0, 1.0)
            remain = max(0, int(a.seconds - t))
            hz = base + int(dopp)
            r.set_rx(hz)
            r.push(int(hz / 10), 14_599_000, dopp, dist, remain,
                   int(el * 10), int(rate * 10), int(100 * min(t / 12.0, 1.0)),
                   0x01, "TEST-ISS")
            if n % 8 == 0:
                print(f"  推帧 {n:>3}  下行 {hz/1e6:.5f} MHz  仰角 {el:+.1f}°  "
                      f"多普勒 {dopp:+.0f} Hz  剩余 {remain}s")
            n += 1
            time.sleep(0.3)

        print(f"[共推 {n} 帧]")
        if a.hold:
            print("保持接管，Ctrl-C 结束（结束前请跑 --release 交还）")
            while True:
                time.sleep(1)
        r.ask(0x0768, bytes([0]), 0.3)
        time.sleep(0.6)
        print("[交还后状态]", r.state())
        print("电台应显示 73! 页 3 秒后退出到频道界面。")
        return 0
    finally:
        r.close()


if __name__ == "__main__":
    raise SystemExit(main())
