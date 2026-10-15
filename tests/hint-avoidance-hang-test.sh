#!/bin/bash
# WK13's bounded window-avoidance live-drag regression in an isolated session.
set -euo pipefail
cd "$(dirname "$0")/.."
hdir=${SCOTTLAND_HEADLESS_DIR:?set a task-specific headless directory}
case $(realpath -m "$hdir") in
  "$PWD"/build/*) ;;
  "${XDG_RUNTIME_DIR:?}"/scottland-headless-avoidance-animation-*) ;;
  *) echo 'use this checkout build/ or its named, isolated test-host runtime directory' >&2; exit 1 ;;
esac
[[ ! -f $hdir/pid ]] || { echo 'headless directory is already running' >&2; exit 1; }
artifacts=$PWD/build/avoidance-animation-evidence
mkdir -p "$artifacts"
cleanup() {
  cp "$hdir/wayfire.log" "$artifacts/hang-wayfire.log" 2>/dev/null || true
  tests/headless.sh stop >/dev/null 2>&1 || true
}
trap cleanup EXIT
tests/headless.sh start
timeout --foreground 24s tests/headless.sh run \
  python3 -u tests/hint-avoidance-hang-test.py "$artifacts" \
  2>&1 | tee "$artifacts/hang-results.log"
