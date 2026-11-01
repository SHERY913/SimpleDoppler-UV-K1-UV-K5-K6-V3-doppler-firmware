#!/usr/bin/env bash
# 宿主端"假电台"：把 doppler.c 原样编到 PC 上，硬件全部打桩成事件记录器。
#
#   bash 多普勒/tests/sim/build.sh             # 编译并跑 m0
#   bash 多普勒/tests/sim/build.sh s1          # 指定场景
#
# 不碰固件源码：只读 App/app/doppler.c 与 App 下的真实头文件，
# -I tests/sim/include 抢在 -I App 前面，顶替 3 个引 MCU 的头 + audio.h。
set -euo pipefail

SIM="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"     # .../多普勒/tests/sim
ROOT="$(cd "$SIM/../.." && pwd)"                        # .../多普勒
FW="$ROOT/固件源码/uvk1-v6.3.0"
APP="$FW/App"
OUT="${SIM_OUT:-$SIM/out}"
SCEN="${1:-m0}"
TABLE="${2:-$SIM/data/table_sim.bin}"

mkdir -p "$OUT"

# ---- all 先把两个静态守卫跑一遍（它们不需要编译器，但一样会红）----
#   为什么放在这里：E1 的另一半（app.c 派发顺序）仿真编不到 app.c，E3/E4 是
#   几何问题而不是事件流问题 —— 这两类都只能静态查，却又是真机翻车的同一批。
if [ "$SCEN" = "all" ]; then
    echo "== 静态守卫 1/2：版面重叠 + 内容要求（E3/E4）"
    PYTHONIOENCODING=utf-8 python "$SIM/check_layout_overlap.py"
    echo "== 静态守卫 2/2：app.c 派发层（E1/E2）"
    PYTHONIOENCODING=utf-8 python "$SIM/check_dispatch.py"
fi

# ---- 宿主 C 编译器：zig cc（本机没有 gcc/clang/MSVC） ----
if [ -z "${ZIG:-}" ]; then
    if [ -f /tmp/zigpath ]; then
        ZIG="$(cat /tmp/zigpath)"
    else
        ZIG="$(python -c 'import os,ziglang;print(os.path.join(os.path.dirname(ziglang.__file__),"zig.exe"))')"
    fi
fi
[ -x "$ZIG" ] || { echo "找不到 zig：$ZIG" >&2; exit 1; }

# ---- 与真机完全同一组 -D（.defs_full.txt 就是 .gfw.py 用的那份） ----
DEFS=$(sed 's/^/-D/' "$FW/.defs_full.txt" | tr '\n' ' ')

CFLAGS=(-std=gnu11 -O1 -g -Wall -Wno-unused-parameter -Wno-date-time
        $DEFS
        -include fw_build_defs.h
        -I "$SIM/include"     # 抢在上游前面：driver/gpio.h、scheduler.h、apps/app_overlay.h、audio.h
        -I "$APP"
        -I "$FW")

echo "== CFLAGS: ${CFLAGS[*]}" | cut -c1-200

# 被编的源文件：默认就是固件里那一份，原样、零改动。
# DOPPLER_SRC 只为阳性对照用（放一份打了补丁的副本在 out/neg 下），
# 固件源码一个字节都不动 —— 仓库里有未提交改动，git checkout 是禁的。
SRC="${DOPPLER_SRC:-$APP/app/doppler.c}"
echo "== 编译 doppler.c：$SRC"
"$ZIG" cc "${CFLAGS[@]}" -c "$SRC" -o "$OUT/doppler.o"

echo "== 编译桩"
for f in sim_hw.c sim_main.c; do
    "$ZIG" cc "${CFLAGS[@]}" -c "$SIM/src/$f" -o "$OUT/${f%.c}.o"
done

echo "== 链接"
"$ZIG" cc "${CFLAGS[@]}" "$OUT/doppler.o" "$OUT/sim_hw.o" "$OUT/sim_main.o" \
    -o "$OUT/doppler_sim.exe"

echo "== 运行场景 $SCEN"
if [ "$SCEN" = "script" ]; then
    # 场景在 $1，脚本文件在 $2，表镜像是 $3
    SCRIPT="${2:?usage: build.sh script <脚本文件> [表镜像]}"
    SCRIPT_TABLE="${3:-$SIM/data/table_sim.bin}"
    "$OUT/doppler_sim.exe" script "$SCRIPT" "$SCRIPT_TABLE"
elif [ -f "$TABLE" ]; then
    "$OUT/doppler_sim.exe" "$SCEN" "$TABLE"
else
    "$OUT/doppler_sim.exe" "$SCEN"
fi
