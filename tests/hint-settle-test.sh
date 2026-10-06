#!/bin/bash
# WK41: hints appear only once settled, judged frame by frame from a lossless recording of real
# Alt holds. Only a caller-owned headless compositor; recordings and logs stay beside its state dir.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set an isolated headless directory under build/}"
[[ $(realpath -m "$SCOTTLAND_HEADLESS_DIR") == "$PWD"/build/* ]] || { echo 'use a directory under build/' >&2; exit 1; }
[[ ! -e $SCOTTLAND_HEADLESS_DIR ]] || { echo 'headless directory occupied' >&2; exit 1; }
artifacts=$SCOTTLAND_HEADLESS_DIR.hint-settle-artifacts
mkdir -p "$artifacts"
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log" 2>/dev/null || true
  tests/headless.sh stop >/dev/null 2>&1 || true
  rm -rf "$SCOTTLAND_HEADLESS_DIR"
}
trap cleanup EXIT
trap 'exit 143' INT TERM
tests/headless.sh start --widgets
# First-use shader compilation can outlast the harness's one-second grace period.
ready=false
for i in $(seq 60); do
  timeout 8s tests/headless.sh ipc scottland/hints >/dev/null 2>&1 && { ready=true; break; }
  sleep 1
done
$ready || { echo 'compositor IPC never became ready' >&2; exit 1; }
timeout --signal=TERM --kill-after=45s 15m tests/headless.sh run python3 "$PWD/tests/hint-settle-test.py" "$artifacts"
