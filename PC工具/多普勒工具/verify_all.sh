#!/usr/bin/env bash
# 一键回归：一致性 + 数据 + 解算 + 变化率 + 显示
#   bash 多普勒工具/verify_all.sh          # 五层全跑（显示层需要模拟器/真机连着）
#   bash 多普勒工具/verify_all.sh --fast   # 跳过显示层（只需 python，不需要设备）
#
# 顺序是有意的：一致性层最先跑，因为它不要编译器、不要设备、只要源码在，
# 而它抓的正是本项目复发最多的那一类 bug（三端各自写一遍同一个约定）。
# 变化率层放在解算层后面：解算对得上位置，不代表对得上"每秒爬多少 Hz"，
# 而后者才是操作员真正听到的东西（单位错一位、时间轴按分钟算，曲线照样好看）。
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

step "1/5 一致性（固件 .h/.c ↔ PC doppler_core.py ↔ App PassCalc/RadioLink）"
python 多普勒工具/verify_layout.py
tally "$?"

step "2/5 数据层（TLE 台账 / 频率表 / 资产一致性）"
python 多普勒工具/verify_data.py | tail -18
tally "${PIPESTATUS[0]}"

step "3/5 解算层（Kotlin SGP4/SDP4 对 python-sgp4 真值）"
bash 多普勒工具/verify_sgp4.sh 2>/dev/null | grep -av '^warning' | tail -8
tally "${PIPESTATUS[0]}"

step "4/5 变化率层（多普勒爬升速度是否符合现实）"
python 多普勒工具/verify_doppler_rate.py | tail -8
tally "${PIPESTATUS[0]}"

if [ "${1:-}" = "--fast" ]; then
  step "5/5 显示层：跳过（--fast）"
else
  step "5/5 显示层（App 实算 vs Python 独立算，需模拟器 adb）"
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
