#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set a fresh directory under the checkout build on Plumbus}"
[[ ! -e $SCOTTLAND_HEADLESS_DIR ]] || { echo 'headless directory occupied' >&2; exit 1; }
artifacts=${1:-build/attention-color-go22}
mkdir -p "$artifacts"
cleanup() {
  [[ ! -f $SCOTTLAND_HEADLESS_DIR/wayfire.log ]] || cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log"
  tests/headless.sh stop
}
trap cleanup EXIT
tests/headless.sh start --widgets
tests/headless.sh run python3 tests/attention-color-family-test.py "$artifacts" | tee "$artifacts/results.log"
