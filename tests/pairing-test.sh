#!/bin/bash
# WK36 pairing / WK35-WK36 hint holds with real stipc input: one output, then two outputs.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set a fresh headless directory under this checkout build/}"
[[ ! -e $SCOTTLAND_HEADLESS_DIR ]] || { echo 'headless directory occupied' >&2; exit 1; }
artifacts=${1:-$PWD/build/pairing-evidence}
mkdir -p "$artifacts"
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire-$run.log" 2>/dev/null || true
  tests/headless.sh stop
}
trap cleanup EXIT
run=one-output
SCOTTLAND_TEST_OUTPUTS=1 tests/headless.sh start --widgets
set +e
tests/headless.sh run python3 -u tests/pairing-test.py "$artifacts/one-output" | tee "$artifacts/one-output.log"
status=${PIPESTATUS[0]}
set -e
cleanup
run=two-outputs
SCOTTLAND_TEST_OUTPUTS=2 tests/headless.sh start --widgets
set +e
tests/headless.sh run python3 -u tests/pairing-test.py "$artifacts/two-outputs" --outputs2 | tee "$artifacts/two-outputs.log"
second=${PIPESTATUS[0]}
exit $(( status || second ))
