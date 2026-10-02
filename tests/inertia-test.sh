#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set an isolated headless test directory}"
[[ ! -f $SCOTTLAND_HEADLESS_DIR/pid ]] || { echo 'headless runtime occupied' >&2; exit 1; }
artifacts=$SCOTTLAND_HEADLESS_DIR.inertia-artifacts
mkdir -p "$artifacts"
tests/headless.sh start --widgets
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log"
  tests/headless.sh stop
}
trap cleanup EXIT
tests/headless.sh run python3 tests/inertia-test.py "$artifacts"
