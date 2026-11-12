#!/usr/bin/env bash
# 用 python-sgp4 的真值核对 Kotlin 的 SGP4/SDP4（不依赖 Gradle）
# 用法：bash 多普勒工具/verify_sgp4.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

J="${JAVA_HOME:-/c/Users/13766/.jdk/jdk-21.0.2}/bin/java"
[ -x "$J" ] || J="/c/Users/13766/.jdk/jdk-21.0.2/bin/java"

G="$HOME/.gradle/caches/modules-2/files-2.1"
pick() { find "$G/$1" -name "$2" 2>/dev/null | head -1; }

KC=$(pick org.jetbrains.kotlin/kotlin-compiler-embeddable "kotlin-compiler-embeddable-*.jar")
ST=$(pick org.jetbrains.kotlin/kotlin-stdlib/2.0.20 "kotlin-stdlib-2.0.20.jar")
TR=$(pick org.jetbrains.intellij.deps/trove4j "trove4j-*.jar")
SR=$(pick org.jetbrains.kotlin/kotlin-script-runtime "kotlin-script-runtime-*.jar")
CO=$(pick org.jetbrains.kotlinx/kotlinx-coroutines-core-jvm/1.8.1 "kotlinx-coroutines-core-jvm-*.jar")
AN=$(pick org.jetbrains/annotations "annotations-23.0.0.jar")

w() { cygpath -w "$1"; }
OUT="$ROOT/.gtmp/kcheck/out"
mkdir -p "$OUT"

"$J" -cp "$(w "$KC");$(w "$ST");$(w "$TR");$(w "$SR");$(w "$CO");$(w "$AN")" \
  org.jetbrains.kotlin.cli.jvm.K2JVMCompiler -nowarn \
  -cp "$(w "$ST")" -d "$(w "$OUT")" \
  "$(w "$ROOT/../../doppler-ng/app/src/main/java/com/f4hwn/doppler2/orbit/Sgp4.kt")" \
  "$(w "$ROOT/多普勒工具/CheckSgp4.kt")"

cd "$ROOT/多普勒工具"
"$J" -cp "$(w "$OUT");$(w "$ST")" CheckSgp4Kt _sdp4_truth.tsv
