#!/bin/bash
# Run the merged idle breathing/GO18 fixture in an isolated --widgets session.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set a fresh isolated headless test directory}"
[[ ! -e $SCOTTLAND_HEADLESS_DIR ]] || { echo 'headless directory occupied' >&2; exit 1; }
artifacts=${1:-build/goo-idle-bench}
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
tests/headless.sh run python3 tests/goo-idle-bench.py "$SCOTTLAND_HEADLESS_DIR" "$artifacts" "$@" \
  | tee "$artifacts/results.log"
