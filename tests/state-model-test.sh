#!/bin/bash
# Model, replica and actual card-render audits, plus reproducible real-input sequences.
# Only owns a headless session. Run on the test machine, never in a live desktop.
set -euo pipefail
cd "$(dirname "$0")/.."
hdir=${SCOTTLAND_HEADLESS_DIR:-${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/scottland-headless}
if [[ -f $hdir/pid ]]; then
  echo 'headless runtime already occupied; leave its owner alone' >&2
  exit 1
fi
tests/headless.sh start --widgets
cleanup() {
  cp "$hdir/wayfire.log" \
    "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/scottland-model-test.log" || true
  tests/headless.sh stop
}
trap cleanup EXIT
tests/headless.sh run python3 tests/state-model-test.py "${1:-104729}" "${2:-50}"
