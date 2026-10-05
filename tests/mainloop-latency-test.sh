#!/bin/bash
# Main-loop latency scenarios in a headless session of this checkout (docs/main-loop.md).
# Run on a test host (the x86 test machine, the aarch64 test machine), never on a daily desktop.
#
#   tests/mainloop-latency-test.sh LABEL [--windows N] [--mike] [--widgets] [--outputs 2] [--gate PHASE] [scenario ...]
#
# Output: build/mainloop/out-LABEL/{report.txt,results.json,wayfire.log}. The session is stopped
# and its directory removed afterwards.
set -euo pipefail
repo=$(cd -- "$(dirname -- "$0")/.." && pwd)
label=${1:?usage: mainloop-latency-test.sh LABEL [options] [scenario ...]}; shift
outdir=$repo/build/mainloop/out-$label
export SCOTTLAND_HEADLESS_DIR=$repo/build/mainloop/headless-$label TMPDIR=$repo/build/mainloop/tmp
mkdir -p "$TMPDIR" "$outdir"
start=() pass=() widgets=
while (($#)); do
  case $1 in
    --widgets) start+=(--widgets); widgets=1 ;;
    --outputs) export SCOTTLAND_TEST_OUTPUTS=$2; shift ;;
    *) pass+=("$1") ;;
  esac
  shift
done
trap 'cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$outdir/wayfire.log" 2>/dev/null || true; "$repo/tests/headless.sh" stop >/dev/null 2>&1 || true' EXIT
"$repo/tests/headless.sh" start "${start[@]}"
timeout --foreground 1200s "$repo/tests/headless.sh" run env MAINLOOP_WIDGETS="$widgets" \
  python3 -u "$repo/tests/mainloop-latency-test.py" "$outdir" "${pass[@]}" 2>&1 | tee "$outdir/results.txt"
exit "${PIPESTATUS[0]}"
