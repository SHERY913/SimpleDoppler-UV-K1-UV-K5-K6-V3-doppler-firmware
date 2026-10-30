#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""显示层自检：拿运行中的 App 自己算出来的数，和 Python 独立算的比。

App 里埋了个钩子（`MainScreen.kt` 的 DOPPLER_DUMP 日志）：只要
    adb shell settings put global doppler_dump 1
它就每 500 ms 打一行完整精度的
    norad / t / el / az / range / rate / doppler / down / deep / tick
本脚本读这些行，用 python-sgp4 + 同一套几何（WGS-84、GMST 多项式、距离差分）
重算一遍，逐 tick 比对。

跑法：
    python 多普勒工具/verify_display.py                 # 用默认台站 43.83N/87.62E/800m
    python 多普勒工具/verify_display.py --lat 31.2 --lon 121.5 --alt 10
    python 多普勒工具/verify_display.py --keep          # 跑完不关掉 dump 开关

退出码：0 全过，1 有超差。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "多普勒工具" / "tle_cache.json"
ADB = "C:/Program Files/Netease/MuMu/nx_main/adb.exe"
SERIAL = "127.0.0.1:16384"
TAG = "DOPPLER_DUMP"

# App 里 MainScreen.kt 写死的台站（模拟器没有 GPS）
DEF_LAT, DEF_LON, DEF_ALT = 43.83, 87.62, 800.0

EARTH_A, EARTH_F = 6378.137, 1.0 / 298.257223563
EARTH_E2 = EARTH_F * (2.0 - EARTH_F)
C_KMS = 299792.458

# 超差阈值：两套实现本该只差浮点末位，留一点余量
TOL_EL, TOL_AZ, TOL_RANGE, TOL_RATE = 0.02, 0.05, 0.02, 0.002


def adb(*args: str, check: bool = False) -> str:
    env = dict(os.environ)
    env.setdefault("TMP", str(ROOT / ".gtmp"))
    env.setdefault("TEMP", env["TMP"])
    p = subprocess.run([ADB, "-s", SERIAL, *args], capture_output=True,
                       text=True, env=env, encoding="utf-8", errors="replace")
    if check and p.returncode != 0:
        raise SystemExit(f"adb {' '.join(args)} 失败：{p.stderr.strip()}")
    return p.stdout


def ecef_station(lat_deg: float, lon_deg: float, alt_m: float):
    lat, lon = math.radians(lat_deg), math.radians(lon_deg)
    sl, cl = math.sin(lat), math.cos(lat)
    n = EARTH_A / math.sqrt(1.0 - EARTH_E2 * sl * sl)
    h = alt_m / 1000.0
    return ((n + h) * cl * math.cos(lon), (n + h) * cl * math.sin(lon), (n * (1.0 - EARTH_E2) + h) * sl)


def gmst(jd: float) -> float:
    t = (jd - 2451545.0) / 36525.0
    g = (280.46061837 + 360.98564736629 * (jd - 2451545.0)
         + 0.000387933 * t * t - t * t * t / 38710000.0)
    g %= 360.0
    return math.radians(g)


def truth(sat, station, t_unix: float):
    """返回 (el, az, range, rate, doppler)，和 App 里的 Geo 完全同一套算法。"""
    from sgp4.api import jday
    import datetime as _dt

    def range_at(tt: float) -> float:
        d = _dt.datetime.fromtimestamp(tt, _dt.timezone.utc)
        jd_, fr_ = jday(d.year, d.month, d.day, d.hour, d.minute,
                        d.second + d.microsecond / 1e6)
        e, r, _v = sat.sgp4(jd_, fr_)
        if e != 0:
            return float("nan")
        g = gmst(tt / 86400.0 + 2440587.5)
        c, s = math.cos(g), math.sin(g)
        xe = (c * r[0] + s * r[1], -s * r[0] + c * r[1], r[2])
        return math.dist(xe, station)

    rng = range_at(t_unix)
    d = _dt.datetime.fromtimestamp(t_unix, _dt.timezone.utc)
    jd_, fr_ = jday(d.year, d.month, d.day, d.hour, d.minute, d.second + d.microsecond / 1e6)
    e, r, _v = sat.sgp4(jd_, fr_)
    if e != 0:
        return None
    g = gmst(t_unix / 86400.0 + 2440587.5)
    c, s = math.cos(g), math.sin(g)
    dx = c * r[0] + s * r[1] - station[0]
    dy = -s * r[0] + c * r[1] - station[1]
    dz = r[2] - station[2]
    rng = math.sqrt(dx * dx + dy * dy + dz * dz)
    # 站心坐标在 ECEF 里直接算东/北/天，和 Geo.look 一样
    lat, lon = math.radians(DEF_LAT), math.radians(DEF_LON)
    sl, cl, so, co = math.sin(lat), math.cos(lat), math.sin(lon), math.cos(lon)
    e_ = -so * dx + co * dy
    n_ = -sl * co * dx - sl * so * dy + cl * dz
    u_ = cl * co * dx + cl * so * dy + sl * dz
    az = math.degrees(math.atan2(e_, n_)) % 360.0
    el = math.degrees(math.atan2(u_, math.hypot(e_, n_)))
    rate = (range_at(t_unix + 0.5) - range_at(t_unix - 0.5)) / 1.0
    return el, az, rng, rate


def parse_line(line: str) -> dict | None:
    out: dict = {}
    for tok in line.split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            out[k] = v
    need = {"norad", "t", "el", "az", "range", "rate", "doppler"}
    if not need.issubset(out):
        return None
    try:
        for k in ("t", "el", "az", "range", "rate", "doppler", "down"):
            if k in out:
                out[k] = float(out[k])
    except ValueError:
        return None
    return out


def main() -> int:
    global SERIAL, DEF_LAT, DEF_LON, DEF_ALT
    ap = argparse.ArgumentParser()
    ap.add_argument("--lat", type=float, default=DEF_LAT)
    ap.add_argument("--lon", type=float, default=DEF_LON)
    ap.add_argument("--alt", type=float, default=DEF_ALT)
    ap.add_argument("--ticks", type=int, default=8, help="取多少行做比对")
    ap.add_argument("--keep", action="store_true", help="跑完不关 dump 开关")
    ap.add_argument("--serial", default=SERIAL)
    args = ap.parse_args()

    SERIAL = args.serial
    DEF_LAT, DEF_LON, DEF_ALT = args.lat, args.lon, args.alt

    devs = adb("devices")
    if SERIAL not in devs:
        print(f"没连上 {SERIAL}，先启模拟器：\n"
              f'  "C:/Program Files/Netease/MuMu/nx_main/MuMuManager.exe" control -v 0 launch')
        return 1

    tles = json.loads(CACHE.read_text(encoding="utf-8"))

    # App 的台站原点现在从 prefs 读（以前是 MainScreen.kt 里写死的一行）。
    # 自检用同一个后门 doppler_station 把原点喂进去，两边才算的是同一个台站；
    # 而 station 是在组合时读一次的，所以必须重开 App 才生效。
    adb("shell", "settings", "put", "global", "doppler_station",
        f"{args.lat},{args.lon},{args.alt}")
    adb("shell", "am", "force-stop", "com.f4hwn.doppler")
    adb("shell", "monkey", "-p", "com.f4hwn.doppler",
        "-c", "android.intent.category.LAUNCHER", "1")
    time.sleep(4)
    adb("shell", "settings", "put", "global", "doppler_dump", "1")
    adb("logcat", "-c")
    try:
        # 让 App 至少跑几个 tick
        deadline = time.time() + 12
        lines: list[str] = []
        while time.time() < deadline and len(lines) < args.ticks:
            time.sleep(1.5)
            out = adb("logcat", "-d", "-s", f"{TAG}:I")
            lines = [ln for ln in out.splitlines() if TAG in ln]
        if not lines:
            print("没收到 DOPPLER_DUMP 日志：确认 App 在前台并且没被 force-stop")
            return 1

        from sgp4.api import Satrec, WGS72

        station = ecef_station(args.lat, args.lon, args.alt)
        parsed = [p for p in (parse_line(ln) for ln in lines[-args.ticks:]) if p]
        if not parsed:
            print("日志格式认不出来，可能 App 版本没有这个钩子")
            return 1

        sats: dict[str, object] = {}
        worst = {"el": 0.0, "az": 0.0, "range": 0.0, "rate": 0.0, "doppler": 0.0}
        bad = 0
        print(f"{'norad':>6} {'t':>12}  {'Δ仰角':>8} {'Δ方位':>8} {'Δ距离km':>9} {'Δ速度':>9} {'Δ多普勒':>9}")
        for p in parsed:
            norad = str(p["norad"])
            if norad not in sats:
                if norad not in tles:
                    print(f"{norad}: TLE 里没有这颗星，跳过")
                    continue
                name, l1, l2 = tles[norad][:3]   # tles.json 现为 12 字段，取前 3 位
                sats[norad] = Satrec.twoline2rv(l1, l2, WGS72)
            tr = truth(sats[norad], station, p["t"])
            if tr is None:
                print(f"{norad}: 参考实现在这个时刻也解不出来（TLE 失效）")
                continue
            el, az, rng, rate = tr
            d_el = abs(el - p["el"])
            d_az = min(abs(az - p["az"]), 360 - abs(az - p["az"]))
            d_rng = abs(rng - p["range"])
            d_rate = abs(rate - p["rate"])
            dop_true = -p.get("down", 0.0) * 1e6 * rate / C_KMS if p.get("down") else 0.0
            d_dop = abs(dop_true - p["doppler"]) if p.get("down") else 0.0
            over = (d_el > TOL_EL or d_az > TOL_AZ or d_rng > TOL_RANGE
                    or d_rate > TOL_RATE or d_dop > 5.0)
            bad += 1 if over else 0
            for k, v in (("el", d_el), ("az", d_az), ("range", d_rng),
                         ("rate", d_rate), ("doppler", d_dop)):
                worst[k] = max(worst[k], v)
            print(f"{norad:>6} {p['t']:>12.0f}  {d_el:>8.5f} {d_az:>8.5f} "
                  f"{d_rng:>9.5f} {d_rate:>9.5f} {d_dop:>9.2f}{'   ←超差' if over else ''}")

        print(f"\n最大偏差：仰角 {worst['el']:.5f}°  方位 {worst['az']:.5f}°  "
              f"距离 {worst['range']:.5f} km  速度 {worst['rate']:.4f} km/s  "
              f"多普勒 {worst['doppler']:.2f} Hz")
        print("结果：" + ("FAIL" if bad else "OK"))
        return 1 if bad else 0
    finally:
        if not args.keep:
            adb("shell", "settings", "delete", "global", "doppler_dump")
            adb("shell", "settings", "delete", "global", "doppler_station")


if __name__ == "__main__":
    sys.exit(main())
