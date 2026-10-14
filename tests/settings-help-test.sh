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
# Do not keep a tee process resident beside the headless compositor and its clients. The
# task-owned headless service has a finite PID/thread cap, and GTK may need a gmain thread
# while the recorder is starting. Save output first, then relay it after the test exits.
if tests/headless.sh run python3 tests/settings-help-test.py >build/settings-help-evidence/results.log 2>&1; then
  test_status=0
else
  test_status=$?
fi
cat build/settings-help-evidence/results.log
exit "$test_status"
