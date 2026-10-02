#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set an isolated directory under build/}"
[[ ! -e $SCOTTLAND_HEADLESS_DIR ]] || { echo "test directory already exists" >&2; exit 1; }
export SCOTTLAND_TEST_OUTPUTS=1
artifacts=$SCOTTLAND_HEADLESS_DIR.results
mkdir -p "$artifacts"
tests/headless.sh start --widgets
cleanup() {
    cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log"
    tests/headless.sh stop
}
trap cleanup EXIT
tests/headless.sh run python3 tests/live-drag-test.py "$artifacts"

cleanup
trap - EXIT
export SCOTTLAND_TEST_OUTPUTS=2
artifacts=$SCOTTLAND_HEADLESS_DIR.outputs-results
mkdir -p "$artifacts"
tests/headless.sh start --widgets
trap cleanup EXIT
tests/headless.sh run python3 tests/live-drag-outputs-test.py "$artifacts"
