#!/bin/bash
# WK36/WP1: real pairing input and independent eventual peripheral geometry.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?use a fresh directory under this checkout build/}"
[[ $(realpath -m "$SCOTTLAND_HEADLESS_DIR") == "$PWD"/build/* && ! -e $SCOTTLAND_HEADLESS_DIR ]] || {
  echo 'use a fresh directory under this checkout build/' >&2; exit 1; }
[[ -f build/libscottland.so && -x build/hooks/libexec/scottland-exec ]] || {
  echo 'build this checkout plugin and hooks first' >&2; exit 1; }
artifacts=${1:-$SCOTTLAND_HEADLESS_DIR.artifacts}
mkdir -p "$artifacts"
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log" 2>/dev/null || true
  tests/headless.sh stop || true
  rm -rf "$SCOTTLAND_HEADLESS_DIR"
}
trap cleanup EXIT
trap 'exit 143' INT TERM
tests/headless.sh start
timeout --signal=TERM --kill-after=30s 3m tests/headless.sh run python3 -u \
  tests/pairing-periphery-test.py "$artifacts" | tee "$artifacts/results.log"
