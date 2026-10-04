#!/bin/bash
# WK13's window-avoidance easing regression in a private headless session.
set -euo pipefail
cd "$(dirname "$0")/.."
hdir=${SCOTTLAND_HEADLESS_DIR:?set a task-specific headless directory}
case $(realpath -m "$hdir") in
  "$PWD"/build/*) ;;
  "${XDG_RUNTIME_DIR:?}"/scottland-headless-avoidance-animation-*) ;;
  *) echo 'use this checkout build/ or its named, isolated plumbus runtime directory' >&2; exit 1 ;;
esac
[[ ! -f $hdir/pid ]] || { echo 'headless directory is already running' >&2; exit 1; }
artifacts=$PWD/build/avoidance-animation-plumbus
mkdir -p "$artifacts"
cleanup() {
  cp "$hdir/wayfire.log" "$artifacts/wayfire.log" 2>/dev/null || true
  tests/headless.sh stop >/dev/null 2>&1 || true
}
trap cleanup EXIT
tests/headless.sh start
timeout --foreground 90s tests/headless.sh run \
  python3 -u tests/hint-avoidance-animation-test.py "$artifacts" \
  2>&1 | tee "$artifacts/results.log"
