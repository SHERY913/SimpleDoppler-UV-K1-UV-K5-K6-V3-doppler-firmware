# -*- coding: utf-8 -*-
"""T4 细查：接管到底有没有生效、什么时候失效。只读+可控，最后一定交还。"""
import os, sys, time, struct
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # 上一级就是工具根，项目搬家/改名不再断
import serial, radio_proto as rp

PORT, BAUD = "COM7", 38400
ser = serial.Serial(PORT, BAUD, timeout=0.05)
ser.reset_input_buffer()


def ask(cmd, payload=b"", timeout=1.0, reply_id=None, want=5):
    want_id = reply_id if reply_id is not None else cmd + 1
    ser.reset_input_buffer()
    ser.write(rp.request_bytes(cmd, payload))
    ser.flush()
    buf = b""
    end = time.time() + timeout
    while time.time() < end:
        chunk = ser.read(256)
        if chunk:
            buf += chunk
            for r in rp.parse_replies(buf):
                rr = r[0] if isinstance(r, list) else r
                if rr is not None and rr.cmd == want_id:
                    return rr
        else:
            time.sleep(0.01)
    return None


def one(cmd, val):
    r = ask(cmd, bytes([val]))
    return (r.payload[0] if r and r.payload else None)


def push(rx10, tx10=14580000, name=b"ISS"):
    frame = (struct.pack("<IIhHhHh", rx10, tx10, 0, 500, 300, 4500, 0)
             + bytes([50, 0, len(name)]) + name)
    frame = frame + bytes((28 - len(frame)) % 8 + 8)   # 保证 >=28 且偶数
    t0 = time.time()
    r = ask(0x076C, frame)
    st = (r.payload[0] if r and r.payload else None)
    return st, len(frame), int((time.time() - t0) * 1000)


def fwtag():
    r = ask(0x0772)
    if not r or len(r.payload) < 16:
        return None
    p = r.payload
    return dict(ver=p[0:8].split(b"\x00")[0].decode("ascii", "replace"),
                ppm=struct.unpack("<b", p[8:9])[0], ppmmax=p[9], proto=p[10], phase=p[11])


try:
    print("== 1) 打开页面开关 0x076E=1 ==")
    print("   state:", one(0x076E, 1))
    print("   fwtag:", fwtag())

    print("== 2) 接管 0x0768=1 ==")
    print("   0769 state:", one(0x0768, 1))
    print("   fwtag:", fwtag())

    print("== 3) 连续推帧（每组前先看 phase / state）==")
    for i in range(4):
        tag = fwtag()
        st, n, ms = push(14580000 + i * 1000)
        rb = ask(0x0770)
        rb10 = struct.unpack("<I", rb.payload[0:4])[0] if rb and len(rb.payload) >= 4 else None
        print("   push#%d len=%d %dms -> 076D=%s | phase=%s | 0770=%s"
              % (i, n, ms, st, tag["phase"] if tag else "?", rb10))

    print("== 4) 再确认接管位 ==")
    print("   fwtag:", fwtag())
finally:
    print("== 交还 0x0768=0 ==")
    print("   state:", one(0x0768, 0))
    print("   0x0770:", ask(0x0770))
    ser.close()
