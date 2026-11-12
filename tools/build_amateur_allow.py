#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 _amsat_audit.json 生成业余卫星白名单 数据/amateur_allow.json。

白名单是 App / PC / 真源三处唯一的准入条件：不在 keep 里的 NORAD 一律
不许出现在 sat_freq.json、freqs.json、transponders.json 里。

用法
----
    python tools/build_amateur_allow.py            # 生成（默认 --write）
    python tools/build_amateur_allow.py --write    # 同上
    python tools/build_amateur_allow.py --check    # 只校验现有名单是否与审计一致

`keep` 来自审计 verdicts 里 verdict == "keep" 的条目（按 norad 升序）。
`unverified` 来自 crosscheck 里 status == "missing_no_amsat" 的条目：
AMSAT 频率页没有这颗星，本地值保留，生成物上标 `_unverified: true`。
`pending_add` 永远留空：那 20 颗要操作员拍板，脚本不替人做决定。
"""
import argparse
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                      # …/多普勒
AUDIT = os.path.join(ROOT, "_amsat_audit.json")
ALLOW = os.path.join(ROOT, "数据", "amateur_allow.json")

PENDING_NOTE = (
    "待操作员拍板的 20 颗（IO-86/LAPAN-A2、AO-129、KOSTKA、JAMX01、"
    "RS83S/59S/66S/90S、BY70-4、EMISAR、Replicator-2、T.MicroSat-1、"
    "Lilium-4、UNNE-1B、BY70-8、CAS-7C、CRAC-01、INCA-2、PARUS-T2、RIDU-Sat 1）"
)


def build_doc(audit):
    verdicts = audit.get("verdicts") or []
    crosscheck = audit.get("crosscheck") or []

    keep = [
        {"norad": int(v["norad"]), "name": v.get("name") or ""}
        for v in verdicts
        if v.get("verdict") == "keep" and str(v.get("norad") or "").strip()
    ]
    keep.sort(key=lambda x: x["norad"])

    unverified = sorted(
        int(c["norad"]) for c in crosscheck
        if c.get("status") == "missing_no_amsat" and str(c.get("norad") or "").strip()
    )

    return {
        "_note": "业余卫星白名单：只有这里列出的 NORAD 才允许出现在 App/PC/真源里",
        "_source": "AMSAT 在役频率页 + OSCAR 编号表 + status 页，抓取 2026-11-11",
        "_generated_by": "tools/build_amateur_allow.py",
        "_pending_note": PENDING_NOTE,
        "_pending_why": (
            "这 20 颗 AMSAT 页面有频率，但目录/审计里没有可确认的 NORAD 或归属，"
            "加进来会造出没有星历的条目；等操作员逐颗拍板后手工从 pending_add 挪进 keep。"
        ),
        "_unverified_note": (
            "unverified 里的 NORAD：AMSAT 频率页没有这颗星的频率，保留本地值，"
            "生成 sat_freq.json / freqs.json 时在星对象上加 \"_unverified\": true。"
        ),
        "keep": keep,
        "pending_add": [],
        "unverified": unverified,
    }


def render(doc):
    return json.dumps(doc, ensure_ascii=False, indent=1) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="写 数据/amateur_allow.json")
    ap.add_argument("--check", action="store_true", help="只比对，不写文件")
    args = ap.parse_args()
    if not (args.write or args.check):
        args.write = True

    if not os.path.exists(AUDIT):
        print("缺审计文件：%s" % AUDIT, file=sys.stderr)
        return 2
    with io.open(AUDIT, encoding="utf-8") as f:
        audit = json.load(f)

    doc = build_doc(audit)
    if not doc["keep"]:
        print("审计里一条 keep 都没有，拒绝生成空名单", file=sys.stderr)
        return 2
    text = render(doc)

    if args.check:
        if not os.path.exists(ALLOW):
            print("缺名单文件：%s（跑 build_amateur_allow.py --write）" % ALLOW, file=sys.stderr)
            return 2
        with io.open(ALLOW, encoding="utf-8") as f:
            have = f.read()
        if have != text:
            print("名单与审计不一致，跑 tools/build_amateur_allow.py --write")
            return 1
        print("名单一致：keep %d 颗 / unverified %d 颗 / pending_add %d 颗"
              % (len(doc["keep"]), len(doc["unverified"]), len(doc["pending_add"])))
        return 0

    os.makedirs(os.path.dirname(ALLOW), exist_ok=True)
    with io.open(ALLOW, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print("已写 %s：keep %d 颗 / unverified %d 颗 / pending_add 0 颗"
          % (ALLOW, len(doc["keep"]), len(doc["unverified"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
