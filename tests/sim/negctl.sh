#!/usr/bin/env bash
# 阳性对照（negative control）：把交接-26 的三条修复逐条"破坏"掉，看 harness 抓不抓得到。
#
#   bash tests/sim/negctl.sh 1        # 破坏 ①：注释掉 doppler.c:904 的 last_freq = up;
#   bash tests/sim/negctl.sh 2        # 破坏 ②：删掉 doppler.c:3358 的 READY 分支
#   bash tests/sim/negctl.sh 3        # 破坏 ③：把 2777 那段功率顶到 HIGH 挪回 DPH_Enter() 之前
#   bash tests/sim/negctl.sh 4        # 破坏 ④（交接-27）：删掉 DPH_OffsetAt/AngleAt 的越界钉住
#   bash tests/sim/negctl.sh 5        # 破坏 ⑤（交接-27）：短按 M 改回当场 DPH_BeginRun()
#   bash tests/sim/negctl.sh 6        # 破坏 E1（交接-28）：DPH_PttEvent 准入失败时的松手兜底
#   bash tests/sim/negctl.sh 7        # 破坏 E2-a（交接-28）：把"纯接收星早退"塞回 DPH_CycleTone
#   bash tests/sim/negctl.sh 8        # 破坏 E2-b（交接-28）：$Z 又跟着 $Y 一起被跳过
#   bash tests/sim/negctl.sh 9        # 破坏 E5（交接-28）：DPH_StepKey 上界判据改回 (uint32_t) 强转
#   bash tests/sim/negctl.sh 10       # 破坏 E1 的另一半（app.c 派发层）→ check_dispatch.py 必须变红
#   bash tests/sim/negctl.sh 11       # 破坏 E3/E4 版面 → check_layout_overlap.py 必须变红
#   bash tests/sim/negctl.sh 12       # 破坏 E6（真机第三轮）：DPH_READY 加回侧键一准入相位
#   bash tests/sim/negctl.sh 13       # 破坏 E9（真机第三轮）：步进/带宽改回"按过才画 3 秒"
#   bash tests/sim/negctl.sh 14       # 破坏交接-31：就绪页 PTT 又准发（拆掉闸门 + 塞回准入）
#   bash tests/sim/negctl.sh 15       # 破坏交接-31：RUN 的 ▲▼ 方向反回去（▲ 又变成往前）
#
# 关键：**固件源码一个字节都不改**。
# 原因有二：硬约束 1（不改 固件源码/ 任何一行）与仓库里已有未提交改动
# （AGENTS.md：禁 git checkout . / git restore .）。做法是把 doppler.c 复制一份
# 到 tests/sim/out/neg/，在副本上打补丁，再用 DOPPLER_SRC 指给 build.sh。
set -euo pipefail

SIM="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SIM/../.." && pwd)"
APP="$ROOT/固件源码/uvk1-v6.3.0/App"
NEG="$SIM/out/neg"

CTRL="${1:?用法: negctl.sh <1..15> [场景]}"
SCEN="${2:-}"

mkdir -p "$NEG"

# ---- 10：app.c 派发层（sim 编不到 app.c，用静守卫做阳性对照）----
if [ "$CTRL" = "10" ]; then
    cp "$APP/app/app.c" "$NEG/app.c"
    python - "$NEG/app.c" <<'PY'
import io, sys
path = sys.argv[1]
s = io.open(path, encoding='utf-8').read()
old = ("            if (!bKeyPressed && Key == KEY_PTT\n"
       "                && (gScreenToDisplay == DISPLAY_DOPPLER || DOPPLER_IsActive())) {\n"
       "                DOPPLER_ProcessKeys(Key, bKeyPressed, bKeyHeld);\n"
       "                goto Skip;\n"
       "            }\n")
assert s.count(old) == 1, s.count(old)
s = s.replace(old, "            /* [NEG-CTRL-10] E1 的抬起回调被删掉 */\n")
io.open(path, 'w', encoding='utf-8', newline='').write(s)
PY
    echo "== 破坏方式：删掉发射分支里把 PTT 抬起送回 DOPPLER_ProcessKeys 的那段（E1）"
    echo "== 与原件差异："
    diff -u "$APP/app/app.c" "$NEG/app.c" | sed -n '1,30p' || true
    echo
    if PYTHONIOENCODING=utf-8 python "$SIM/check_dispatch.py" --app "$NEG/app.c"; then
        echo "!! 阳性对照**失败**：删掉 E1 修复之后 check_dispatch.py 还是绿的"
        exit 1
    fi
    echo "== 阳性对照成立：删掉 E1 修复 → check_dispatch.py 变红"
    exit 0
fi

# ---- 11：版面（doppler_layout.h 是脚本生成的，同样只改副本）----
if [ "$CTRL" = "11" ]; then
    cp "$APP/app/doppler_layout.h" "$NEG/doppler_layout.h"
    python - "$NEG/doppler_layout.h" <<'PY'
import io, sys
path = sys.argv[1]
s = io.open(path, encoding='utf-8').read()
# E3：把 "$*s" 从 x=46 y=39 拖到 x=10 —— 那一段正压在
# "$H km/s"（x3..38 / y39..44）身上，必然报重叠。
old = '{ 1, 0, 0, 46, 39, "$*s" }'
assert s.count(old) == 1, s.count(old)
s = s.replace(old, '{ 1, 0, 0, 10, 39, "$*s" }')
# E4：预听页/尾音页的 TX $Y 整条删掉
for pg in (7, 8):
    old = '    { %d, 0, 0, 5, 22, "TX $Y" },\n' % pg
    assert s.count(old) == 1, (pg, s.count(old))
    s = s.replace(old, '')
io.open(path, 'w', encoding='utf-8', newline='').write(s)
PY
    echo "== 破坏方式：把 \$*s 拖到 x=10（压在 \"\$H km/s\" 上），删掉 page 7/8 的 TX \$Y（E3/E4）"
    echo "== 与原件差异："
    diff -u "$APP/app/doppler_layout.h" "$NEG/doppler_layout.h" | sed -n '1,40p' || true
    echo
    if PYTHONIOENCODING=utf-8 python "$SIM/check_layout_overlap.py" --layout "$NEG/doppler_layout.h"; then
        echo "!! 阳性对照**失败**：把版面改回交接-28 那一版之后 check_layout_overlap.py 还是绿的"
        exit 1
    fi
    echo "== 阳性对照成立：版面回退 → check_layout_overlap.py 变红"
    exit 0
fi

cp "$APP/app/doppler.c" "$NEG/doppler.c"

python - "$CTRL" "$NEG/doppler.c" <<'PY'
import io, sys

ctrl, path = sys.argv[1], sys.argv[2]
s = io.open(path, encoding='utf-8').read()
orig = s

if ctrl == '1':
    # 破坏 ①：DPH_TxApply 不动 last_freq（交接-26 修的就是这一行，doppler.c:904）
    old = "    last_freq = up;\n}\n"
    assert s.count(old) == 1, s.count(old)
    s = s.replace(old, "    /* [NEG-CTRL-1] last_freq = up; 被注释掉 */\n}\n")
    what = '注释掉 doppler.c:904 的 last_freq = up;'

elif ctrl == '2':
    # 破坏 ②：删掉 READY 页松手那支（doppler.c:3358-3359）
    old = ("        else if (phase == DPH_READY && !ext_ctrl)\n"
           "            DPH_Tune(cur.freq10);\n")
    assert s.count(old) == 1, s.count(old)
    s = s.replace(old, "        /* [NEG-CTRL-2] READY 分支被删掉 */\n")
    what = '删掉 doppler.c:3358 的 `else if (phase == DPH_READY && !ext_ctrl) DPH_Tune(cur.freq10);`'

elif ctrl == '3':
    # 破坏 ③：把"顶到 HIGH"那段挪回 DPH_Enter() 之前，并把 radio_saved 守卫去掉
    #（守卫保留的话挪到前面等于什么都没做，破不出原缺陷）
    blk = """    if (fresh_entry && radio_saved)
    {
        gRxVfo->OUTPUT_POWER = OUTPUT_POWER_HIGH;
        if (gTxVfo != NULL)
            gTxVfo->OUTPUT_POWER = OUTPUT_POWER_HIGH;
    }

"""
    assert s.count(blk) == 1, s.count(blk)
    s = s.replace(blk, "")
    moved = """    /* [NEG-CTRL-3] 拨到 5 W 挪回 DPH_Enter() 之前（交接-26 之前的顺序） */
    if (fresh_entry)
    {
        gRxVfo->OUTPUT_POWER = OUTPUT_POWER_HIGH;
        if (gTxVfo != NULL)
            gTxVfo->OUTPUT_POWER = OUTPUT_POWER_HIGH;
    }

    DPH_Enter();
"""
    old = "    DPH_Enter();\n"
    assert s.count(old) >= 1
    s = s.replace(old, moved, 1)
    what = '把 doppler.c:2777 的功率块挪回 DPH_Enter() 之前（去掉 radio_saved 守卫）'

elif ctrl == '4':
    # 破坏 ④（交接-27 第一条）：删掉「越过末样点就钉住」的两处早退
    tail_c = chr(10).join([
        "    if (q >= (uint32_t)(cur.pts - 1u) * 1000u)",
        "        return DPH_Sample((uint16_t)(cur.pts - 1u));",
        "", ""])
    tail_a = chr(10).join([
        "    if (q >= (uint32_t)(cur.pts - 1u) * 1000u)",
        "        return DPH_SampleEl((uint16_t)(cur.pts - 1u));",
        "", ""])
    assert s.count(tail_c) == 1, s.count(tail_c)
    assert s.count(tail_a) == 1, s.count(tail_a)
    s = s.replace(tail_c, '    /* [NEG-CTRL-4] OffsetAt 尾段钉住被删掉 */' + chr(10))
    s = s.replace(tail_a, '    /* [NEG-CTRL-4] AngleAt 尾段钉住被删掉 */' + chr(10))
    what = '删掉 DPH_OffsetAt / DPH_AngleAt 的尾段钉住（交接-27）'

elif ctrl == '5':
    # 破坏 ⑤（交接-27 第二条）：短按 M 改回当场开跑，长按再到时已不是 READY/PRE
    old = "                menu_tap_pending = true;"
    assert s.count(old) == 1, s.count(old)
    s = s.replace(old, "                DPH_BeginRun();   /* [NEG-CTRL-5] 当场开跑（交接-27 之前） */")
    what = '短按 M 改回当场 DPH_BeginRun()（交接-27 之前）'

elif ctrl == '6':
    # 破坏 E1 的 doppler.c 那一半：准入失败时的松手兜底
    old = "    else if (!bKeyPressed)\n    {\n"
    assert s.count(old) == 1, s.count(old)
    s = s.replace(old, "    else if (0)   /* [NEG-CTRL-6] 准入失败时的松手兜底被废掉 */\n    {\n")
    what = '废掉 DPH_PttEvent 准入失败时的 `GENERIC_Key_PTT(false)` 兜底（E1）'

elif ctrl == '7':
    # 破坏 E2 的必然那条：把"纯接收星直接双响"塞回 DPH_CycleTone
    old = "    cur_tone = (a->CodeType == CODE_TYPE_CONTINUOUS_TONE && a->Code < 50u)"
    assert s.count(old) == 1, s.count(old)
    s = s.replace(old,
        "    /* [NEG-CTRL-7] 纯接收星早退被塞回来（E2 的必然那条） */\n"
        "    if (!ext_ctrl && cur.up10 == 0u)\n"
        "    {\n"
        "        gBeepToPlay = BEEP_500HZ_60MS_DOUBLE_BEEP_OPTIONAL;\n"
        "        return;\n"
        "    }\n\n" + old)
    what = '把 `if (!ext_ctrl && cur.up10 == 0u) 双响 return;` 塞回 DPH_CycleTone（E2）'

elif ctrl == '8':
    # 破坏 E2 的"屏上不说话"那条：$Z 又跟着 $Y 一起被跳过
    old = 'if (phase != DPH_EXT && cur.up10 == 0u && strstr(it->text, "$Y") != 0)'
    assert s.count(old) == 1, s.count(old)
    s = s.replace(old,
        'if (phase != DPH_EXT && cur.up10 == 0u && (strstr(it->text, "$Y") != 0'
        ' || strstr(it->text, "$Z") != 0))   /* [NEG-CTRL-8] */')
    what = 'DPH_DrawPage 又跳过含 $Z 的整条（E2：按键之后屏上什么都不说）'

elif ctrl == '9':
    # 破坏 E5：上界判据改回 (uint32_t) 强转，负 el 被钳到末样点
    old = "        if (el >= (int32_t)total)\n"
    assert s.count(old) == 1, s.count(old)
    s = s.replace(old, "        if ((uint32_t)el >= (uint32_t)total)   /* [NEG-CTRL-9] */\n")
    what = 'DPH_StepKey 上界判据改回 `(uint32_t)el >= total`（E5：一按就跳尾音页）'


elif ctrl == '12':
    # 破坏 E6（真机第三轮）：把 READY 塞回侧键一的准入相位里。
    # 只动那一行，不碰换行与括号收尾 —— 多行匹配容易把自己写坏。
    # 交接-31 之后还得多拆一处：DPH_PttEvent 顶上那道 READY 闸门是侧键一和
    # PTT 共用的，只塞回侧键一那一行会被它先挡掉，对照就变成假阳性。
    gate = "    if (bKeyPressed && phase == DPH_READY)"
    assert s.count(gate) == 1, s.count(gate)
    s = s.replace(gate, "    if (false && bKeyPressed && phase == DPH_READY)   /* [NEG-CTRL-12] */")
    adm = "    if ((phase == DPH_RUN || phase == DPH_EXT"
    assert s.count(adm) == 1, s.count(adm)
    s = s.replace(adm, "    if ((phase == DPH_RUN || phase == DPH_READY || phase == DPH_EXT   /* [NEG-CTRL-12] */")
    old = "        if (!(phase == DPH_RUN || phase == DPH_EXT"
    assert s.count(old) == 1, s.count(old)
    s = s.replace(old,
        "        if (!(phase == DPH_RUN || phase == DPH_EXT"
        " || phase == DPH_READY   /* [NEG-CTRL-12] */")
    what = '把 DPH_READY 加回侧键一的准入相位（E6：就绪页也能起发）'

elif ctrl == '13':
    # 破坏 E9（第三轮形态）：把“只按过才画 3 秒”那套塞回 DPH_DrawPage。
    # 用 chr(34)/chr(10) 拼，不在本文件里写反斜杠转义 —— 两层 heredoc 太容易写坏。
    Q = chr(34)
    old = "        if (it->font == 4u)"
    assert s.count(old) == 1, s.count(old)
    s = s.replace(old,
        "        if (strstr(it->text, " + Q + "$*" + Q + ") != 0) continue;   /* [NEG-CTRL-13] */" + chr(10) + old)
    what = '把“步进/带宽按过才画”塞回 DPH_DrawPage（E9：不再常驻）'

elif ctrl == '14':
    # 破坏交接-31 的第一条："ready 界面 PTT 不许发射"。要两处一起拆才算破坏：
    # ① 拆掉 DPH_PttEvent 顶上那道 READY 闸门；② 把 DPH_READY 塞回 PTT 的准入相位。
    old = "    if (bKeyPressed && phase == DPH_READY)"
    assert s.count(old) == 1, s.count(old)
    s = s.replace(old, "    if (false && bKeyPressed && phase == DPH_READY)   /* [NEG-CTRL-14] */")
    old2 = "    if ((phase == DPH_RUN || phase == DPH_EXT"
    assert s.count(old2) == 1, s.count(old2)
    s = s.replace(old2, "    if ((phase == DPH_RUN || phase == DPH_READY || phase == DPH_EXT   /* [NEG-CTRL-14] */")
    what = '拆掉就绪页 PTT 的闸门并把 DPH_READY 塞回准入（交接-31：就绪页又能发射）'

elif ctrl == '15':
    # 破坏交接-31 的第三条：RUN 时间轴的方向。▲ 改回 +1（往前）。
    old = "    int32_t dir = (Key == KEY_UP) ? -1 : 1;"
    assert s.count(old) == 1, s.count(old)
    s = s.replace(old, "    int32_t dir = (Key == KEY_UP) ? 1 : -1;   /* [NEG-CTRL-15] */")
    what = '把 ▲▼ 的方向反回去（▲ 又变成往前推时间轴）'

else:
    raise SystemExit('未知对照编号：' + ctrl)

assert s != orig
io.open(path, 'w', encoding='utf-8', newline='').write(s)
print('== 破坏方式：' + what)
PY

echo "== 补丁副本：$NEG/doppler.c（固件源码未动）"
echo "== 与原件差异："
diff -u "$APP/app/doppler.c" "$NEG/doppler.c" | sed -n '1,40p' || true
echo

case "$CTRL" in
    1) RUN=("s1") ;;
    2) RUN=("s2") ;;
    3) RUN=("s3") ;;
    4) RUN=("s4") ;;
    5) RUN=("s1") ;;
    6) RUN=("s5") ;;
    7) RUN=("s6") ;;
    8) RUN=("s6") ;;
    9) RUN=("s7") ;;
    12) RUN=("s5") ;;
    13) RUN=("s7") ;;
    14) RUN=("s5") ;;
    15) RUN=("s7") ;;
esac
[ -n "$SCEN" ] && RUN=("$SCEN")

SIM_OUT="$NEG" DOPPLER_SRC="$NEG/doppler.c" bash "$SIM/build.sh" "${RUN[0]}"
