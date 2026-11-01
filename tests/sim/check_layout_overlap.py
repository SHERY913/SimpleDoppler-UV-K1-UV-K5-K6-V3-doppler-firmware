#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""版面重叠检查 —— doppler_layout.h 里同一页同一行上，有没有两格真的压在
一起（像素级，不是"看着挤"）。

为什么要有这个：doppler_layout.h 是 layout_editor.html 导出 + 手改的，
它有 verify_layout.py 守"三端逐条一致"，但**没有任何一处在算字宽**。
本轮 E3（"S2s" 与 "BW 12k" 挤在一起）就是纯几何问题：三端一致、全都绿，
真机上照样是一串糊在一起的字符。所以这里按固件 DPH_DrawPage 的算法复算一遍：

  * font 0（tiny 3x5）  : 每字 4 px，高 5
  * font 2（bold 6x7）  : 每字 7 px，高 7
  * font 3（big 8x16）  : 每字 DPH_BIG_STEP=13 px，高 16，align 0/1 才用它
  * align 2（右对齐）   : x = it->x - w（w >= it->x 时固件夹成 0）
  * align 1（居中）     : x = (128 - w) / 2
  * 占位符按**最坏串**展开（$Y 取 "1267.99000"、$Z 取 "T254.0" ……），
    因为要守的是"最坏情况不重叠"，常用值不重叠没有意义。

用法：
    python 多普勒/tests/sim/check_layout_overlap.py            # 只查，红就退出 1
    python 多普勒/tests/sim/check_layout_overlap.py -v         # 每页都打印
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
LAYOUT = os.path.join(ROOT, '固件源码', 'uvk1-v6.3.0', 'App', 'app', 'doppler_layout.h')

LCD_WIDTH = 128
LCD_HEIGHT = 56            # FRAME_LINES(7) * 8
FRAME_LINES = 7
DPH_TINY_STEP = 4          # doppler.c:4099
DPH_SMALL_STEP = 7         # doppler.c:4100
DPH_BIG_STEP = 13          # doppler.c:4101

# 最坏串（像素 = 字符数 * 字宽）。$D/$Y/$F 都按最长打印宽度给。
WORST = {
    '$F': '1267.99000', '$Y': '1267.99000', '$Z': 'T254.0', '$D': '<-125',
    '$O': '-12345', '$P': '100', '$J': 'LOCK', '$Q': '5W', '$C': '22:41:07',
    '$S': 'RS-44 & BR', '$R': '04:12', '$B': '10-26', '$A': '10:20',
    '$U': '06:18', '$M': '68', '$E': '42', '$G': '65535', '$H': '-0.8',
    '$L': '-04:12', '$V': '1.0062', '$*': '10', '$!': 'ARMED', '$@': 'NO TABLE',
    '$#': 'v6.3.0', '$%': 'Oct 27 2026', '$&': '23:05:56', '$?': '1', '$=': '7.9V',
    '$^': '17 26-10-27', '$<': '128', '$>': '+12', '$+': '3h12m', '$I': '------',
    '$T': '17', '$K': '12:34', '$N': '1', '$W': '6.5K', '$X': '1.0062',
}
# '-': BANDWIDTH_NARROW ? "12k" : "25k"
WORST['$-'] = '25k'

# '$!' 的最坏串跟**页**有关：只有就绪页（page 0）会印 "M RUN  HOLD:PRE"，
# 预听/尾音页最多 "ARMED"（doppler.c:4678-4683 的 auto_armed/phase 分支）。
WORST_PAGE = {(0, '$!'): 'M RUN  HOLD:PRE'}

# 已知、**不是本轮引入**的贴边：只报警告，不判红。每一条都要写清为什么。
KNOWN = {
    ('RX $F', '$L'):
        'page 1：$F 只有 1.2G 那档（1267.99000，10 位）才会铺到 97 压住 94 起的 $L；'
        '145/435 段 9 位收在 90。交接-28 没报，本轮不动。',
    ('$P%', '$J'):
        'page 1：两格共用右下角，但互斥——doppler.c:4883 在 ext_lock_until 未到时'
        '跳过 $P%，那两秒里只有 $J 会画。',
}

# '$' 后面不是合法占位符时按原样印（doppler.c 的 DPH_Expand default 分支）。
PH_RE = re.compile(r'\$.')

ITEM_RE = re.compile(
    r'\{\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*"([^"]*)"\s*\}')


def expand(text, page=None):
    def pick(m):
        key = (page, m.group(0))
        if key in WORST_PAGE:
            return WORST_PAGE[key]
        return WORST.get(m.group(0), m.group(0))
    return PH_RE.sub(pick, text)


def metrics(font, align, x, y, text, page=None):
    """按固件真正画的那套算：tiny/bold 的 y 是像素行，big 的 y 是**行号**
    （DPH_DrawPage 把它直接当 UI_PrintString 的 Line，8x16 吃两行）。"""
    s = expand(text, page)
    if font == 0:                       # DPH_DrawTiny：3 宽 1 空 = 步进 4，高 6
        cw, h, top = DPH_TINY_STEP, 6, y
    elif font in (1, 2):                # DPH_DrawSmall：6 宽 1 空 = 步进 7，高 7
        # doppler.c:5023-5025：font 0 走 DPH_DrawTiny，其余全走 DPH_DrawSmall
        # （bold 只在 font==2 时为真）。所以 1 和 2 的宽度、行高完全一样，
        # 只差笔画粗细 —— 排版器的 'small' → 1、'bold' → 2。
        cw, h, top = DPH_SMALL_STEP, 7, y
    else:                               # font 3：UI_PrintString，Line 是行号
        cw, h, top = DPH_BIG_STEP, 16, min(y, FRAME_LINES - 2) * 8
    w = len(s) * cw
    if align == 2:
        left = 0 if w >= x else x - w
    elif align == 1:
        left = 0 if w >= LCD_WIDTH else (LCD_WIDTH - w) // 2
    else:
        left = x
    return left, left + w - 1, top, top + h - 1, s


def parse(layout=LAYOUT):
    with open(layout, encoding='utf-8') as f:
        src = f.read()
    items = []
    for m in ITEM_RE.finditer(src):
        page, font, align, x, y, text = m.groups()
        items.append((int(page), int(font), int(align), int(x), int(y), text))
    return items


# 版面必须有哪几格（交接-28 E3/E4）。与"重叠"是两件事：
# 重叠检查只能发现"两格压在一起"，发现不了"这一格干脆没有"。
#
#   E3：跟踪页原来只有 `S$*s`（x=39）与 `BW $-`（x=58），中间 19 px ≈ 4 个
#       tiny 字符，屏幕上读成一串；而且 `S` 从来没解释过是"步进"。先改成两格
#       分开并各带前缀 "STEP" / "BW"，真机回"不挤但太丑"；第三轮把两格并成
#       一格 `$*s/$-`（"10s/12k"）；第四轮真机回"还是分开"，于是又拆回
#       `STEP $*s`（x=31）与 `BW $-`（x=66），都在 y=31，见 doppler_layout.h。
#       这条 REQUIRED 只守"两样都在屏幕上"，具体形态随版面迭代，别再按字面
#       写死成带 STEP/BW 前缀 —— 那会让每次改版都要动一次检查。
#   E4：预听页与尾音页原来都没有上行频点 $Y —— 操作员在最需要确认"我这一下
#       按出去是哪个频率"的两页上看不到它。
REQUIRED = [
    (1, '$*s', '跟踪页要显示 ▲▼ 一格的步进（E3）'),
    (1, '$-', '跟踪页要显示接收带宽（E3）'),
    (7, '$Y', '预听页要显示上行频点（E4）'),
    (8, '$Y', '尾音页要显示上行频点（E4）'),
]


def main():
    verbose = '-v' in sys.argv
    layout = LAYOUT
    if '--layout' in sys.argv:
        layout = sys.argv[sys.argv.index('--layout') + 1]
    items = parse(layout)
    if not items:
        print('!! 没解析到任何条目：正则或文件路径变了')
        return 1

    bad = 0
    warns = []
    pages = {}
    for it in items:
        pages.setdefault(it[0], []).append(it)

    # ---- 内容要求（E3/E4）----
    for (pg, tok, why) in REQUIRED:
        hit = [it for it in pages.get(pg, []) if tok in it[5]]
        if hit:
            if verbose:
                print('   page %d 有 %r（%s）' % (pg, tok, hit[0][5]))
        else:
            print('!! page %d 缺 %r：%s' % (pg, tok, why))
            bad += 1

    for page in sorted(pages):
        cells = []
        for (pg, font, align, x, y, text) in pages[page]:
            l, r, t, b, s = metrics(font, align, x, y, text, page)
            if b > LCD_HEIGHT - 1 or t < 0 or l < 0:
                print('!! page %d 出屏：%r → x %d..%d y %d..%d（128x%d）'
                      % (page, text, l, r, t, b, LCD_HEIGHT))
                bad += 1
            elif r > LCD_WIDTH - 1 and font != 3:
                # font 3 超宽由 DPH_DrawPage 自己砍字（:4917 起），小字没有这一层
                print('!! page %d 超右边界：%r → x %d..%d' % (page, text, l, r))
                bad += 1
            cells.append((l, r, t, b, text))
        msgs = []
        for i in range(len(cells)):
            for j in range(i + 1, len(cells)):
                a, b2 = cells[i], cells[j]
                # 像素级：真的同时占住同一行同一列才算重叠（贴边不算）
                if a[1] < b2[0] or b2[1] < a[0]:
                    continue
                if a[3] < b2[2] or b2[3] < a[2]:
                    continue
                key = (a[4], b2[4])
                line = ('   page %d：%r x%d..%d 与 %r x%d..%d 重叠'
                        % (page, a[4], a[0], a[1], b2[4], b2[0], b2[1]))
                if key in KNOWN:
                    warns.append(line + '\n      已知：' + KNOWN[key])
                else:
                    msgs.append(line)
        if msgs:
            bad += len(msgs)
            print('!! page %d 有 %d 处重叠：' % (page, len(msgs)))
            for m in msgs:
                print(m)
        elif verbose:
            print('   page %d OK（%d 格）' % (page, len(pages[page])))

    if bad:
        print('\n结果：重叠/出屏 %d 处' % bad)
        return 1
    print('结果：OK（%d 格 / %d 页，最坏串下无重叠、无出屏）' % (len(items), len(pages)))
    if warns:
        print('（另有 %d 处已知贴边，见上）' % len(warns))
    return 0


if __name__ == '__main__':
    sys.exit(main())
