#!/bin/bash
# Run only in this checkout's private headless session. tests/key-layers-test.py uses real stipc
# input and real GTK toplevel/layer-shell surfaces sharing one Wayland client.
set -euo pipefail
cd "$(dirname -- "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set a private SCOTTLAND_HEADLESS_DIR}"
if [[ -f $SCOTTLAND_HEADLESS_DIR/pid ]] && kill -0 "$(cat "$SCOTTLAND_HEADLESS_DIR/pid")" 2>/dev/null; then
  echo "test session already running; refusing to replace it" >&2
  exit 1
fi
tests/headless.sh start
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$SCOTTLAND_HEADLESS_DIR.log" || true
  tests/headless.sh stop
}
trap cleanup EXIT
tests/headless.sh run python3 "$PWD/tests/key-layers-test.py"
