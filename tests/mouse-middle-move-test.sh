#!/bin/bash
# Focused middle-button and adjacent touchpad gesture checks in an isolated session.
set -euo pipefail
cd "$(dirname "$0")/.."
run_id=$$
scratch=$PWD/build/mouse-middle-scratch-$run_id
headless_dir=$scratch/session
artifacts=$PWD/build/mouse-middle-evidence-$run_id
tmp_dir=$headless_dir.tmp
[[ ! -e $scratch && ! -e $artifacts && ! -e $tmp_dir ]] || {
  echo "test scratch already exists" >&2
  exit 1
}
mkdir -m 700 -- "$scratch" "$artifacts" "$tmp_dir"
export SCOTTLAND_HEADLESS_DIR=$headless_dir TMPDIR=$tmp_dir \
  SCOTTLAND_TEST_SCRATCH=$scratch SCOTTLAND_TEST_OUTPUTS=1
started=1
cleanup() {
  status=$?
  stopped=0
  trap - EXIT
  if [[ -f $headless_dir/wayfire.log ]]; then
    cp "$headless_dir/wayfire.log" "$artifacts/wayfire.log" 2>/dev/null || true
  fi
  if ((started)); then
    if tests/headless.sh stop; then
      stopped=1
    else
      echo "headless cleanup did not complete; preserving owned scratch" >&2
      status=1
    fi
  else
    stopped=1
  fi
  if ((stopped)); then
    rm -rf -- "$tmp_dir"
    if ! rmdir -- "$scratch"; then
      echo "headless cleanup left owned scratch; preserving it" >&2
      status=1
    fi
  fi
  exit "$status"
}
trap cleanup EXIT
tests/headless.sh start
tests/headless.sh run python3 -u tests/mouse-middle-move-test.py "$artifacts" |
  tee "$artifacts/test.log"
