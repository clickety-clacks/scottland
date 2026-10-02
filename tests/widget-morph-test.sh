#!/bin/bash
# WG13/WG16/WG22: real-input form and presentation sampling, in this checkout's private headless session only.
set -euo pipefail
cd "$(dirname "$0")/.."
hdir=${SCOTTLAND_HEADLESS_DIR:?set a task-specific SCOTTLAND_HEADLESS_DIR}
[[ ! -f $hdir/pid ]] || { echo 'headless runtime occupied' >&2; exit 1; }
export SCOTTLAND_WIDGET_PATH=$PWD/tests/widgets
tests/headless.sh start --widgets
cleanup() {
  mkdir -p "$hdir.results"
  cp "$hdir/wayfire.log" "$hdir.results/morph-wayfire.log"
  tests/headless.sh stop
}
trap cleanup EXIT
tests/headless.sh run python3 tests/widget-morph-test.py "$hdir.results"
