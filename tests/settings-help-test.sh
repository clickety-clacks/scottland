#!/bin/bash
# S1-S19 in an isolated two-output session; never reuse or change a running session.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set a fresh isolated headless test directory}"
[[ ! -f $SCOTTLAND_HEADLESS_DIR/pid ]] || { echo 'headless runtime occupied' >&2; exit 1; }
export SCOTTLAND_TEST_OUTPUTS=2
mkdir -p build/settings-help-evidence
tests/headless.sh start
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" build/settings-help-evidence/wayfire.log
  tests/headless.sh stop
}
trap cleanup EXIT
tests/headless.sh run python3 tests/settings-help-test.py | tee build/settings-help-evidence/results.log
