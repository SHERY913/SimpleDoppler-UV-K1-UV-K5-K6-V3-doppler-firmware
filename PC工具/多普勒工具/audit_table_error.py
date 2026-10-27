# -*- coding: utf-8 -*-
"""过境表精度审计：App 写进电台的表 + 固件插值，到底偏多少 Hz。

按两边真实代码走，时间基准统一用绝对 epoch 秒：
  App  : AOS 存的是"当天秒数 / 2"（uint16，2 秒量化）→ 整条曲线相对真值平移
         shift ∈ (-2, 0] 秒。
         从 AOS 起每 stepS 采一点，多普勒 = -rate/c*f，四舍五入成 int16 Hz。
  固件 : idx = (t - aos)/step，线性插值，再 DPH_Hz2Ten 四舍五入到 10 Hz 步进。
对照：同一套 core.look 直接算的真值。
"""
import json
import math
import sys
from datetime import datetime, timezone

sys.path.insert(0, ".")
import doppler_core as core
from sgp4.api import Satrec, WGS72


class G:
    def __init__(s, lat, lon, alt):
        a, f = 6378.137, 1 / 298.257223563
        e2 = f * (2 - f)
        la, lo = math.radians(lat), math.radians(lon)
        N = a / math.sqrt(1 - e2 * math.sin(la) ** 2)
        s.x = (N + alt / 1000) * math.cos(la) * math.cos(lo)
        s.y = (N + alt / 1000) * math.cos(la) * math.sin(lo)
        s.z = (N * (1 - e2) + alt / 1000) * math.sin(la)
        s.lat, s.lon = la, lo

    def up(s):
        return (math.cos(s.lat) * math.cos(s.lon),
                math.cos(s.lat) * math.sin(s.lon),
                math.sin(s.lat))


def study(norad="25544", freq_mhz=145.8, step_s=1.0, lat=23.1869, lon=113.4192, alt=800):
    cache = json.load(open("tle_cache.json", encoding="utf-8"))
    _, l1, l2 = cache[norad][:3]
    sat = Satrec.twoline2rv(l1, l2, WGS72)
    g = G(lat, lon, alt)
    freq = freq_mhz * 1e6

    p = core.find_passes(sat, g, freq, days=1.0, min_el=0.0,
                         start=datetime.now(timezone.utc))[0]
    aos, los = p.aos.timestamp(), p.los.timestamp()
    dur = int(los - aos)

    def dop_at(t):
        _, _, _, rate = core.look(sat, g, datetime.fromtimestamp(t, timezone.utc))
        return core.doppler_hz(freq, rate)

    # App 的 AOS 量化：v5 表只存 secOfDay / 2。
    #   老版本：向下取整、采样仍从真实 AOS 起 → 整条曲线提前最多 2 s
    #   现版本：四舍五入、采样也从该秒起 → 误差 ±1 s，不再单边偏
    aos_sec = aos % 86400
    q_floor = int(aos_sec) // 2
    q_round = (int(aos_sec) + 1) // 2
    base_floor = aos - (aos_sec - q_floor * 2)     # 表头写的时刻（旧：向下取整）
    base_round = aos - (aos_sec - q_round * 2)     # 表头写的时刻（新：四舍五入）

    cases = (
        # (名字, 采样起点, 固件读表起点)
        # 旧：采样从真实 AOS 起，表头却记成向下取整 → 整条曲线被平移
        ("向下取整(旧)", aos, base_floor),
        # 新：采样起点跟着表头一起挪，两边同一个原点
        ("四舍五入(新)", base_round, base_round),
    )
    for name, samp_base, read_base in cases:
        n = int(dur / step_s) + 1
        tab = [round(dop_at(samp_base + i * step_s)) for i in range(n)]

        def fw_read(t, tab=tab, read_base=read_base, n=n):
            e = t - read_base
            if e < 0 or e > (n - 1) * step_s:
                return None
            i = min(int(e // step_s), n - 2)
            frac = (e - i * step_s) / step_s
            off = tab[i] + (tab[i + 1] - tab[i]) * frac
            hz = round(off)
            ten = (hz + 5) // 10 if hz >= 0 else -((5 - hz) // 10)
            return ten * 10

        errs = []
        for k in range(n):
            t = aos + k * step_s
            got = fw_read(t)
            if got is not None:
                errs.append(got - dop_at(t))
        a = sorted(abs(x) for x in errs)
        print("  %s：表头差 %+.2f s，误差中位 %+.1f Hz |max| %.1f  p95 %.1f"
              % (name, read_base - samp_base, errs[len(errs) // 2], a[-1], a[int(len(a) * .95)]))

    print("NORAD %s @ %.1f MHz  台站 %.4f,%.4f %dm" % (norad, freq_mhz, lat, lon, alt))
    print("  过境 %s → %s，%d s，采样 %d 点（%.0f s 间隔）" %
          (p.aos.strftime("%H:%M:%S"), p.los.strftime("%H:%M:%S"), dur, n, step_s))

    # 时钟偏差敏感性（用"新"的取整方式，即当前 App 的行为）
    print("  电台时钟偏差敏感性（表本身误差 <6 Hz，时钟才是主项）：")
    for dsec in (1, 2, 5, 15):
        e2 = [abs(got - dop_at(read_base + k * step_s + dsec))
              for k, got in ((k, fw_read(read_base + k * step_s)) for k in range(0, n, max(1, n // 300)))
              if got is not None]
        print("  若电台时钟慢 %2d s：|max| %.0f Hz" % (dsec, max(e2)))


if __name__ == "__main__":
    n = sys.argv[1] if len(sys.argv) > 1 else "25544"
    f = float(sys.argv[2]) if len(sys.argv) > 2 else 145.8
    study(n, f)
