#!/bin/bash
# Spread and solo with real stipc input in a headless session (docs/spread.md):
#   SCOTTLAND_HEADLESS_DIR=$PWD/build/hl-spread tests/spread-test.sh [ARTIFACTS]
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set a fresh headless directory under this checkout build/}"
[[ ! -e $SCOTTLAND_HEADLESS_DIR ]] || { echo 'headless directory occupied' >&2; exit 1; }
artifacts=${1:-$PWD/build/spread-evidence}
mkdir -p "$artifacts"
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log" 2>/dev/null || true
  tests/headless.sh stop
  rm -rf "$SCOTTLAND_HEADLESS_DIR"
}
trap cleanup EXIT
SCOTTLAND_TEST_OUTPUTS=1 tests/headless.sh start
tests/headless.sh run python3 -u tests/spread-test.py "$artifacts" | tee "$artifacts/spread-test.log"
exit "${PIPESTATUS[0]}"
