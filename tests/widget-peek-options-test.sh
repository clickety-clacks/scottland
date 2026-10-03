#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set a fresh isolated headless directory under build/}"
art=$PWD/build/widget-peek-options-evidence
mkdir -p "$art"
export SCOTTLAND_WIDGET_PATH=$PWD/tests/widgets
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$art/wayfire.log" 2>/dev/null || true
  tests/headless.sh stop
}
trap cleanup EXIT
tests/headless.sh start --widgets
tests/headless.sh run python3 tests/widget-peek-options-test.py | tee "$art/results.log"
