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

HERE = Path(__file__).resolve().parent          # PC工具/多普勒工具
ROOT = HERE.parent.parent                       # 多普勒/
# App 已搬到 <workspace>/doppler-ng/（包名 com.f4hwn.doppler2），旧路径 多普勒/安卓App/
# 从来没进过库。资产也换了形态：TLE 从 tles.json 变成三行文本 tles.txt，
# 频点从手写的 Freqs.kt 变成 build_catalog.py 生成的 freqs.json。
WS = ROOT.parent
APP = WS / "doppler-ng"
CACHE = HERE / "tle_cache.json"
ASSETS = APP / "app" / "src" / "main" / "assets" / "tles.txt"
FREQS_JSON = APP / "app" / "src" / "main" / "assets" / "freqs.json"

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
    """TLE 里的卫星编号（第 3..7 列）。

    **必须归一成 int 字符串**：TLE 是定宽格式，`07530` 这种带前导零；
    而 freqs.json / tle_cache.json 的键是 `7530`。不归一的话两套键永远对不上，
    对比时会报出上千条"只在某一侧有"的假红（2026-11-08 踩过）。
    """
    raw = line[2:7].strip()
    return str(int(raw)) if raw.isdigit() else raw


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
        # `_` 开头的顶层键是元数据（如 refresh_tle.py 写的 _sources），不是卫星，
        # 不参与"结构对不对 / 两侧是否一致"的比对，否则每次刷新都多一条假告警。
        if str(k).startswith("_"):
            continue
        if not isinstance(v, list) or len(v) < 3:
            p0(f"{path.name} 里 {k} 结构不对（应为 [名称, line1, line2]）")
            continue
        out[str(k)] = [str(x) for x in v[:3]]
    return out


def load_tles_txt(path: Path) -> dict[str, list[str]]:
    """freqs 那套之外 App 还有一份 tles.txt：每颗星三行（名称 / line1 / line2）。

    比 tles.json 脆一点（对齐靠行数），所以这里逐块校验 line1 以 '1 ' 开头、
    line2 以 '2 ' 开头，错了就 P0 而不是悄悄少一颗。
    """
    if not path.exists():
        p0(f"缺文件：{path.relative_to(ROOT)}")
        return {}
    raw = [ln.rstrip("\r\n") for ln in path.read_text(encoding="utf-8", errors="replace").splitlines()]
    lines = [ln for ln in raw if ln.strip()]
    out: dict[str, list[str]] = {}
    i = 0
    while i + 2 < len(lines) + 0 and i + 2 <= len(lines) - 1:
        name, l1, l2 = lines[i], lines[i + 1], lines[i + 2]
        if not (l1.startswith("1 ") and l2.startswith("2 ")):
            p0(f"tles.txt 第 {i + 2} 行起不是 TLE 块（'{name[:24]}' 后面跟的不是 '1 '/'2 '）")
            i += 1
            continue
        out[tle_satnum(l1)] = [name, l1, l2]
        i += 3
    if not out:
        p0("tles.txt 里一个星都没解析出来")
    return out


def load_freqs() -> dict[str, tuple[float, float, float]]:
    """freqs.json 的 sats 表 -> norad -> (downMhz, upMhz, tone)。"""
    if not FREQS_JSON.exists():
        p0("缺 freqs.json")
        return {}
    try:
        data = json.loads(FREQS_JSON.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        p0(f"freqs.json 不是合法 JSON：{e}")
        return {}
    sats = data.get("sats")
    if not isinstance(sats, dict) or not sats:
        p0("freqs.json 里没有 sats 表（格式变了？）")
        return {}
    out: dict[str, tuple[float, float, float]] = {}
    for k, v in sats.items():
        if not str(k).isdigit() or not isinstance(v, dict):
            continue
        try:
            out[str(k)] = (float(v.get("down") or 0.0),
                           float(v.get("up") or 0.0),
                           float(v.get("tone") or 0.0))
        except (TypeError, ValueError):
            p0(f"freqs.json {k}：down/up/tone 不是数字")
    if not out:
        p0("freqs.json 里一个星都没解析出来")
    return out


# ── 主流程 ───────────────────────────────────────────────────────────
def main() -> int:
    now = datetime.now(timezone.utc)
    cache = load(CACHE)
    assets = load_tles_txt(ASSETS)

    if cache and assets:
        # 两份东西不是同一个集合，不能直接做差集：
        #   tle_cache.json = 工具侧全量目录（约 1.6 万颗，每次 refresh_tle 拉新的）
        #   assets/tles.txt = App 打包进去的常用子集（约 1 千颗，build_catalog 生成）
        # 所以"只在缓存里有"是常态（不是错误），"只在 assets 里有"才是值得看一眼的
        # （说明这颗星在 catalog 里被删了，但 App 还带着）。内容不一致同理：
        # assets 是打包时的快照，缓存是新拉的，两边 epoch 不同很正常。
        only_c = set(cache) - set(assets)
        only_a = set(assets) - set(cache)
        both = set(cache) & set(assets)
        differ = [k for k in both if cache[k] != assets[k]]
        if only_a:
            p2(f"App assets 里有、工具缓存里没有（catalog 可能已删）：{sorted(only_a)}")
        if both:
            p2(
                f"App assets {len(assets)} 颗 ⊂ 工具缓存 {len(cache)} 颗"
                f"（{len(only_c)} 颗只在缓存里 = 全量目录，正常）；"
                f"内容不一致 {len(differ)}/{len(both)} 颗（assets 是打包快照，正常）"
            )
        if not both:
            p1("App assets 与工具缓存没有任何交集 —— 两边的编号体系可能又对不上了")

    freqs = load_freqs()
    for norad in freqs:
        if norad not in assets:
            p2(f"freqs.json 有 {norad}，但 tles.txt 里没有它的 TLE —— 列表是拿 TLE 建的，"
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
                # sgp4 错误码 6 = 卫星已经再入。catalog 里留着这种条目不会算错，
                # 只会白跑一条永远不出现的过境 —— 降到 P1 并提示剔除。
                if e == 6:
                    p1(f"{norad} {name}: sgp4 报 6（已再入），catalog 里是僵尸条目，建议剔除")
                else:
                    p0(f"{norad} {name}: sgp4 在 t=+{hours}h 报错 {e}")
        if err0 and err0[0] == 0 and err0[1]:
            alt = math.sqrt(sum(c * c for c in err0[1])) - 6378.135
            if alt < 100.0:
                p1(f"{norad} {name}: 当前高度只有 {alt:.0f} km，可能已再入，TLE 是僵尸数据")

        rows.append((norad, name, age_d, period, deep, mm, True))

    # 频率检查
    for norad, (name, *_rest) in [(k, v) for k, v in assets.items()]:
        pass
    out_of_range: list[tuple[str, str, str, float]] = []
    near_same: list[tuple[str, str, float, float]] = []
    for norad, (down, up, tone) in sorted(freqs.items()):
        nm = assets.get(norad, [norad])[0]
        for label, f in (("下行", down), ("上行", up)):
            if f == 0.0:
                continue
            if not (RADIO_MIN_MHZ <= f <= RADIO_MAX_MHZ):
                # 这类是"目录里带着、但手台收不到"的数据星（S 波段/毫米波）。
                # 455 颗逐条打会把 P1 淹掉，聚合成一条，只列前 5 个样本。
                out_of_range.append((norad, nm, label, f))
        # 1.0 MHz 会把"V/V 或 U/U 跨频中继"也当成错误：CSS 就是
        # 145.985 下行 / 145.850 上行，只差 135 kHz，但它是真中继。
        # 真正错的是"上下行完全一样"，那是 simplex 记录被当成转发器。
        if down and up and abs(down - up) < 0.05:
            near_same.append((norad, nm, down, up))
        if tone < 0 or tone > 254:
            p2(f"Freqs {norad} {nm}: 哑音 {tone} Hz 不像 CTCSS 标准值")

    if out_of_range:
        sample = ", ".join(f"{n} {nm} {lb} {f}" for n, nm, lb, f in out_of_range[:5])
        p2(f"目录里有 {len(out_of_range)} 条频点超出 UV-K5 接收范围 "
           f"({RADIO_MIN_MHZ:.0f}-{RADIO_MAX_MHZ:.0f} MHz)，手台收不到、只能看数据；样本：{sample}")
    if near_same:
        p1(f"{len(near_same)} 条上下行几乎同频（除非 simplex，否则写反）："
           + ", ".join(f"{n} {nm}" for n, nm, _d, _u in near_same[:8]))

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
