#!/bin/bash
# Four power/reload scenarios with the built plugin in a private pixman compositor.
# Use --expect-bug only with a pre-fix plugin to establish the regression.
set -euo pipefail
repo=$(cd -- "$(dirname -- "$0")/.." && pwd -P)
[[ $# == 0 || ($# == 1 && $1 == --expect-bug) ]] || { echo 'usage: output-power-reload-test.sh [--expect-bug]' >&2; exit 2; }
[[ -f "$repo/build/libscottland.so" ]] || { echo 'Build the plugin with make plugin first.' >&2; exit 2; }
run=$(mktemp -d "$repo/build/output-power-reload.XXXXXXXX")
mkdir -p "$run"/{home,runtime,config,configdirs,state,cache,data}
chmod 700 "$run/runtime"
cat >"$run/config/wayfire.ini" <<'INI'
[core]
plugins = ipc ipc-rules move scottland
xwayland = false
close_top_view = <super> KEY_Q

[output:HEADLESS-1]
mode = 640x480@60000
scale = 1
INI
cat >"$run/config/foot.ini" <<'INI'
[main]
font=monospace:size=14
initial-window-size-pixels=600x420
INI
printf 'Evidence: %s\n' "$run"
bwrap --unshare-all --die-with-parent --new-session \
  --ro-bind /usr /usr --ro-bind /etc /etc \
  --symlink usr/bin /bin --symlink usr/lib /lib --symlink usr/lib /lib64 \
  --proc /proc --dev /dev --tmpfs /tmp \
  --bind "$run" /test --ro-bind "$repo" /source --chdir /test --clearenv \
  --setenv HOME /test/home --setenv PATH /usr/bin:/bin \
  --setenv XDG_RUNTIME_DIR /test/runtime --setenv XDG_CONFIG_HOME /test/config \
  --setenv XDG_CONFIG_DIRS /test/configdirs --setenv XDG_STATE_HOME /test/state \
  --setenv XDG_CACHE_HOME /test/cache --setenv XDG_DATA_HOME /test/data \
  --setenv XDG_DATA_DIRS /usr/share --setenv WLR_BACKENDS headless \
  --setenv WLR_HEADLESS_OUTPUTS 1 --setenv WLR_RENDERER pixman \
  --setenv WLR_NO_HARDWARE_CURSORS 1 --setenv LIBGL_ALWAYS_SOFTWARE 1 \
  --setenv XDG_SESSION_TYPE wayland --setenv LANG C.UTF-8 \
  --setenv WAYFIRE_PLUGIN_PATH /source/build \
  --setenv WAYFIRE_PLUGIN_XML_PATH /source/core/plugin/metadata:/usr/share/wayfire/metadata \
  /usr/bin/python3 /source/tests/output-power-reload-test.py /test "$@" >"$run/comparison.log" 2>&1
