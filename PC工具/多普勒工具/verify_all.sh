#!/usr/bin/env bash
# 一键回归：数据层 + 解算层 + 显示层
#   bash 多普勒工具/verify_all.sh          # 三层全跑（显示层需要模拟器/真机连着）
#   bash 多普勒工具/verify_all.sh --fast   # 跳过显示层
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONIOENCODING=utf-8

fail=0
warn=0
step() { printf '\n=== %s ===\n' "$1"; }

step "1/3 数据层（TLE 台账 / 频率表 / 资产一致性）"
python 多普勒工具/verify_data.py | tail -18
rc=${PIPESTATUS[0]}
case "$rc" in
  0) echo "→ OK" ;;
  2) echo "→ 有告警（rc=2）"; warn=1 ;;
  *) echo "→ 失败（rc=$rc）"; fail=1 ;;
esac

step "2/3 解算层（Kotlin SGP4/SDP4 对 python-sgp4 真值）"
bash 多普勒工具/verify_sgp4.sh 2>/dev/null | grep -v '^warning' | tail -8
[ "${PIPESTATUS[0]}" = 0 ] && echo "→ OK" || { echo "→ 失败"; fail=1; }

if [ "${1:-}" = "--fast" ]; then
  step "3/3 显示层：跳过（--fast）"
else
  step "3/3 显示层（App 实算 vs Python 独立算）"
  python 多普勒工具/verify_display.py | tail -6
  [ "${PIPESTATUS[0]}" = 0 ] && echo "→ OK" || { echo "→ 失败"; fail=1; }
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
