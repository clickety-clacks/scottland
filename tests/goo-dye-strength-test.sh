#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set a fresh directory under the checkout build on Plumbus}"
[[ ! -e $SCOTTLAND_HEADLESS_DIR ]] || { echo 'headless directory occupied' >&2; exit 1; }
artifacts=${1:-build/go23-dye-strength}
mkdir -p "$artifacts"
cleanup() {
  [[ ! -f $SCOTTLAND_HEADLESS_DIR/wayfire.log ]] || cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log"
  tests/headless.sh stop
}
trap cleanup EXIT
tests/headless.sh start --widgets
if [[ ${GO23_BASELINE:-0} == 1 ]]; then
  tests/headless.sh run env GO23_BASELINE=1 python3 tests/goo-dye-strength-test.py "$artifacts" | tee "$artifacts/results.log"
else
  tests/headless.sh run python3 tests/goo-dye-strength-test.py "$artifacts" | tee "$artifacts/results.log"
fi
