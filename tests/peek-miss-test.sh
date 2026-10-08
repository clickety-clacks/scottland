#!/bin/bash
# P12: a completely covered window peeks, whatever happened before (real input, headless):
#   SCOTTLAND_HEADLESS_DIR=$PWD/build/hl-peek-miss tests/peek-miss-test.sh [ARTIFACTS] [CASE ...]
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set a fresh headless directory under this checkout build/}"
build_dir=$(realpath -m -- "$PWD/build")
headless_dir=$(realpath -m -- "$SCOTTLAND_HEADLESS_DIR")
[[ $headless_dir == "$build_dir"/* && $headless_dir != "$build_dir" ]] || {
  echo 'headless directory must be a child of this checkout build/' >&2; exit 1;
}
[[ ! -e $SCOTTLAND_HEADLESS_DIR && ! -L $SCOTTLAND_HEADLESS_DIR && \
   ! -e $headless_dir && ! -L $headless_dir ]] || {
  echo 'headless directory occupied; refusing to reuse it' >&2; exit 1;
}
SCOTTLAND_HEADLESS_DIR=$headless_dir
export SCOTTLAND_HEADLESS_DIR
artifacts=${1:-$PWD/build/peek-miss-evidence}; shift || true
mkdir -p "$artifacts"
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log" 2>/dev/null || true
  tests/headless.sh stop
}
trap cleanup EXIT
SCOTTLAND_TEST_OUTPUTS=1 tests/headless.sh start --widgets
tests/headless.sh run python3 -u tests/peek-miss-test.py "$artifacts" "$@" | tee "$artifacts/peek-miss-test.log"
exit "${PIPESTATUS[0]}"
