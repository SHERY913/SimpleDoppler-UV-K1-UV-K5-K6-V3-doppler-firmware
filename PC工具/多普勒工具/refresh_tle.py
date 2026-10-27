#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""刷新 TLE：amsat nasabare 批量 → 多普勒工具/tle_cache.json + android-app 的 assets/tles.json

用法：
    python refresh_tle.py            # 联网拉最新，写两份
    python refresh_tle.py --check    # 只报当前缓存的老化天数，不写文件

amsat 的 nasabare.txt 是三行一组（名称 / line1 / line2），一次 97 颗。
celestrak 长期不通，所以只用这一个源；拿不到就保留旧缓存不动。
"""
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = os.path.join(HERE, "tle_cache.json")
ASSETS = os.path.join(ROOT, "android-app", "app", "src", "main", "assets", "tles.json")

SRC = "https://www.amsat.org/tle/current/nasabare.txt"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


def fetch(url=SRC, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _f(s, default=0.0):
    try:
        return float(str(s).strip())
    except (TypeError, ValueError):
        return default


def parse(text, fetched_at=None):
    """三行一组 → {norad: [name, l1, l2, 抓取时间, 历元, 倾角, RAAN, 偏心率,
    近地点幅角, 平近点角, 平均运动, B*]}

    以前只写 name/l1/l2 三个字段，工具侧想判"这条 TLE 到底多老了"只能看文件
    mtime，assets 里的数据更是连抓取时间都没有（报告 第六部分·回归项 1）。前 3 项位置不变，
    老读者照旧能用；后面按 sgp4 解算需要的量补齐，取不到的写 None。
    """
    now = time.time() if fetched_at is None else fetched_at
    lines = [l.rstrip() for l in text.splitlines() if l.strip()]
    out = {}
    i = 0
    while i + 2 < len(lines) + 1 and i + 2 <= len(lines):
        name, l1, l2 = lines[i], lines[i + 1], lines[i + 2]
        if not (l1.startswith("1 ") and l2.startswith("2 ")):
            i += 1
            continue
        norad = l1[2:7].strip().lstrip("0") or "0"
        try:
            epoch = epoch_dt(l1).timestamp()
        except Exception:
            epoch = None
        out[norad] = [
            name.strip(), l1, l2, now, epoch,
            _f(l2[8:16]),    # 倾角 °
            _f(l2[17:25]),   # RAAN °
            _f("0." + l2[26:33].strip()),  # 偏心率（隐含前导 0.）
            _f(l2[34:42]),   # 近地点幅角 °
            _f(l2[43:51]),   # 平近点角 °
            _f(l2[52:63]),   # 平均运动 rev/day
            l1[53:61].strip(),  # B*
        ]
        i += 3
    return out


def epoch_dt(line1):
    yy = int(line1[18:20])
    year = 2000 + yy if yy < 57 else 1900 + yy
    doy = float(line1[20:32])
    return datetime(year, 1, 1, tzinfo=timezone.utc) + __import__("datetime").timedelta(days=doy - 1)


def period_min(line2):
    return 1440.0 / float(line2[52:63])


def report(tles, tag):
    if not tles:
        print(f"{tag}: 空")
        return
    now = datetime.now(timezone.utc)
    ages = [(now - epoch_dt(v[1])).total_seconds() / 86400.0 for v in tles.values()]
    deep = [(k, v[0], period_min(v[2])) for k, v in tles.items() if period_min(v[2]) >= 225]
    print(f"{tag}: {len(tles)} 颗，epoch 距今 {min(ages):.2f}~{max(ages):.2f} 天（中位 {sorted(ages)[len(ages)//2]:.2f}）")
    print(f"  深空(周期>=225min): {len(deep)} 颗 " + ", ".join(f"{n}({p:.0f}min)" for _, n, p in deep))


def validate(tles):
    """解算一遍，剔掉已经再入/数据坏掉的条目。

    sgp4 的 error 非 0（常见 6 = 已经掉到地面以下）说明这颗星的 TLE 是僵尸数据，
    留在 assets 里只会在界面上显示一堆假数字。
    """
    from sgp4.api import Satrec, WGS72, jday

    now = datetime.now(timezone.utc)
    jd, fr = jday(now.year, now.month, now.day, now.hour, now.minute,
                  now.second + now.microsecond / 1e6)
    good, bad = {}, []
    for k, v in tles.items():
        try:
            sat = Satrec.twoline2rv(v[1], v[2], WGS72)
            e, r, _v = sat.sgp4(jd, fr)
        except Exception as ex:  # noqa: BLE001
            bad.append((k, v[0], f"解析异常 {ex}"))
            continue
        if e != 0:
            bad.append((k, v[0], f"解算错 {e}"))
            continue
        good[k] = v
    return good, bad


def main():
    check = "--check" in sys.argv

    if os.path.exists(CACHE):
        try:
            old = json.load(open(CACHE, encoding="utf-8"))
            report(old, "现有缓存")
        except Exception as e:
            print("现有缓存读不了:", e)

    if check:
        return 0

    try:
        text = fetch()
    except Exception as e:
        print("下载失败，保留旧缓存:", e)
        return 1

    tles = parse(text)
    if len(tles) < 50:
        print(f"只解析到 {len(tles)} 颗，源可能变了，不覆盖")
        return 1

    tles, dropped = validate(tles)
    if dropped:
        print(f"剔除 {len(dropped)} 颗（解算失败/已再入）:")
        for k, n, why in dropped:
            print(f"  {k} {n}: {why}")
    if len(tles) < 50:
        print(f"剔除后只剩 {len(tles)} 颗，源不对劲，不覆盖")
        return 1

    report(tles, "新数据")

    json.dump(tles, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("写入", CACHE)

    os.makedirs(os.path.dirname(ASSETS), exist_ok=True)
    # assets 只写前 3 项：App 侧按 [name, l1, l2] 解析，多塞指纹一样会炸。
    slim = {k: v[:3] for k, v in tles.items()}
    json.dump(slim, open(ASSETS, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("写入", ASSETS)

    # 顺手报一下这次刷新值多少：最常见的星在不在
    for n in ("25544", "43700", "7530"):
        if n in tles:
            print(f"  {n} {tles[n][0]} epoch {epoch_dt(tles[n][1]):%Y-%m-%d %H:%M}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
