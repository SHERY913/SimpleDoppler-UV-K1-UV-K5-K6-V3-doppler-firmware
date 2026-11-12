"""命令行写表：算过境 → 构造表 → 写进电台。

走的是 doppler_tool.radio_write() —— 和 GUI 按「写入电台」完全同一条路径，
所以日志（HELLO / TABLE / OK）与 GUI 里的一致。

    python write_pass.py --port COM7 --norad 25544 --step 10 --max-passes 6
    python write_pass.py --dry-run --norad 25544 --step 10 --max-passes 1 --out t.bin

--dry-run 只算不写；--out 把要写的表存成文件，供写后逐字节比对。
退出码：0 成功；1 失败；2 算不出过境/参数错。
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import doppler_core as core          # noqa: E402
import doppler_tool as dt            # noqa: E402


def load_tle(norad):
    path = os.path.join(dt.DATA_DIR, "tle_cache.json")
    with open(path, encoding="utf-8") as f:
        cache = json.load(f)
    hit = cache.get(str(norad))
    if not hit:
        raise RuntimeError(f"tle_cache.json 里没有 NORAD {norad}")
    return hit[1], hit[2]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=None)
    ap.add_argument("--norad", type=int, default=25544)
    ap.add_argument("--step", type=float, default=10.0)
    ap.add_argument("--min-el", type=float, default=10.0)
    ap.add_argument("--days", type=float, default=3.0)
    ap.add_argument("--max-passes", type=int, default=6)
    ap.add_argument("--allow-unlock", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    cfg_path = os.path.join(dt.DATA_DIR, "doppler_tool.json")
    cfg = {"lat": 43.83, "lon": 87.62}
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding="utf-8") as f:
            cfg.update(json.load(f))
    gnd = core.Ground(float(cfg["lat"]), float(cfg["lon"]))

    sat = core.find_sat(a.norad)
    freq10 = sat.freq10 if sat else 0
    if not freq10:
        print(f"NORAD {a.norad} 没有下行频率")
        return 2
    l1, l2 = load_tle(a.norad)
    satrec = core.Satrec.twoline2rv(l1, l2)

    ps = core.find_passes(satrec, gnd, freq10 * 10, days=a.days,
                          min_el=a.min_el, step=a.step,
                          max_passes=a.max_passes)
    if not ps:
        print("这段窗口里没有过境")
        return 2
    print(f"过境 {len(ps)} 段：")
    for p in ps:
        print(f"  {p.aos:%m-%d %H:%M:%S}Z  {p.duration:4d}s  "
              f"max_el={p.max_el:5.1f}  pts={len(p.samples)}")

    table = core.build_table(ps, freq10, a.step, name=(sat.name if sat else "SAT")[:16])
    errs = core.validate_table(table)
    if errs:
        print("表格自检不过：" + "；".join(errs[:3]))
        return 2
    st = dt.table_stats(table)
    print(f"TABLE len={st['len']} sectors={st['sectors']} entries={st['count']} "
          f"pts={st['pts']} step_ms={st['step_ms']} data_off=0x{st['data_off']:06X}")
    print(f"公式 16 + 40×{st['count']} + 6×{st['pts']} = "
          f"{16 + 40 * st['count'] + 6 * st['pts']}（表长 {st['len']}）")

    if a.out:
        with open(a.out, "wb") as f:
            f.write(table)
        print(f"已存 {a.out}（{len(table)} B）")
    if a.dry_run:
        return 0

    port = a.port or cfg.get("port") or "COM7"

    def tick(text, done, total):
        # 每个扇区/每一步都打出来：真机写表出问题时，日志里必须能看出
        # 停在第几个扇区，而不是只有一句"失败了"。
        print(f"[{done}/{total}] {text}", flush=True)

    try:
        out = dt.radio_write(port, table, allow_unlock=a.allow_unlock, tick=tick)
    except Exception as e:
        print(f"写入失败：{e}")
        return 1
    print(f"写入成功：{out['len']} B / {out['sectors']} 扇区，port={port}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
