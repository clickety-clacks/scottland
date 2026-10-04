#!/bin/bash
set -euo pipefail
cd "$(dirname -- "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set a unique headless directory under this checkout build/}"
[[ ! -f $SCOTTLAND_HEADLESS_DIR/pid ]] || { echo 'headless runtime occupied' >&2; exit 1; }
export SCOTTLAND_WIDGET_PATH=$PWD/tests/widgets
artifacts=$PWD/build/rail-make-room-pause
mkdir -p "$artifacts"
tests/headless.sh start --widgets
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log" 2>/dev/null || true
  tests/headless.sh stop
}
trap cleanup EXIT
tests/headless.sh run python3 tests/rail-make-room-pause-test.py | tee "$artifacts/test.log"
