#!/bin/bash
set -euo pipefail
: "${SCOTTLAND_HEADLESS_DIR:?set an unused headless directory under build/}"
[[ ! -e $SCOTTLAND_HEADLESS_DIR ]] || { echo 'headless directory occupied' >&2; exit 1; }
artifacts=$PWD/build/shim-reconnect-evidence
mkdir -p "$artifacts"
cleanup() {
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log" 2>/dev/null || true
  cp "$SCOTTLAND_HEADLESS_DIR/state/scottland/hyprshim.log" "$artifacts/hyprshim.log" 2>/dev/null || true
  tests/headless.sh stop
}
trap cleanup EXIT
tests/headless.sh start --omarchy
tests/headless.sh run python3 tests/shim-reconnect-test.py
display=$(cat "$SCOTTLAND_HEADLESS_DIR/display")
signature=$(tests/headless.sh run sh -c 'printf "%s" "$HYPRLAND_INSTANCE_SIGNATURE"')
lock=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/hypr/$signature/hyprland.lock
pid=$(sed -n 1p "$lock")
hooks=$(tests/headless.sh run sh -c 'printf "%s" "$SCOTTLAND_HOOKS"')
tests/headless.sh run "$hooks/reload.d/10-hyprshim"
[[ $(sed -n 1p "$lock") == "$pid" ]]
echo 'PASS reload keeps a live shim'
kill "$pid"
for _ in $(seq 30); do kill -0 "$pid" 2>/dev/null || break; sleep 0.1; done
tests/headless.sh run "$hooks/reload.d/10-hyprshim"
new_pid=$(sed -n 1p "$lock")
[[ $new_pid != "$pid" ]] && kill -0 "$new_pid"
tests/headless.sh run hyprctl -j monitors >/dev/null
echo "PASS reload restarts the dead shim for $display and hyprctl answers"
