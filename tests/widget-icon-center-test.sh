#!/bin/bash
# WG10/WG16: the default card's icon is centered on its visible body (pixels), headless only.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set a fresh isolated headless directory under build/}"
[[ ! -f $SCOTTLAND_HEADLESS_DIR/pid ]] || { echo 'headless directory occupied' >&2; exit 1; }
art=$SCOTTLAND_HEADLESS_DIR.icon-center
mkdir -p "$art"
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$art/wayfire.log" 2>/dev/null || true
  tests/headless.sh stop
}
trap cleanup EXIT
tests/headless.sh start --widgets
tests/headless.sh run python3 tests/widget-icon-center-test.py "$art"
