#!/usr/bin/env bash
# 一键回归：三端一致性 + 链路契约 + 布局 + 帧校验 + 数据 + 解算 + 变化率 + 表区容量 + 显示
#   bash 多普勒工具/verify_all.sh          # 九层全跑（显示层需要模拟器/真机连着）
#   bash 多普勒工具/verify_all.sh --fast   # 跳过显示层（只需 python，不需要设备）
#
# 顺序是有意的：
#   1 三端一致性最先跑，因为它不要编译器、不要设备、只要源码在，
#     而它抓的正是本项目复发最多的那一类 bug（三端各自写一遍同一个约定）。
#   2 链路契约层用假电台跑一遍擦/写/读/对时，抓的是"字节数差 4 个"这类
#     只有接上真机才会暴露、而真机上又不该反复试的错。
#   3 布局层、4 帧校验层同样不要设备。
#   6/7 解算与变化率：解算对得上位置，不代表对得上"每秒爬多少 Hz"，
#     而后者才是操作员真正听到的东西（单位错一位、时间轴按分钟算，曲线照样好看）。
#   8 表区容量与归属：这一层是"绝不写到别人的数据上"的守门人。它不需要
#     设备，但要求 10 s 栅格的插值误差、"这张表按真实字节装得下"（条数不再
#     钉死，10 s 与 5 s 两档实测）、4 bit 本地日偏移越界必须被拒、
#     以及"表区里不是我们的东西就拒擦"这几件事同时成立——任何一条退化，
#     后果都是电台里某个人的语音包或别人的固件被擦掉。
#     表区挑在 0x162000（48 个 app 槽声明区末尾、到汉化字库 0x180000 还留 56 KiB），
#     原厂镜像那里躺着语音包尾巴，所以第一次写表要人工过一道归属确认。
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONIOENCODING=utf-8

fail=0
warn=0
step() { printf '\n=== %s ===\n' "$1"; }
tally() {  # rc: 0 通过 / 2 只有告警 / 其它 失败
  case "$1" in
    0) echo "→ OK" ;;
    2) echo "→ 有告警（rc=2）"; warn=1 ;;
    *) echo "→ 失败（rc=$1）"; fail=1 ;;
  esac
}

step "1/11 三端一致性（固件 doppler.h/uart.c ↔ PC doppler_core.py ↔ App Freqs/Model/RadioSession）"
python 多普勒工具/verify_three_way.py
tally "$?"

step "2/11 链路契约（假电台：擦/写/读/对时的字节数、CRC、回显）"
python 多普勒工具/verify_link_ask.py | tail -6
tally "${PIPESTATUS[0]}"

step "3/11 布局一致性（固件 .h/.c ↔ PC doppler_core.py ↔ App PassCalc/RadioLink）"
python 多普勒工具/verify_layout.py
tally "$?"

step "4/11 帧校验（回包 CRC 三端一致 + 别退化成恒 0xFFFF）"
python 多普勒工具/verify_crc.py | tail -12
tally "${PIPESTATUS[0]}"

step "5/11 数据层（TLE 台账 / 频率表 / 资产一致性 / 转发器选择）"
python 多普勒工具/verify_data.py | tail -18
rc=${PIPESTATUS[0]}
# 频点对了不等于写表写对了：同一颗星有多条转发器时，写进表头的必须是
# 界面上选中的那条（ISS：中继 437.800 / SSTV 437.550）。
python 多普勒工具/verify_transponder.py | tail -8
rc2=${PIPESTATUS[0]}
if [ "$rc2" != 0 ]; then
  rc=1
elif [ "$rc" = 0 ] && [ "$rc2" = 2 ]; then
  rc=2
fi
tally "$rc"

step "6/11 解算层（Kotlin SGP4/SDP4 对 python-sgp4 真值）"
bash 多普勒工具/verify_sgp4.sh 2>/dev/null | grep -av '^warning' | tail -8
tally "${PIPESTATUS[0]}"

step "7/11 变化率层（多普勒爬升速度是否符合现实）"
python 多普勒工具/verify_doppler_rate.py | tail -8
tally "${PIPESTATUS[0]}"

step "8/11 表区容量与归属（栅格精度 / 64 KiB 容量 / 拒擦别人的数据 / 放行自己的地）"
python 多普勒工具/verify_table_fit.py | tail -14
tally "${PIPESTATUS[0]}"

if [ "${1:-}" = "--fast" ]; then
  step "9/11 显示层：跳过（--fast）"
else
  step "9/11 显示层（App 实算 vs Python 独立算，需模拟器 adb）"
  python 多普勒工具/verify_display.py | tail -6
  tally "${PIPESTATUS[0]}"
fi

# 唯一真源守卫：名单外的星不许出现在任何一处（生成链已过滤，这里守回归）。
step "10/11 业余名单守卫（三处 NORAD 集合 ⊆ 数据/amateur_allow.json）"
python 多普勒工具/verify_amateur.py | tail -12
tally "${PIPESTATUS[0]}"

# 自定义标称频率：下拉最后一项「自定义…」算出来的那条要真的能落表。抓的是
# 「同一颗星的频率在三个地方各写一遍」的老毛病——退回老字段、打错一个字被静默
# 当成 0（up10=0 就变成仅接收）、tone 跟着别的星走，三种都只在落表那一刻才暴露。
step "11/11 自定义标称频率（自定义… 条目落表后 freq10/up10/ctcss10 与输入一致）"
python 多普勒工具/verify_custom_trans.py | tail -12
tally "${PIPESTATUS[0]}"

printf '\n==================================\n'
if [ "$fail" != 0 ]; then
  echo "有失败项，见上"
elif [ "$warn" != 0 ]; then
  echo "没有失败项，但有告警（P1/P2）"
else
  echo "全部通过"
fi
exit "$fail"
