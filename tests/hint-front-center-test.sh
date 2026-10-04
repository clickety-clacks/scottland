#!/bin/bash
# WK31: an uncovered window's hint sits at its exact center (real Alt, letters and drags).
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set an isolated directory under build/}"
[[ ! -f $SCOTTLAND_HEADLESS_DIR/pid ]] || exit 1
artifacts=$SCOTTLAND_HEADLESS_DIR.hint-front-center-artifacts
mkdir -p "$artifacts"
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log" 2>/dev/null || true
  tests/headless.sh stop >/dev/null 2>&1
}
trap cleanup EXIT
tests/headless.sh start
python3 tests/hint-front-center-test.py "$artifacts"
