#!/bin/bash
# A plugin reload after a touch drag, then mouse motion (tests/reload-touch-focus-test.py). Run only
# on a test host (plumbus, nacelle), never on a daily desktop. The caller supplies its own headless
# directory. Builds this checkout's plugin and test helpers first (make test-hooks).
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set an isolated headless test directory}"
artifacts=$SCOTTLAND_HEADLESS_DIR.reload-touch-artifacts
mkdir -p "$artifacts"
export TMPDIR=${TMPDIR:-$PWD/build/tmp}; mkdir -p "$TMPDIR"
make test-hooks >"$artifacts/build.log" 2>&1 || { echo "make test-hooks failed; see $artifacts/build.log" >&2; exit 1; }
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log" 2>/dev/null || true
  tests/headless.sh stop >/dev/null 2>&1
}
trap cleanup EXIT
tests/headless.sh stop >/dev/null 2>&1
tests/headless.sh start --widgets
tests/headless.sh run python3 tests/reload-touch-focus-test.py
