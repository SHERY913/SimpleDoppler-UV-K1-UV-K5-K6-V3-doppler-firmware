# -*- coding: utf-8 -*-
"""业余白名单守卫：三处数据源的 NORAD 必须全部落在 数据/amateur_allow.json 里。

为什么单独一个脚本
------------------
三个生成器各自会过滤，但"生成器跑过"和"现在磁盘上的资产是合法的"是两件事：
有人手改 JSON、有人从旧分支拷回 assets、有人只跑了一半生成链，都会让非业余星
重新长回来。这个守卫只看结果文件，不看生成逻辑 —— 它是最后一道闸。

判据（缺一条就红）：
  1. 名单文件存在，且 keep 非空；
  2. 数据/sat_freq.json 的 NORAD 集合 ⊆ 名单；
  3. App assets/freqs.json 的 NORAD 集合 ⊆ 名单；
  4. App assets/transponders.json 的 key 集合 ⊆ 名单。
匹配规则与生成器同一份实现（多普勒/tools/allowlist.py）：NORAD 优先，
NORAD 缺失按 name 精确匹配（去空格、忽略大小写）。

用法：
    python verify_amateur.py          # 校验 + 打印三处条数表；rc=0 通过 / 1 失败
"""
import io
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.abspath(os.path.join(HERE, "..", "..", ".."))          # …/default-workspace
ALLOW_PATH = os.path.join(WS, "多普勒", "数据", "amateur_allow.json")
SAT_FREQ = os.path.join(WS, "多普勒", "数据", "sat_freq.json")
APP_FREQS = os.path.join(WS, "doppler-ng", "app", "src", "main", "assets", "freqs.json")
APP_TRANS = os.path.join(WS, "doppler-ng", "app", "src", "main", "assets", "transponders.json")
TOOLS_DIR = os.path.join(WS, "多普勒", "tools")
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)
import allowlist as _allow  # noqa: E402


def load_sats(path):
    """读 {norad: entry}。缺文件返回 (None, 错误文本)。"""
    if not os.path.exists(path):
        return None, "缺文件：%s" % path
    with io.open(path, encoding="utf-8") as f:
        doc = json.load(f)
    return doc.get("sats") or {}, None


def offenders(sats, allow):
    """返回 (名单外的条目描述列表, 无 NORAD 且名字也没命中的条数)。"""
    bad, nonorad = [], 0
    for key, entry in sats.items():
        norad = key if str(key).isdigit() else (
            entry.get("norad") if isinstance(entry, dict) else None)
        # transponders.json 的值是转发器数组，名字只在 key（NORAD）里，没有 name 字段。
        name = (entry.get("name") if isinstance(entry, dict) else "") or (
            "" if str(key).isdigit() else str(key))
        if allow.match(norad, name):
            continue
        if norad in (None, ""):
            nonorad += 1
            bad.append("no-norad %s" % (name or "?"))
        else:
            bad.append("%s %s" % (norad, name or "?"))
    return bad, nonorad


def main():
    print("业余白名单守卫（唯一真源：数据/amateur_allow.json）")

    # 1) 名单本身
    try:
        allow = _allow.load(ALLOW_PATH)
    except SystemExit as e:
        print("  ✗ %s" % e)
        print("结果：FAIL（名单不可用）")
        return 1
    if not allow.norads:
        print("  ✗ 名单 keep 为空")
        print("结果：FAIL（名单为空）")
        return 1
    print("  名单：%d 颗（%s）" % (len(allow.norads), os.path.relpath(ALLOW_PATH, WS)))

    fail = 0
    rows = []
    for label, path in (("数据/sat_freq.json", SAT_FREQ),
                        ("App freqs.json", APP_FREQS),
                        ("App transponders.json", APP_TRANS)):
        sats, err = load_sats(path)
        if err:
            print("  ✗ %s" % err)
            rows.append((label, "-", "-", "缺文件"))
            fail = 1
            continue
        bad, nonorad = offenders(sats, allow)
        rows.append((label, len(sats), len(sats) - len(bad),
                     "OK" if not bad else "名单外 %d" % len(bad)))
        if bad:
            fail = 1
            print("  ✗ %s 有 %d 条不在名单内（其中无 NORAD %d 条）："
                  % (label, len(bad), nonorad))
            for b in bad[:6]:
                print("      - %s" % b)
            if len(bad) > 6:
                print("      - ……共 %d 条；跑生成链会重写 tools/_dropped_from_source.txt" % len(bad))
        else:
            print("  ✓ %s 全部在名单内" % label)

    print("")
    print("  文件                          条数   在名单内   判定")
    for label, total, ok, verdict in rows:
        print("  %-28s %6s %9s   %s" % (label, total, ok, verdict))
    print("  名单 keep                     %6d" % len(allow.norads))

    if fail:
        print("结果：FAIL（三处 NORAD 集合未收敛到业余名单）")
        return 1
    print("结果：OK（三处 NORAD 集合 ⊆ 名单）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
