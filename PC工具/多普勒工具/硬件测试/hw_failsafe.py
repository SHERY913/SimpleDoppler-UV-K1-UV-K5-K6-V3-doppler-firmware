# -*- coding: utf-8 -*-
"""防呆实验：接管+推帧后直接拔线（关串口），8 s 后重连看频率有没有自己回来。"""
import os, sys, time, struct
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # 上一级就是工具根，项目搬家/改名不再断
import serial, radio_proto as rp


def openport():
    s = serial.Serial("COM7", 38400, timeout=0.05)
    time.sleep(0.3); s.reset_input_buffer()
    return s


def ask(ser, cmd, payload=b"", timeout=1.5):
    ser.reset_input_buffer()
    ser.write(rp.request_bytes(cmd, payload)); ser.flush()
    buf = b""; end = time.time() + timeout
    while time.time() < end:
        c = ser.read(512)
        if c:
            buf += c
            for r in rp.parse_replies(buf):
                rr = r[0] if isinstance(r, list) else r
                if rr is not None and rr.cmd == cmd + 1:
                    return rr
        else:
            time.sleep(0.005)
    return None


def rx10(ser):
    r = ask(ser, 0x0770)
    return struct.unpack("<I", r.payload[0:4])[0] if r and len(r.payload) >= 4 else None


def frame(rx):
    f = struct.pack("<IIhHhHh", rx, 14580000, 0, 500, 300, 4500, 0) + bytes([50, 0, 3]) + b"ISS"
    return f + bytes(36 - len(f))


s = openport()
print("起始 0x0770 =", rx10(s))
ask(s, 0x076E, b"\x01"); time.sleep(0.2)
print("接管 =", ask(s, 0x0768, b"\x01").payload[0])
for i in range(5):
    ask(s, 0x076C, frame(14580000), timeout=0.3); time.sleep(0.2)
print("推到 145.8000 MHz 后 0x0770 =", rx10(s))

print(">>> 直接关串口（模拟拔线），不再发交还命令")
s.close()
time.sleep(8.0)

s = openport()
print("重连后 0x0770 =", rx10(s))
r = ask(s, 0x0772)
p = r.payload if r else b""
print("重连后 fwtag phase =", p[11] if len(p) >= 12 else "?")
s.close()
