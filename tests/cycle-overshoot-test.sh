#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set a fresh isolated directory under build/}"
[[ ! -e $SCOTTLAND_HEADLESS_DIR ]] || { echo 'test directory occupied' >&2; exit 1; }
artifacts=$SCOTTLAND_HEADLESS_DIR.cycle-artifacts
mkdir -p "$artifacts"
cleanup() {
    cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log" 2>/dev/null || true
    tests/headless.sh stop
}
trap cleanup EXIT
export SCOTTLAND_TEST_OUTPUTS=2
tests/headless.sh start --widgets
tests/headless.sh run python3 tests/cycle-overshoot-test.py "$artifacts"
