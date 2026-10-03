#!/bin/bash
# Repeat-widgetization/avoidance probes with automatic all-thread capture on failure.
# Run only on the test host. Owns this headless session and its build/ artifacts.
set -euo pipefail
cd "$(dirname "$0")/.."
hdir=${SCOTTLAND_HEADLESS_DIR:?set a task-specific directory under build/}
[[ $(realpath -m "$hdir") == "$PWD"/build/* ]] || { echo 'keep headless logs under this checkout build/' >&2; exit 1; }
[[ ! -f $hdir/pid ]] || { echo 'headless runtime occupied' >&2; exit 1; }
artifacts=$PWD/build/widget-repeat-results
mkdir -p "$artifacts"
tests/headless.sh start --widgets --gdb
cleanup() {
  status=$?
  if [[ $status != 0 ]]; then
    debugger=$(pgrep -P "$(cat "$hdir/pid")" -x gdb || true)
    [[ -z $debugger ]] || kill -INT "$debugger" 2>/dev/null || true
    sleep 1
  fi
  cp "$hdir/wayfire.log" "$artifacts/wayfire-gdb.log" || true
  cp "$hdir/state/scottland/widgets.log" "$artifacts/widgets.log" 2>/dev/null || true
  tests/headless.sh stop
}
trap cleanup EXIT
for suite in ${SCOTTLAND_FREEZE_CASES:-avoidance repeat race-foot race-ghostty batch}; do
  case $suite in
    avoidance) script=avoidance-widget-test.py; extra=() ;;
    repeat) script=widget-repeat-test.py; extra=() ;;
    race-foot) script=widget-race-test.py; extra=(foot) ;;
    race-ghostty) script=widget-race-test.py; extra=(ghostty) ;;
    batch) script=widget-batch-race-test.py; extra=() ;;
    *) echo "unknown suite: $suite" >&2; exit 1 ;;
  esac
  tests/headless.sh run python3 -u "tests/$script" "$artifacts/$suite" "${extra[@]}" \
    >"$artifacts/$suite.log" 2>&1
  echo "PASS $suite (see $artifacts/$suite.log)"
done
