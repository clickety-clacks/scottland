#!/bin/bash
# Reload rehearsal for spread (final.md section 4): swap the plugin with a solve in flight,
# headless.  SCOTTLAND_HEADLESS_DIR=$PWD/build/hl-sr tests/spread-reload-test.sh [ARTIFACTS]
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set a fresh headless directory under this checkout build/}"
[[ ! -e $SCOTTLAND_HEADLESS_DIR ]] || { echo 'headless directory occupied' >&2; exit 1; }
artifacts=${1:-$PWD/build/spread-reload-evidence}
mkdir -p "$artifacts"
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log" 2>/dev/null || true
  tests/headless.sh stop
  rm -rf "$SCOTTLAND_HEADLESS_DIR" "$artifacts"/libscottland-reload-*.so
}
trap cleanup EXIT
SCOTTLAND_TEST_OUTPUTS=1 tests/headless.sh start
tests/headless.sh run python3 -u tests/spread-reload-test.py "$artifacts" "$PWD/build/libscottland.so" | tee "$artifacts/spread-reload-test.log"
exit "${PIPESTATUS[0]}"
