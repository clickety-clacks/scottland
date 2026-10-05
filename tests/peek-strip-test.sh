#!/bin/bash
# WK13/P12/P13: the peeking strip with real input (strips measured from screenshots).
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set an isolated directory under build/}"
[[ ! -f $SCOTTLAND_HEADLESS_DIR/pid ]] || exit 1
artifacts=$SCOTTLAND_HEADLESS_DIR.peek-strip-artifacts
mkdir -p "$artifacts"
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log" 2>/dev/null || true
  tests/headless.sh stop >/dev/null 2>&1
}
trap cleanup EXIT
tests/headless.sh start
python3 tests/peek-strip-test.py "$artifacts"
