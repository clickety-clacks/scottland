#!/bin/bash
# Goo whose own GL work fails at runtime falls back to the plain halo, logs one line and stops
# erroring. A test switch raises the GL error, so any GLES driver runs it.
#   tests/goo-runtime-fallback-test.sh ARTIFACTS
# Needs a fresh SCOTTLAND_HEADLESS_DIR.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set a fresh isolated headless test directory}"
[[ ! -e $SCOTTLAND_HEADLESS_DIR ]] || { echo 'headless directory occupied' >&2; exit 1; }
artifacts=${1:?artifact directory}
mkdir -p "$artifacts"
SCOTTLAND_TEST_GOO=1 tests/headless.sh start --widgets
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log" 2>/dev/null || true
  cp build/goo-runtime-fallback.png "$artifacts/" 2>/dev/null || true
  tests/headless.sh stop
}
trap cleanup EXIT
tests/headless.sh run python3 tests/goo-fallback-test.py runtime "$SCOTTLAND_HEADLESS_DIR" | tee "$artifacts/results.log"
