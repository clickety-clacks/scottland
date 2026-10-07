#!/usr/bin/env bash
# GO28 one dye, in a private headless session on a test machine (never the daily machine).
#   SCOTTLAND_HEADLESS_DIR=$PWD/build/hl-onedye tests/goo-one-dye-test.sh [ARTIFACTS]
# SCOTTLAND_TEST_GOO_GLES=2 runs the packed GLES 2 path.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set a fresh directory under the checkout build/}"
[[ ! -e $SCOTTLAND_HEADLESS_DIR ]] || { echo 'headless directory occupied' >&2; exit 1; }
artifacts=${1:-build/go28-one-dye}
mkdir -p "$artifacts"
cleanup() {
  [[ ! -f $SCOTTLAND_HEADLESS_DIR/wayfire.log ]] || cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log"
  tests/headless.sh stop
}
trap cleanup EXIT
tests/headless.sh start
tests/headless.sh run python3 tests/goo-one-dye-test.py "$artifacts" | tee "$artifacts/results.log"
