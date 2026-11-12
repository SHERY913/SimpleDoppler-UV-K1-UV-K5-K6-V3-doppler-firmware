#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""罗盘采样器（B2 用）—— 两种模式：

  --mode text    走 uiautomator 读「方位/误差」文本。准，但一次 dump 约 2 s（实测 1.9~2.0 s），
                 只够看 10 s 静置漂移，量不出 1.5 s 级的回稳。
  --mode pixels  走 screencap（约 0.35 s/帧 ≈ 2.8 Hz），从像素里找盘心（白圈）与红圈，
                 算「红圈相对盘心的角度」。角度 = 目标方位 − 手机朝向，所以：
                   静置 10 s 角度峰峰值 = 罗盘抖动
                   转 30° 后角度回到 ±2° 内的耗时 = 回稳时间
                 这是 B2 判据要的两个量，且帧率够。

判据（写进交接/报告时照抄）：
    静置 10 s：角度峰峰值 > 2°          → 判「抖」
    转 30°：角度落到末值 ±2° 内 > 1.5 s → 判「黏」

用法：
    python compass_watch.py                        # 像素模式，采到 Ctrl-C
    python compass_watch.py --secs 30 --csv x.csv
    python compass_watch.py --mode text --secs 12   # 文本模式（慢，交叉核对用）
"""
from __future__ import annotations

import argparse
import csv
import io
import math
import os
import re
import subprocess
import sys
import time
from datetime import datetime

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))                    # 多普勒/
DEFAULT_ADB = os.path.abspath(os.path.join(
    HERE, "..", "..", "..", "tc", "sdk", "platform-tools", "adb.exe"))

# 罗盘在页面上的粗略范围（1080×2340，dp×3）。只在框内找像素，避免被文字干扰。
BOX = (60, 260, 1020, 1100)


def adb_out(adb: str, args: list[str], timeout: float = 20.0) -> bytes:
    return subprocess.run([adb, *args], capture_output=True, timeout=timeout).stdout


def grab(adb: str) -> Image.Image:
    raw = adb_out(adb, ["exec-out", "screencap", "-p"])
    return Image.open(io.BytesIO(raw)).convert("RGB")


def center_of(img: Image.Image) -> tuple[float, float] | None:
    """盘心：框内最亮的那个白色圆环的质心。找不到返回 None。"""
    x0, y0, x1, y1 = BOX
    px = img.load()
    sx = sy = n = 0
    for y in range(y0, y1, 2):
        for x in range(x0, x1, 2):
            r, g, b = px[x, y]
            if r > 215 and g > 215 and b > 215:
                sx += x
                sy += y
                n += 1
    if n < 50:
        return None
    return sx / n, sy / n


def red_angle(img: Image.Image, cx: float, cy: float) -> tuple[float, float] | None:
    """红圈方向（度，0=东、顺时针为正，屏幕坐标 y 向下）。返回 (角度, 半径)。"""
    x0, y0, x1, y1 = BOX
    px = img.load()
    sx = sy = n = 0
    for y in range(y0, y1, 2):
        for x in range(x0, x1, 2):
            r, g, b = px[x, y]
            if r > 130 and g < 95 and b < 95:
                sx += x
                sy += y
                n += 1
    if n < 20:
        return None
    ax, ay = sx / n, sy / n
    ang = math.degrees(math.atan2(ay - cy, ax - cx))
    rad = math.hypot(ax - cx, ay - cy)
    return ang, rad


def texts(adb: str) -> list[str]:
    subprocess.run([adb, "shell", "uiautomator", "dump", "--compressed", "/sdcard/dsh_cw.xml"],
                   capture_output=True, timeout=30)
    out = adb_out(adb, ["exec-out", "cat", "/sdcard/dsh_cw.xml"]).decode("utf-8", "replace")
    return [t.strip() for t in re.findall(r'text="([^"]*)"', out) if t.strip()]


def parse_text(ts: list[str]) -> tuple[float | None, float | None]:
    az = err = None
    for t in ts:
        m = re.fullmatch(r"(\d{3})°\s*/\s*([+-]\d+)°", t)
        if m and az is None:
            az = float(m.group(1))
            continue
        m = re.fullmatch(r"(\d+(?:\.\d+)?)°", t)
        if m and float(m.group(1)) <= 180.5 and err is None and az is not None:
            err = float(m.group(1))
    return az, err


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--adb", default=DEFAULT_ADB)
    ap.add_argument("--mode", choices=["pixels", "text"], default="pixels")
    ap.add_argument("--secs", type=float, default=0.0, help="0 = 一直采到 Ctrl-C")
    ap.add_argument("--csv", default="")
    ap.add_argument("--calib", type=int, default=5, help="盘心标定帧数（取中位数，之后固定；0 = 每帧重算）")
    ap.add_argument("--center", default="", help="直接给盘心，如 540,620（跳过标定）")
    args = ap.parse_args()

    if not os.path.exists(args.adb):
        print(f"找不到 adb：{args.adb}", file=sys.stderr)
        return 2
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    csv_path = args.csv or os.path.join(ROOT, "verify", f"compass-{args.mode}-{stamp}.csv")
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)

    print(f"# adb {args.adb}")
    print(f"# 模式 {args.mode} · CSV {csv_path}")
    hdr = "t(s)  angle  radius  az  err" if args.mode == "pixels" else "t(s)  az  err"
    print(hdr)

    t0 = time.time()
    rows = 0
    # 盘心：白圈质心会被四个白色方位字（北/东/南/西）拉偏，而且每帧抖 ±6 px（=±1.6°）。
    # 所以先标定若干帧取中位数，之后整段用同一个盘心；抖动只留红圈自己的质心分量。
    center: tuple[float, float] | None = None
    if args.center:
        cx_s, cy_s = args.center.split(",")
        center = (float(cx_s), float(cy_s))
        print(f"# 盘心（人工给） {center[0]:.0f},{center[1]:.0f}")
    elif args.calib > 0:
        xs, ys = [], []
        for _ in range(args.calib):
            c = center_of(grab(args.adb))
            if c:
                xs.append(c[0])
                ys.append(c[1])
        if xs:
            xs.sort()
            ys.sort()
            center = (xs[len(xs) // 2], ys[len(ys) // 2])
            print(f"# 盘心（{len(xs)} 帧中位数） {center[0]:.0f},{center[1]:.0f}")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["t_s", "angle", "radius", "az", "err"] if args.mode == "pixels"
                   else ["t_s", "az", "err"])
        try:
            while True:
                t = time.time() - t0
                if args.secs and t > args.secs:
                    break
                if args.mode == "pixels":
                    img = grab(args.adb)
                    c = center if (center and args.calib >= 0) else center_of(img)
                    if c is None:
                        print(f"{t:6.2f}       -      -   [no-center]")
                        continue
                    r = red_angle(img, *c)
                    ang = f"{r[0]:7.1f}" if r else "     -"
                    rad = f"{r[1]:6.1f}" if r else "     -"
                    cx = f"{c[0]:.0f},{c[1]:.0f}" if c else "no-center"
                    print(f"{t:6.2f} {ang} {rad}   [{cx}]")
                    w.writerow([f"{t:.2f}", f"{r[0]:.2f}" if r else "", f"{r[1]:.1f}" if r else "", "", ""])
                else:
                    az, err = parse_text(texts(args.adb))
                    print(f"{t:6.2f}  {az if az is not None else '-'}  {err if err is not None else '-'}")
                    w.writerow([f"{t:.2f}", az if az is not None else "", err if err is not None else ""])
                f.flush()
                rows += 1
        except KeyboardInterrupt:
            pass

    print(f"# {rows} 行 → {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
