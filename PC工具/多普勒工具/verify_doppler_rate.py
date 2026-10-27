#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多普勒变化率自检：算出来的 df/dt 符合不符合现实。

为什么要单独一条：多普勒是"每秒几十 Hz 地爬"的量。公式写错一位小数、
时间轴按分钟算、或者把 TEME 当 ECEF 直接和台站相减（少了 GMST 旋转），
曲线依然平滑好看，只有把"峰值变化率""AOS 处径向速度""TCA 变号"钉成区间才露馅。

真值锚点（Look4Sat v4.4.9 + sgp4 2.27 实测，见 文档/外部参考-Look4Sat.md）：
  ISS @ 145.8 MHz：摆幅约 ±3.37 kHz，峰值 |df/dt| ≈ 3.56 kHz/min ≈ 59 Hz/s
  AOS 附近 |rdot| 已接近满值 ~6.9 km/s，不是从 0 涨起来
  TCA 前后 rdot 变号

复用 doppler_core.look()（含 GMST、TEME→ECEF 和 OMEGA_E 修正），所以这条
检查验的是"PC 工具正在用的那套公式"，不是另写的一份。

用法：
  python verify_doppler_rate.py                # ISS @ 145.8 MHz
  python verify_doppler_rate.py 25544 145.8
  python verify_doppler_rate.py --lat=23.1869 --lon=113.4192 --alt=800
"""
import datetime as dt
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import doppler_core as core  # noqa: E402
from sgp4.api import Satrec, WGS72  # noqa: E402

BAND_PEAK_KHZ_MIN = (3.0, 4.2)      # kHz/min
BAND_AOS_RDOT_KMS = (6.3, 7.2)      # km/s，AOS 就该接近满值
TCA_RDOT_MAX_KMS = 0.3              # TCA 附近径向速度必须接近 0

CACHE = os.path.join(HERE, "tle_cache.json")


def load_tle(norad):
    """从 tle_cache.json 取 [name, line1, line2, ...]。

    用缓存不联网：这条检查要能在没网的时候重复跑，结论才能当"证据"用。
    缓存里每条是 [name, l1, l2, epoch, fetchedAt, …]，只取前三项。
    """
    with open(CACHE, encoding="utf-8") as f:
        cache = json.load(f)
    key = str(norad)
    if key not in cache:
        near = [k for k in cache if key in k or k in key]
        raise SystemExit(f"tle_cache.json 里没有 {norad}"
                         + (f"（相近的：{near}）" if near else "（先跑 refresh_tle.py）"))
    row = cache[key]
    return row[0], row[1], row[2]


class Gnd:
    """doppler_core 需要的最小台站接口：x/y/z + up()。"""

    def __init__(self, lat_deg, lon_deg, alt_m):
        a = 6378.137
        f = 1 / 298.257223563
        e2 = f * (2 - f)
        self.lat = math.radians(lat_deg)
        self.lon = math.radians(lon_deg)
        N = a / math.sqrt(1 - e2 * math.sin(self.lat) ** 2)
        h = alt_m / 1000.0
        self.x = (N + h) * math.cos(self.lat) * math.cos(self.lon)
        self.y = (N + h) * math.cos(self.lat) * math.sin(self.lon)
        self.z = (N * (1 - e2) + h) * math.sin(self.lat)

    def up(self):
        return (math.cos(self.lat) * math.cos(self.lon),
                math.cos(self.lat) * math.sin(self.lon),
                math.sin(self.lat))


def station_from_argv():
    lat, lon, alt = 23.1869, 113.4192, 800.0
    for kv in sys.argv[1:]:
        if kv.startswith("--lat="):
            lat = float(kv.split("=", 1)[1])
        elif kv.startswith("--lon="):
            lon = float(kv.split("=", 1)[1])
        elif kv.startswith("--alt="):
            alt = float(kv.split("=", 1)[1])
    return lat, lon, alt


def main():
    pos = [a for a in sys.argv[1:] if not a.startswith("--")]
    norad = pos[0] if pos else "25544"
    freq_mhz = float(pos[1]) if len(pos) > 1 else 145.8
    freq_hz = freq_mhz * 1e6
    lat, lon, alt = station_from_argv()

    tle = load_tle(norad)
    sat = Satrec.twoline2rv(tle[1], tle[2], WGS72)
    gnd = Gnd(lat, lon, alt)

    start = dt.datetime.now(dt.timezone.utc)
    passes = core.find_passes(sat, gnd, freq_hz, days=2, min_el=5.0,
                              coarse=20.0, step=2.0, start=start)
    if not passes:
        raise SystemExit("48 小时内没有 min_el>=5° 的过境（换颗星）")

    p = max(passes, key=lambda x: x.max_el)
    aos, los = p.aos, p.los
    print(f"{norad} @ {freq_mhz:g} MHz  台站 {lat:.4f},{lon:.4f} {alt:.0f}m")
    print(f"  过境 AOS {aos:%m-%d %H:%M:%S}Z  LOS {los:%m-%d %H:%M:%S}Z  "
          f"最高仰角 {p.max_el:.1f}°")

    samples = []
    t = aos
    while t <= los:
        el, _az, dist, rate = core.look(sat, gnd, t)
        samples.append((t, el, dist, rate, core.doppler_hz(freq_hz, rate)))
        t += dt.timedelta(seconds=1)
    if len(samples) < 10:
        raise SystemExit("过境采样点太少")

    dv = [s[4] for s in samples]
    swing = (max(dv) - min(dv)) / 2.0

    peak, peak_el = 0.0, 0.0
    for i in range(1, len(samples)):
        dts = (samples[i][0] - samples[i - 1][0]).total_seconds()
        rate = abs(samples[i][4] - samples[i - 1][4]) / max(dts, 1e-6)
        if rate > peak:
            peak, peak_el = rate, samples[i][1]

    aos_row = samples[0]
    tca_row = max(samples, key=lambda s: s[1])

    print(f"  摆幅 ±{swing:.0f} Hz（全摆 {2*swing:.0f} Hz）")
    print(f"  峰值 |df/dt| {peak:.1f} Hz/s = {peak*60/1000:.2f} kHz/min"
          f"（仰角 {peak_el:.1f}°）")
    print(f"  AOS 仰角 {aos_row[1]:.2f}° 斜距 {aos_row[2]:.0f} km  "
          f"rdot {aos_row[3]*1000:+.0f} m/s")
    print(f"  TCA 仰角 {tca_row[1]:.1f}°  rdot {tca_row[3]*1000:+.0f} m/s")

    bad = []
    khz_min = peak * 60 / 1000
    if not (BAND_PEAK_KHZ_MIN[0] <= khz_min <= BAND_PEAK_KHZ_MIN[1]):
        bad.append(f"峰值 {khz_min:.2f} kHz/min 不在 {BAND_PEAK_KHZ_MIN}")
    if abs(aos_row[3]) < BAND_AOS_RDOT_KMS[0]:
        bad.append(f"AOS 处 |rdot| {abs(aos_row[3]):.2f} km/s < "
                   f"{BAND_AOS_RDOT_KMS[0]}（应接近满值，AOS 不是从 0 涨起来）")
    if abs(tca_row[3]) > TCA_RDOT_MAX_KMS:
        bad.append(f"TCA 处 rdot {tca_row[3]:+.2f} km/s 没接近 0（应在此变号）")

    print()
    if bad:
        for b in bad:
            print("FAIL", b)
        return 1
    print("OK 多普勒变化率符合现实（峰值 / AOS 满速 / TCA 变号 三条都对）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
