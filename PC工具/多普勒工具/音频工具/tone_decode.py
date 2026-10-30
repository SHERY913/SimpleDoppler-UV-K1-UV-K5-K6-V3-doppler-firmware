# -*- coding: utf-8 -*-
"""从一段录音里判读 CTCSS（亚音）——用来证明"亚音到底有没有真的发出去"。

原理：CTCSS 是 67~254 Hz 的低频单音，叠加在 FM 解调后的音频上。把录音带通
到 60~300 Hz，再用 Goertzel 在 EIA 的 50 个标准频点上逐个测能量，取最大者；
同时用"该频点能量 / 相邻频点能量"给出可信度，避免把语音低频误判成亚音。

用法：
  python tone_decode.py 录音.wav            # 16-bit PCM，任意采样率，单/双声道
  python tone_decode.py 录音.wav --freq 67  # 只验某一个音（比如这颗星的 67.0）
  python tone_decode.py 录音.wav --frames 0.5   # 每 0.5 s 一帧，看它稳不稳

怎么录：
  1) 另一台对讲机同频、CTCSS 关掉（或开到"监听"），用音频线/喇叭对着手机录；
     录的时候让被测电台按 PTT 发一段（吹气或静音都行）。
  2) 或用 SDR（RTL-SDR 等）收同频，NFM 解调后录成 wav。
  录音里必须有"被测电台正在发射"的那几秒，否则只会测到本机静噪噪声。
"""
import argparse
import math
import struct
import sys
import wave

# EIA/TIA 标准 50 个 CTCSS 频点（Hz）
EIA = [
    67.0, 69.3, 71.9, 74.4, 77.0, 79.7, 82.5, 85.4, 88.5, 91.5,
    94.8, 97.4, 100.0, 103.5, 107.2, 110.9, 114.8, 118.8, 123.0, 127.3,
    131.8, 136.5, 141.3, 146.2, 151.4, 156.7, 159.8, 162.2, 165.5, 167.9,
    171.3, 173.8, 177.3, 179.9, 183.5, 186.2, 189.9, 192.8, 196.6, 199.5,
    203.5, 206.5, 210.7, 218.1, 225.7, 229.1, 233.6, 241.8, 250.3, 254.1,
]


def read_wav(path):
    with wave.open(path, "rb") as w:
        if w.getsampwidth() != 2:
            raise SystemExit(f"只支持 16-bit PCM，这个文件是 {w.getsampwidth() * 8}-bit")
        ch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    total = len(raw) // 2
    samples = struct.unpack("<%dh" % total, raw)
    if ch > 1:
        samples = samples[0::ch]           # 只取第一声道
    return list(samples), sr


def bandpass(x, sr, lo=60.0, hi=300.0):
    """二阶 IIR 带通（RBJ），只为压掉语音主体，判读不需要高保真。"""
    w0 = 2.0 * math.pi * math.sqrt(lo * hi) / sr
    alpha = math.sin(w0) / (2.0 * 0.7)
    b0, b2 = alpha, -alpha
    a0, a1, a2 = 1 + alpha, -2 * math.cos(w0), 1 - alpha
    b0, b2, a1, a2 = b0 / a0, b2 / a0, a1 / a0, a2 / a0
    y = [0.0] * len(x)
    x1 = x2 = y1 = y2 = 0.0
    for i, v in enumerate(x):
        out = b0 * v + b2 * x2 - a1 * y1 - a2 * y2
        x2, x1 = x1, v
        y2, y1 = y1, out
        y[i] = out
    return y


def goertzel(x, sr, f):
    """单频点能量（均方）。"""
    n = len(x)
    if n == 0:
        return 0.0
    k = int(0.5 + n * f / sr)
    w = 2.0 * math.pi * k / n
    coeff = 2.0 * math.cos(w)
    s0 = s1 = s2 = 0.0
    for v in x:
        s0 = v + coeff * s1 - s2
        s2, s1 = s1, s0
    power = s1 * s1 + s2 * s2 - coeff * s1 * s2
    return power / (n * n)


def analyse(frame, sr):
    x = bandpass(frame, sr)
    tot = sum(v * v for v in x) / max(1, len(x))
    if tot <= 1e-9:
        return None, 0.0, {}
    res = {f: goertzel(x, sr, f) for f in EIA}
    best = max(res, key=res.get)
    # 可信度：该音能量占比（相对 60~300 Hz 总能量）与相对邻音的突出度
    share = res[best] / tot if tot else 0.0
    others = [v for f, v in res.items() if abs(f - best) > 6.0]
    runner = max(others) if others else 0.0
    margin = res[best] / runner if runner > 0 else float("inf")
    return best, share, {"margin": margin, "share": share, "power": res}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wav")
    ap.add_argument("--freq", type=float, default=None, help="只验这个频率")
    ap.add_argument("--frames", type=float, default=0.4, help="每帧秒数，默认 0.4")
    a = ap.parse_args()

    x, sr = read_wav(a.wav)
    dur = len(x) / sr
    print(f"{a.wav}: {sr} Hz, {dur:.2f} s")

    if a.freq:
        xf = bandpass(x, sr)
        p = goertzel(xf, sr, a.freq)
        tot = sum(v * v for v in xf) / max(1, len(xf))
        print(f"  {a.freq:.1f} Hz 能量占比 {p / tot:.4f}" if tot else "  全静音")
        ok = tot > 0 and p / tot > 0.05
        print(f"  → {'有声' if ok else '没测到'} {a.freq:.1f} Hz")
        return 0 if ok else 2

    n = int(a.frames * sr)
    hits = {}
    for i in range(0, len(x) - n + 1, n):
        best, share, info = analyse(x[i:i + n], sr)
        if best is None:
            continue
        if share < 0.05 or info["margin"] < 3.0:
            continue                      # 这一帧不像纯音，跳过（多半是说话或噪声）
        hits[best] = hits.get(best, 0) + 1
        print(f"  [{(i / sr):6.2f}s] {best:6.1f} Hz  占比 {share * 100:5.1f}%  突出度 {info['margin']:5.1f}x")

    if not hits:
        print("  没判出稳定亚音：要么录音里没有发射段，要么这台确实没带亚音。")
        return 2
    best = max(hits, key=hits.get)
    print(f"\n  判定：{best:.1f} Hz（{hits[best]}/{sum(hits.values())} 帧一致）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
