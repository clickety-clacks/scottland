#!/bin/bash
# P12: a completely covered window peeks, whatever happened before (real input, headless):
#   SCOTTLAND_HEADLESS_DIR=$PWD/build/hl-peek-miss tests/peek-miss-test.sh [ARTIFACTS] [CASE ...]
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set a fresh headless directory under this checkout build/}"
[[ ! -e $SCOTTLAND_HEADLESS_DIR ]] || { echo 'headless directory occupied' >&2; exit 1; }
artifacts=${1:-$PWD/build/peek-miss-evidence}; shift || true
mkdir -p "$artifacts"
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log" 2>/dev/null || true
  tests/headless.sh stop
  rm -rf "$SCOTTLAND_HEADLESS_DIR"
}
trap cleanup EXIT
SCOTTLAND_TEST_OUTPUTS=1 tests/headless.sh start --widgets
tests/headless.sh run python3 -u tests/peek-miss-test.py "$artifacts" "$@" | tee "$artifacts/peek-miss-test.log"
exit "${PIPESTATUS[0]}"
