#!/bin/bash
# Run the stock Omarchy shell inside a headless Scottland and record every Hyprland call it makes.
#
#   tests/shell-probe.sh [seconds] [out-dir]
#   PROBE_SHIM=omarchy/shim/scottland-hyprshim tests/shell-probe.sh ...   test against the shim
#
# Isolation, so the probe cannot touch the real desktop:
#   - headless Wayfire (no window), its own D-Bus session bus
#   - without PROBE_SHIM: HYPRLAND_INSTANCE_SIGNATURE unset; a fake `hyprctl` first on PATH logs
#     each call and fails
#   - with PROBE_SHIM: a private HYPRLAND_INSTANCE_SIGNATURE served by the shim; the real hyprctl
#     and Quickshell talk to it. Unsupported requests land in home/.local/state/scottland/hyprshim.log
#   - sandbox HOME: small Omarchy settings copied, large read-only folders symlinked
#   - a copy of the shell at a different path, so its IPC never answers the real shell's callers
#   - XDG config/state/cache dirs point into the sandbox, and fake `systemctl`, `systemd-run` and
#     `uwsm-app` log instead of acting: plugins that manage user services (e.g. Tightbeam decision
#     windows) must not restart the real desktop's services
set -uo pipefail

seconds=${1:-20}
out=${2:-$(mktemp -d -t scottland-probe.XXXXXX)}
repo=$(cd -- "$(dirname -- "$0")/.." && pwd)
config_source=${SCOTTLAND_CONFIG:-$repo/core/config/scottland.ini}
shim=${PROBE_SHIM:-}
[[ -n $shim ]] && shim=$(realpath "$shim")

mkdir -p "$out"/{home/.config/omarchy,home/.local/state/omarchy,home/.local/share,bin}
rmdir "$out/home/.local/bin" 2>/dev/null || true
sandbox=$out/home

# Plugins that manage the real desktop's services or single-instance windows stay out of the probe:
# Tightbeam decisions reaches its live window host through Quickshell IPC and restarts its unit.
skip_plugins=${PROBE_SKIP_PLUGINS:-mike.tightbeam-decisions}
mkdir -p "$sandbox/.config/omarchy/plugins"
for plugin in "$HOME"/.config/omarchy/plugins/*; do
  [[ " $skip_plugins " == *" $(basename "$plugin") "* ]] && continue
  ln -sfn "$plugin" "$sandbox/.config/omarchy/plugins/$(basename "$plugin")"
done

for entry in "$HOME"/.config/omarchy/*; do
  name=$(basename "$entry")
  [[ $name == plugins ]] && continue
  if [[ -d $entry && $(du -sm "$entry" | cut -f1) -gt 1 ]]; then
    ln -sfn "$entry" "$sandbox/.config/omarchy/$name"
  else
    cp -a "$entry" "$sandbox/.config/omarchy/$name"
  fi
done
cp -a "$HOME/.local/state/omarchy/current" "$sandbox/.local/state/omarchy/"
for linked in .config/fontconfig .local/share/fonts .config/gtk-3.0 .config/gtk-4.0 .local/bin; do
  [[ -e $HOME/$linked ]] && ln -sfn "$HOME/$linked" "$sandbox/$linked"
done
cp -a /usr/share/omarchy/shell "$out/shell"
# The probe starts what it needs itself; installed autostart hooks must not run here.
config=$out/scottland.ini
grep -v "^scottland_hooks" "$config_source" >"$config"

if [[ -z $shim ]]; then
  cat >"$out/bin/hyprctl" <<FAKE
#!/bin/sh
printf '%s\t%s\n' "\$(ps -o args= -p \$PPID | cut -c1-120)" "\$*" >>"$out/hyprctl-calls.log"
echo "HYPRLAND_INSTANCE_SIGNATURE not set! (is hyprland running?)" >&2
exit 1
FAKE
  chmod +x "$out/bin/hyprctl"
fi
: >"$out/hyprctl-calls.log"
for blocked in systemctl systemd-run uwsm-app uwsm; do
  cat >"$out/bin/$blocked" <<FAKE
#!/bin/sh
printf '%s\t%s\n' "$blocked" "\$*" >>"$out/blocked-calls.log"
exit 0
FAKE
  chmod +x "$out/bin/$blocked"
done

env -u HYPRLAND_INSTANCE_SIGNATURE -u WAYLAND_DISPLAY -u DISPLAY -u WAYFIRE_SOCKET \
  HOME="$sandbox" PATH="$out/bin:$PATH" SHIM="$shim" \
  XDG_CONFIG_HOME="$sandbox/.config" XDG_STATE_HOME="$sandbox/.local/state" XDG_CACHE_HOME="$sandbox/.cache" \
  XDG_CURRENT_DESKTOP=Scottland:Wayfire:wlroots XDG_SESSION_DESKTOP=scottland \
  WLR_BACKENDS=headless WLR_LIBINPUT_NO_DEVICES=1 \
  dbus-run-session -- bash -c '
    out=$1 seconds=$2 config=$3
    wayfire -c "$config" >"$out/wayfire.log" 2>&1 &
    wf=$!
    for _ in $(seq 50); do
      sock=$(sed -n "s/.*Using socket name \(wayland-[0-9]*\).*/\1/p" "$out/wayfire.log" | head -1)
      [[ -n $sock ]] && break
      sleep 0.2
    done
    export WAYLAND_DISPLAY=$sock
    if [[ -n $SHIM ]]; then
      export WAYFIRE_SOCKET=$XDG_RUNTIME_DIR/wayfire-$sock-.socket
      export HYPRLAND_INSTANCE_SIGNATURE=probe_$$
      python3 "$SHIM" >"$out/shim.stderr" 2>&1 &
      shim_pid=$!
      for _ in $(seq 50); do [[ -S $XDG_RUNTIME_DIR/hypr/$HYPRLAND_INSTANCE_SIGNATURE/.socket.sock ]] && break; sleep 0.1; done
    fi
    foot -T probe-window sleep "$((seconds + 30))" >/dev/null 2>&1 &
    setsid quickshell -p "$out/shell" >"$out/shell.log" 2>&1 &
    qs=$!
    sleep "$seconds"
    grim "$out/screen.png" 2>"$out/grim.log"
    kill -- -"$qs" 2>/dev/null; kill "$qs" 2>/dev/null; sleep 1
    if [[ -n $SHIM ]]; then
      for q in "j/monitors" "j/workspaces" "j/activeworkspace" "j/clients" "j/activewindow" "j/devices" "j/getoption general:gaps_out"; do
        printf "%s\n" "== $q" >>"$out/shim-replies.txt"
        python3 -c "import socket,sys;s=socket.socket(socket.AF_UNIX);s.connect(sys.argv[1]);s.sendall(sys.argv[2].encode());print(s.recv(1<<20).decode())" \
          "$XDG_RUNTIME_DIR/hypr/$HYPRLAND_INSTANCE_SIGNATURE/.socket.sock" "$q" >>"$out/shim-replies.txt" 2>&1
      done
      hyprctl -j monitors >"$out/real-hyprctl-monitors.json" 2>&1
      kill "$shim_pid" 2>/dev/null
      rm -rf "$XDG_RUNTIME_DIR/hypr/$HYPRLAND_INSTANCE_SIGNATURE"
    fi
    kill "$wf" 2>/dev/null; wait
  ' probe "$out" "$seconds" "$config"

echo "$out"
