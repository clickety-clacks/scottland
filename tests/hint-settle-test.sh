#!/bin/bash
# WK40: hints appear only once settled, judged frame by frame from a lossless recording of real
# Alt holds. Only a caller-owned headless compositor; recordings and logs stay beside its state dir.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set an isolated headless directory under build/}"
[[ ! -e $SCOTTLAND_HEADLESS_DIR ]] || { echo 'headless directory occupied' >&2; exit 1; }
artifacts=$SCOTTLAND_HEADLESS_DIR.hint-settle-artifacts
mkdir -p "$artifacts"
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log" 2>/dev/null || true
  tests/headless.sh stop >/dev/null 2>&1
}
trap cleanup EXIT
tests/headless.sh start --widgets
# First-use shader compilation can outlast the harness's one-second grace period.
for i in $(seq 60); do
  tests/headless.sh ipc scottland/hints >/dev/null 2>&1 && break
  sleep 1
done
tests/headless.sh run python3 "$PWD/tests/hint-settle-test.py" "$artifacts"
