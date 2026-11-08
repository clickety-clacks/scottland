#!/bin/bash
# Run middle-press pointer and tablet acceptance checks in an isolated Wayfire session.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set a fresh, unique headless directory for this checkout}"
headless_dir=$(realpath -m "$SCOTTLAND_HEADLESS_DIR")
case "$headless_dir/" in
  "$PWD"/build/*/) ;;
  *) echo 'SCOTTLAND_HEADLESS_DIR must be under this checkout build/' >&2; exit 1 ;;
esac
[[ ! -e $headless_dir ]] || {
  echo "headless directory occupied: $headless_dir" >&2
  exit 1
}
tmp_dir="${headless_dir}.tmp"
[[ ! -e $tmp_dir ]] || { echo "TMPDIR occupied: $tmp_dir" >&2; exit 1; }
mkdir -p "$tmp_dir"
export SCOTTLAND_HEADLESS_DIR=$headless_dir TMPDIR=$tmp_dir
artifacts=${1:-$PWD/build/middle-press-evidence}
artifacts=$(realpath -m "$artifacts")
case "$artifacts/" in
  "$PWD"/build/*/) ;;
  *) echo 'middle-press artifacts must be under this checkout build/' >&2; exit 1 ;;
esac
mkdir -p "$artifacts"

# The real tablet client observes Wayfire's tablet-v2 delivery. Generate its client protocol
# bindings and binary into this run's checkout/build evidence tree; no product target depends on
# the helper or its test-only protocol sources.
protocols=$(pkg-config --variable=pkgdatadir wayland-protocols)
tablet_gen="$artifacts/tablet-protocols"
mkdir -m 700 -p "$tablet_gen"
wayland-scanner client-header "$protocols/stable/xdg-shell/xdg-shell.xml" \
  "$tablet_gen/xdg-shell-client-protocol.h"
wayland-scanner private-code "$protocols/stable/xdg-shell/xdg-shell.xml" \
  "$tablet_gen/xdg-shell-protocol.c"
wayland-scanner client-header "$protocols/unstable/tablet/tablet-v2.xml" \
  "$tablet_gen/tablet-v2-client-protocol.h"
wayland-scanner private-code "$protocols/unstable/tablet/tablet-v2.xml" \
  "$tablet_gen/tablet-v2-protocol.c"
cc -std=c11 -O2 -Wall -Wextra -I"$tablet_gen" \
  tests/middle-press-tablet-app.c \
  "$tablet_gen/xdg-shell-protocol.c" "$tablet_gen/tablet-v2-protocol.c" \
  $(pkg-config --cflags --libs wayland-client) \
  -o "$artifacts/middle-press-tablet-app"
started=0
cleanup() {
  if ((started)); then
    if [[ -f $headless_dir/display ]]; then
      cp "$headless_dir/wayfire.log" "$artifacts/wayfire.log" 2>/dev/null || true
      tests/headless.sh stop
    elif [[ -e $headless_dir ]]; then
      cp "$headless_dir/wayfire.log" "$artifacts/wayfire.log" 2>/dev/null || true
      tests/headless.sh stop --preserve-scratch || {
        echo 'partial headless startup could not be verified; preserving its scratch for the outer runner' >&2
      }
    fi
  fi
  rm -rf "$tmp_dir"
}
trap cleanup EXIT
started=1
SCOTTLAND_TEST_OUTPUTS=1 tests/headless.sh start
tests/headless.sh run python3 -u tests/middle-press-test.py "$artifacts" | tee "$artifacts/test.log"
tests/headless.sh run python3 -u tests/tablet-middle-press-test.py \
  "$artifacts" "$artifacts/middle-press-tablet-app" | tee "$artifacts/tablet-test.log"
