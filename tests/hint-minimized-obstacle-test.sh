#!/bin/bash
# WK41/WK13: a minimized window is neither an obstacle nor cover, judged by captured pixels.
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
SCOTTLAND_TEST_OUTPUTS=1 tests/headless.sh start
ready=false
for i in $(seq 30); do
  timeout 8s tests/headless.sh ipc scottland/hints >/dev/null 2>&1 && { ready=true; break; }
  sleep 1
done
$ready || { echo 'compositor IPC never became ready' >&2; exit 1; }
timeout --signal=TERM --kill-after=30s 2m tests/headless.sh run python3 -u \
  "$PWD/tests/hint-minimized-obstacle-test.py" "$artifacts" | tee "$artifacts/results.log"
