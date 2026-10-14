#!/bin/bash
# File wins (docs/rulings.md 10-07) in an isolated headless session; never reuses or changes a
# running session.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set a fresh isolated headless test directory}"
[[ ! -f $SCOTTLAND_HEADLESS_DIR/pid ]] || { echo 'headless runtime occupied' >&2; exit 1; }
mkdir -p build/settings-file-wins-evidence
tests/headless.sh start
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" build/settings-file-wins-evidence/wayfire.log
  tests/headless.sh stop
}
trap cleanup EXIT
tests/headless.sh run python3 tests/settings-file-wins-test.py "$SCOTTLAND_HEADLESS_DIR" | tee build/settings-file-wins-evidence/results.log
