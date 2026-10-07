#!/usr/bin/env bash
# Run only on the booked adapter test host; this never installs or reloads the live session.
set -euo pipefail
cd "$(dirname "$0")/.."
repo=$PWD
headless_dir=$(realpath -m "${SCOTTLAND_HEADLESS_DIR:-$repo/build/hl-solar-session-$$}")
artifacts=$(realpath -m "${1:-$repo/build/solar-session-evidence-$$}")
case "$headless_dir" in "$repo"/build/*) ;; *) echo 'headless directory must be unique under this checkout build/' >&2; exit 1 ;; esac
case "$artifacts" in "$repo"/build/*) ;; *) echo 'evidence directory must be under this checkout build/' >&2; exit 1 ;; esac
[[ ! -e $headless_dir ]] || { echo "headless directory already exists: $headless_dir" >&2; exit 1; }
[[ ! -e $artifacts ]] || { echo "evidence directory already exists: $artifacts" >&2; exit 1; }
mkdir -p "$artifacts"
export SCOTTLAND_HEADLESS_DIR=$headless_dir

cleanup() {
  status=$?
  trap - EXIT
  [[ ! -f $headless_dir/wayfire.log ]] || cp "$headless_dir/wayfire.log" "$artifacts/wayfire.log" || status=$?
  if [[ -f $headless_dir/pid ]]; then tests/headless.sh stop || status=$?; fi
  exit "$status"
}
trap cleanup EXIT

make test-hooks
tests/headless.sh start --omarchy
tests/headless.sh run python3 -u tests/solar-session-test.py | tee "$artifacts/results.log"
