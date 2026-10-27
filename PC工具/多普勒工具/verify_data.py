#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据层自检：TLE 台账 + 频率表 + 资产一致性。

跑法：python 多普勒工具/verify_data.py
退出码：0 全过，1 有 P0（会导致算错或显示错），2 只有告警。

查这些东西：
  1. 两份 TLE（工具侧 tle_cache.json、App 侧 assets/tles.json）是否同源
  2. 每颗星的 TLE 格式、校验位、卫星编号是否自洽
  3. TLE 老化天数（SGP4 用旧 TLE 会静默偏差，不报错）
  4. 轨道周期 → 是否走深空分支（>=225 min）
  5. 能否在 t=0 和 t=+24h 正常解算
  6. Freqs.kt 的 13 颗星与 TLE 资产是否对得上（缺 TLE 就永远解不出位置）
  7. 频率是否在 UV-K5 的接收范围内、上下行是否写反
"""

from __future__ import annotations

import json
import math
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "多普勒工具" / "tle_cache.json"
ASSETS = ROOT / "android-app" / "app" / "src" / "main" / "assets" / "tles.json"
FREQS_KT = ROOT / "android-app" / "app" / "src" / "main" / "java" / "com" / "f4hwn" / "doppler" / "Freqs.kt"

# UV-K5/K1 的接收范围（MHz），超出就不可能收到
RADIO_MIN_MHZ, RADIO_MAX_MHZ = 18.0, 1300.0
AGE_WARN_D, AGE_ALERT_D = 7.0, 30.0

P0: list[str] = []
P1: list[str] = []
P2: list[str] = []


def p0(msg: str) -> None:
    P0.append(msg)


def p1(msg: str) -> None:
    P1.append(msg)


def p2(msg: str) -> None:
    P2.append(msg)


# ── TLE 基础 ──────────────────────────────────────────────────────────
def tle_checksum(line: str) -> int:
    s = 0
    for ch in line[:68]:
        if ch.isdigit():
            s += int(ch)
        elif ch == "-":
            s += 1
    return s % 10


def tle_epoch(line1: str) -> datetime:
    yy = int(line1[18:20])
    year = 2000 + yy if yy < 57 else 1900 + yy
    doy = float(line1[20:32])
    return datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(days=doy - 1.0)


def tle_satnum(line: str) -> str:
    return line[2:7].strip()


def mean_motion(line2: str) -> float:
    return float(line2[52:63])


def rev_per_day_to_period_min(rev_day: float) -> float:
    return 1440.0 / rev_day


def check_tle_pair(norad: str, name: str, l1: str, l2: str) -> None:
    tag = f"{norad} {name}"
    if not l1.startswith("1 ") or not l2.startswith("2 "):
        p0(f"{tag}: TLE 行首不是 '1 '/'2 '")
        return
    if len(l1) < 69 or len(l2) < 69:
        p0(f"{tag}: TLE 行长度不足（{len(l1)}/{len(l2)}，应 ≥69）")
        return
    if l1[68].isdigit() and tle_checksum(l1) != int(l1[68]):
        p0(f"{tag}: line1 校验位错（算出 {tle_checksum(l1)}，文件里 {l1[68]}）")
    if l2[68].isdigit() and tle_checksum(l2) != int(l2[68]):
        p0(f"{tag}: line2 校验位错（算出 {tle_checksum(l2)}，文件里 {l2[68]}）")
    if tle_satnum(l1) != tle_satnum(l2):
        p0(f"{tag}: line1/line2 卫星编号不一致（{tle_satnum(l1)} vs {tle_satnum(l2)}）")
    try:
        if int(tle_satnum(l1)) != int(norad):
            p1(f"{tag}: 字典键 {norad} 与 TLE 里的编号 {tle_satnum(l1)} 不一致")
    except ValueError:
        p1(f"{tag}: 字典键或 TLE 编号不是数字")


# ── 读取 ─────────────────────────────────────────────────────────────
def load(path: Path) -> dict[str, list[str]]:
    if not path.exists():
        p0(f"缺文件：{path.relative_to(ROOT)}")
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        p0(f"{path.name} 不是合法 JSON：{e}")
        return {}
    out: dict[str, list[str]] = {}
    for k, v in data.items():
        if not isinstance(v, list) or len(v) < 3:
            p0(f"{path.name} 里 {k} 结构不对（应为 [名称, line1, line2]）")
            continue
        out[str(k)] = [str(x) for x in v[:3]]
    return out


def load_freqs() -> dict[str, tuple[float, float, float]]:
    """从 Freqs.kt 里抠 norad -> (downMhz, upMhz, tone)。"""
    if not FREQS_KT.exists():
        p0("缺 Freqs.kt")
        return {}
    txt = FREQS_KT.read_text(encoding="utf-8")
    out: dict[str, tuple[float, float, float]] = {}
    for m in re.finditer(r'"(\d+)"\s*to\s*SatFreq\(([^)]*)\)', txt):
        norad = m.group(1)
        nums = [float(x) for x in re.findall(r"-?\d+\.?\d*", m.group(2))[:3]]
        while len(nums) < 3:
            nums.append(0.0)
        out[norad] = (nums[0], nums[1], nums[2])
    if not out:
        p0("Freqs.kt 里一个星都没解析出来（正则失效？）")
    return out


# ── 主流程 ───────────────────────────────────────────────────────────
def main() -> int:
    now = datetime.now(timezone.utc)
    cache = load(CACHE)
    assets = load(ASSETS)

    if cache and assets:
        only_c = set(cache) - set(assets)
        only_a = set(assets) - set(cache)
        if only_c:
            p1(f"只在工具侧 tle_cache.json 里有：{sorted(only_c)}")
        if only_a:
            p1(f"只在 App assets 里有：{sorted(only_a)}")
        for k in sorted(set(cache) & set(assets)):
            if cache[k] != assets[k]:
                p1(f"{k} {cache[k][0]}：两侧 TLE 内容不一致（App 里的可能没同步）")

    freqs = load_freqs()
    for norad in freqs:
        if norad not in assets:
            p2(f"Freqs.kt 有 {norad}，但 assets 里没有它的 TLE —— 列表是拿 TLE 建的，"
               f"这条属于死数据，界面永远看不到它")

    from sgp4.api import Satrec, WGS72, jday  # 延迟导入，缺库时报错更清楚

    rows = []
    deep_count = 0
    for norad, (name, l1, l2) in sorted(assets.items(), key=lambda kv: kv[1][0]):
        check_tle_pair(norad, name, l1, l2)
        try:
            ep = tle_epoch(l1)
            age_d = (now - ep).total_seconds() / 86400.0
            mm = mean_motion(l2)
            period = rev_per_day_to_period_min(mm)
        except Exception as e:  # noqa: BLE001
            p0(f"{norad} {name}: TLE 字段解析失败（{e}）")
            continue

        deep = period >= 225.0
        deep_count += 1 if deep else 0
        if age_d < -0.25:
            p0(f"{norad} {name}: TLE epoch 在未来（{age_d:+.1f} 天），八成是年份或 doy 解析错")
        elif age_d > AGE_ALERT_D:
            p1(f"{norad} {name}: TLE 已 {age_d:.0f} 天没更新（>{AGE_ALERT_D:.0f} 天，位置会明显偏）")
        elif age_d > AGE_WARN_D:
            p2(f"{norad} {name}: TLE 已 {age_d:.0f} 天没更新（>{AGE_WARN_D:.0f} 天）")

        sat = Satrec.twoline2rv(l1, l2, WGS72)
        err0 = err24 = None
        for hours in (0, 24):
            t = now + timedelta(hours=hours)
            jd, fr = jday(t.year, t.month, t.day, t.hour, t.minute,
                          t.second + t.microsecond / 1e6)
            e, r, _v = sat.sgp4(jd, fr)
            if hours == 0:
                err0 = e, r
            else:
                err24 = e
            if e != 0:
                p0(f"{norad} {name}: sgp4 在 t=+{hours}h 报错 {e}")
        if err0 and err0[0] == 0 and err0[1]:
            alt = math.sqrt(sum(c * c for c in err0[1])) - 6378.135
            if alt < 100.0:
                p1(f"{norad} {name}: 当前高度只有 {alt:.0f} km，可能已再入，TLE 是僵尸数据")

        rows.append((norad, name, age_d, period, deep, mm, True))

    # 频率检查
    for norad, (name, *_rest) in [(k, v) for k, v in assets.items()]:
        pass
    for norad, (down, up, tone) in sorted(freqs.items()):
        nm = assets.get(norad, [norad])[0]
        for label, f in (("下行", down), ("上行", up)):
            if f == 0.0:
                continue
            if not (RADIO_MIN_MHZ <= f <= RADIO_MAX_MHZ):
                p1(f"Freqs {norad} {nm}: {label} {f} MHz 超出 UV-K5 接收范围 "
                   f"({RADIO_MIN_MHZ:.0f}-{RADIO_MAX_MHZ:.0f}) —— 收不到，只能看数据")
        if down and up and abs(down - up) < 1.0:
            p1(f"Freqs {norad} {nm}: 下行 {down} 与上行 {up} MHz 太近，怀疑写反或填错")
        if tone < 0 or tone > 254:
            p2(f"Freqs {norad} {nm}: 哑音 {tone} Hz 不像 CTCSS 标准值")

    # 输出
    print(f"TLE 资产：{len(assets)} 颗（其中深空 {deep_count} 颗），"
          f"界面表：{len(freqs)} 颗")
    print(f"{'NORAD':>6}  {'名称':<10} {'老化':>7}  {'周期min':>9}  分支")
    for norad, name, age, period, deep, _mm, _ok in rows:
        print(f"{norad:>6}  {name:<10} {age:>6.1f}d  {period:>9.1f}  {'深空' if deep else '近地'}")

    def dump(title: str, items: list[str]) -> None:
        if not items:
            return
        print(f"\n{title}（{len(items)}）")
        for it in items:
            print(f"  - {it}")

    dump("P0 会导致算错/显示错", P0)
    dump("P1 需要处理", P1)
    dump("P2 提示", P2)

    if P0:
        print("\n结果：FAIL")
        return 1
    if P1:
        print("\n结果：WARN")
        return 2
    print("\n结果：OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
