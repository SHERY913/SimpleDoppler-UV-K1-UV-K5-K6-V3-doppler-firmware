# -*- coding: utf-8 -*-
"""对照实验：page 关/开 两种顺序下，接管位能不能活下来。"""
import os, sys, time, struct
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # 上一级就是工具根，项目搬家/改名不再断
import serial, radio_proto as rp

ser = serial.Serial("COM7", 38400, timeout=0.05)
ser.reset_input_buffer()


def ask(cmd, payload=b"", timeout=0.8, reply_id=None):
    want = reply_id if reply_id is not None else cmd + 1
    ser.reset_input_buffer()
    ser.write(rp.request_bytes(cmd, payload)); ser.flush()
    buf = b""; end = time.time() + timeout
    while time.time() < end:
        c = ser.read(256)
        if c:
            buf += c
            for r in rp.parse_replies(buf):
                rr = r[0] if isinstance(r, list) else r
                if rr is not None and rr.cmd == want:
                    return rr
        else:
            time.sleep(0.01)
    return None


def one(cmd, val):
    r = ask(cmd, bytes([val]))
    return (r.payload[0] if r and r.payload else None)


def push(rx10):
    f = struct.pack("<IIhHhHh", rx10, 14580000, 0, 500, 300, 4500, 0) + bytes([50, 0, 3]) + b"ISS"
    f += bytes(36 - len(f))
    r = ask(0x076C, f)
    return (r.payload[0] if r and r.payload else None)


def case(page, label):
    one(0x0768, 0); time.sleep(0.4)
    one(0x076E, page); time.sleep(0.2)
    st0 = one(0x0768, 1)
    seq = [st0]
    for _ in range(6):
        time.sleep(0.25)
        seq.append(push(14580000))
    print("%-14s 0769=%s  推帧回 state 序列=%s" % (label, st0, seq))
    one(0x0768, 0); time.sleep(0.3)


try:
    case(0, "page=0 先接管")
    case(1, "page=1 先开页")
finally:
    one(0x076E, 0)
    one(0x0768, 0)
    r = ask(0x0770)
    if r and len(r.payload) >= 4:
        v = struct.unpack("<I", r.payload[0:4])[0]
        print("收尾 0x0770 = %d (=%.4f MHz)" % (v, v / 10000.0))
    ser.close()
