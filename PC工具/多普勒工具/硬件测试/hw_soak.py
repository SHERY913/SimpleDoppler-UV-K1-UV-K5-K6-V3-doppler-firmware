# -*- coding: utf-8 -*-
"""连推 10 s（5 Hz，像 App 那样扫频），每 2 s 抽一次 0x0770 / 0x0772，最后交还。"""
import sys, time, struct
sys.path.insert(0, r"C:\Users\13766\Documents\deepseek-harness\default-workspace\多普勒\PC工具\多普勒工具")
import serial, radio_proto as rp

ser = serial.Serial("COM7", 38400, timeout=0.05)
ser.reset_input_buffer()


def ask(cmd, payload=b"", timeout=1.5):
    want = cmd + 1
    ser.reset_input_buffer()
    ser.write(rp.request_bytes(cmd, payload)); ser.flush()
    buf = b""; end = time.time() + timeout
    while time.time() < end:
        c = ser.read(512)
        if c:
            buf += c
            for r in rp.parse_replies(buf):
                rr = r[0] if isinstance(r, list) else r
                if rr is not None and rr.cmd == want:
                    return rr
        else:
            time.sleep(0.005)
    return None


def rx10():
    r = ask(0x0770)
    return struct.unpack("<I", r.payload[0:4])[0] if r and len(r.payload) >= 4 else None


def tag():
    r = ask(0x0772)
    if not r or len(r.payload) < 12:
        return None
    p = r.payload
    return "%s ppm=%d/%d proto=%d phase=%d" % (
        p[0:8].split(b"\x00")[0].decode("ascii", "replace"),
        struct.unpack("<b", p[8:9])[0], p[9], p[10], p[11])


def frame(rx, tx=14580000, name=b"ISS"):
    f = struct.pack("<IIhHhHh", rx, tx, 0, 500, 300, 4500, 0) + bytes([50, 0, 3]) + name
    return f + bytes(36 - len(f))


try:
    ask(0x0768, b"\x00"); ask(0x076E, b"\x01"); time.sleep(0.3)
    print("接管:", ask(0x0768, b"\x01").payload[0], "| fwtag:", tag())

    t0 = time.time(); n = 0; bad = 0
    base = 14580000
    while time.time() - t0 < 10.0:
        el = time.time() - t0
        rx = base - int(el * 3000)          # 每秒降 3 kHz，模拟过境
        r = ask(0x076C, frame(rx), timeout=0.15)
        n += 1
        if not r:
            bad += 1
        time.sleep(0.2)
        if n % 10 == 0:
            print("  t=%4.1fs 推 rx10=%d 回读 0x0770=%s" % (el, rx, rx10()))
    print("共推 %d 帧，无回包 %d" % (n, bad))
    print("末帧 0x0770 =", rx10(), "| fwtag:", tag())
finally:
    ask(0x076E, b"\x00"); ask(0x0768, b"\x00"); time.sleep(0.5)
    print("交还后 0x0770 =", rx10(), "| fwtag:", tag())
    ser.close()
