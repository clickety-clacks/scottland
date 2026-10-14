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
retain_receipt() {
  local name=$1 src="$headless/$1"
  [[ -e $src || -L $src ]] || return 0
  if [[ -L $src || ! -f $src ]]; then
    printf 'refused non-regular headless receipt: %s\n' "$name" >>"$art/receipt-retention.log"
    return 1
  fi
  cp -- "$src" "$art/$name" || {
    printf 'failed to retain headless receipt: %s\n' "$name" >>"$art/receipt-retention.log"
    return 1
  }
}
retain_widget_log() {
  local src="$headless/state/scottland/widgets.log"
  [[ -e $src || -L $src ]] || return 0
  if [[ -L $src || ! -f $src ]]; then
    printf 'refused non-regular widget log\n' >>"$art/receipt-retention.log"
    return 1
  fi
  cp -- "$src" "$art/widgets.log" || {
    printf 'failed to retain widget log\n' >>"$art/receipt-retention.log"
    return 1
  }
}
cleanup() {
  local run_status=$? stop_status=0 receipt
  trap - EXIT
  cp "$headless/wayfire.log" "$art/wayfire.log" 2>/dev/null || true
  for receipt in launch-owner.json launch-terminal.json .scottland-headless-mode; do
    retain_receipt "$receipt" || true
  done
  retain_widget_log || true
  if tests/headless.sh stop >"$art/headless-stop.log" 2>&1; then
    stop_status=0
  else
    stop_status=$?
  fi
  printf '%s\n' "$stop_status" >"$art/headless-stop.exit" || true
  for receipt in launch-owner.json launch-terminal.json .scottland-headless-mode; do
    retain_receipt "$receipt" || true
  done
  retain_widget_log || true
  rm -rf -- "$SCOTTLAND_HEADLESS_DIR" "$tmp"
  exit "$run_status"
}
trap cleanup EXIT
export TMPDIR="$tmp"
export SCOTTLAND_WIDGET_PATH="$PWD/tests/widgets"
tests/headless.sh start --widgets
tests/headless.sh run timeout --signal=TERM --kill-after=30s 6m \
  python3 -u "$PWD/tests/transparency-toggle-test.py" "$art" | tee "$art/results.log"
