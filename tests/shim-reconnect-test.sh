#!/bin/bash
set -euo pipefail
: "${SCOTTLAND_HEADLESS_DIR:?set an unused headless directory under build/}"
repo=$(cd -- "$(dirname -- "$0")/.." && pwd -P)
SCOTTLAND_HEADLESS_DIR=$(realpath -m -- "$SCOTTLAND_HEADLESS_DIR")
export SCOTTLAND_HEADLESS_DIR
case "$SCOTTLAND_HEADLESS_DIR/" in
  "$repo"/build/*) ;;
  *) echo 'SCOTTLAND_HEADLESS_DIR must be under this checkout build/' >&2; exit 2 ;;
esac
[[ ! -e $SCOTTLAND_HEADLESS_DIR && ! -L $SCOTTLAND_HEADLESS_DIR ]] || {
  echo 'headless directory occupied or redirected' >&2; exit 1;
}
artifacts=$PWD/build/shim-reconnect-evidence
mkdir -p "$artifacts"
# The session environment hooks require a UUID owner token and its scratch directory.
export SCOTTLAND_HEADLESS_OWNER="$(python3 -c 'import uuid; print(uuid.uuid4())')"
export SCOTTLAND_HEADLESS_OWNER_DIR="$SCOTTLAND_HEADLESS_DIR"
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log" 2>/dev/null || true
  cp "$SCOTTLAND_HEADLESS_DIR/state/scottland/hyprshim.log" "$artifacts/hyprshim.log" 2>/dev/null || true
  tests/headless.sh stop
}
trap cleanup EXIT
tests/headless.sh start --omarchy
session_dir=$(realpath -e -- "$SCOTTLAND_HEADLESS_DIR")
[[ $session_dir == "$SCOTTLAND_HEADLESS_DIR" ]] || {
  echo 'headless runtime path changed during startup' >&2; exit 2;
}
session_runtime=$session_dir/runtime
runtime=$(realpath -e -- "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}")
tmp_scratch=$session_dir/tmp
state_home=$session_dir/state
owner_record=$session_dir/.scottland-headless-owner
[[ -f $owner_record && ! -L $owner_record && $(sed -n '1p' "$owner_record") == "$(id -u)" && \
   $(sed -n '2p' "$owner_record") == "$runtime" && \
   $(sed -n '3p' "$owner_record") == "$SCOTTLAND_HEADLESS_OWNER" ]] || {
  echo 'headless scratch owner record does not match this test run' >&2; exit 2;
}
tests/headless.sh run python3 tests/shim-reconnect-test.py
display=$(cat "$session_dir/display")
signature=$(tests/headless.sh run sh -c 'printf "%s" "$HYPRLAND_INSTANCE_SIGNATURE"')
read_session_shim_pid() {
  local lock="$session_runtime/hypr/$signature/hyprland.lock" pid
  [[ $signature == scottland_* && $signature != */* ]] || {
    echo 'headless session has an invalid shim signature' >&2; return 1;
  }
  [[ -d $session_runtime && ! -L $session_runtime && -d $session_runtime/hypr && \
     ! -L $session_runtime/hypr && -d "$session_runtime/hypr/$signature" && \
     ! -L "$session_runtime/hypr/$signature" && -f $lock && ! -L $lock ]] || {
    echo 'headless shim lock is missing or redirected outside run-owned runtime scratch' >&2
    return 1
  }
  [[ $(sed -n '2p' "$lock") == "$display" ]] || {
    echo 'headless shim lock names a different display' >&2; return 1;
  }
  pid=$(sed -n '1p' "$lock")
  [[ $pid =~ ^[0-9]+$ ]] || { echo 'headless shim lock has an invalid PID' >&2; return 1; }
  python3 - "$pid" "$session_dir" "$runtime" "$tmp_scratch" "$state_home" \
    "$display" "$signature" "$SCOTTLAND_HEADLESS_OWNER" <<'PY'
import pathlib, sys

pid, scratch, runtime, tmpdir, state, display, signature, owner = sys.argv[1:]
try:
    proc = pathlib.Path('/proc') / pid
    env = dict(entry.split(b'=', 1) for entry in
               (proc / 'environ').read_bytes().split(b'\0') if b'=' in entry)
    command = (proc / 'cmdline').read_bytes().split(b'\0')
except OSError as error:
    print(f'refusing to use headless shim PID {pid}: cannot inspect process: {error}', file=sys.stderr)
    sys.exit(1)
expected = {
    b'SCOTTLAND_HEADLESS_DIR': scratch.encode(),
    b'SCOTTLAND_HEADLESS_OWNER': owner.encode(),
    b'TMPDIR': tmpdir.encode(),
    b'XDG_RUNTIME_DIR': runtime.encode(),
    b'XDG_STATE_HOME': state.encode(),
    b'WAYLAND_DISPLAY': display.encode(),
    b'HYPRLAND_INSTANCE_SIGNATURE': signature.encode(),
}
if not all(env.get(key) == value for key, value in expected.items()) or \
   not any(b'scottland-hyprshim' in part for part in command):
    print(f'refusing to use PID {pid}: process does not belong to this headless shim session',
          file=sys.stderr)
    sys.exit(1)
print(pid)
PY
}
pid=$(read_session_shim_pid)
hooks=$(tests/headless.sh run sh -c 'printf "%s" "$SCOTTLAND_HOOKS"')
tests/headless.sh run "$hooks/reload.d/10-hyprshim"
[[ $(read_session_shim_pid) == "$pid" ]]
echo 'PASS reload keeps a live shim'
[[ $(read_session_shim_pid) == "$pid" ]] || {
  echo 'refusing to signal a shim PID that no longer belongs to this headless session' >&2
  exit 1
}
kill "$pid"
for _ in $(seq 30); do
  if ! read_session_shim_pid >/dev/null 2>&1; then break; fi
  sleep 0.1
done
if read_session_shim_pid >/dev/null 2>&1; then
  echo 'headless shim did not stop after its owned PID was signaled' >&2
  exit 1
fi
tests/headless.sh run "$hooks/reload.d/10-hyprshim"
new_pid=$(read_session_shim_pid)
[[ $new_pid != "$pid" ]] && kill -0 "$new_pid"
tests/headless.sh run hyprctl -j monitors >/dev/null
echo "PASS reload restarts the dead shim for $display and hyprctl answers"
