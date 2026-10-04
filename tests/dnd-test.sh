#!/bin/bash
# Native data-device transfer, isolated from the user's desktop and settings.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set a fresh directory under this checkout build/ on the test host}"
[[ ! -e $SCOTTLAND_HEADLESS_DIR ]] || { echo 'headless directory occupied' >&2; exit 1; }
artifacts=${1:-build/dnd-evidence}
if (($#)); then shift; fi
mkdir -p "$artifacts"
export SCOTTLAND_WIDGET_PATH=$PWD/tests/widgets
options=(--widgets --omarchy)
if [[ ${1:-} == --stock ]]; then options=(--stock --omarchy); fi
cleanup() {
  [[ ! -f $SCOTTLAND_HEADLESS_DIR/wayfire.log ]] || cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log"
  tests/headless.sh stop
}
trap cleanup EXIT
tests/headless.sh start "${options[@]}"
tests/headless.sh run python3 tests/dnd-test.py "$artifacts" --nautilus "$@" | tee "$artifacts/results.log"
