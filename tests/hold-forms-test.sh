#!/bin/bash
# Hold forms of every window-drag gesture, and the hold ring (WK35/WK36/WK39), with real input.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set a fresh headless directory under this checkout build/}"
[[ ! -e $SCOTTLAND_HEADLESS_DIR ]] || { echo 'headless directory occupied' >&2; exit 1; }
artifacts=${1:-$PWD/build/hold-forms-evidence}
mkdir -p "$artifacts"
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log" 2>/dev/null || true
  tests/headless.sh stop
}
trap cleanup EXIT
tests/headless.sh start --widgets
tests/headless.sh run python3 -u tests/hold-forms-test.py "$artifacts" | tee "$artifacts/results.log"
exit "${PIPESTATUS[0]}"
