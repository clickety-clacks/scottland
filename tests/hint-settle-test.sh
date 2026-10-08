#!/bin/bash
# WK41: hints appear only once settled, judged frame by frame from a lossless recording of real
# Alt holds. Only a caller-owned headless compositor; recordings and logs stay beside its state dir.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set an isolated headless directory under build/}"
repo=$(pwd -P)
build=$repo/build
[[ -d $build && ! -L $build && $(realpath -e -- "$build") == "$build" ]] || {
  echo 'build/ must be a real checkout directory' >&2; exit 1;
}
SCOTTLAND_HEADLESS_DIR=$(realpath -m -- "$SCOTTLAND_HEADLESS_DIR")
[[ $(dirname -- "$SCOTTLAND_HEADLESS_DIR") == "$build" ]] || {
  echo 'use a fresh direct child of build/ for the headless directory' >&2; exit 1;
}
export SCOTTLAND_HEADLESS_DIR
[[ ! -e $SCOTTLAND_HEADLESS_DIR && ! -L $SCOTTLAND_HEADLESS_DIR ]] || {
  echo 'headless directory occupied' >&2; exit 1;
}
artifacts=$SCOTTLAND_HEADLESS_DIR.hint-settle-artifacts
[[ ! -e $artifacts && ! -L $artifacts ]] || { echo 'hint-settle artifact directory occupied' >&2; exit 1; }
umask 077
mkdir -m 700 -- "$artifacts"
cleanup() {
  status=$?
  trap - EXIT
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log" 2>/dev/null || true
  if ! tests/headless.sh stop; then
    echo 'failed to clean the owned headless session; preserving its state directory' >&2
    status=1
  fi
  exit "$status"
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
timeout --signal=TERM --kill-after=45s 15m tests/headless.sh run env \
  SCOTTLAND_TEST_RECORDER="${SCOTTLAND_TEST_RECORDER:-wf-recorder}" \
  python3 "$PWD/tests/hint-settle-test.py" "$artifacts"
