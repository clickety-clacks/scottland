#!/bin/bash
# Deliberate signal/core verification in this checkout's own headless sessions only.
set -euo pipefail
cd "$(dirname "$0")/.."
export SCOTTLAND_HEADLESS_DIR=${SCOTTLAND_HEADLESS_DIR:?set a task-specific directory under build/}
[[ $(realpath -m "$SCOTTLAND_HEADLESS_DIR") == "$PWD"/build/* ]] || { echo 'keep headless logs under this checkout build/' >&2; exit 1; }
[[ ! -f $SCOTTLAND_HEADLESS_DIR/pid ]] || { echo 'headless runtime occupied' >&2; exit 1; }
artifacts=$PWD/build/session-core-results
mkdir -p "$artifacts"
cleanup() {
  tests/headless.sh stop >/dev/null 2>&1 || true
}
trap cleanup EXIT
for sig in ABRT QUIT; do
  tests/headless.sh start --widgets
  tests/headless.sh run foot -c /dev/null -T "core-test-$sig" sleep 600 >"$artifacts/foot-$sig.log" 2>&1 &
  sleep .5
  tests/headless.sh ipc stipc/move_cursor '{"x":640,"y":360}' >/dev/null
  tests/headless.sh ipc stipc/feed_key '{"key":"KEY_LEFTMETA","state":true}' >/dev/null
  tests/headless.sh ipc stipc/feed_button '{"combo":"BTN_LEFT","mode":"press"}' >/dev/null
  tests/headless.sh ipc stipc/move_cursor '{"x":1250,"y":360}' >/dev/null
  tests/headless.sh ipc stipc/feed_button '{"combo":"BTN_LEFT","mode":"release"}' >/dev/null
  tests/headless.sh ipc stipc/feed_key '{"key":"KEY_LEFTMETA","state":false}' >/dev/null
  sleep 1
  pid=$(cat "$SCOTTLAND_HEADLESS_DIR/compositor.pid")
  # Validate our recorded process before sending a terminating signal.
  python3 - "$pid" "$SCOTTLAND_HEADLESS_DIR/wayfire.ini" <<'PY'
import pathlib, sys
args = pathlib.Path('/proc/' + sys.argv[1] + '/cmdline').read_bytes().split(b'\0')
assert sys.argv[2].encode() in args and any(a.endswith(b'wayfire') for a in args)
assert int(pathlib.Path('/proc/' + sys.argv[1] + '/status').read_text().split('SigCgt:')[1].split()[0],16) & ((1 << 5) | (1 << 2)) == 0
PY
  kill -s "$sig" "$pid"
  found=0
  for _ in $(seq 100); do
    if coredumpctl info "$pid" >"$artifacts/$sig.info" 2>/dev/null; then found=1; break; fi
    sleep .2
  done
  [[ $found == 1 ]]
  core=$(mktemp "$artifacts/core-$sig.XXXXXX")
  coredumpctl dump "$pid" --output="$core" 2>"$artifacts/$sig.dump.log"
  gdb -q -batch /usr/bin/wayfire "$core" -ex 'set pagination off' -ex 'thread apply all bt' >"$artifacts/$sig.stacks" 2>&1
  rm -f "$core"
  grep -q '^Thread ' "$artifacts/$sig.stacks"
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/$sig.wayfire.log"
  echo "PASS $sig: systemd core for PID $pid, $(rg -c '^Thread ' "$artifacts/$sig.stacks") thread stacks"
  tests/headless.sh stop
done
