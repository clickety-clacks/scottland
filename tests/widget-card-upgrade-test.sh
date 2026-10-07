#!/bin/bash
# Upgrading the card package under a docked card leaves the card and its app open (WG5).
#   tests/widget-card-upgrade-test.sh ARTIFACTS
# Needs a fresh SCOTTLAND_HEADLESS_DIR under build/. The test upgrades a scratch copy of the card
# package inside that directory, never the checkout's.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set a fresh isolated headless test directory}"
[[ $(realpath -m "$SCOTTLAND_HEADLESS_DIR") == "$PWD"/build/* ]] || { echo 'keep the headless directory under this checkout build/' >&2; exit 1; }
[[ ! -e $SCOTTLAND_HEADLESS_DIR ]] || { echo 'headless directory occupied' >&2; exit 1; }
artifacts=${1:?artifact directory}
mkdir -p "$artifacts"
export SCOTTLAND_WIDGET_PATH=$SCOTTLAND_HEADLESS_DIR/widgets
tests/headless.sh start --widgets
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log" 2>/dev/null || true
  cp "$SCOTTLAND_HEADLESS_DIR/state/scottland/widgets.log" "$artifacts/widgets.log" 2>/dev/null || true
  tests/headless.sh stop
}
trap cleanup EXIT
mkdir -p "$SCOTTLAND_WIDGET_PATH"
cp -r core/widgets/card "$SCOTTLAND_WIDGET_PATH/card"
tests/headless.sh run python3 -u tests/widget-card-upgrade-test.py "$SCOTTLAND_WIDGET_PATH/card" "$artifacts" | tee "$artifacts/results.log"
