#!/bin/bash
# Run Scottland nested in a floating window on the current Hyprland desktop, for hands-on testing
# with the repo's plugin build. Test config differences: no integration hooks, and move is
# Alt+drag (Super+drag would be taken by Hyprland).
#
#   tests/nested.sh start [windows...]   start and open foot windows (default: alpha beta gamma)
#   tests/nested.sh stop
#   tests/nested.sh state                 each window's zone and scale (scottland/layout-state)
set -euo pipefail
repo=$(cd -- "$(dirname -- "$0")/.." && pwd)
dir=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/scottland-nested
mkdir -p "$dir"

ipc() {
  WAYFIRE_SOCKET=$XDG_RUNTIME_DIR/wayfire-$(cat "$dir/sock")-.socket python3 - "$@" <<'PY'
import json, os, socket, struct, sys
s = socket.socket(socket.AF_UNIX); s.connect(os.environ["WAYFIRE_SOCKET"])
body = json.dumps({"method": sys.argv[1], "data": json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}}).encode()
s.sendall(struct.pack("<I", len(body)) + body)
n = struct.unpack("<I", s.recv(4))[0]; r = b""
while len(r) < n: r += s.recv(n - len(r))
print(r.decode())
PY
}

case ${1:-} in
  start)
    shift
    mkdir -p "$dir/xml"
    cp "$repo/core/plugin/metadata/scottland.xml" "$dir/xml/"
    grep -v '^scottland_hooks' "$repo/core/config/scottland.ini" |
      sed '/^\[move\]/,/^activate/ s/^activate = .*/activate = <alt> BTN_LEFT/' >"$dir/test.ini"
    env -u HYPRLAND_INSTANCE_SIGNATURE -u WAYFIRE_SOCKET WLR_WL_OUTPUTS=1 \
      WAYFIRE_PLUGIN_PATH="$repo/build" WAYFIRE_PLUGIN_XML_PATH="$dir/xml:/usr/share/wayfire/metadata" \
      setsid wayfire -c "$dir/test.ini" >"$dir/wayfire.log" 2>&1 </dev/null &
    echo $! >"$dir/pid"
    for _ in $(seq 50); do
      sock=$(sed -n 's/.*Using socket name \(wayland-[0-9]*\).*/\1/p' "$dir/wayfire.log")
      [[ -n $sock ]] && break
      sleep 0.1
    done
    echo "$sock" >"$dir/sock"
    sleep 0.5
    address=$(hyprctl clients -j | jq -r --argjson pid "$(cat "$dir/pid")" '.[] | select(.pid == $pid) | .address')
    hyprctl dispatch "hl.dsp.window.float({ action = \"enable\", window = \"address:$address\" })" >/dev/null
    hyprctl dispatch "hl.dsp.window.resize({ x = 1600, y = 1000, window = \"address:$address\" })" >/dev/null
    hyprctl dispatch "hl.dsp.window.center({ window = \"address:$address\" })" >/dev/null
    for title in "${@:-alpha beta gamma}"; do
      for t in $title; do
        WAYLAND_DISPLAY=$sock setsid foot -T "$t" -W 44x10 sh -c "echo Scottland test window $t; exec sleep 36000" >/dev/null 2>&1 </dev/null &
      done
    done
    sleep 1
    hyprctl clients -j | jq -r --arg a "$address" '.[] | select(.address == $a) | "nested Scottland: \(.at) \(.size) on \(.workspace.name)"'
    ;;
  stop)
    [[ -f $dir/pid ]] && kill "$(cat "$dir/pid")" 2>/dev/null || true
    ;;
  state)
    ipc scottland/layout-state | jq -c '.views[] | {title, zone, scale: (.applied_scale * 1000 | round / 1000)}'
    ;;
  ipc)
    shift; ipc "$@"
    ;;
  *)
    sed -n '2,10p' "$0"; exit 1 ;;
esac
