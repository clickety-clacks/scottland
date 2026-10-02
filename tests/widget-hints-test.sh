#!/bin/bash
# WK26: real input in a caller-owned isolated session, with artifacts beside it.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set an isolated headless test directory}"
[[ ! -f $SCOTTLAND_HEADLESS_DIR/pid ]] || { echo 'headless directory occupied' >&2; exit 1; }
artifacts=$SCOTTLAND_HEADLESS_DIR.widget-hints-artifacts
mkdir -p "$artifacts"
export SCOTTLAND_WIDGET_PATH=${SCOTTLAND_WIDGET_PATH:-$PWD/tests/widgets}
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log" 2>/dev/null || true
  tests/headless.sh stop >/dev/null 2>&1
}
trap cleanup EXIT
tests/headless.sh start --widgets
python3 tests/widget-hints-test.py "$artifacts"
