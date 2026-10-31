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
#     设备，但要求 10 s 栅格的插值误差、64 KiB 装 30 条 1500 s 的容量、
#     以及"表区里不是我们的东西就拒擦"这几件事同时成立——任何一条退化，
#     后果都是电台里某个人的语音包或别人的固件被擦掉。
#     表区挑在 0x162000（48 个 app 槽声明区末尾、汉化字库前 88 KiB 空档），
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

step "1/9 三端一致性（固件 doppler.h/uart.c ↔ PC doppler_core.py ↔ App Freqs/Model/RadioSession）"
python 多普勒工具/verify_three_way.py
tally "$?"

step "2/9 链路契约（假电台：擦/写/读/对时的字节数、CRC、回显）"
python 多普勒工具/verify_link_ask.py | tail -6
tally "${PIPESTATUS[0]}"

step "3/9 布局一致性（固件 .h/.c ↔ PC doppler_core.py ↔ App PassCalc/RadioLink）"
python 多普勒工具/verify_layout.py
tally "$?"

step "4/9 帧校验（回包 CRC 三端一致 + 别退化成恒 0xFFFF）"
python 多普勒工具/verify_crc.py | tail -12
tally "${PIPESTATUS[0]}"

step "5/9 数据层（TLE 台账 / 频率表 / 资产一致性）"
python 多普勒工具/verify_data.py | tail -18
tally "${PIPESTATUS[0]}"

step "6/9 解算层（Kotlin SGP4/SDP4 对 python-sgp4 真值）"
bash 多普勒工具/verify_sgp4.sh 2>/dev/null | grep -av '^warning' | tail -8
tally "${PIPESTATUS[0]}"

step "7/9 变化率层（多普勒爬升速度是否符合现实）"
python 多普勒工具/verify_doppler_rate.py | tail -8
tally "${PIPESTATUS[0]}"

step "8/9 表区容量与归属（栅格精度 / 64 KiB 容量 / 拒擦别人的数据 / 放行自己的地）"
python 多普勒工具/verify_table_fit.py | tail -14
tally "${PIPESTATUS[0]}"

if [ "${1:-}" = "--fast" ]; then
  step "9/9 显示层：跳过（--fast）"
else
  step "9/9 显示层（App 实算 vs Python 独立算，需模拟器 adb）"
  python 多普勒工具/verify_display.py | tail -6
  tally "${PIPESTATUS[0]}"
fi

printf '\n==================================\n'
if [ "$fail" != 0 ]; then
  echo "有失败项，见上"
elif [ "$warn" != 0 ]; then
  echo "没有失败项，但有告警（P1/P2）"
else
  echo "全部通过"
fi
exit "$fail"
