#!/bin/bash
# P12: a completely covered window peeks, whatever happened before (real input, headless):
#   SCOTTLAND_HEADLESS_DIR=$PWD/build/hl-peek-miss tests/peek-miss-test.sh [ARTIFACTS] [CASE ...]
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set a fresh headless directory under this checkout build/}"
repo=$(pwd -P)
build=$repo/build
[[ -d $build && ! -L $build && $(realpath -e -- "$build") == "$build" ]] || {
  echo 'build/ must be a real checkout directory' >&2; exit 1;
}
SCOTTLAND_HEADLESS_DIR=$(realpath -m -- "$SCOTTLAND_HEADLESS_DIR")
[[ $(dirname -- "$SCOTTLAND_HEADLESS_DIR") == "$build" ]] || {
  echo 'use a fresh direct child of build/ for the headless directory' >&2; exit 1;
}
export SCOTTLAND_HEADLESS_DIR
[[ ! -e $SCOTTLAND_HEADLESS_DIR && ! -L $SCOTTLAND_HEADLESS_DIR ]] || {
  echo 'headless directory occupied' >&2; exit 1;
}
artifacts_input=${1:-$build/peek-miss-evidence}; shift || true
case $artifacts_input in /*) ;; *) artifacts_input=$repo/$artifacts_input ;; esac
artifacts=$(realpath -m -- "$artifacts_input")
[[ $(dirname -- "$artifacts") == "$build" ]] || {
  echo 'use a fresh direct child of build/ for peek-miss evidence' >&2; exit 1;
}
[[ ! -e $artifacts && ! -L $artifacts ]] || { echo 'peek-miss artifact directory occupied' >&2; exit 1; }
umask 077
mkdir -m 700 -- "$artifacts"
cleanup() {
  status=$?
  trap - EXIT
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log" 2>/dev/null || true
  if ! tests/headless.sh stop; then
    echo 'failed to clean the owned headless session; preserving its state directory' >&2
    status=1
  fi
  exit "$status"
}
trap cleanup EXIT
SCOTTLAND_TEST_OUTPUTS=1 tests/headless.sh start --widgets
tests/headless.sh run python3 -u tests/peek-miss-test.py "$artifacts" "$@" | tee "$artifacts/peek-miss-test.log"
exit "${PIPESTATUS[0]}"
