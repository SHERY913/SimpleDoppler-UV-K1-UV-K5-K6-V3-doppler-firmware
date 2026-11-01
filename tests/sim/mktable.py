#!/usr/bin/env python3
"""生成仿真用的 v5 过境表（不是真机 dump，是自造的合成表）。

版式依据 固件源码/uvk1-v6.3.0/App/app/doppler.h:221-246 与 doppler.c:1246 的校验：
  DPH_BASE_ADDR=0x162000、DPH_SAMPLE_OFF=0x1000 → 头里的 data_off 必须是 0x163000。
真机备份 table_backup.bin 是旧的 0x165000 版式，现固件会判 BAD LAYOUT(5) 拒收，
所以这里自己造一份合规表。

输出：data/table_sim.bin —— 64 KiB，正好覆盖 0x162000..0x172000，
仿真里把它当作外部 SPI flash 的表区镜像。
"""
import os
import struct

BASE = 0x162000          # DPH_BASE_ADDR
SOFF = 0x1000            # DPH_SAMPLE_OFF
TBL = BASE + SOFF        # 0x163000，样本区起点（头里的 data_off）

flash = bytearray(b'\xff' * 0x10000)

HDR_FMT = '<4sBBHII'     # dph_header_t  16 B
ENT_FMT = '<16sIIIHHHHHBB'  # dph_entry_t  40 B
PT_FMT = '<hBHb'         # <int16 多普勒偏移Hz, uint8 仰角, uint16 斜距km, int8 径向速度0.1km/s>


def make_samples(pts, dopp_lo, dopp_hi, el_max, rng_lo, rng_hi, rate_max):
    """一条过境：多普勒按抛物线走 lo→hi→lo，仰角抛物线，距离两头远中间近。"""
    out = bytearray()
    for k in range(pts):
        t = k / (pts - 1)
        mid = 1.0 - abs(2.0 * t - 1.0)        # 0 → 1 → 0
        dopp = int(round(dopp_lo + (dopp_hi - dopp_lo) * mid))
        el = int(round(el_max * mid))
        rng = int(round(rng_hi - (rng_hi - rng_lo) * mid))
        rate = int(round(rate_max * (2.0 * t - 1.0)))
        out += struct.pack(PT_FMT, dopp, el, rng, rate)
    return out


def put_entry(idx, name, freq10, data_off, up10, aos_min, dur_s, pts, step_ms,
              ctcss10, flags, max_el):
    e = struct.pack(ENT_FMT, name.encode('ascii')[:15].ljust(16, b'\0'),
                    freq10, data_off, up10, aos_min, dur_s, pts, step_ms,
                    ctcss10, flags, max_el)
    assert len(e) == 40
    off = 16 + idx * 40
    flash[off:off + 40] = e


# ---- 条目 0：145.800 MHz，带上行 145.990 MHz / 74.4 Hz，600 s、5 s 一点 ----
pts0, step0, dur0 = 120, 5000, 600
s0 = make_samples(pts0, -3200, 3200, 82, 700, 2400, 68)
flash[SOFF:SOFF + len(s0)] = s0
put_entry(0, 'SIM-PASS-A', 14580000, TBL, 14599000, 5, dur0, pts0, step0,
          744, 0x01, 82)

# ---- 条目 1：145.850 MHz，无上行，短表，用来测只收路径 ----
pts1, step1, dur1 = 20, 5000, 100
o1 = SOFF + len(s0)
s1 = make_samples(pts1, -1500, 1500, 30, 900, 1800, 20)
flash[o1:o1 + len(s1)] = s1
put_entry(1, 'SIM-PASS-B', 14585000, BASE + o1, 0, 10, dur1, pts1, step1, 0, 0x00, 30)

# ---- 头 ----
flash[0:16] = struct.pack(HDR_FMT, b'DPT1', 5, 2, 16, TBL, (26 << 9) | (11 << 5) | 1)

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'table_sim.bin')
os.makedirs(os.path.dirname(out), exist_ok=True)
with open(out, 'wb') as f:
    f.write(bytes(flash))
print('wrote %s  (%d bytes, data_off=0x%X)' % (out, len(flash), TBL))
