#!/bin/bash
# A Scottland session with no screen, for testing without touching anyone's display: the real
# config (shipped base + generated fragments, e.g. imported shortcuts) and the repo's plugin
# build, driven with Wayfire's stipc virtual input. It runs only hooks that stay inside the test
# session: never 05-import-environment (would repoint the user's systemd services), 06-watch-
# config (would rewrite the live session's config), 20-omarchy-shell or 40-handover.
#
#   tests/headless.sh start [--omarchy] [--widgets]   start; --omarchy adds the Hyprland shim and
#                                         Lua host; --widgets adds the widget service, on a private
#                                         D-Bus session bus (all headless sessions have a private bus)
#                                         (SCOTTLAND_WIDGET_PATH and SCOTTLAND_WIDGET_SCOPE pass through)
#                                         --gdb runs Wayfire under gdb; SIGINT to that gdb prints
#                                         all thread stacks into wayfire.log, then resumes
#                                         --stock omits Scottland for a protocol control
#                                         SCOTTLAND_TEST_PRELOAD=LIB preloads LIB into Wayfire only
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
repo=$(cd -- "$(dirname -- "$0")/.." && pwd -P)
runtime=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}
build=$repo/build
# SCOTTLAND_HEADLESS_DIR: where this test session keeps its state, so test sessions of several
# checkouts (e.g. agents on branches sharing a test machine) can run at once.
dir_input=${SCOTTLAND_HEADLESS_DIR:-$build/headless}
case $dir_input in /*) ;; *) dir_input=$repo/$dir_input ;; esac
dir=$(realpath -m -- "$dir_input")
session_helper=$repo/tests/headless-session.py
# The checkout's own helpers (make test-hooks) when it has them, else the dev install, else the
# package's.
hooks=$repo/build/hooks
[[ -d $hooks/libexec ]] || hooks=${XDG_DATA_HOME:-$HOME/.local/share}/scottland/dev
[[ -d $hooks/libexec ]] || hooks=/usr/lib/scottland
exec_tool=$hooks/libexec/scottland-exec

display() { cat "$dir/display"; }

case ${1:-} in
  start)
    [[ ! -L $build ]] || { echo "build directory is a symlink: $build" >&2; exit 1; }
    mkdir -p -- "$build"
    [[ ! -L $build && $(realpath -e -- "$build") == "$build" ]] || {
      echo "build directory must be a real directory in this checkout: $build" >&2
      exit 1
    }
    [[ $dir != "$build" && $(dirname -- "$dir") == "$build" ]] || {
      echo "SCOTTLAND_HEADLESS_DIR must be a direct child of this checkout's build/: $dir" >&2
      exit 1
    }
    [[ ! -e $dir && ! -L $dir ]] || {
      echo "headless directory occupied; refusing to adopt or remove it: $dir" >&2
      exit 1
    }
    runtime=$(python3 "$session_helper" canonical-runtime "$runtime")
    session_id=$(python3 "$session_helper" token)
    umask 077
    mkdir -m 700 -- "$dir"
    printf '%s\n' "$session_id" >"$dir/session-id"
    printf '%s\n' "$runtime" >"$dir/runtime-path"
    mkdir -m 700 -- "$dir/hooks" "$dir/hooks/autostart.d"
    ln -s -- "$hooks/libexec" "$dir/hooks/libexec"
    ln -s -- "$hooks/session-env.d" "$dir/hooks/session-env.d"
    for hook in "$hooks"/autostart.d/*; do
      [[ -e $hook || -L $hook ]] || continue
      hook_name=${hook##*/}
      if [[ $hook_name == 01-record-environment ]]; then
        ln -s -- "$repo/tests/headless-record-environment.sh" "$dir/hooks/autostart.d/$hook_name"
      else
        ln -s -- "$hook" "$dir/hooks/autostart.d/$hook_name"
      fi
    done
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
    # SCOTTLAND_TEST_PRELOAD: a library preloaded into this Wayfire only (e.g. tests/transform-census.c).
    preload=()
    [[ -n ${SCOTTLAND_TEST_PRELOAD:-} ]] && preload=(env "LD_PRELOAD=$SCOTTLAND_TEST_PRELOAD")
    (
      # A clean environment, as a display manager gives a login (not this shell's: an agent's or a
      # terminal's environment carries another desktop's variables and hides session gaps), plus
      # the session's own variables.
      for name in $(env | sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p'); do
        case $name in
          HOME|USER|LOGNAME|SHELL|LANG|LC_*|TERM|TMPDIR|stock|test_goo|test_gles|test_outputs|debugger|XDG_RUNTIME_DIR|DBUS_SESSION_BUS_ADDRESS|SCOTTLAND_WIDGET_PATH|SCOTTLAND_WIDGET_SCOPE|SCOTTLAND_HEADLESS_OUTPUTS|SCOTTLAND_DBUS_LEGACY|repo|dir|hooks|runtime|exec_tool|started) ;;
          *) unset "$name" 2>/dev/null || true ;;
        esac
      done
      export PATH=/usr/local/bin:/usr/bin:/bin
      export SCOTTLAND_TEST_MODEL=1
      # Isolate every child's settings and logs, not just config generation. Keep the real
      # runtime so Wayland display names (and systemd widget scopes) remain unique.
      export XDG_RUNTIME_DIR=$runtime SCOTTLAND_HEADLESS_DIR=$dir SCOTTLAND_TEST_SESSION_ID=$session_id
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
      export SCOTTLAND_HOOKS=$dir/hooks SCOTTLAND_HEADLESS_SESSION_HELPER=$session_helper
      export XDG_CURRENT_DESKTOP=Scottland:Wayfire:wlroots XDG_SESSION_TYPE=wayland
      # Focus-mode hooks (full screen) touch the desktop (e.g. its notifications): a test session
      # runs only its own, from its folder.
      mkdir -p "$dir/focus.d"; export SCOTTLAND_FOCUS_HOOKS=$dir/focus.d
      for env_hook in "$hooks"/session-env.d/*.sh; do [[ -r $env_hook ]] && . "$env_hook"; done
      # Test this checkout's shipped defaults, not the installed package's possibly older base
      # config or the machine's personal settings (layout.ini, overrides.ini).
      mkdir -p "$dir/config/scottland"
      cp "$repo/core/config/scottland.ini" "$dir/config/scottland/scottland.ini"
      "$hooks/libexec/scottland-build-config" --output "$dir/wayfire.ini" >/dev/null
      hook_list=${started[*]}
      sed -i -e 's/^plugins = \\$/plugins = stipc \\/' \
        -e "s#^scottland_hooks = .*#scottland_hooks = sh -c 'for h in $hook_list; do \"\$SCOTTLAND_HOOKS/autostart.d/\$h\" || exit; done'#" \
        "$dir/wayfire.ini"
      # A protocol control: same clients/config/stock Wayfire, without Scottland's
      # scene transforms or input handlers. Autostart still records its environment.
      if ((stock)); then sed -i '/^  scottland \\/d' "$dir/wayfire.ini"; fi
      # No override exercises shipped defaults; 0 explicitly tests the fallback halo.
      if [[ $test_goo == 1 || $test_goo == 0 ]]; then
        goo_value=false; [[ $test_goo == 1 ]] && goo_value=true
        sed -i "/^goo =/d; /^\[scottland\]/a goo = $goo_value" "$dir/wayfire.ini"
      fi
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
        setsid ${private_bus:+dbus-run-session --} "${debugger[@]}" "${preload[@]}" wayfire -c "$dir/wayfire.ini" >"$dir/wayfire.log" 2>&1 </dev/null &
      echo $! >"$dir/pid"
    )
    for _ in $(seq 100); do
      name=$(sed -n 's/.*Using socket name \(wayland-[0-9]*\).*/\1/p' "$dir/wayfire.log")
      [[ -n $name ]] && python3 "$session_helper" owns-env "$runtime/scottland/$name.env" "$session_id" && break
      sleep 0.1
    done
    [[ -n ${name:-} ]] && python3 "$session_helper" owns-env "$runtime/scottland/$name.env" "$session_id" || {
      echo "headless Scottland didn't start with an owned environment record; see $dir/wayfire.log" >&2
      exit 1
    }
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
    # A runner may stream this script to a remote `bash -s` over SSH. Never let
    # a client or command launched here consume the shell's remaining script.
    exec "$exec_tool" --display "$(display)" -- "$@" </dev/null
    ;;
  ipc)
    shift
    exec "$exec_tool" --display "$(display)" -- python3 "$repo/tests/wfipc.py" "$@" </dev/null
    ;;
  stop)
    [[ -e $dir || -L $dir ]] || exit 0
    session_id=$(python3 "$session_helper" verify "$build" "$dir" --field token)
    owned_runtime=$(python3 "$session_helper" verify "$build" "$dir" --field runtime)
    name=$(cat "$dir/display" 2>/dev/null || true)
    capture_failed=0
    python3 "$session_helper" capture-runtime "$build" "$dir" "$owned_runtime" "$session_id" || capture_failed=1
    python3 "$session_helper" signal "$session_id" TERM
    if ! python3 "$session_helper" wait "$session_id" 3; then
      python3 "$session_helper" signal "$session_id" KILL
      python3 "$session_helper" wait "$session_id" 2
    fi
    if ((capture_failed)); then
      echo 'runtime ownership audit failed; stopped owned processes and preserved session state' >&2
      exit 1
    fi
    python3 "$session_helper" clean-runtime "$build" "$dir" "$owned_runtime" "$session_id" "$name"
    python3 "$session_helper" remove-dir "$build" "$dir" "$session_id"
    echo "stopped owned headless Scottland session${name:+ on $name}"
    ;;
  *)
    sed -n '2,15p' "$0"; exit 1
    ;;
esac
