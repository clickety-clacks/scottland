#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set a fresh isolated directory under build/}"
headless=$(realpath -m "$SCOTTLAND_HEADLESS_DIR")
art=$(realpath -m "${1:-$PWD/build/transparency-toggle-evidence}")
[[ $headless == "$PWD"/build/* && ! -e $headless ]] || {
  echo 'headless runtime must be a fresh directory under this checkout build/' >&2; exit 1; }
[[ $art == "$PWD"/build/* && ! -e $art ]] || {
  echo 'evidence must be a fresh directory under this checkout build/' >&2; exit 1; }
[[ -x build/hooks/libexec/scottland-exec && -f build/libscottland.so ]] || {
  echo 'build this exact checkout before running the headless candidate test' >&2; exit 1; }
mkdir -p "$art"
tmp=$(mktemp -d "$PWD/build/transparency-toggle-tmp.XXXXXX")
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$art/wayfire.log" 2>/dev/null || true
  tests/headless.sh stop >/dev/null 2>&1 || true
  rm -rf -- "$SCOTTLAND_HEADLESS_DIR" "$tmp"
}
trap cleanup EXIT
export TMPDIR="$tmp"
export SCOTTLAND_WIDGET_PATH="$PWD/tests/widgets"
tests/headless.sh start --widgets
tests/headless.sh run timeout --signal=TERM --kill-after=30s 6m \
  python3 -u "$PWD/tests/transparency-toggle-test.py" "$art" | tee "$art/results.log"
