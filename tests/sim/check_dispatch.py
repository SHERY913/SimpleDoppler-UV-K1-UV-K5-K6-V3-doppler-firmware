#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""app.c 派发层守卫 —— 交接-28 E1/E2 里**仿真编不到的那一半**。

为什么需要它：tests/sim 只把 App/app/doppler.c 编到宿主上（其余全是桩），
app.c 那 3000 行进不了 sim。可 E1 的真因恰恰在 app.c 的派发顺序上：

    app.c:2731  if (gCurrentFunction == FUNCTION_TRANSMIT) {
    app.c:2738      if (Key == KEY_PTT) { GENERIC_Key_PTT(bKeyPressed); goto Skip; }
                    └─ 发射中（含 PTT 抬起那一刻）KEY_PTT 一律走这里，
                       DOPPLER_ProcessKeys 永远调不到 → DPH_PttEvent(false)
                       没人调 → doppler.c 的 ptt_down 永远残留 true → 侧键一
                       之后只换亚音、不再请求发射（E1）。

所以这里守三条**结构**约束（不是"文件里有没有那句话"那种飘的检查）：
  A. 发射分支里，把"抬起"送进 DOPPLER_ProcessKeys 的那段必须在
     GENERIC_Key_PTT(bKeyPressed) 之前，并且条件里带 !bKeyPressed。
  B. 侧键二的拦截条件必须含 DOPPLER_IsActive()：起发那一刻 GENERIC_Key_PTT
     会把屏请求到 DISPLAY_MAIN，只认 gScreenToDisplay 会漏掉那一拍，
     键就落进 1750 Hz 分支（E2 的"有概率"）。
  C. 侧键一的拦截条件必须含 DOPPLER_Side1Held()（既有兜底，防回归）。

用法：
    python 多普勒/tests/sim/check_dispatch.py            # 查固件里的 app.c
    python 多普勒/tests/sim/check_dispatch.py --app PATH # 查一份副本（阳性对照用）

已知它抓不到什么：把 DOPPLER_ProcessKeys 写在 `goto Skip;` 之后的那种
"编译得过、逻辑永不执行"的写法 —— 这需要真的跑 app.c，仿真没有这个能力。
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
APP_C = os.path.join(ROOT, '固件源码', 'uvk1-v6.3.0', 'App', 'app', 'app.c')


def block_after(src, start_idx):
    """从 start_idx 处的 '{' 开始，返回（块内文本, 右花括号之后的下标）。"""
    i = src.index('{', start_idx)
    depth = 0
    j = i
    while j < len(src):
        if src[j] == '{':
            depth += 1
        elif src[j] == '}':
            depth -= 1
            if depth == 0:
                return src[i:j + 1], j + 1
        j += 1
    raise SystemExit('!! app.c 花括号不配对（这条本身就是个严重问题）')


def find_dispatch_block(src):
    """返回"发射中派发"那一段：它是唯一同时含 GENERIC_Key_PTT(bKeyPressed)
    的 `if (gCurrentFunction == FUNCTION_TRANSMIT)` 块。"""
    pat = re.compile(r'if\s*\(\s*gCurrentFunction\s*==\s*FUNCTION_TRANSMIT\s*\)\s*\{')
    for m in pat.finditer(src):
        blk, _ = block_after(src, m.end() - 1)
        if 'GENERIC_Key_PTT(bKeyPressed)' in blk:
            return blk, m.start()
    return None, -1


def main():
    path = APP_C
    if '--app' in sys.argv:
        path = sys.argv[sys.argv.index('--app') + 1]

    with open(path, encoding='utf-8') as f:
        src = f.read()

    bad = 0

    blk, at = find_dispatch_block(src)
    if blk is None:
        print('!! app.c:2712 附近找不到"发射中派发"分支（gCurrentFunction == FUNCTION_TRANSMIT '
              '且内含 GENERIC_Key_PTT）')
        return 1

    line = src[:at].count('\n') + 1
    p_gen = blk.find('GENERIC_Key_PTT(bKeyPressed)')
    p_dop = blk.find('DOPPLER_ProcessKeys')
    p_rel = blk.find('!bKeyPressed')

    if p_dop < 0:
        print('!! app.c:%d 的发射分支里没有把 PTT 抬起送回 DOPPLER_ProcessKeys（E1）' % line)
        bad += 1
    elif p_dop > p_gen:
        print('!! app.c:%d 里 DOPPLER_ProcessKeys 排到了 GENERIC_Key_PTT 之后 —— '
              'go to Skip 之后那句永远执行不到（E1）' % line)
        bad += 1
    else:
        seg = blk[max(0, p_dop - 400):p_dop]
        if '!bKeyPressed' not in seg:
            print('!! app.c:%d 的 DOPPLER_ProcessKeys 那段没判 !bKeyPressed —— '
                  '按下沿也走这里会在同一拍里再 PrepareTX 一次（E1）' % line)
            bad += 1
        else:
            print('   app.c:%d 发射分支：抬起先回到 DOPPLER_ProcessKeys  --- OK' % line)

    if p_rel >= 0 and p_dop >= 0 and p_rel > p_gen:
        print('!! app.c:%d 的 !bKeyPressed 出现在 GENERIC_Key_PTT 之后（顺序不对）' % line)
        bad += 1

    # ---- B：侧键二必须认 DOPPLER_IsActive() ----
    # 定位方式是"找那句 DOPPLER_KeySideX(...) 调用，再回看它的条件" ——
    # 不能只找 `Key == KEY_SIDE1`，app.c:2643/2704/2821 还有三处无关出现。
    def intercept(call, keyname, want, why):
        idx = src.find(call)
        if idx < 0:
            print('!! app.c 里找不到侧键拦截 %s（%s）' % (call, keyname))
            return 1
        seg = src[max(0, idx - 400):idx]
        if keyname not in seg:
            print('!! %s 前面 400 字符里没有 %s —— 那段条件被挪走了？' % (call, keyname))
            return 1
        if want not in seg:
            print('!! %s 的拦截条件里没有 %s（%s）' % (keyname, want, why))
            return 1
        print('   %s 拦截认 %s  --- OK' % (keyname, want))
        return 0

    bad += intercept('DOPPLER_KeySide2(bKeyPressed)', 'Key == KEY_SIDE2',
                     'DOPPLER_IsActive()', 'E2：起发那一拍屏请求在 DISPLAY_MAIN')
    # ---- C：侧键一必须认 DOPPLER_Side1Held() ----
    bad += intercept('DOPPLER_KeySide1(bKeyPressed)', 'Key == KEY_SIDE1',
                     'DOPPLER_Side1Held()', '侧键一按住时退页会漏掉抬起')

    if bad:
        print('\n结果：%d 处不满足（app.c 派发层）' % bad)
        return 1
    print('\n结果：OK（app.c 派发层的三条结构约束都成立）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
