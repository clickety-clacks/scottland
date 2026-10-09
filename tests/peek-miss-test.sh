#!/bin/bash
# P12: a completely covered window peeks, whatever happened before (real input, headless):
#   SCOTTLAND_HEADLESS_DIR=$PWD/build/hl-peek-miss tests/peek-miss-test.sh [ARTIFACTS] [CASE ...]
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set a fresh headless directory under this checkout build/}"
build_dir=$(realpath -m -- "$PWD/build")
headless_dir=$(realpath -m -- "$SCOTTLAND_HEADLESS_DIR")
[[ -d $PWD/build && ! -L $PWD/build && $(realpath -e -- "$PWD/build") == "$build_dir" ]] || {
  echo 'build/ must be a real directory in this checkout' >&2; exit 1;
}
[[ $(dirname -- "$headless_dir") == "$build_dir" ]] || {
  echo 'headless directory must be a direct child of this checkout build/' >&2; exit 1;
}
[[ ! -e $SCOTTLAND_HEADLESS_DIR && ! -L $SCOTTLAND_HEADLESS_DIR && \
   ! -e $headless_dir && ! -L $headless_dir ]] || {
  echo 'headless directory occupied; refusing to reuse it' >&2; exit 1;
}
SCOTTLAND_HEADLESS_DIR=$headless_dir
export SCOTTLAND_HEADLESS_DIR
artifacts_input=${1:-$build_dir/peek-miss-evidence}; shift || true
case $artifacts_input in /*) ;; *) artifacts_input=$PWD/$artifacts_input ;; esac
artifacts=$(realpath -m -- "$artifacts_input")
[[ $(dirname -- "$artifacts") == "$build_dir" && \
   ! -e $artifacts_input && ! -L $artifacts_input && ! -e $artifacts && ! -L $artifacts ]] || {
  echo 'peek-miss artifact directory must be a fresh direct child of build/' >&2; exit 1;
}
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
