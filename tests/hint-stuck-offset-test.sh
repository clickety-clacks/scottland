#!/bin/bash
# WK41: a foreground offset that never settles does not hide the hints behind it (pixels).
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set a fresh directory under this checkout build/}"
[[ $(realpath -m "$SCOTTLAND_HEADLESS_DIR") == "$PWD"/build/* && ! -e $SCOTTLAND_HEADLESS_DIR ]] || {
  echo 'use a fresh directory under this checkout build/' >&2; exit 1; }
artifacts=${1:-$SCOTTLAND_HEADLESS_DIR.artifacts}
mkdir -p "$artifacts"
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log" 2>/dev/null || true
  tests/headless.sh stop || true
  rm -rf "$SCOTTLAND_HEADLESS_DIR"
}
trap cleanup EXIT
trap 'exit 143' INT TERM
tests/headless.sh start
timeout --signal=TERM --kill-after=30s 2m tests/headless.sh run python3 -u \
  tests/hint-stuck-offset-test.py "$artifacts" | tee "$artifacts/results.log"
