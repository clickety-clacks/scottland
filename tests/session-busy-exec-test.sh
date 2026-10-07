#!/bin/bash
# E5: scottland-exec with a busy or gone session (tests/session-busy-exec-test.py). Run only on a
# test host, never on a daily desktop. The caller supplies its own headless directory under build/.
# Builds this checkout's plugin and test helpers first (make test-hooks): without them
# tests/headless.sh would fall back to whatever helpers the machine has dev-installed.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set an isolated headless test directory}"
[[ $(realpath -m "$SCOTTLAND_HEADLESS_DIR") == "$PWD"/build/* ]] ||
  { echo 'keep the headless directory under this checkout build/' >&2; exit 1; }
artifacts=$SCOTTLAND_HEADLESS_DIR.busy-exec-artifacts
mkdir -p "$artifacts"
export TMPDIR=${TMPDIR:-$PWD/build/tmp}; mkdir -p "$TMPDIR"
make test-hooks >"$artifacts/build.log" 2>&1 || { echo "make test-hooks failed; see $artifacts/build.log" >&2; exit 1; }
[[ -x build/hooks/libexec/scottland-exec && -f build/libscottland.so ]] ||
  { echo "this checkout's test helpers are missing after make test-hooks" >&2; exit 1; }
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log" 2>/dev/null || true
  tests/headless.sh stop >/dev/null 2>&1
}
trap cleanup EXIT
tests/headless.sh stop >/dev/null 2>&1
tests/headless.sh start
python3 tests/session-busy-exec-test.py "$artifacts"
