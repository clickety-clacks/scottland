#!/bin/bash
# Pausing during a drag does nothing (docs/spread.md; the drag audition is removed), with real
# stipc input in a headless session, also with a stale solo_audition_delay in its config:
#   SCOTTLAND_HEADLESS_DIR=$PWD/build/hl-drag-pause tests/drag-pause-test.sh [ARTIFACTS]
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set a fresh headless directory under this checkout build/}"
[[ ! -e $SCOTTLAND_HEADLESS_DIR ]] || { echo 'headless directory occupied' >&2; exit 1; }
artifacts=${1:-$PWD/build/drag-pause-evidence}
mkdir -p "$artifacts"
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log" 2>/dev/null || true
  tests/headless.sh stop
  rm -rf "$SCOTTLAND_HEADLESS_DIR"
}
trap cleanup EXIT
SCOTTLAND_TEST_OUTPUTS=1 tests/headless.sh start
tests/headless.sh run python3 -u tests/drag-pause-test.py "$artifacts" "$SCOTTLAND_HEADLESS_DIR/wayfire.ini" |
  tee "$artifacts/drag-pause-test.log"
exit "${PIPESTATUS[0]}"
