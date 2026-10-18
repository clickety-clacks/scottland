#!/bin/bash
# A hint hold on a double-tap's second press, by real keys (WK15, WK39; ruling 10-07).
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set a fresh headless directory under this checkout build/}"
[[ ! -e $SCOTTLAND_HEADLESS_DIR ]] || { echo 'headless directory occupied' >&2; exit 1; }
artifacts=${1:-$PWD/build/hint-hold-double-tap-evidence}
mkdir -p "$artifacts"
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log" 2>/dev/null || true
  tests/headless.sh stop
}
trap cleanup EXIT
SCOTTLAND_TEST_OUTPUTS=1 tests/headless.sh start --widgets
tests/headless.sh run python3 -u tests/hint-hold-double-tap-test.py "$artifacts" | tee "$artifacts/results.log"
exit "${PIPESTATUS[0]}"
