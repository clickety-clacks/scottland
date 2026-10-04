#!/bin/bash
# WK37/WK38 in isolated headless sessions (one output, two outputs, then 2x scale); never reuse or change a
# running session. Builds this checkout's plugin and test helpers (make test-hooks) and uses
# only those, so the result always belongs to this checkout.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set a fresh isolated headless test directory under build/}"
[[ ! -f $SCOTTLAND_HEADLESS_DIR/pid ]] || { echo 'headless runtime occupied' >&2; exit 1; }
export TMPDIR=${TMPDIR:-$HOME/.cache/scottland-build-tmp}; mkdir -p "$TMPDIR"
make test-hooks >/dev/null
[[ -x build/hooks/libexec/scottland-exec && -f build/libscottland.so ]] || {
  echo 'this checkout has no test hooks (make test-hooks failed)' >&2; exit 1; }
mkdir -p build/hint-outline-evidence
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" build/hint-outline-evidence/wayfire.log 2>/dev/null || true
  tests/headless.sh stop >/dev/null 2>&1 || true
}
trap cleanup EXIT
status=0
tests/headless.sh start --widgets
tests/headless.sh run python3 tests/hint-outline-test.py || status=1
cleanup
SCOTTLAND_TEST_OUTPUTS=2 tests/headless.sh start
tests/headless.sh run python3 tests/hint-outline-test.py --second-output || status=1
cleanup
tests/headless.sh start
tests/headless.sh run python3 tests/hint-outline-test.py --hidpi || status=1
exit $status
