#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""刷新 TLE（多源合并版）→ 多普勒工具/tle_cache.json + 安卓App 的 assets/tles.json

用法
----
    python refresh_tle.py                  # 联网抓所有源，按 NORAD 合并后写两份
    python refresh_tle.py --check          # 只读缓存：逐颗 TLE 历元距今多少天 + 分档统计（不联网）
    python refresh_tle.py --resync         # 不联网，只把缓存同步进 assets/tles.json
    python refresh_tle.py --stale          # 只补"历元超过 14 天"的星（tleapi + SatNOGS 单星）
    python refresh_tle.py --stale 7        # 同上，把门槛改成 7 天
    python refresh_tle.py --extra          # 额外跑 SatNOGS 大库（只补已知星，刷新历元）
    python refresh_tle.py --extra-all      # 额外跑 SatNOGS 大库并全量并入（96 → 1700 颗，慎用）
    python refresh_tle.py --only amsat_nasabare      # 只跑某些源（调试用）
    python refresh_tle.py --skip celestrak_active    # 跳过某些源（条目多的源很慢）
    python refresh_tle.py --timeout 15     # 单次请求超时，默认 25 s
    python refresh_tle.py --list           # 打印源表，不联网

源表（数字小 = 先抓 = 历元打平时的裁决顺序；定义在 tle_sources.py）
-------------------------------------------------------------------
    10  amsat_nasabare     AMSAT nasabare.txt（三行一组，约 97 颗活跃业余星）
    20  celestrak_gp       CelesTrak amateur 分组 JSON（字段最全）
    30  celestrak_tle      CelesTrak amateur 分组纯 TLE（与 20 同源，互为备份）
    40  celestrak_satnogs  CelesTrak satnogs 分组（业余/科研立方星，覆盖最广）
    50  celestrak_active   CelesTrak active 分组（全在轨，条目极多，仅兜底）
    60  celestrak_stations CelesTrak stations 分组（含 ISS/天宫，历元常最新）
    70  satnogs            db.satnogs.org 全库 TLE（1700 条 / 537 KB / 40~180 s）
                           **默认不跑**：只在大源全挂时兜底，或 --extra / --extra-all 开启
    90  tleapi             tle.ivanstanojevic.me 单星查询，只给缺失星补一条

合并规则
--------
* 按优先级逐源抓取，任何一个源失败都只记一行状态，不影响整体；
* 以 NORAD 编号为键合并，同一颗星保留 **TLE 历元最新** 的那条（比较 l1[18:32] 解出的
  历元，不是抓取时间 —— 某源今天刚抓到的可能是半年前的旧元素；SatNOGS 的 `updated`
  字段是入库时间，同样不能拿来做比较）；
* 历元相同（差 <1 s）时保留优先级高的源（先到的胜）；每次替换都记一条"冲突"明细；
* SatNOGS 默认**只收缓存里已有的 NORAD**（`keep` 过滤）：它 1700 条里 1605 条是非业余星，
  全灌进来会把界面列表从 96 条撑到 1700 条。要全量并入就用 --extra-all；
* 至少一个源成功就写盘；**全部失败则保留旧缓存不动，退出码 1**。

缓存格式（`_sources` 是元数据，不是卫星）
----------------------------------------
    {
      "7530":  ["AO-07", "1 07530U ...", "2 07530 ...", 抓取时间, 历元, 倾角, ...],
      "_sources": [ {"source": "amsat_nasabare", "http": 200, "count": 97, ...}, ... ]
    }

`{norad: [名称, line1, line2, ...]}` 的主结构不变，老读者按 norad 取值的代码照旧可用。
`_sources` 特意写成 **列表**而不是字典：同目录的 verify_data.py 把任何非列表的顶层值
当成结构错误（P0），doppler_tool.py 的 tle_stamp() 会对字典取 v[3] 抛 KeyError。
列表形式对这两处都是安全的（前者降级成一条"只在工具侧有"的告警，后者返回 0）。

退出码
------
    0  全部批量源成功（或 --check/--resync/--list/--stale 正常完成）
    1  所有源都失败 / 解析出的条目太少（<50）/ 写盘失败 —— 旧缓存保持原样
    2  部分源成功：数据已写盘，但有源失败或条目为 0（看上面对应的状态表）
"""

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                      # 多普勒/PC工具
REPO = os.path.dirname(ROOT)                      # 多普勒
CACHE = os.path.join(HERE, "tle_cache.json")
ASSETS = os.path.join(REPO, "安卓App", "android-app", "app",
                      "src", "main", "assets", "tles.json")

if HERE not in sys.path:                           # 允许从任意目录调用
    sys.path.insert(0, HERE)

from tle_sources import (  # noqa: E402
    DEFAULT_TIMEOUT, SOURCES, epoch_dt, epoch_of, fetch_all, fetch_one,
    fetch_one_satnogs, format_status_table, parse_three_line,
)

# 界面表（Freqs.kt）依赖的 10 颗。合并后要是缺了哪颗，用 tleapi 单星补抓。
PRIORITY_NORADS = ["24278", "25544", "27607", "39444", "43017",
                   "43678", "43770", "43803", "44909", "50466"]
MIN_SATS = 50          # 少于这个数就认为源坏了，不覆盖旧缓存
PC_TTL_DAYS = 14       # doppler_tool.py 的 tle_stamp TTL
APP_STALE_DAYS = 30    # 安卓 tleStale 的门槛


# ── 缓存读写 ──────────────────────────────────────────────────────────
def iter_sats(data):
    """只吐真正的卫星条目：跳过 `_` 开头的元数据键和结构不对的值。"""
    for k, v in data.items():
        if str(k).startswith("_"):
            continue
        if isinstance(v, list) and len(v) >= 3 and str(v[0]) is not None:
            yield str(k), v


def load_cache(path=CACHE):
    """读缓存 → {norad: 记录}（元数据键已滤掉）。读不了返回 {}。"""
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:  # noqa: BLE001
        print(f"现有缓存读不了（{e}）")
        return {}
    return {k: v for k, v in iter_sats(raw)}


def load_meta(path=CACHE):
    """读缓存里的 `_sources` 元数据；没有就返回 []。"""
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:  # noqa: BLE001
        return []
    meta = raw.get("_sources")
    return meta if isinstance(meta, list) else []


def parse(text, fetched_at=None):
    """三行一组 → {norad: 记录}（保留给旧调用方，实现已挪到 tle_sources）。"""
    return parse_three_line(text, fetched_at)


def period_min(line2):
    return 1440.0 / float(line2[52:63])


# ── 合并 ─────────────────────────────────────────────────────────────
def merge_sources(results):
    """逐源合并 → (merged, origin, conflicts)。

    merged    {norad: 记录}，同一颗星留历元最新的
    origin    {norad: 源名}，最终留下的是哪个源给的
    conflicts [(norad, 名称, 输了的源, 输的历元, 赢的源, 赢的历元), ...]
    """
    merged, origin, conflicts = {}, {}, []
    for r in sorted(results, key=lambda x: x["priority"]):
        if not r.get("ok"):
            continue
        for norad, rec in r["records"].items():
            cur = merged.get(norad)
            if cur is None:
                merged[norad] = rec
                origin[norad] = r["name"]
                continue
            new_ep, old_ep = epoch_of(rec), epoch_of(cur)
            if new_ep and (not old_ep or new_ep > old_ep + 1.0):
                conflicts.append((norad, rec[0], origin[norad], old_ep,
                                  r["name"], new_ep))
                merged[norad] = rec
                origin[norad] = r["name"]
            elif old_ep and new_ep and old_ep > new_ep + 1.0:
                conflicts.append((norad, rec[0], r["name"], new_ep,
                                  origin[norad], old_ep))
    return merged, origin, conflicts


def _iso(ts):
    if not ts:
        return ""
    return datetime.fromtimestamp(float(ts), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_meta(results, gapfill=None, prev=None):
    """`_sources` 元数据：每个源的抓取时间 / HTTP / 条目数 / 耗时 / 失败原因。

    prev 是上一次的元数据：本次没跑到的源（比如用了 --skip 或默认不跑 satnogs）
    保留上次的记录，只覆盖本次真的跑过的那几条。这样缓存里的源状态表永远是
    "每个源最后一次实测结果"，而不是被一次 --skip 冲成一行。
    """
    ran = {r["name"] for r in results}
    if gapfill:
        ran.add(gapfill.get("source", ""))
    keep_prev = []
    for m in (prev or []):
        if not isinstance(m, dict):
            continue
        src = str(m.get("source", ""))
        if src in ran:
            continue
        if src.startswith("gapfill") or src.startswith("stale"):
            continue                      # 这两条每次运行重新生成
        keep_prev.append(m)
    meta = list(keep_prev)
    for r in sorted(results, key=lambda x: x["priority"]):
        meta.append({
            "source": r["name"],
            "priority": r["priority"],
            "url": r["url"],
            "http": r["http"],
            "count": r["count"],
            "ok": bool(r["ok"]),
            "elapsed_s": r["elapsed"],
            "fetched_at": r["fetched_at"],
            "fetched_iso": _iso(r["fetched_at"]),
            "error": r["error"],
        })
    if gapfill:
        meta.append(gapfill)
    return meta


# ── 报告 ─────────────────────────────────────────────────────────────
def report(tles, tag):
    if not tles:
        print(f"{tag}: 空")
        return
    now = datetime.now(timezone.utc)
    ages = [(now - epoch_dt(v[1])).total_seconds() / 86400.0 for v in tles.values()]
    deep = [(k, v[0], period_min(v[2])) for k, v in tles.items()
            if v[2][52:63].strip() and period_min(v[2]) >= 225]
    ages.sort()
    print(f"{tag}: {len(tles)} 颗，历元距今 {ages[0]:.2f}~{ages[-1]:.2f} 天"
          f"（中位 {ages[len(ages) // 2]:.2f}）")
    if deep:
        print(f"  深空(周期>=225min): {len(deep)} 颗 "
              + ", ".join(f"{n}({p:.0f}min)" for _, n, p in deep))


def age_buckets(tles):
    """按 >7 / >14 / >30 天分档，返回 (rows, 各档列表)。"""
    now = datetime.now(timezone.utc)
    rows = []
    for norad, v in tles.items():
        try:
            age = (now - epoch_dt(v[1])).total_seconds() / 86400.0
        except Exception:  # noqa: BLE001
            continue
        rows.append((norad, str(v[0]), age))
    rows.sort(key=lambda x: -x[2])
    return rows


def check(tles):
    """--check：只读缓存，报每颗星历元距今多少天 + 分档统计。"""
    print("=" * 74)
    print("TLE 老化体检（离线，只读 tle_cache.json）")
    print("=" * 74)
    if not tles:
        print("缓存里没有可用条目。先跑一次不带参数的 refresh_tle.py")
        return 1
    rows = age_buckets(tles)
    over7 = [r for r in rows if r[2] > 7]
    over14 = [r for r in rows if r[2] > 14]
    over30 = [r for r in rows if r[2] > 30]
    print(f"共 {len(rows)} 颗；历元距今 最新 {rows[-1][2]:.2f} 天 / "
          f"最老 {rows[0][2]:.2f} 天")
    print(f"  >7  天: {len(over7):>3} 颗"
          f"   （超过一周，低轨位置开始有偏差）")
    print(f"  >14 天: {len(over14):>3} 颗"
          f"   （PC 侧 tle_stamp TTL={PC_TTL_DAYS} 天，界面会提示旧数据）")
    print(f"  >30 天: {len(over30):>3} 颗"
          f"   （App 侧 tleStale 门槛={APP_STALE_DAYS} 天，会标红「位置会偏」）")

    meta = load_meta()
    if meta:
        print("\n上次刷新的源状态（缓存 _sources）：")
        for m in meta:
            if not isinstance(m, dict):
                continue
            print(f"  {str(m.get('source', '?')):<20} "
                  f"HTTP {str(m.get('http') or '-'):>4}  "
                  f"{str(m.get('count', 0)):>5} 条  "
                  f"{m.get('elapsed_s', 0):>5}s  "
                  f"{m.get('fetched_iso', '')}  "
                  f"{'' if m.get('ok') else (m.get('error') or '')}")
    else:
        print("\n缓存里没有 _sources（上次是用旧版 refresh_tle.py 写的）。")

    print(f"\n{'NORAD':>6}  {'名称':<12} {'历元距今':>9}  标记")
    for norad, name, age in rows:
        flag = ("！>30d" if age > APP_STALE_DAYS else
                "!>14d" if age > PC_TTL_DAYS else
                " >7d" if age > 7 else "")
        print(f"{norad:>6}  {name[:12]:<12} {age:>8.2f}d  {flag}")
    return 0


def list_sources():
    print(f"{'优先级':>4}  {'源名':<20} {'类型':<6}  说明")
    for s in sorted(SOURCES, key=lambda s: s.priority):
        print(f"{s.priority:>4}  {s.name:<20} "
              f"{'批量' if s.batch else '单星':<6}  {s.note}")
    return 0


def validate(tles):
    """解算一遍，剔掉已经再入 / 数据坏掉的条目。

    sgp4 的 error 非 0（常见 6 = 掉到地面以下）说明这颗星的 TLE 是僵尸数据，
    留在 assets 里只会在界面上显示一堆假数字。缺 sgp4 库时跳过，不丢数据。
    """
    try:
        from sgp4.api import Satrec, WGS72, jday
    except ImportError:
        print("（没有 sgp4 库，跳过解算校验，不做剔除）")
        return tles, []

    now = datetime.now(timezone.utc)
    jd, fr = jday(now.year, now.month, now.day, now.hour, now.minute,
                  now.second + now.microsecond / 1e6)
    good, bad = {}, []
    for k, v in tles.items():
        try:
            sat = Satrec.twoline2rv(v[1], v[2], WGS72)
            e, _r, _v = sat.sgp4(jd, fr)
        except Exception as ex:  # noqa: BLE001
            bad.append((k, v[0], f"解析异常 {ex}"))
            continue
        if e != 0:
            bad.append((k, v[0], f"解算错 {e}"))
            continue
        good[k] = v
    return good, bad


def write_outputs(tles, meta):
    """写 tle_cache.json（含 _sources）和 assets/tles.json（只写前 3 项）。"""
    out = dict(tles)
    if meta:
        out["_sources"] = meta
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("写入", CACHE)

    os.makedirs(os.path.dirname(ASSETS), exist_ok=True)
    # assets 只写前 3 项：App 侧按 JSONObject → JSONArray 解析，
    # 多塞一个非数组的键虽然会被 optJSONArray 跳过，但没必要给 App 送垃圾。
    slim = {k: v[:3] for k, v in tles.items()}
    with open(ASSETS, "w", encoding="utf-8") as f:
        json.dump(slim, f, ensure_ascii=False, indent=1)
    print("写入", ASSETS)
    return len(slim)


def stale_refresh(tles, days=PC_TTL_DAYS, timeout=8.0, workers=4):
    """--stale：只补"历元超过 days 天"的星，用单星接口，不跑全量刷新。

    每颗先问 tleapi（1~5 s），再问 SatNOGS 单星（~6 s，它不支持多值过滤，
    只能一颗一颗问）。两个都拿不到就保留旧值，并明确报"源里也没有"。
    并发 4；只有历元确实更新的才替换（老历元不覆盖新历元）。
    返回 (tles, updated, unresolved)。
    """
    now = datetime.now(timezone.utc)
    targets = []
    for norad, v in tles.items():
        try:
            age = (now - epoch_dt(v[1])).total_seconds() / 86400.0
        except Exception:  # noqa: BLE001
            continue
        if age > days:
            targets.append((norad, v, age))
    targets.sort(key=lambda x: -x[2])
    if not targets:
        print(f"没有历元超过 {days:.0f} 天的星，无需补抓。")
        return tles, [], []

    def job(item):
        norad, old, age = item
        got = []
        r = fetch_one(norad, timeout=timeout)
        if r:
            got.append(("tleapi", r))
        r = fetch_one_satnogs(norad, timeout=max(20.0, timeout * 3))
        if r:
            got.append(("satnogs", r))
        best_src, best = None, None
        for src_name, rec in got:
            ep = epoch_of(rec) or 0
            if best is None or ep > (epoch_of(best) or 0):
                best_src, best = src_name, rec
        return norad, old, age, best_src, best, [n for n, _ in got]

    print(f"\n补抓历元超过 {days:.0f} 天的星：{len(targets)} 颗"
          f"（并发 {workers}，tleapi {timeout:.0f}s / satnogs {max(20.0, timeout*3):.0f}s）…")
    updated, unresolved = [], []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for norad, old, age, src, rec, tried in ex.map(job, targets):
            new_ep, old_ep = (epoch_of(rec) if rec else None), epoch_of(old)
            if rec is None:
                unresolved.append((norad, old[0], age, tried))
                print(f"  {norad:>6} {old[0][:16]:<16} {age:>7.1f}d  "
                      f"✗ 源里也没有（试过 {','.join(tried) or '两个接口都没响应'}）")
                continue
            if new_ep and (not old_ep or new_ep > old_ep + 1.0):
                tles[norad] = rec
                delta = (f"+{(new_ep - old_ep)/3600:.1f}h" if old_ep else "旧值无历元")
                updated.append((norad, rec[0], age, delta, src))
                print(f"  {norad:>6} {rec[0][:16]:<16} {age:>7.1f}d  "
                      f"✓ {src} 历元 {epoch_dt(rec[1]):%m-%d %H:%M} ({delta})")
            else:
                unresolved.append((norad, rec[0], age, [src]))
                print(f"  {norad:>6} {rec[0][:16]:<16} {age:>7.1f}d  "
                      f"= {src} 的历元没更新，保留旧值")
    return tles, updated, unresolved


def _size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def main():
    argv = sys.argv[1:]
    check_only = "--check" in argv
    resync = "--resync" in argv
    if "--list" in argv:
        return list_sources()

    timeout = DEFAULT_TIMEOUT
    only, skip = set(), set()
    extra = "--extra" in argv
    extra_all = "--extra-all" in argv
    stale_days = None
    for i, a in enumerate(argv):
        if a == "--timeout" and i + 1 < len(argv):
            timeout = float(argv[i + 1])
        elif a.startswith("--timeout="):
            timeout = float(a.split("=", 1)[1])
        elif a.startswith("--only="):
            only = {x.strip() for x in a.split("=", 1)[1].split(",") if x.strip()}
        elif a.startswith("--skip="):
            skip = {x.strip() for x in a.split("=", 1)[1].split(",") if x.strip()}
        elif a == "--stale":
            stale_days = PC_TTL_DAYS
            if i + 1 < len(argv) and argv[i + 1].replace(".", "", 1).isdigit():
                stale_days = float(argv[i + 1])
        elif a.startswith("--stale="):
            stale_days = float(a.split("=", 1)[1])

    old = load_cache()
    if old:
        report(old, "现有缓存")

    if check_only:
        return check(old)

    if stale_days is not None:
        # 只补陈旧星：tleapi + SatNOGS 单星，不跑全量刷新（全量要 20 s~3 min，
        # 而通常真正需要更新的只有个位数星）。更新的才替换，失败的保留旧值。
        if not old:
            print("没有缓存，先跑一次不带参数的 refresh_tle.py")
            return 1
        n_before = len(old)
        tles, upd, unres = stale_refresh(old, stale_days, timeout=min(timeout, 8.0))
        meta = [m for m in load_meta()
                if not (isinstance(m, dict)
                        and str(m.get("source", "")).startswith("stale"))]
        if upd:
            meta.append({
                "source": "stale(tleapi+satnogs)", "priority": 95,
                "url": "单星补抓", "http": 200, "count": len(upd), "ok": True,
                "elapsed_s": 0.0, "fetched_at": time.time(),
                "fetched_iso": _iso(time.time()),
                "error": "" if not unres else f"{len(unres)} 颗源里也没有",
            })
            n = write_outputs(tles, meta)
            print(f"\n已更新 {len(upd)} 颗并写盘（{n_before} → {len(tles)} 颗，assets {n} 条）")
        else:
            print("\n没有一颗被更新，缓存未改动。")
        if unres:
            print(f"仍未刷新 {len(unres)} 颗：" +
                  ", ".join(f"{n}({a:.0f}d)" for n, _, a, _ in unres))
            return 2
        return 0

    if resync:
        # 只把缓存搬一份进 assets，不联网。用途：assets 落后于缓存时
        # （上次刷新被 Ctrl-C、或者别人手工改过 assets），verify_data 会一直报
        # "两侧 TLE 内容不一致"，而真正的修法不是再下一次数据，是把它同步过去。
        if not old:
            print("没有缓存，先跑一次不带参数的 refresh_tle.py")
            return 1
        n = write_outputs(old, load_meta())
        print(f"已把 {n} 颗从缓存同步进 assets（没有联网）")
        return 0

    old_cache_size, old_asset_size = _size(CACHE), _size(ASSETS)

    include_sn = extra or extra_all
    keep = None if extra_all else (set(old) | set(PRIORITY_NORADS))
    skip_run = set(skip)
    if not include_sn:
        skip_run.add("satnogs")

    # ── 逐源抓取 ─────────────────────────────────────────────────────
    print(f"\n开始抓取（连接预探 5s / 单源 {timeout:.0f}s，失败重试 1 次）…")

    def live(st):
        code = st["http"] or "-"
        if st["ok"]:
            extra_txt = "（过滤后）" if st.get("filtered") else ""
            print(f"  {st['name']:<20} HTTP {str(code):>4}  {st['count']:>4} 条"
                  f"{extra_txt}  {st['elapsed']:>5.1f}s")
        else:
            print(f"  {st['name']:<20} HTTP {str(code):>4}  {'失败':>4}"
                  f"  {st['elapsed']:>5.1f}s  {st['error']}")

    t0 = time.time()
    results = fetch_all(timeout=timeout, on_status=live, skip=skip_run,
                        only=only, keep=keep if include_sn else None)

    # 兜底：常规源全军覆没时，拉 SatNOGS 大库顶上（这时不做过滤，按全量合并），
    # 否则"AMSAT 挂了 = 什么都没有"，而 SatNOGS 是实测可达的那一个。
    if not any(r["ok"] for r in results) and not include_sn and not only:
        print("\n常规源全灭 → SatNOGS 全库兜底（1700 条 / 537 KB，可能要 1~3 分钟）…")
        results += fetch_all(only={"satnogs"}, on_status=live)
        keep = None
    total_elapsed = time.time() - t0

    print("\n源状态表")
    print(format_status_table(results))
    ok_src = [r for r in results if r["ok"]]
    print(f"成功 {len(ok_src)}/{len(results)} 个源，总耗时 {total_elapsed:.1f}s")

    if not ok_src:
        print("\n所有源都失败 —— 保留旧缓存不动（%s 未修改）" % os.path.basename(CACHE))
        return 1

    merged, origin, conflicts = merge_sources(results)

    # 防回退：旧缓存里某颗星的历元如果比这次抓到的还新（典型场景：昨晚用
    # --stale 补过 IO-86，今天默认只跑 AMSAT，而 AMSAT 那条还是三个月前的），
    # 就保留旧缓存里的那条。否则"补一次、下次刷新又被打回原形"。
    kept_back = []
    for norad, old_rec in old.items():
        cur = merged.get(norad)
        if cur is None:
            continue
        old_ep, cur_ep = epoch_of(old_rec), epoch_of(cur)
        if old_ep and (not cur_ep or old_ep > cur_ep + 1.0):
            merged[norad] = old_rec
            origin[norad] = "缓存(更新，保留)"
            kept_back.append((norad, old_rec[0], cur_ep, old_ep))
    if kept_back:
        print(f"\n保留旧缓存 {len(kept_back)} 颗（源里给的反而是旧历元）：")
        for norad, name, cur_ep, old_ep in kept_back[:5]:
            print(f"  {norad} {name[:14]:<14} 源 {_iso(cur_ep)[:19]} < 缓存 {_iso(old_ep)[:19]}")

    raw_total = sum(r["count"] for r in ok_src)
    print(f"\n合并：{raw_total} 条（含重复）→ {len(merged)} 颗唯一卫星")
    if conflicts:
        print(f"历元冲突 {len(conflicts)} 处，留下历元更新的一条（前 5 条）：")
        for norad, name, lose_src, lose_ep, win_src, win_ep in conflicts[:5]:
            l = _iso(lose_ep)[:19] if lose_ep else "无历元"
            w = _iso(win_ep)[:19] if win_ep else "无历元"
            print(f"  {norad} {name[:14]:<14} {lose_src}({l}) → {win_src}({w})")

    # SatNOGS 这轮到底带来了什么 —— "多找几个源"的真实增益，别只报总数
    sn = [r for r in results if r["name"] == "satnogs" and r["ok"]]
    if sn:
        recs = sn[0]["records"]
        new_stars = [k for k in recs if k not in old]
        over = [k for k in recs if k in old
                and (epoch_of(recs[k]) or 0) > (epoch_of(old[k]) or 0) + 1.0]
        scope = "已过滤为已知 NORAD" if sn[0].get("filtered") else "全量并入"
        print(f"\nSatNOGS 增益：{len(recs)} 条（{scope}）→ "
              f"新增星 {len(new_stars)} 颗 / 历元更新顶掉旧值 {len(over)} 颗")
        for k in over[:5]:
            print(f"  {k} {old[k][0][:14]:<14} "
                  f"{epoch_dt(old[k][1]):%m-%d %H:%M} → {epoch_dt(recs[k][1]):%m-%d %H:%M}")
        if new_stars:
            print("  新增举例：" + ", ".join(
                f"{k} {recs[k][0][:14]}" for k in new_stars[:8])
                + (f" …共 {len(new_stars)} 颗" if len(new_stars) > 8 else ""))

    # ── 缺星补齐（tleapi 单星） ──────────────────────────────────────
    gap = [n for n in PRIORITY_NORADS if n not in merged]
    gapfill = None
    if gap:
        print(f"\n界面表还缺 {len(gap)} 颗（{','.join(gap)}），试 tleapi 单星补抓 …")
        filled, t_gap = 0, time.time()
        for norad in gap:
            rec = fetch_one(norad, timeout=min(timeout, 15))
            if rec:
                merged[norad] = rec
                origin[norad] = "tleapi"
                filled += 1
            time.sleep(1.0)          # 该站有 508 资源限制，别连着打
        gapfill = {
            "source": "tleapi(gapfill)", "priority": 90,
            "url": "https://tle.ivanstanojevic.me/api/tle/{norad}",
            "http": 200 if filled else 0, "count": filled,
            "ok": bool(filled) or not gap,
            "elapsed_s": round(time.time() - t_gap, 1),
            "fetched_at": time.time(), "fetched_iso": _iso(time.time()),
            "error": "" if filled else "一颗都没补到（508 资源限制或该星不在库里）",
        }
        print(f"  tleapi 补到 {filled}/{len(gap)} 颗")

    if len(merged) < MIN_SATS:
        print(f"\n合并后只有 {len(merged)} 颗（<{MIN_SATS}），源看着不对劲，不覆盖旧缓存")
        return 1

    good, dropped = validate(merged)
    if dropped:
        print(f"\n剔除 {len(dropped)} 颗（解算失败/已再入）:")
        for k, n, why in dropped:
            print(f"  {k} {n}: {why}")
    if len(good) < MIN_SATS:
        print(f"\n剔除后只剩 {len(good)} 颗，源不对劲，不覆盖旧缓存")
        return 1

    report(good, "\n新数据")

    meta = build_meta(results, gapfill, prev=load_meta())
    n_assets = write_outputs(good, meta)

    print(f"\n条目：{len(old)} → {len(good)} 颗")
    print(f"tle_cache.json: {old_cache_size} → {_size(CACHE)} B  "
          f"（+_sources 元数据 {len(meta)} 条）")
    print(f"assets/tles.json: {old_asset_size} → {_size(ASSETS)} B  （{n_assets} 条）")

    # 顺手报一下这次刷新值多少：最常见的星在不在
    for n in ("25544", "43700", "7530"):
        if n in good:
            rec = good[n]
            print(f"  {n} {rec[0]} epoch {epoch_dt(rec[1]):%Y-%m-%d %H:%M}"
                  f"  ← {origin.get(n, '?')}")

    failed = [r for r in results if not r["ok"]]
    if failed:
        print(f"\n部分源失败（数据已写盘，但覆盖可能不全）："
              f"{', '.join(r['name'] for r in failed)}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
