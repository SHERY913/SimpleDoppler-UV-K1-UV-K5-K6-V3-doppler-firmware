# -*- coding: utf-8 -*-
"""判断电台里刷的是旧固件还是新固件：看 0x0601 回哪个 ID、0x0770 什么单位。"""
import sys, time, struct
sys.path.insert(0, r"C:\Users\13766\Documents\deepseek-harness\default-workspace\多普勒\PC工具\多普勒工具")
import serial, radio_proto as rp

s = serial.Serial("COM7", 38400, timeout=0.05)
time.sleep(0.2); s.reset_input_buffer()


def raw(cmd, payload=b"", timeout=0.9):
    s.reset_input_buffer()
    s.write(rp.request_bytes(cmd, payload)); s.flush()
    buf = b""; ids = []
    end = time.time() + timeout
    while time.time() < end:
        c = s.read(256)
        if c:
            buf += c
            for r in rp.parse_replies(buf):
                rr = r[0] if isinstance(r, list) else r
                if rr is not None and hasattr(rr, 'cmd'):
                    ids.append((rr.cmd, bytes(rr.payload)))
        else:
            time.sleep(0.01)
    return ids


try:
    ids = raw(0x0601, bytes([0x38]))
    print("0x0601 回包:", [(hex(c), p.hex()) for c, p in ids])

    ids = raw(0x076C, (struct.pack("<IIhHhHh", 14580000, 14580000, 0, 500, 300, 0, 0)
                       + bytes([0, 0, 3]) + b"ISS" + bytes(12)))
    print("接管状态:", ids[0][1].hex() if ids else "无")
    ids = raw(0x0770)
    if ids and len(ids[0][1]) >= 4:
        v = struct.unpack("<I", ids[0][1][0:4])[0]
        print("0x0770 =", v, "-> 与推的 14580000 比:", "新固件(10Hz)" if v == 14580000 else "旧固件(÷10)")
finally:
    raw(0x0768, b"\x00")
    raw(0x076E, b"\x00")
    s.close()
