#!/bin/bash
# No backdrop inside a window (GO27), in a private headless session.
#   tests/goo-strip-test.sh ARTIFACTS [STRIP_LIMIT [natural]]
# Default: Mike's first layout with the strip limit lowered to 3 (strips merge across the front
# window). `16 natural`: his second layout at the shipped limit, where merging happens by itself.
# Needs a fresh SCOTTLAND_HEADLESS_DIR; SCOTTLAND_TEST_GOO_GLES=2 selects the packed path.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set a fresh isolated headless test directory}"
[[ ! -e $SCOTTLAND_HEADLESS_DIR ]] || { echo 'headless directory occupied' >&2; exit 1; }
artifacts=${1:?artifact directory}; shift
mkdir -p "$artifacts"
SCOTTLAND_TEST_GOO=1 tests/headless.sh start --widgets
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log" 2>/dev/null || true
  tests/headless.sh stop
}
trap cleanup EXIT
tests/headless.sh run python3 tests/goo-strip-test.py "$artifacts" "$@" | tee "$artifacts/results.log"
