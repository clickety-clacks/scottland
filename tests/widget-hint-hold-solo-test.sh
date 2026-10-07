#!/bin/bash
# One real-input selected-widget hint hold in a private compositor session.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set a unique headless directory under this checkout build/}"
[[ $(realpath -m "$SCOTTLAND_HEADLESS_DIR") == "$PWD"/build/* ]] || {
  echo 'headless directory must be under this checkout build/' >&2; exit 1;
}
[[ ! -e $SCOTTLAND_HEADLESS_DIR ]] || { echo 'headless directory occupied' >&2; exit 1; }
artifacts=${1:?pass a unique artifact directory under this checkout build/}
[[ $(realpath -m "$artifacts") == "$PWD"/build/* ]] || {
  echo 'artifact directory must be under this checkout build/' >&2; exit 1;
}
mkdir -p "$artifacts"
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log" 2>/dev/null || true
  tests/headless.sh stop
}
trap cleanup EXIT
SCOTTLAND_TEST_OUTPUTS=1 tests/headless.sh start --widgets
tests/headless.sh run python3 -u tests/widget-hint-hold-solo-test.py "$artifacts" \
  | tee "$artifacts/result.log"
