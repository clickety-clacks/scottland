#!/bin/bash
# E9 footprint soak (a few minutes): terminal retitle/redraw churn, in a caller-owned isolated session.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set an isolated headless test directory}"
[[ ! -f $SCOTTLAND_HEADLESS_DIR/pid ]] || { echo 'headless directory occupied' >&2; exit 1; }
artifacts=$SCOTTLAND_HEADLESS_DIR.heap-growth-artifacts
mkdir -p "$artifacts"
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log" 2>/dev/null || true
  tests/headless.sh stop >/dev/null 2>&1
}
trap cleanup EXIT
tests/headless.sh start
tests/headless.sh run python3 "$PWD/tests/heap-growth-test.py" "$artifacts" "$SCOTTLAND_HEADLESS_DIR/compositor.pid"
