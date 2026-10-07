#!/bin/bash
# The goo's backdrop copy works on drivers that enforce GLES copy formats (NVIDIA): the halo
# follows the wallpaper beneath it and the compositor logs no GL errors.
#   tests/goo-backdrop-copy-test.sh ARTIFACTS
# Needs a fresh SCOTTLAND_HEADLESS_DIR. Mesa passes before and after the fix; NVIDIA fails before.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set a fresh isolated headless test directory}"
[[ ! -e $SCOTTLAND_HEADLESS_DIR ]] || { echo 'headless directory occupied' >&2; exit 1; }
artifacts=${1:?artifact directory}
mkdir -p "$artifacts"
SCOTTLAND_TEST_GOO=1 tests/headless.sh start
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log" 2>/dev/null || true
  tests/headless.sh stop
}
trap cleanup EXIT
tests/headless.sh run python3 tests/goo-backdrop-copy-test.py "$SCOTTLAND_HEADLESS_DIR" "$artifacts" | tee "$artifacts/results.log"
