#!/bin/bash
# Grabbing a peeking window (peek-strip decision 9, WK27): goo, pixels and input agree throughout
# every drag path, in the periphery and the center, in a private headless session with goo.
#   tests/grab-peek-test.sh ARTIFACTS [SCENARIO ...] [--mode MODE ...]
# Needs a fresh SCOTTLAND_HEADLESS_DIR under this checkout's build/.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set a fresh isolated headless test directory}"
[[ $(realpath -m "$SCOTTLAND_HEADLESS_DIR") == "$PWD"/build/* ]] || { echo 'use a directory under this checkout build/' >&2; exit 1; }
[[ ! -e $SCOTTLAND_HEADLESS_DIR/pid ]] || { echo 'headless directory occupied' >&2; exit 1; }
artifacts=${1:?artifact directory}; shift
mkdir -p "$artifacts"
SCOTTLAND_TEST_GOO=1 tests/headless.sh start
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log" 2>/dev/null || true
  tests/headless.sh stop
}
trap cleanup EXIT
timeout --foreground 600s tests/headless.sh run python3 -u tests/grab-peek-test.py "$artifacts" "$@" | tee "$artifacts/results.log"
