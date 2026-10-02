#!/bin/bash
# Only the caller's isolated headless session on the test host; never a physical display.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set an isolated headless test directory}"
[[ ! -f $SCOTTLAND_HEADLESS_DIR/pid ]] || { echo 'headless directory occupied' >&2; exit 1; }
artifacts=$SCOTTLAND_HEADLESS_DIR.hint-style-artifacts
mkdir -p "$artifacts"
tests/headless.sh start --widgets
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log" || true
  tests/headless.sh stop >/dev/null 2>&1
}
trap cleanup EXIT
python3 tests/hint-style-test.py "$artifacts"
