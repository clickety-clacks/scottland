#!/bin/bash
# Run the large-window breathing damage fixture in an isolated --widgets session.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set a fresh isolated headless test directory}"
[[ ! -e $SCOTTLAND_HEADLESS_DIR ]] || { echo 'headless directory occupied' >&2; exit 1; }
artifacts=${1:-build/goo-breath-damage}
if (($#)); then shift; fi
mkdir -p "$artifacts"
tests/headless.sh start --widgets
cleanup() {
  if [[ -f $SCOTTLAND_HEADLESS_DIR/wayfire.log ]]; then
    cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log"
  fi
  tests/headless.sh stop
}
trap cleanup EXIT
tests/headless.sh run python3 tests/goo-breath-damage.py "$SCOTTLAND_HEADLESS_DIR" "$artifacts" "$@" \
  | tee "$artifacts/results.log"
