#!/bin/bash
# Isolated pixel regression for the Window-mode tint layer; never attaches to a live display.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set a unique headless directory under this checkout build/}"
[[ ! -e $SCOTTLAND_HEADLESS_DIR ]] || { echo 'headless directory must be fresh' >&2; exit 1; }
headless_dir=$(realpath -m -- "$SCOTTLAND_HEADLESS_DIR")
build_dir=$(realpath -m -- "$PWD/build")
[[ $headless_dir == "$build_dir/"* ]] || {
  echo 'SCOTTLAND_HEADLESS_DIR must be under this checkout build/' >&2
  exit 1
}
art=$PWD/build/window-mode-tint-pixel-results
mkdir -p "$art"
export SCOTTLAND_TEST_GOO=1
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$art/wayfire.log" 2>/dev/null || true
  tests/headless.sh stop >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM
tests/headless.sh start --widgets
tests/headless.sh run python3 tests/window-mode-tint-pixel-test.py "$art" | tee "$art/results.log"
