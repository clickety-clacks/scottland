#!/bin/bash
# E9: offscreen renders release what they allocate, path by path (census preloaded into Wayfire),
# in a caller-owned isolated session.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set an isolated headless test directory}"
[[ ! -f $SCOTTLAND_HEADLESS_DIR/pid ]] || { echo 'headless directory occupied' >&2; exit 1; }
[[ -f build/libscottland.so ]] || { echo 'build the plugin first (make test-hooks)' >&2; exit 1; }
artifacts=$SCOTTLAND_HEADLESS_DIR.offscreen-leak-artifacts
mkdir -p "$artifacts"
protocols=$(pkg-config --variable=pkgdatadir wayland-protocols)
wayland-scanner client-header "$protocols/stable/xdg-shell/xdg-shell.xml" "$artifacts/xdg-shell-client.h"
wayland-scanner private-code "$protocols/stable/xdg-shell/xdg-shell.xml" "$artifacts/xdg-shell-client.c"
cc -Wall -Wextra -I"$artifacts" tests/subsurface-app.c "$artifacts/xdg-shell-client.c" \
  $(pkg-config --cflags --libs wayland-client) -o "$artifacts/subsurface-app"
cc -Wall -Wextra -O2 -shared -fPIC tests/transform-census.c -o "$artifacts/transform-census.so" -ldl -lpthread
export SCOTTLAND_WIDGET_PATH=$PWD/tests/widgets
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log" 2>/dev/null || true
  cp "$SCOTTLAND_HEADLESS_DIR/state/transform-census.json" "$artifacts/" 2>/dev/null || true
  tests/headless.sh stop >/dev/null 2>&1
}
trap cleanup EXIT
SCOTTLAND_TEST_PRELOAD=$artifacts/transform-census.so tests/headless.sh start --widgets
tests/headless.sh run python3 "$PWD/tests/offscreen-leak-test.py" "$artifacts" "$PWD/build/libscottland.so" \
  "$artifacts/subsurface-app"
