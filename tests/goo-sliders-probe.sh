#!/usr/bin/env bash
# Measures what Dye spread, swirl and release do, in a private headless session on a test machine
# (never the daily machine). SCOTTLAND_TEST_GOO_GLES=2 runs the packed GLES 2 path.
#   SCOTTLAND_HEADLESS_DIR=$PWD/build/hl-sliders tests/goo-sliders-probe.sh [ARTIFACTS] [settled,wake]
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set a fresh directory under the checkout build/}"
[[ ! -e $SCOTTLAND_HEADLESS_DIR ]] || { echo 'headless directory occupied' >&2; exit 1; }
artifacts=${1:-build/goo-sliders}
mkdir -p "$artifacts"
cleanup() {
  [[ ! -f $SCOTTLAND_HEADLESS_DIR/wayfire.log ]] || cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log"
  tests/headless.sh stop
}
trap cleanup EXIT
tests/headless.sh start
tests/headless.sh run python3 tests/goo-sliders-probe.py "$(realpath "$artifacts")" "${2:-settled,wake}" | tee "$artifacts/results.log"
