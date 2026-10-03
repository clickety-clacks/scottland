#!/bin/bash
# Build a real XRGB Wayland client and exercise the conversion in an owned session.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set a unique directory under build/}"
art=$PWD/build/widget-rgbx-results
mkdir -p "$art"
protocols=$(pkg-config --variable=pkgdatadir wayland-protocols)
wayland-scanner client-header "$protocols/stable/xdg-shell/xdg-shell.xml" "$art/xdg-shell-client.h"
wayland-scanner private-code "$protocols/stable/xdg-shell/xdg-shell.xml" "$art/xdg-shell-client.c"
cc -Wall -Wextra -I"$art" tests/widget-rgbx-app.c "$art/xdg-shell-client.c" \
  $(pkg-config --cflags --libs wayland-client) -o "$art/widget-rgbx-app"
export SCOTTLAND_WIDGET_PATH=$PWD/tests/widgets
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$art/wayfire.log" 2>/dev/null || true
  tests/headless.sh stop
}
trap cleanup EXIT
tests/headless.sh start --widgets
tests/headless.sh run python3 tests/widget-rgbx-test.py "$art" "$art/widget-rgbx-app"
