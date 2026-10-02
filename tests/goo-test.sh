#!/bin/bash
# Run the Goo regression with this checkout's defaults and helpers, never a live display.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set a fresh isolated headless test directory}"
[[ ! -e $SCOTTLAND_HEADLESS_DIR ]] || { echo 'headless directory occupied' >&2; exit 1; }
mkdir -p build/goo-evidence
tests/headless.sh start --widgets
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" build/goo-evidence/wayfire.log
  tests/headless.sh stop
}
trap cleanup EXIT
tests/headless.sh run python3 tests/goo-test.py | tee build/goo-evidence/results.log
