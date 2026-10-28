#!/bin/bash
# Hold forms, offers and the hold ring (WK35/WK36/WK39) with real input: one output, then two.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set a fresh headless directory under this checkout build/}"
[[ ! -e $SCOTTLAND_HEADLESS_DIR ]] || { echo 'headless directory occupied' >&2; exit 1; }
artifacts=${1:-$PWD/build/hold-forms-evidence}
mkdir -p "$artifacts"
run=one-output
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire-$run.log" 2>/dev/null || true
  tests/headless.sh stop
}
trap cleanup EXIT
status=0
SCOTTLAND_TEST_OUTPUTS=1 tests/headless.sh start --widgets
tests/headless.sh run python3 -u tests/hold-forms-test.py "$artifacts/one-output" | tee "$artifacts/one-output.log" || status=1
cleanup
run=two-outputs
SCOTTLAND_TEST_OUTPUTS=2 tests/headless.sh start --widgets
tests/headless.sh run python3 -u tests/hold-forms-test.py "$artifacts/two-outputs" --outputs2 | tee "$artifacts/two-outputs.log" || status=1
exit $status
