#!/usr/bin/env bash
# Every goo shader variant compiles in both GLSL dialects, and each variant switch changes what
# it should (no runtime text surgery is left to fail at startup). Needs glslangValidator.
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
  glslangValidator -E "$out/$v.frag" > "$out/$v.pre" 2>/dev/null || true
done
pre() { tr -d ' \n' < "$out/$1.pre"; }
for dialect in es100 es300; do
  tex=$([[ $dialect == es300 ]] && echo 'texture(' || echo 'texture2D(')
  check "render samples the backdrop and the dye ($dialect)" grep -qF "${tex}uBackground,bgUV)" <(pre render.$dialect)
  for v in intrinsic refraction $([[ $dialect == es300 ]] && echo cache_both); do
    check "$v holds neither backdrop nor dye ($dialect)" bash -c "! grep -qF '${tex}uBackground,bgUV)' <(tr -d ' \n' < '$out/$v.$dialect.pre') && ! grep -qF '${tex}uDyeTex,uv)' <(tr -d ' \n' < '$out/$v.$dialect.pre')"
    check "$v shares the surface's rim tint ($dialect)" grep -qF 'rim*.22*rimTint+cloud' <(pre $v.$dialect)
  done
  check "refraction writes the cached parameters ($dialect)" grep -qE '(gl_FragColor|goo_color)=cacheParams' <(pre refraction.$dialect)
  check "intrinsic writes clamped intrinsic light ($dialect)" grep -qE '(gl_FragColor|goo_color)=vec4\(clamp\(color,0\.,1\.\),a\);' <(pre intrinsic.$dialect)
  for s in field mask wave dye render; do
    check "${s}_fast is the no-overlap, no-controls specialization ($dialect)" grep -qF 'constfloatuOverlap=0.,uControls=0.;' <(pre ${s}_fast.$dialect)
    check "$s keeps the general declarations ($dialect)" bash -c "! grep -qF 'constfloatuOverlap=0.' <(tr -d ' \n' < '$out/$s.$dialect.pre')"
  done
done
check "cache_both writes both caches in one pass" grep -qF 'goo_params=cacheParams' <(pre cache_both.es300)
echo "RESULT $passed passed, $failed failed"
((failed == 0))
