#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成深空 SDP4 真值，给 Kotlin 实现做对照。

真值来源：python-sgp4 2.27（C 加速版，Vallado 官方实现）。
输出：多普勒工具/_sdp4_truth.json

    python make_sdp4_truth.py
"""
import json
import os

from sgp4.api import Satrec, WGS72

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "_sdp4_truth.json")

# 覆盖：同步轨道（QO-100）、12h 共振（Molniya 类）、普通高轨
CASES = {
    # QO-100 / Esail-2，24h 同步
    "43700": None,
}


def load_tles():
    cache = json.load(open(os.path.join(HERE, "tle_cache.json"), encoding="utf-8"))
    return cache


def gen(norad, name, l1, l2, times):
    sat = Satrec.twoline2rv(l1, l2, WGS72)
    rows = []
    for t in times:
        e, r, v = sat.sgp4(sat.jdsatepoch, sat.jdsatepochF + t / 1440.0)
        rows.append([t, e, r[0], r[1], r[2], v[0], v[1], v[2]])
    return {
        "norad": norad,
        "name": name,
        "line1": l1,
        "line2": l2,
        "method": sat.method,
        "period_min": 1440.0 / float(l2[52:63]),
        "rows": rows,
    }


def main():
    cache = load_tles()
    times = list(range(-720, 2881, 60)) + [4320, 5760, 7200, 10080]
    out = {"generated_by": "python-sgp4 2.27", "times_min": times, "cases": []}

    for norad, _ in CASES.items():
        if norad not in cache:
            print("缺 TLE:", norad)
            continue
        name, l1, l2 = cache[norad]
        c = gen(norad, name, l1, l2, times)
        out["cases"].append(c)
        print(f"{norad} {name}: method={c['method']} 周期={c['period_min']:.1f} min  {len(c['rows'])} 点")

    # 再补几颗近地星做回归，确认没把近地改坏
    for norad in ("25544", "7530", "43017"):
        if norad in cache:
            name, l1, l2 = cache[norad]
            c = gen(norad, name, l1, l2, times)
            out["cases"].append(c)
            print(f"{norad} {name}: method={c['method']} 周期={c['period_min']:.1f} min")

    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("写入", OUT)


if __name__ == "__main__":
    main()
