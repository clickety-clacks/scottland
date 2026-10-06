#!/bin/bash
# A Scottland session with no screen, for testing without touching anyone's display: the real
# config (shipped base + generated fragments, e.g. imported shortcuts) and the repo's plugin
# build, driven with Wayfire's stipc virtual input. It runs only hooks that stay inside the test
# session: never 05-import-environment (would repoint the user's systemd services), 06-watch-
# config (would rewrite the live session's config) or 20-omarchy-shell.
#
#   tests/headless.sh start [--omarchy] [--widgets]   start; --omarchy adds the Hyprland shim and
#                                         Lua host (--hyprland-start also runs the config's
#                                         startup handlers: fixture HOMEs only); --widgets adds the widget service, on a private
#                                         D-Bus session bus (all headless sessions have a private bus)
#                                         (SCOTTLAND_WIDGET_PATH and SCOTTLAND_WIDGET_SCOPE pass through)
#                                         --gdb runs Wayfire under gdb; SIGINT to that gdb prints
#                                         all thread stacks into wayfire.log, then resumes
#                                         --stock omits Scottland for a protocol control
#   tests/headless.sh run CMD [ARGS...]   run CMD inside it (scottland-exec: its own environment)
#   tests/headless.sh ipc METHOD [JSON]   call its Wayfire IPC (e.g. stipc/feed_key)
#   tests/headless.sh stop
#
# Requires bubblewrap to redirect Quickshell logs into the test directory without changing
# XDG_RUNTIME_DIR. Put SCOTTLAND_HEADLESS_DIR and TMPDIR under the checkout's build/ directory.
# Helpers come from this checkout (make test-hooks) if built, else the dev install. Set
# SCOTTLAND_HEADLESS_DIR to run test sessions of several checkouts at once.
#
# Example: tests/headless.sh start --omarchy && tests/headless.sh run foot &
#          tests/headless.sh ipc stipc/feed_key '{"key":"KEY_LEFTMETA","state":true}'
set -euo pipefail
repo=$(cd -- "$(dirname -- "$0")/.." && pwd)
runtime=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}
# SCOTTLAND_HEADLESS_DIR: where this test session keeps its state, so test sessions of several
# checkouts (e.g. agents on branches sharing a test machine) can run at once.
dir=${SCOTTLAND_HEADLESS_DIR:-$repo/build/headless}
# The checkout's own helpers (make test-hooks) when it has them, else the dev install, else the
# package's.
hooks=$repo/build/hooks
[[ -d $hooks/libexec ]] || hooks=${XDG_DATA_HOME:-$HOME/.local/share}/scottland/dev
[[ -d $hooks/libexec ]] || hooks=/usr/lib/scottland
exec_tool=$hooks/libexec/scottland-exec

display() { cat "$dir/display"; }

case ${1:-} in
  start)
    [[ -f $dir/pid ]] && kill -0 "$(cat "$dir/pid")" 2>/dev/null && { echo "already running on $(display)"; exit 0; }
    rm -rf "$dir"; mkdir -p "$dir"
    started=(01-record-environment)
    test_goo=${SCOTTLAND_TEST_GOO:-}
    test_gles=${SCOTTLAND_TEST_GOO_GLES:-}
    test_outputs=${SCOTTLAND_TEST_OUTPUTS:-${SCOTTLAND_HEADLESS_OUTPUTS:-1}}
    stock=0
    private_bus=1
    debugger=()
    for option in "${@:2}"; do
      case $option in
        --stock) stock=1 ;;
        --omarchy) started+=(10-hyprshim 25-omarchy-override-report 30-lua-host) ;;
        # Runs the Hyprland config's startup handlers: only for a fixture HOME (the machine's
        # own config would start its real autostart apps inside the test session).
        --hyprland-start) started+=(45-hyprland-start) ;;
        --widgets) started+=(08-widget-bus); private_bus=1 ;;
        --gdb)
          [[ $(realpath -m "$dir") == "$repo"/build/* ]] || {
            echo '--gdb requires SCOTTLAND_HEADLESS_DIR under this checkout build/ (stack logs can be large)' >&2
            exit 1
          }
          cat >"$dir/gdb.commands" <<'GDB'
set pagination off
set confirm off
run
while $_isvoid($_exitcode) && $_isvoid($_exitsignal)
thread apply all bt full
continue
end
GDB
          debugger=(gdb -q -batch -x "$dir/gdb.commands" --args) ;;
      esac
    done
    (
      # A clean environment, as a display manager gives a login (not this shell's: an agent's or a
      # terminal's environment carries another desktop's variables and hides session gaps), plus
      # the session's own variables.
      for name in $(env | sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p'); do
        case $name in
          HOME|USER|LOGNAME|SHELL|LANG|LC_*|TERM|TMPDIR|SCOTTLAND_TEST_PATH|stock|test_goo|test_gles|test_outputs|debugger|XDG_RUNTIME_DIR|DBUS_SESSION_BUS_ADDRESS|SCOTTLAND_WIDGET_PATH|SCOTTLAND_WIDGET_SCOPE|SCOTTLAND_HEADLESS_OUTPUTS|SCOTTLAND_DBUS_LEGACY|repo|dir|hooks|runtime|exec_tool|started) ;;
          *) unset "$name" 2>/dev/null || true ;;
        esac
      done
      # SCOTTLAND_TEST_PATH: a test's stand-in commands, found before the system's.
      export PATH=${SCOTTLAND_TEST_PATH:+$SCOTTLAND_TEST_PATH:}/usr/local/bin:/usr/bin:/bin
      export SCOTTLAND_TEST_MODEL=1
      # Isolate every child's settings and logs, not just config generation. Keep the real
      # runtime so Wayland display names (and systemd widget scopes) remain unique.
      export XDG_CONFIG_HOME=$dir/config XDG_STATE_HOME=$dir/state XDG_CACHE_HOME=$dir/cache
      export SCOTTLAND_TEST_STATE=$dir/state
      # Quickshell hardcodes logs under $XDG_RUNTIME_DIR/quickshell. Bind only that
      # subtree for test clients, leaving Wayland/systemd sockets and display names alone.
      mkdir -p "$dir/bin" "$dir/quickshell" "$dir/state" "$dir/cache"
      command -v bwrap >/dev/null || { echo 'headless tests need bubblewrap for Quickshell logs' >&2; exit 1; }
      cat >"$dir/bin/quickshell" <<'WRAPPER'
#!/bin/sh
exec bwrap --bind / / --bind "$XDG_STATE_HOME/../quickshell" "$XDG_RUNTIME_DIR/quickshell" -- /usr/bin/quickshell "$@"
WRAPPER
      chmod +x "$dir/bin/quickshell"
      ln -s quickshell "$dir/bin/qs"
      export PATH=$dir/bin:$PATH
      export SCOTTLAND_HOOKS=$hooks XDG_CURRENT_DESKTOP=Scottland:Wayfire:wlroots XDG_SESSION_TYPE=wayland
      # Focus-mode hooks (full screen) touch the desktop (e.g. its notifications): a test session
      # runs only its own, from its folder.
      mkdir -p "$dir/focus.d"; export SCOTTLAND_FOCUS_HOOKS=$dir/focus.d
      for env_hook in "$hooks"/session-env.d/*.sh; do [[ -r $env_hook ]] && . "$env_hook"; done
      # Test this checkout's shipped defaults, not the installed package's possibly older base
      # config or the machine's personal settings (layout.ini, overrides.ini).
      mkdir -p "$dir/config/scottland"
      cp "$repo/core/config/scottland.ini" "$dir/config/scottland/scottland.ini"
      # The test's own edits to the built config, as a sed script that every rebuild inside this
      # session applies too (a shim `hyprctl reload`), so a rebuild never writes the machine's
      # live config and never drops the test plugin or hook list.
      hook_list=${started[*]}
      {
        printf '%s\n' 's/^plugins = \\$/plugins = stipc \\/'
        printf '%s\n' "s#^scottland_hooks = .*#scottland_hooks = sh -c 'for h in $hook_list; do \"\$SCOTTLAND_HOOKS/autostart.d/\$h\" \\& done; wait'#"
        # A protocol control: same clients/config/stock Wayfire, without Scottland's
        # scene transforms or input handlers. Autostart still records its environment.
        if ((stock)); then printf '%s\n' '/^  scottland \\$/d'; fi
        # No override exercises shipped defaults; 0 explicitly tests the fallback halo.
        if [[ $test_goo == 1 || $test_goo == 0 ]]; then
          goo_value=false; [[ $test_goo == 1 ]] && goo_value=true
          printf '%s\n' '/^goo =/d' "/^\[scottland\]/a goo = $goo_value"
        fi
      } >"$dir/config-edit.sed"
      export SCOTTLAND_CONFIG_OUTPUT=$dir/wayfire.ini SCOTTLAND_CONFIG_EDIT=$dir/config-edit.sed
      "$hooks/libexec/scottland-build-config" >/dev/null
      if [[ $test_gles == 2 || $test_gles == unsupported || $test_gles == no-derivatives ]]; then
        export MESA_GLES_VERSION_OVERRIDE=2.0
        export MESA_EXTENSION_OVERRIDE="-GL_EXT_color_buffer_float -GL_EXT_color_buffer_half_float -GL_OES_texture_half_float -GL_OES_texture_half_float_linear"
        if [[ $test_gles == unsupported ]]; then
          MESA_EXTENSION_OVERRIDE+=" -GL_OES_texture_float"
        elif [[ $test_gles == no-derivatives ]]; then
          MESA_EXTENSION_OVERRIDE+=" -GL_OES_standard_derivatives"
        fi
      fi
      WLR_BACKENDS=headless WLR_LIBINPUT_NO_DEVICES=1 WLR_HEADLESS_OUTPUTS=$test_outputs \
        WAYFIRE_PLUGIN_PATH="$repo/build" WAYFIRE_PLUGIN_XML_PATH="$repo/core/plugin/metadata:/usr/share/wayfire/metadata" \
        setsid ${private_bus:+dbus-run-session --} "${debugger[@]}" wayfire -c "$dir/wayfire.ini" >"$dir/wayfire.log" 2>&1 </dev/null &
      echo $! >"$dir/pid"
    )
    for _ in $(seq 100); do
      name=$(sed -n 's/.*Using socket name \(wayland-[0-9]*\).*/\1/p' "$dir/wayfire.log")
      [[ -n $name && -f $runtime/scottland/$name.env ]] && break
      sleep 0.1
    done
    [[ -n ${name:-} ]] || { echo "headless Scottland didn't start; see $dir/wayfire.log" >&2; exit 1; }
    echo "$name" >"$dir/display"
    # The debugger/private-bus wrapper can exit independently. Retain the actual
    # compositor identity so stop still reaps our inferior in that case.
    python3 - "$runtime/scottland/$name.env" "$dir/compositor.pid" <<'PY'
import pathlib, socket, struct, sys
entries = pathlib.Path(sys.argv[1]).read_bytes().split(b'\0')
endpoint = next(e.split(b'=', 1)[1] for e in entries if e.startswith(b'WAYFIRE_SOCKET='))
with socket.socket(socket.AF_UNIX) as peer:
    peer.settimeout(2)
    peer.connect(endpoint.decode())
    pid, _, _ = struct.unpack('3i', peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
pathlib.Path(sys.argv[2]).write_text(str(pid) + '\n')
PY
    sleep 1
    echo "headless Scottland on $name (hooks: ${started[*]})"
    ;;
  run)
    shift
    exec "$exec_tool" --display "$(display)" -- "$@"
    ;;
  ipc)
    shift
    exec "$exec_tool" --display "$(display)" -- python3 "$repo/tests/wfipc.py" "$@"
    ;;
  stop)
    [[ -f $dir/display ]] || exit 0
    name=$(display)
    # Stop the session's helpers by their pid files, then Wayfire (its clients follow).
    # (Pid files hold the pid on their first line; the color-scheme watcher leads its own group,
    # with its monitors.)
    for pid_file in "$runtime/scottland/$name.lua.pid" "$runtime/scottland/$name.color-scheme.pid" "$runtime/scottland/$name.widget-bus.pid"; do
      helper=$(sed -n 1p "$pid_file" 2>/dev/null || true)
      if [[ $helper =~ ^[0-9]+$ ]] && grep -qa -e scottland-color-scheme -e lua -e scottland-widget-bus "/proc/$helper/cmdline" 2>/dev/null; then
        if [[ $(ps -o pgid= -p "$helper" | tr -d ' ') == "$helper" ]]; then kill -- "-$helper" 2>/dev/null; else kill "$helper" 2>/dev/null; fi
      fi
      rm -f "$pid_file"
    done
    signature=$(python3 - "$runtime/scottland/$name.env" <<'PY'
import pathlib, sys
path = pathlib.Path(sys.argv[1])
if path.exists():
    for entry in path.read_bytes().split(b'\0'):
        if entry.startswith(b'HYPRLAND_INSTANCE_SIGNATURE='):
            print(entry.split(b'=', 1)[1].decode())
PY
)
    lock="$runtime/hypr/$signature/hyprland.lock"
    if [[ $signature == scottland_* && $signature != */* && -f $lock && $(sed -n 2p "$lock") == "$name" ]]; then
      kill "$(sed -n 1p "$lock")" 2>/dev/null || true
      rm -rf "$(dirname "$lock")"
    fi
    pid=$(cat "$dir/pid")
    compositor=$(cat "$dir/compositor.pid" 2>/dev/null || true)
    if [[ $compositor =~ ^[0-9]+$ ]] && \
      python3 - "$compositor" "$dir/wayfire.ini" <<'PY'
import pathlib, sys
try:
    args = pathlib.Path('/proc/' + sys.argv[1] + '/cmdline').read_bytes().split(b'\0')
    sys.exit(not (sys.argv[2].encode() in args and any(a.endswith(b'wayfire') for a in args)))
except OSError:
    sys.exit(1)
PY
    then
      kill "$compositor" 2>/dev/null || true
    else compositor=; fi
    # setsid gives this harness its own group. Include the debugger's inferior,
    # not only dbus-run-session's immediate child, when stopping --gdb sessions.
    group=$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ' || true)
    if [[ $group == "$pid" ]]; then
      kill -- "-$pid" 2>/dev/null || true
    else
      for child in $(ps -o pid= --ppid "$pid" 2>/dev/null); do kill "$child" 2>/dev/null || true; done
    fi
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 30); do kill -0 "$pid" 2>/dev/null || break; sleep 0.1; done
    kill -9 "$pid" 2>/dev/null || true  # Wayfire can hang on SIGTERM with no outputs
    [[ $group != "$pid" ]] || kill -9 -- "-$pid" 2>/dev/null || true
    [[ -z $compositor ]] || kill -9 "$compositor" 2>/dev/null || true
    rm -f "$runtime/scottland/$name.env" "$runtime/scottland/$name.lua.fifo" \
      "$runtime/scottland/$name.hyprland-started"
    rm -rf "$dir"
    echo "stopped headless Scottland on $name"
    ;;
  *)
    sed -n '2,15p' "$0"; exit 1
    ;;
esac
