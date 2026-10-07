#!/bin/bash
# Caller supplies an unused owned runtime under this checkout's build/; run only on a test machine.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set a fresh owned headless directory}"
[[ $(realpath -m "$SCOTTLAND_HEADLESS_DIR") == "$PWD"/build/* && ! -e $SCOTTLAND_HEADLESS_DIR ]] || {
  echo 'test runtime must be a fresh directory under this checkout build/' >&2; exit 1; }
[[ -x build/hooks/libexec/scottland-exec && -f build/libscottland.so ]] || {
  echo 'build this checkout plugin and hooks first' >&2; exit 1; }
art=$(realpath -m "${1:-build/hint-background-opacity-evidence}")
[[ $art == "$PWD"/build/* && ! -e $art ]] || { echo 'artifacts must be fresh under build/' >&2; exit 1; }
mkdir -p "$art"
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$art/wayfire.log" 2>/dev/null || true
  tests/headless.sh stop >/dev/null 2>&1 || true
  rm -rf -- "$SCOTTLAND_HEADLESS_DIR"
}
trap cleanup EXIT
export TMPDIR="$PWD/build/test-tmp";mkdir -p "$TMPDIR"
tests/headless.sh start --widgets
tests/headless.sh run timeout --signal=TERM --kill-after=30s 4m python3 tests/hint-background-opacity-test.py "$art" | tee "$art/results.log"
