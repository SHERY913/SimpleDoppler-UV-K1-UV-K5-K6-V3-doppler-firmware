#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 APP图标.png 生成 app.ico（exe 图标 + Tk 窗口图标共用一张）。

做法：找剪影包围盒 → 以剪影中心裁一个正方形 → **四边泛洪**把背景漆白
（不是阈值一刀切：母图右边缘有一条很淡的灰斜线，一刀切要么留下它，
要么把剪影里的白格线一起削掉）→ 缩到 512 当母版 → 存多尺寸 ico。

    python tools/gen_app_ico.py            # 生成 ../app.ico
    python tools/gen_app_ico.py --preview  # 额外存 ../_ico_preview.png 方便眼看

可重跑：同一张母图跑几次输出一致（无随机、无时间戳）。
"""
import argparse
import os
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.dirname(HERE)                     # PC工具/多普勒工具
SRC = os.path.normpath(os.path.join(TOOL, "..", "..", "APP图标.png"))
OUT = os.path.join(TOOL, "app.ico")

DARK = 60          # 剪影判定：亮度 < DARK
BG = 200           # 背景判定：亮度 >= BG 才算"能走"的背景
SIDE_RATIO = 0.78  # 裁切边长 = 剪影宽 / SIDE_RATIO
MASTER = 512
SIZES = [16, 24, 32, 48, 64, 128, 256]
PNG_SIZES = [16, 32, 48, 64, 256]


def lum(p):
    return (p[0] * 299 + p[1] * 587 + p[2] * 114) // 1000



def small_frame(master, size, threshold=180):
    """小尺寸不能"整张照片缩小"，也不能留抗锯齿的灰。

    原图里卫星本体是带明暗渐变的，太阳翼还有白色格线；缩到 16~48 px 时这些
    中间调全变成灰，任务栏上就是一团糊（16 px 实测中间调占 13.7%）。
    做法：母版先按亮度二值化成纯剪影 → LANCZOS 缩到目标尺寸 → 再按 50% 二值化
    一次。第二次阈值去掉了缩放产生的灰边，同时保留缩放算出来的形状权重，
    比直接"最近邻抽点"更少丢细节。
    """
    g = master.convert("L").point(lambda v: 0 if v < threshold else 255)
    g = g.resize((size, size), Image.LANCZOS).point(lambda v: 0 if v < 128 else 255)
    return Image.merge("RGB", (g, g, g))


SMOOTH = {16, 24, 32, 48}          # 这几个尺寸走二值化，其余直接缩

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true")
    a = ap.parse_args()

    im = Image.open(SRC).convert("RGB")
    w, h = im.size
    print(f"母图 {SRC}")
    print(f"  尺寸 {w}x{h}")

    px = im.load()

    # ── 1. 剪影包围盒（亮度 < DARK 的像素）──
    minx, miny, maxx, maxy = w, h, -1, -1
    for y in range(h):
        for x in range(w):
            if lum(px[x, y]) < DARK:
                if x < minx:
                    minx = x
                if x > maxx:
                    maxx = x
                if y < miny:
                    miny = y
                if y > maxy:
                    maxy = y
    if maxx < 0:
        print("找不到剪影（整张都不够暗）")
        return 2
    bw, bh = maxx - minx + 1, maxy - miny + 1
    cx, cy = (minx + maxx) // 2, (miny + maxy) // 2
    print(f"  剪影 bbox = x {minx}..{maxx}、y {miny}..{maxy}（{bw}x{bh}）")
    print(f"  剪影中心 = ({cx},{cy})")

    # ── 2. 正方形裁切框 ──
    side = int(round(bw / SIDE_RATIO))
    x0, y0 = cx - side // 2, cy - side // 2
    x1, y1 = x0 + side, y0 + side
    print(f"  side = round({bw}/{SIDE_RATIO}) = {side}")
    print(f"  裁切框 = ({x0},{y0},{x1},{y1})")
    tile = im.crop((x0, y0, x1, y1))
    tw, th = tile.size
    tpx = tile.load()

    # ── 3. 四边泛洪：只走过亮度 ≥ BG 的像素，走到的全漆白 ──
    seen = bytearray(tw * th)
    stack = []
    for x in range(tw):
        stack.append((x, 0))
        stack.append((x, th - 1))
    for y in range(th):
        stack.append((0, y))
        stack.append((tw - 1, y))
    painted = 0
    while stack:
        x, y = stack.pop()
        if x < 0 or y < 0 or x >= tw or y >= th:
            continue
        i = y * tw + x
        if seen[i]:
            continue
        if lum(tpx[x, y]) < BG:
            continue                     # 剪影（或剪影里的暗格线）挡住泛洪
        seen[i] = 1
        tpx[x, y] = (255, 255, 255)
        painted += 1
        stack.append((x + 1, y))
        stack.append((x - 1, y))
        stack.append((x, y + 1))
        stack.append((x, y - 1))

    # ── 4. 水印位整块漆白（泛洪可能被水印自己的灰像素挡住）──
    wm = 0
    for y in range(int(th * 0.13)):
        for x in range(int(tw * 0.16)):
            if tpx[x, y] != (255, 255, 255):
                tpx[x, y] = (255, 255, 255)
                wm += 1
    print(f"  泛洪漆白 {painted} 像素；水印矩形另漆白 {wm} 像素（合计 "
          f"{painted + wm}，裁切面积 {tw * th}）")

    # ── 5. 母版 + ico ──
    master = tile.resize((MASTER, MASTER), Image.LANCZOS)
    if a.preview:
        master.save(os.path.join(TOOL, "_ico_preview.png"))
        print("  预览 → _ico_preview.png")
    # 任务栏/标题栏要的是小图标：Tk 只有 iconphoto 能吃到多尺寸 PNG，
    # iconbitmap 在 Windows 上会自己从 ico 里挑最接近的一帧，挑不到就拉大图缩，
    # 结果是 16 px 上一团糊。所以小尺寸单独落盘，由 iconphoto 精确给。
    png_dir = os.path.join(TOOL, "ico")
    os.makedirs(png_dir, exist_ok=True)
    for sz in PNG_SIZES:
        img = small_frame(master, sz) if sz in SMOOTH else master.resize((sz, sz), Image.LANCZOS)
        img.save(os.path.join(png_dir, f"app_{sz}.png"))
    print("  PNG：" + " ".join("ico/app_%d.png" % sz for sz in PNG_SIZES))

    # ico 的每一帧也要分开做：大帧保持原样，小帧二值化，否则 Windows 自己从
    # 大帧缩出来的 16 px 还是糊的。
    frames = [small_frame(master, sz) if sz in SMOOTH
              else master.resize((sz, sz), Image.LANCZOS) for sz in SIZES]
    frames[-1].save(OUT, format="ICO", sizes=[(sz, sz) for sz in SIZES],
                    append_images=frames[:-1])
    print(f"  ico = {OUT}（{os.path.getsize(OUT)} B）")
    ico = Image.open(OUT)
    print(f"  ico 内含尺寸：{sorted(ico.info.get('sizes', []))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
