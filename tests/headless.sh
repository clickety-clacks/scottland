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
#   tests/headless.sh stop [--preserve-scratch]
#
# Requires bubblewrap to put a run-owned runtime view at XDG_RUNTIME_DIR without changing its
# value. All runtime files and TMPDIR then live under the validated checkout build scratch.
# Put SCOTTLAND_HEADLESS_DIR under the checkout's build/ directory.
# Helpers come from this checkout (make test-hooks) if built, else the dev install. Set
# SCOTTLAND_HEADLESS_DIR to run test sessions of several checkouts at once.
#
# Example: tests/headless.sh start --omarchy && tests/headless.sh run foot &
#          tests/headless.sh ipc stipc/feed_key '{"key":"KEY_LEFTMETA","state":true}'
set -euo pipefail
repo=$(cd -- "$(dirname -- "$0")/.." && pwd -P)
runtime=$(realpath -e -- "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}")
uid=$(id -u)
build=$(realpath -m -- "$repo/build")
# SCOTTLAND_HEADLESS_DIR: where this test session keeps its state, so test sessions of several
# checkouts (e.g. agents on branches sharing a test machine) can run at once.
dir=$(realpath -m -- "${SCOTTLAND_HEADLESS_DIR:-$build/headless}")
runtime_scratch=$dir/runtime
tmp_scratch=$dir/tmp
owner=$dir/.scottland-headless-owner
session_mode_record=$dir/.scottland-headless-mode
inside=0
if [[ ${1:-} == __scottland_headless_private_runtime ]]; then
  inside=1
  shift
fi
case "$dir/" in
  "$build"/*) ;;
  *) echo 'SCOTTLAND_HEADLESS_DIR must be a child of this checkout build/' >&2; exit 2 ;;
esac
[[ $dir != "$build" ]] || { echo 'SCOTTLAND_HEADLESS_DIR cannot be the build directory itself' >&2; exit 2; }
[[ -d $runtime && $(stat -c %u -- "$runtime") == "$uid" ]] || {
  echo "XDG_RUNTIME_DIR must be an existing directory owned by uid $uid" >&2
  exit 2
}
verify_owner() {
  [[ -d $dir && ! -L $dir && -f $owner && ! -L $owner ]] || {
    echo 'headless scratch has no regular owner record; refusing to use or remove it' >&2
    return 1
  }
  [[ $(sed -n '1p' "$owner") == "$uid" && $(sed -n '2p' "$owner") == "$runtime" && \
     $(sed -n '3p' "$owner") == "${SCOTTLAND_HEADLESS_OWNER:-}" ]] || {
    echo 'headless scratch belongs to another uid or runtime; refusing to use or remove it' >&2
    return 1
  }
  [[ -d $runtime_scratch && ! -L $runtime_scratch && -d $tmp_scratch && ! -L $tmp_scratch ]] || {
    echo 'headless runtime/TMPDIR scratch is missing or redirected; refusing to continue' >&2
    return 1
  }
  [[ $(stat -c %u -- "$dir") == "$uid" && $(stat -c %u -- "$runtime_scratch") == "$uid" && \
     $(stat -c %u -- "$tmp_scratch") == "$uid" ]] || {
    echo 'headless scratch is not owned by this uid; refusing to use or remove it' >&2
    return 1
  }
  [[ $(stat -c %a -- "$dir") == 700 && $(stat -c %a -- "$runtime_scratch") == 700 && \
     $(stat -c %a -- "$tmp_scratch") == 700 ]] || {
    echo 'headless scratch permissions must remain private (0700)' >&2
    return 1
  }
}
wayfire_process_is_ours() {
  python3 - "$1" "$dir/wayfire.ini" <<'PY'
import pathlib, sys
try:
    args = pathlib.Path('/proc/' + sys.argv[1] + '/cmdline').read_bytes().split(b'\0')
    sys.exit(not (sys.argv[2].encode() in args and any(a.endswith(b'wayfire') for a in args)))
except OSError:
    sys.exit(1)
PY
}
scratch_mounts_clear() {
  python3 - "$dir" "$runtime_scratch" "$tmp_scratch" <<'PY'
import os, re, sys

roots = tuple(os.path.normpath(path) for path in sys.argv[1:])
def unescape_mount_path(path):
    return re.sub(r'\\([0-7]{3})', lambda match: chr(int(match.group(1), 8)), path)

try:
    with open('/proc/self/mountinfo', encoding='utf-8') as mountinfo:
        for line in mountinfo:
            fields = line.split()
            if len(fields) < 5:
                continue
            mounted = unescape_mount_path(fields[4])
            if any(mounted == root or mounted.startswith(root + os.sep) for root in roots):
                print(f'refusing to remove headless scratch while a mount remains at {mounted}',
                      file=sys.stderr)
                sys.exit(1)
except OSError as error:
    print(f'cannot verify headless scratch mounts; preserving scratch: {error}', file=sys.stderr)
    sys.exit(1)
PY
}
remove_owned_scratch() {
  verify_owner || return 1
  scratch_mounts_clear || return 1
  rm -rf -- "$dir"
}
created_scratch=0
cleanup_created_scratch() {
  status=$?
  trap - EXIT
  if ((status != 0 && created_scratch)) && [[ -d $dir && ! -L $dir ]]; then
    rm -rf -- "$dir"
  fi
  exit "$status"
}
if ((inside == 0)); then
  action=${1:-}
  preserve_scratch=0
  case ${1:-} in
    start)
      command -v bwrap >/dev/null || { echo 'headless tests need bubblewrap for runtime isolation' >&2; exit 2; }
      if [[ -e $dir || -L $dir ]]; then
        verify_owner || exit 2
      else
        mkdir -m 700 -- "$dir"
        created_scratch=1
        trap cleanup_created_scratch EXIT
        mkdir -m 700 -- "$runtime_scratch" "$tmp_scratch"
        printf '%s\n%s\n%s\n' "$uid" "$runtime" "${SCOTTLAND_HEADLESS_OWNER:-}" >"$owner"
        chmod 600 -- "$owner"
        trap - EXIT
        created_scratch=0
      fi
      ;;
    stop)
      if [[ $# -gt 2 || ( -n ${2:-} && ${2:-} != --preserve-scratch ) ]]; then
        echo 'stop accepts only the optional --preserve-scratch flag' >&2
        exit 2
      fi
      [[ ${2:-} != --preserve-scratch ]] || preserve_scratch=1
      [[ -e $dir || -L $dir ]] || exit 0
      command -v bwrap >/dev/null || { echo 'headless cleanup needs bubblewrap for runtime isolation' >&2; exit 2; }
      verify_owner || exit 2
      ;;
    run|ipc)
      command -v bwrap >/dev/null || { echo 'headless commands need bubblewrap for runtime isolation' >&2; exit 2; }
      verify_owner || exit 2
      ;;
    *)
      sed -n '8,20p' "$0" >&2
      exit 1
      ;;
  esac
  session_userns_args=()
  if [[ -f $dir/display ]]; then
    compositor=$(cat "$dir/compositor.pid" 2>/dev/null || true)
    if [[ $compositor =~ ^[0-9]+$ ]] && kill -0 "$compositor" 2>/dev/null; then
      wayfire_process_is_ours "$compositor" || {
        echo 'headless compositor PID is not this run; refusing to join its namespace' >&2
        exit 2
      }
      if ! exec {session_userns_fd}<"/proc/$compositor/ns/user"; then
        echo 'cannot open this headless session user namespace; refusing an isolated run' >&2
        exit 2
      fi
      session_userns_args=(--userns "$session_userns_fd")
    elif [[ ${1:-} == run || ${1:-} == ipc ]]; then
      echo 'headless compositor is not live; refusing to run outside its session namespace' >&2
      exit 2
    fi
  elif [[ $action == stop ]]; then
    # A startup can fail after Wayfire starts but before the display/environment markers are
    # published. Join only when the partial-start PID still proves it is this run's Wayfire.
    for pid_file in "$dir/compositor.pid" "$dir/pid"; do
      cleanup_pid=$(cat "$pid_file" 2>/dev/null || true)
      if [[ $cleanup_pid =~ ^[0-9]+$ ]] && kill -0 "$cleanup_pid" 2>/dev/null; then
        wayfire_process_is_ours "$cleanup_pid" || {
          echo 'partial headless PID is not this run; refusing to join its namespace' >&2
          exit 2
        }
        if ! exec {session_userns_fd}<"/proc/$cleanup_pid/ns/user"; then
          echo 'cannot open this partial headless session user namespace; preserving scratch' >&2
          exit 2
        fi
        session_userns_args=(--userns "$session_userns_fd")
        break
      fi
    done
  fi
  bwrap_args=(--bind / / --dev-bind /dev /dev)
  if ((${#session_userns_args[@]})); then
    bwrap_args+=("${session_userns_args[@]}")
  fi
  bwrap_args+=(--bind "$runtime_scratch" "$runtime" --bind "$tmp_scratch" /tmp
    --setenv XDG_RUNTIME_DIR "$runtime" --setenv TMPDIR "$tmp_scratch")
  # Explicitly bind /dev in the root-bind namespace so Bash can open /dev/null and the
  # compositor can see its render node. Reuse a live session's user namespace for later
  # invocations so run processes retain access to that session's /proc entries; runtime and
  # TMPDIR are still privately bound to this run's owned scratch.
  if bwrap "${bwrap_args[@]}" -- \
    "$repo/tests/headless.sh" __scottland_headless_private_runtime "$@"; then
    status=0
  else
    status=$?
  fi
  if [[ $action == start && $status -ne 0 && -d $dir && ! -L $dir ]] && verify_owner; then
    # The failed start has left its private namespace. Stop any partial session from the outer
    # namespace and retain logs/owner state for the caller's EXIT trap to capture first.
    if ! "$repo/tests/headless.sh" stop --preserve-scratch; then
      echo 'failed to stop partial headless session; preserving owned scratch for inspection' >&2
    fi
  fi
  if [[ $action == stop && $status -eq 0 && $preserve_scratch -eq 0 ]]; then
    remove_owned_scratch || exit 2
    echo 'removed run-owned headless scratch after its private namespace exited'
  fi
  exit "$status"
fi
verify_owner || exit 2
export XDG_RUNTIME_DIR=$runtime TMPDIR=$tmp_scratch SCOTTLAND_HEADLESS_DIR=$dir
# The checkout's own helpers (make test-hooks) when it has them, else the dev install, else the
# package's.
hooks=$repo/build/hooks
[[ -d $hooks/libexec ]] || hooks=${XDG_DATA_HOME:-$HOME/.local/share}/scottland/dev
[[ -d $hooks/libexec ]] || hooks=/usr/lib/scottland
exec_tool=$hooks/libexec/scottland-exec

display() { cat "$dir/display"; }
session_process_is_ours() {
  python3 - "$1" "$dir" "$runtime" "$tmp_scratch" "$2" <<'PY'
import pathlib, sys
pid, scratch, runtime, tmpdir, display = sys.argv[1:]
try:
    entries = pathlib.Path('/proc/' + pid + '/environ').read_bytes().split(b'\0')
    env = dict(entry.split(b'=', 1) for entry in entries if b'=' in entry)
except OSError:
    sys.exit(1)
expected = {
    b'SCOTTLAND_HEADLESS_DIR': scratch.encode(),
    b'TMPDIR': tmpdir.encode(),
    b'XDG_RUNTIME_DIR': runtime.encode(),
    b'XDG_STATE_HOME': (scratch + '/state').encode(),
    b'WAYLAND_DISPLAY': display.encode(),
}
sys.exit(not all(env.get(key) == value for key, value in expected.items()))
PY
}
read_session_signature() {
  python3 - "$1" <<'PY'
import pathlib, re, sys

path = pathlib.Path(sys.argv[1])
try:
    if path.is_symlink() or not path.is_file():
        sys.exit(2)
    entries = path.read_bytes().split(b'\0')
except OSError:
    sys.exit(2)
signatures = [entry.split(b'=', 1)[1] for entry in entries
              if entry.startswith(b'HYPRLAND_INSTANCE_SIGNATURE=')]
if not signatures:
    print('')
    sys.exit(0)
if len(signatures) != 1 or not re.fullmatch(rb'scottland_[A-Za-z0-9_-]+', signatures[0]):
    sys.exit(2)
try:
    print(signatures[0].decode('ascii'))
except UnicodeDecodeError:
    sys.exit(2)
PY
}
validate_session_environment() {
  local session_display=$1 expected_signature=$2 env_file actual_signature
  env_file="$runtime/scottland/$session_display.env"
  [[ -d $runtime/scottland && ! -L $runtime/scottland && -f $env_file && \
     ! -L $env_file && -s $env_file ]] || {
    echo 'headless session environment record is missing or redirected; preserving runtime and scratch' >&2
    return 1
  }
  if ! actual_signature=$(read_session_signature "$env_file"); then
    echo 'headless session signature is invalid; preserving runtime and scratch' >&2
    return 1
  fi
  [[ $actual_signature == "$expected_signature" ]] || {
    echo 'headless session signature does not match its owned mode record; preserving runtime and scratch' >&2
    return 1
  }
}
session_shim_process_state() {
  # Return 0 for this run's live shim, 1 when the PID has exited, and 2 when
  # identity cannot be proved. The lock lives in this run's private runtime,
  # but its PID may still be stale or reused.
  python3 - "$1" "$dir" "$runtime" "$tmp_scratch" "$2" "$3" \
    "${SCOTTLAND_HEADLESS_OWNER:-}" "$4" <<'PY'
import pathlib, sys

pid, scratch, runtime, tmpdir, display, signature, owner, shim = sys.argv[1:]
proc = pathlib.Path('/proc') / pid
try:
    stat = (proc / 'stat').read_bytes()
except FileNotFoundError:
    sys.exit(1)
except OSError:
    sys.exit(2)
try:
    state = stat[stat.rfind(b')') + 2:].split()[0]
except (IndexError, ValueError):
    sys.exit(2)
if state in (b'Z', b'X'):
    sys.exit(1)
try:
    entries = (proc / 'environ').read_bytes().split(b'\0')
    env = dict(entry.split(b'=', 1) for entry in entries if b'=' in entry)
    command = (proc / 'cmdline').read_bytes().split(b'\0')
except FileNotFoundError:
    sys.exit(1)
except OSError:
    sys.exit(2)
expected = {
    b'SCOTTLAND_HEADLESS_DIR': scratch.encode(),
    b'TMPDIR': tmpdir.encode(),
    b'XDG_RUNTIME_DIR': runtime.encode(),
    b'XDG_STATE_HOME': (scratch + '/state').encode(),
    b'WAYLAND_DISPLAY': display.encode(),
    b'HYPRLAND_INSTANCE_SIGNATURE': signature.encode(),
}
if owner:
    expected[b'SCOTTLAND_HEADLESS_OWNER'] = owner.encode()
if not all(env.get(key) == value for key, value in expected.items()) or \
   shim.encode() not in command:
    sys.exit(2)
sys.exit(0)
PY
}
stop_session_shim() {
  local pid=$1 display=$2 signature=$3 shim=$4 process_state signal
  if session_shim_process_state "$pid" "$display" "$signature" "$shim"; then
    :
  else
    process_state=$?
    if ((process_state == 1)); then return 0; fi
    echo "headless shim PID $pid is not this run's shim; preserving runtime and scratch" >&2
    return 1
  fi
  for signal in TERM KILL; do
    if [[ $signal == TERM ]]; then
      kill -TERM "$pid" 2>/dev/null || true
    else
      # Revalidate the exact run-owned process immediately before escalation.
      if session_shim_process_state "$pid" "$display" "$signature" "$shim"; then
        kill -KILL "$pid" 2>/dev/null || true
      else
        process_state=$?
        if ((process_state == 1)); then return 0; fi
        echo "headless shim PID $pid changed identity before forced stop; preserving runtime and scratch" >&2
        return 1
      fi
    fi
    for _ in $(seq 30); do
      if session_shim_process_state "$pid" "$display" "$signature" "$shim"; then
        sleep 0.1
      else
        process_state=$?
        if ((process_state == 1)); then return 0; fi
        echo "headless shim PID $pid no longer proves this run; preserving runtime and scratch" >&2
        return 1
      fi
    done
  done
  echo "headless shim PID $pid did not exit; preserving runtime and scratch" >&2
  return 1
}
stop_recorded_shim() {
  local session_display=$1 signature=$2 lock_dir lock hypr_pid shim
  [[ $signature =~ ^scottland_[A-Za-z0-9_-]+$ ]] || {
    echo 'headless shim session signature is missing or invalid; preserving runtime and scratch' >&2
    return 1
  }
  validate_session_environment "$session_display" "$signature" || return 1
  lock_dir="$runtime/hypr/$signature"
  lock="$lock_dir/hyprland.lock"
  [[ -d $runtime/hypr && ! -L $runtime/hypr && -d $lock_dir && \
     ! -L $lock_dir && -f $lock && ! -L $lock ]] || {
    echo 'headless shim lock path is missing, redirected, or not a regular file; preserving runtime and scratch' >&2
    return 1
  }
  [[ $(sed -n 2p "$lock") == "$session_display" ]] || {
    echo 'headless shim lock belongs to a different display; preserving runtime and scratch' >&2
    return 1
  }
  hypr_pid=$(sed -n 1p "$lock")
  [[ $hypr_pid =~ ^[0-9]+$ ]] || {
    echo 'headless shim lock has an invalid PID; preserving runtime and scratch' >&2
    return 1
  }
  shim="$hooks/libexec/scottland-hyprshim"
  stop_session_shim "$hypr_pid" "$session_display" "$signature" "$shim" || return 1
  # Remove the lock only after its process is gone or its exact run identity was
  # proved and the shim exited. All ambiguous lock cases preserve scratch.
  rm -rf -- "$lock_dir"
}

case ${1:-} in
  start)
    if [[ -f $dir/display && -f $dir/compositor.pid ]]; then
      compositor=$(cat "$dir/compositor.pid")
      if [[ $compositor =~ ^[0-9]+$ ]] && wayfire_process_is_ours "$compositor"; then
        echo "already running on $(display)"
        exit 0
      fi
      echo 'headless scratch has stale session state; stop it or choose a fresh directory' >&2
      exit 1
    fi
    if [[ -e $dir/pid || -e $dir/display ]]; then
      echo 'headless scratch has partial session state; stop it or choose a fresh directory' >&2
      exit 1
    fi
    if [[ -n $(find "$dir" -mindepth 1 -maxdepth 1 ! -name .scottland-headless-owner ! -name runtime ! -name tmp -print -quit) || \
          -n $(find "$runtime_scratch" -mindepth 1 -print -quit) || \
          -n $(find "$tmp_scratch" -mindepth 1 -print -quit) ]]; then
      echo 'headless scratch is not fresh; stop it or choose a fresh directory' >&2
      exit 1
    fi
    [[ ! -e $session_mode_record && ! -L $session_mode_record ]] || {
      echo 'headless session mode marker already exists; refusing to start in reused scratch' >&2
      exit 2
    }
    printf 'plain-pending\n' >"$session_mode_record"
    chmod 600 -- "$session_mode_record"
    started=(01-record-environment)
    shim_expected=0
    test_goo=${SCOTTLAND_TEST_GOO:-}
    test_gles=${SCOTTLAND_TEST_GOO_GLES:-}
    test_outputs=${SCOTTLAND_TEST_OUTPUTS:-${SCOTTLAND_HEADLESS_OUTPUTS:-1}}
    stock=0
    private_bus=1
    debugger=()
    for option in "${@:2}"; do
      case $option in
        --stock) stock=1 ;;
        --omarchy)
          printf 'shim-pending\n' >"$session_mode_record"
          shim_expected=1
          started+=(10-hyprshim 25-omarchy-override-report 30-lua-host)
          ;;
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
          HOME|USER|LOGNAME|SHELL|LANG|LC_*|TERM|TMPDIR|stock|test_goo|test_gles|test_outputs|debugger|XDG_RUNTIME_DIR|DBUS_SESSION_BUS_ADDRESS|SCOTTLAND_WIDGET_PATH|SCOTTLAND_WIDGET_SCOPE|SCOTTLAND_HEADLESS_OUTPUTS|SCOTTLAND_HEADLESS_DIR|SCOTTLAND_HEADLESS_OWNER|SCOTTLAND_HEADLESS_OWNER_DIR|SCOTTLAND_DBUS_LEGACY|repo|dir|hooks|runtime|exec_tool|started) ;;
          *) unset "$name" 2>/dev/null || true ;;
        esac
      done
      export PATH=/usr/local/bin:/usr/bin:/bin
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
exec bwrap --bind / / --dev-bind /dev /dev --bind "$XDG_STATE_HOME/../quickshell" "$XDG_RUNTIME_DIR/quickshell" -- /usr/bin/quickshell "$@"
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
      "$hooks/libexec/scottland-build-config" --output "$dir/wayfire.ini" >/dev/null
      hook_list=${started[*]}
      sed -i -e 's/^plugins = \\$/plugins = stipc \\/' \
        -e "s#^scottland_hooks = .*#scottland_hooks = sh -c 'for h in $hook_list; do \"\$SCOTTLAND_HOOKS/autostart.d/\$h\" \& done; wait'#" \
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
      env_file=$runtime/scottland/$name.env
      [[ -n $name && -f $env_file && ! -L $env_file && -s $env_file ]] && break
      sleep 0.1
    done
    [[ -n ${name:-} ]] || { echo "headless Wayfire didn't publish a socket name; see $dir/wayfire.log" >&2; exit 1; }
    env_file=$runtime/scottland/$name.env
    [[ -f $env_file && ! -L $env_file && -s $env_file ]] || {
      echo "headless session environment record is missing or redirected: $env_file; see $dir/wayfire.log" >&2
      exit 1
    }
    if ! signature=$(read_session_signature "$env_file"); then
      echo 'headless session signature is invalid; preserving runtime and scratch' >&2
      exit 1
    fi
    if ((shim_expected)); then
      [[ $signature =~ ^scottland_[A-Za-z0-9_-]+$ ]] || {
        echo 'headless shim session signature is missing or invalid; preserving runtime and scratch' >&2
        exit 1
      }
      printf 'shim\n%s\n%s\n' "$name" "$signature" >"$session_mode_record"
    else
      printf 'plain\n%s\n%s\n' "$name" "$signature" >"$session_mode_record"
    fi
    chmod 600 -- "$session_mode_record"
    # The debugger/private-bus wrapper can exit independently. Retain the actual
    # compositor identity so stop still reaps our inferior in that case.
    python3 - "$env_file" "$dir/compositor.pid" <<'PY'
import pathlib, socket, struct, sys
entries = pathlib.Path(sys.argv[1]).read_bytes().split(b'\0')
endpoint = next(e.split(b'=', 1)[1] for e in entries if e.startswith(b'WAYFIRE_SOCKET='))
with socket.socket(socket.AF_UNIX) as peer:
    peer.settimeout(2)
    peer.connect(endpoint.decode())
    pid, _, _ = struct.unpack('3i', peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
pathlib.Path(sys.argv[2]).write_text(str(pid) + '\n')
PY
    echo "$name" >"$dir/display"
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
    if [[ ! -f $dir/display ]]; then
      [[ -f $session_mode_record && ! -L $session_mode_record ]] || {
        echo 'headless session mode is missing or redirected; preserving runtime and scratch' >&2
        exit 2
      }
      session_mode=$(sed -n '1p' "$session_mode_record")
      expected_display=$(sed -n '2p' "$session_mode_record")
      expected_signature=$(sed -n '3p' "$session_mode_record")
      case $session_mode in
        plain-pending)
          echo 'headless session mode was not fully recorded; preserving runtime and scratch' >&2
          exit 2
          ;;
        shim-pending)
          echo 'headless shim identity was not fully recorded; preserving runtime and scratch' >&2
          exit 2
          ;;
        plain)
          [[ $expected_display =~ ^wayland-[0-9]+$ ]] || {
            echo 'headless partial session display is invalid; preserving runtime and scratch' >&2
            exit 2
          }
          validate_session_environment "$expected_display" "$expected_signature" || exit 2
          ;;
        shim)
          [[ $expected_display =~ ^wayland-[0-9]+$ ]] || {
            echo 'headless partial shim display is invalid; preserving runtime and scratch' >&2
            exit 2
          }
          stop_recorded_shim "$expected_display" "$expected_signature" || exit 2
          ;;
        *)
          echo 'headless session mode is invalid; preserving runtime and scratch' >&2
          exit 2
          ;;
      esac
      if [[ -f $dir/pid ]]; then
        pid=$(cat "$dir/pid")
        if [[ $pid =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
          wayfire_process_is_ours "$pid" || { echo 'partial headless PID is not this run; refusing to kill it' >&2; exit 1; }
          group=$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ' || true)
          if [[ $group == "$pid" ]]; then
            kill -- "-$pid" 2>/dev/null || true
          else
            for child in $(ps -o pid= --ppid "$pid" 2>/dev/null); do kill "$child" 2>/dev/null || true; done
            kill "$pid" 2>/dev/null || true
          fi
          for _ in $(seq 30); do kill -0 "$pid" 2>/dev/null || break; sleep 0.1; done
          kill -9 "$pid" 2>/dev/null || true
          [[ $group != "$pid" ]] || kill -9 -- "-$pid" 2>/dev/null || true
        fi
      fi
      if [[ $session_mode == plain || $session_mode == shim ]]; then
        rm -f -- "$runtime/scottland/$expected_display.env" \
          "$runtime/scottland/$expected_display.lua.fifo"
      fi
      echo 'stopped partial headless session; retaining owned scratch for outer cleanup'
      exit 0
    fi
    name=$(display)
    [[ -f $session_mode_record && ! -L $session_mode_record ]] || {
      echo 'headless session mode is missing or redirected; preserving runtime and scratch' >&2
      exit 2
    }
    session_mode=$(sed -n '1p' "$session_mode_record")
    expected_display=$(sed -n '2p' "$session_mode_record")
    expected_signature=$(sed -n '3p' "$session_mode_record")
    [[ $expected_display == "$name" ]] || {
      echo 'headless session mode display does not match the started session; preserving runtime and scratch' >&2
      exit 2
    }
    case $session_mode in
      plain) validate_session_environment "$name" "$expected_signature" || exit 2 ;;
      shim) stop_recorded_shim "$name" "$expected_signature" || exit 2 ;;
      *)
        echo 'headless session mode is incomplete or invalid; preserving runtime and scratch' >&2
        exit 2
        ;;
    esac
    # Stop the session's helpers by their pid files, then Wayfire (its clients follow).
    # (Pid files hold the pid on their first line; the color-scheme watcher leads its own group,
    # with its monitors.)
    for pid_file in "$runtime/scottland/$name.lua.pid" "$runtime/scottland/$name.color-scheme.pid" "$runtime/scottland/$name.widget-bus.pid"; do
      helper=$(sed -n 1p "$pid_file" 2>/dev/null || true)
      if [[ $helper =~ ^[0-9]+$ ]] && session_process_is_ours "$helper" "$name" && \
         grep -qa -e scottland-color-scheme -e lua -e scottland-widget-bus "/proc/$helper/cmdline" 2>/dev/null; then
        if [[ $(ps -o pgid= -p "$helper" | tr -d ' ') == "$helper" ]]; then kill -- "-$helper" 2>/dev/null; else kill "$helper" 2>/dev/null; fi
      fi
      rm -f "$pid_file"
    done
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
    rm -f "$runtime/scottland/$name.env" "$runtime/scottland/$name.lua.fifo"
    echo "stopped headless Scottland on $name; retaining owned scratch for outer cleanup"
    ;;
  *)
    sed -n '2,15p' "$0"; exit 1
    ;;
esac
