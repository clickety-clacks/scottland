#!/usr/bin/env bash
# Every goo shader variant compiles in both GLSL dialects (no runtime text surgery is left to fail
# at startup). Needs glslangValidator.
set -euo pipefail
repo=$(cd -- "$(dirname -- "$0")/.." && pwd)
out=$repo/build/goo-shader-variants
rm -rf "$out"; mkdir -p "$out"
c++ -std=c++17 -Wall -Wextra -Werror -O1 -I"$repo/core/plugin/src" \
  "$repo/tests/goo-shader-variants-test.cpp" -o "$out/dump"
mapfile -t variants < <("$out/dump" "$out")
passed=0 failed=0
check() {  # name, then a command that must succeed
  local name=$1; shift
  if "$@" >"$out/last.log" 2>&1; then echo "PASS $name"; passed=$((passed+1))
  else echo "FAIL $name"; sed 's/^/    /' "$out/last.log" | head -20; failed=$((failed+1)); fi
}
for dialect in es100 es300; do
  check "vertex compiles ($dialect)" glslangValidator "$out/vertex.$dialect.vert"
done
for v in "${variants[@]}"; do
  check "$v compiles" glslangValidator "$out/$v.frag"
done
echo "RESULT $passed passed, $failed failed"
((failed == 0))
