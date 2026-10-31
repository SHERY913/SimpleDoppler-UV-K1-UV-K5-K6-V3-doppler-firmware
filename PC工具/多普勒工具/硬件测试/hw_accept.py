# -*- coding: utf-8 -*-
"""新固件验收：一条脚本跑完全套，最后一定交还。

T0 0x076A 自检        T1 0x0772 固件自述      T2 0x0601 回包 ID
T3 接管生效           T4 24 字节短帧（不补零）  T5 36 字节补零帧
T6 0x0770 单位 == 10 Hz  T7 10 s 连推          T8 拔线防呆
T9 交还还频
"""
import os, sys, time, struct
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # 上一级就是工具根，项目搬家/改名不再断
import serial, radio_proto as rp

PORT = "COM7"
PUSH = 14580000
# 协议号：7 起 0x0775 的 State 是亚音自己的四位（ACCEPT/APPLIED/REJECT/PENDING）、
# Tone10 是真正装进发射配置的值。6 及以前回的是请求值本身。
# 这里必须跟 `DOPPLER_Proto()`（doppler.c）同一个数 —— 上一版写死 6，proto 升 7
# 之后 T0 一直红，而红的原因和固件没关系。
PROTO_EXPECT = 7
DPH_BASE = 0x00162000          # DPH_BASE_ADDR，读表用
res = []


def ok(name, cond, detail=""):
    res.append((name, bool(cond), detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("   " + detail if detail else ""))


def openport():
    s = serial.Serial(PORT, 38400, timeout=0.05)
    time.sleep(0.25)
    s.reset_input_buffer()
    return s


def ask(s, cmd, payload=b"", timeout=1.2):
    """返回 (reply_cmd, payload) 或 (None, None)；不假设回包 ID。"""
    s.reset_input_buffer()
    s.write(rp.request_bytes(cmd, payload)); s.flush()
    buf = b""
    end = time.time() + timeout
    while time.time() < end:
        c = s.read(512)
        if c:
            buf += c
            for r in rp.parse_replies(buf):
                rr = r[0] if isinstance(r, list) else r
                if rr is not None and hasattr(rr, "cmd"):
                    return rr.cmd, bytes(rr.payload)
        else:
            time.sleep(0.005)
    return None, None


def one(s, cmd, val):
    c, p = ask(s, cmd, bytes([val]))
    return (p[0] if p else None), c


def push(s, rx10, name=b"ISS", pad=False, timeout=1.0):
    f = (struct.pack("<IIhHhHh", rx10, PUSH, 0, 500, 300, 4500, 0)
         + bytes([50, 0, len(name)]) + name)
    if pad:
        f += bytes(36 - len(f))
    c, p = ask(s, 0x076C, f, timeout)
    return (p[0] if p else None), len(f), c


def rx10(s):
    c, p = ask(s, 0x0770)
    return (struct.unpack("<I", p[0:4])[0] if p and len(p) >= 4 else None)


def table_freqs(s):
    """读电台过境表里每一条的下行频点（10 Hz）。返回 set，读不到返回 None。

    T9 的参照物必须是**机器里真实那张表**，不是测试自己推下去的 PUSH。上一版
    写死"回到 145.8 附近 ±250 kHz"，于是当表里装的是 437.800（SSTV 那颗）时
    必然报红——固件其实精确回到了 `cur.freq10`，错的是测试拿的参照。
    读不到表要明说"无法判定"，不许当成通过。
    """
    c, p = ask(s, 0x0764, struct.pack("<IH", DPH_BASE, 16))
    if not p or len(p) < 7 + 16 or p[6] != 0:
        return None
    hdr = p[7:7 + 16]
    if hdr[0:4] != b"DPT1":
        return None
    count, hsz = hdr[5], struct.unpack("<H", hdr[6:8])[0]
    if count == 0 or count > 255:
        return None
    out = set()
    for i in range(count):
        c, p = ask(s, 0x0764, struct.pack("<IH", DPH_BASE + hsz + i * 40, 40))
        if not p or len(p) < 7 + 40 or p[6] != 0:
            return None
        out.add(struct.unpack("<I", p[7 + 16:7 + 20])[0])   # dph_entry_t.freq10
    return out


print("=== T0 0x076A 自检 ===")
s = openport()
try:
    c, p = ask(s, 0x076A)
    ok("回包 ID 0x076B", c == 0x076B, c and hex(c) or "")
    # 回包整帧 7 字节 = Header 4 + Proto/State/Phase 3，这里拿到的是 payload 3 字节
    ok(f"payload 3 字节、proto={PROTO_EXPECT}",
       p and len(p) == 3 and p[0] == PROTO_EXPECT, p and p.hex() or "")

    print("=== T1 0x0772 固件自述 ===")
    c, p = ask(s, 0x0772)
    ver = p[0:8].split(b"\x00")[0].decode("ascii", "replace") if p else ""
    ok("回包 ID 0x0773 + 帧长 12", c == 0x0773 and p and len(p) == 12, p and p.hex() or "")
    ok("版本串 v6.3.0", ver == "v6.3.0", ver)
    if p and len(p) >= 12:
        print("         ppm=%d/%d proto=%d phase=%d" %
              (struct.unpack("<b", p[8:9])[0], p[9], p[10], p[11]))

    print("=== T2 0x0601 回包 ID（这条是本轮修的）===")
    c, p = ask(s, 0x0601, bytes([0x38]))
    ok("回包 ID = 0x0602（旧固件是 0x0601）", c == 0x0602, c and hex(c) or "超时")
    ok("回的是 reg 0x38 + 值", p and len(p) == 3 and p[0] == 0x38, p and p.hex() or "")

    print("=== T3 接管 ===")
    one(s, 0x076E, 1); time.sleep(0.2)
    st, _ = one(s, 0x0768, 1)
    ok("0x0769 bit0=1（已接管）", st is not None and (st & 0x01), st and bin(st) or "")
    c, p = ask(s, 0x0772)
    ok("phase=5（进了第二套逻辑页）", p and len(p) >= 12 and p[11] == 5, p and str(p[11]) or "")

    print("=== T6 上行亚音 0x0774（本轮新增：接管期间按 PTT 出去的就是它）===")
    c, p = ask(s, 0x0774, struct.pack("<H", 670))     # 67.0 Hz
    ok("回包 0x0775 + 回显 670", c == 0x0775 and p is not None and len(p) == 3
       and struct.unpack("<H", p[0:2])[0] == 670,
       p.hex() if p else "超时（旧固件不认这条）")
    c, p = ask(s, 0x0774, struct.pack("<H", 0))       # 明确不要亚音
    ok("能关掉（回显 0）", c == 0x0775 and p is not None
       and struct.unpack("<H", p[0:2])[0] == 0,
       p.hex() if p else "超时")
    c, p = ask(s, 0x0774, struct.pack("<H", 12345))   # 不在 CTCSS_Options 里
    # proto 7 起：回显必须是"真正装进去的值"，非法值 → 回 0（被当 OFF），
    # 且 State.bit2 (0x04) 置起。proto 6 及以前这里回显请求值 12345 —— 那是谎话。
    if c == 0x0775 and p is not None and len(p) == 3:
        echo = struct.unpack("<H", p[0:2])[0]
        st = p[2]
        ok("非法亚音回真实值并置拒绝位（proto 7）",
           echo == 0 and (st & 0x04) != 0, f"echo={echo} state=0x{st:02X}")
    else:
        ok("非法亚音仍然回包", c == 0x0775, hex(c) if c else "超时")
    ask(s, 0x0774, struct.pack("<H", 670))            # 恢复 67.0，后面照常推帧

    print("=== T4 24 字节短帧（不补零，本轮门槛改 21 才收得下）===")
    st, n, _ = push(s, PUSH, b"ISS")          # 21 + 3 = 24
    ok("24 字节帧被接收（有回包）", st is not None, f"len={n} state={st}")
    v = rx10(s)
    ok("0x0770 单位 = 10 Hz（推 14580000 读回 14580000）", v == PUSH, f"读回 {v}")

    print("=== T5 36 字节补零帧（老 App 的写法）===")
    st2, n2, _ = push(s, PUSH + 1000, b"ISS", pad=True)
    ok("补零帧同样被接收", st2 is not None, f"len={n2} state={st2}")
    v2 = rx10(s)
    ok("频点跟着走", v2 == PUSH + 1000, f"读回 {v2}")

    print("=== T7 10 s 连推（5 Hz）===")
    t0 = time.time(); sent = lost = 0
    while time.time() - t0 < 10.0:
        el = time.time() - t0
        st, _, _ = push(s, PUSH - int(el * 3000), b"ISS", pad=True, timeout=0.15)
        sent += 1
        if st is None:
            lost += 1
        time.sleep(0.2)
    ok("40 帧内不掉线", lost <= 2, f"发 {sent} 帧 / 无回包 {lost}")
    v3 = rx10(s)
    ok("连推后仍在 10 Hz 量级", v3 is not None and abs(v3 - (PUSH - 29000)) < 20000, f"读回 {v3}")
finally:
    one(s, 0x076E, 0)
    one(s, 0x0768, 0)
    time.sleep(0.4)
    print("=== T9 交还 ===")
    v4 = rx10(s)
    c, p = ask(s, 0x0772)
    ph = p[11] if (p and len(p) >= 12) else None
    # 两种合法收尾：页面没开着 → 回操作员信道；页面还开着（phase=1）→ 回到
    # 表里那条自己的频点（本轮新增：交还时把页面自己的 VFO 重新接回来）。
    # 两种都不该停在手机最后一帧的偏移值 PUSH+1000 上。
    ok("交还后不再跟着手机最后一帧", v4 is not None and v4 != PUSH + 1000,
       f"读回 {v4} phase={ph}")
    if ph == 1:
        freqs = table_freqs(s)
        if freqs is None:
            ok("页面还开着时，频点回到表里那条", False,
               "读不到过境表 —— 无法判定，不算通过")
        else:
            ok("页面还开着时，频点回到表里某一条（精确等于 cur.freq10）",
               v4 in freqs, f"读回 {v4}，表里的下行频点 {sorted(freqs)}")
    s.close()

print("=== T8 拔线防呆（重连后频率应自己回来）===")
s = openport()
try:
    before = rx10(s)
    one(s, 0x076E, 1); one(s, 0x0768, 1)
    push(s, PUSH, b"ISS", pad=True)
    during = rx10(s)
    ok("接管期间频率被改到推帧值", during == PUSH, f"{during}")
finally:
    s.close()                      # 不发交还，直接拔
time.sleep(8.0)
s = openport()
try:
    after = rx10(s)
    ok("8 s 后频率自己回到操作员信道", after == before, f"{before} -> {after}")
    c, p = ask(s, 0x0772)
    ok("页面回 READY（phase=1）", p and len(p) >= 12 and p[11] == 1, p and str(p[11]) or "")
finally:
    one(s, 0x0768, 0); one(s, 0x076E, 0)
    s.close()

bad = [r for r in res if not r[1]]
print("\n=== 汇总：%d 项通过 / %d 项失败 ===" % (len(res) - len(bad), len(bad)))
for n, _, d in bad:
    print("  FAIL:", n, d)
