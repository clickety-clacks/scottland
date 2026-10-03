#!/bin/bash
# WK13's optional always-avoid mode and widgetization stress on an isolated test session.
set -euo pipefail
cd "$(dirname "$0")/.."
hdir=${SCOTTLAND_HEADLESS_DIR:?set a task-specific headless directory}
case $(realpath -m "$hdir") in
  "$PWD"/build/*) ;;
  "${XDG_RUNTIME_DIR:?}"/scottland-headless-ship-batch2-avoid-part5-*) ;;
  *) echo 'use this checkout build/ or its named, isolated plumbus runtime directory' >&2; exit 1 ;;
esac
[[ ! -f $hdir/pid ]] || { echo 'headless directory is already running' >&2; exit 1; }
artifacts=$PWD/build/hint-avoidance-always-plumbus
mkdir -p "$artifacts"
cleanup() {
  cp "$hdir/wayfire.log" "$artifacts/wayfire.log" 2>/dev/null || true
  tests/headless.sh stop >/dev/null 2>&1 || true
}
trap cleanup EXIT
tests/headless.sh start --widgets
timeout --foreground 180s tests/headless.sh run python3 -u tests/hint-avoidance-always-test.py "$artifacts" "$hdir" \
  2>&1 | tee "$artifacts/results.log"
