#!/bin/bash
# Focused regressions in a private session; never installs or touches a real screen.
set -euo pipefail
cd "$(dirname "$0")/.."
hdir=${SCOTTLAND_HEADLESS_DIR:-$XDG_RUNTIME_DIR/scottland-headless-state-fixes}
[[ ! -f $hdir/pid ]] || { echo 'headless runtime occupied; leave its owner alone' >&2; exit 1; }
fixtures=$(mktemp -d)
mkdir -p "$fixtures/daemon" "$fixtures/slow"
cat >"$fixtures/daemon/widget.toml" <<'TOML'
id = "regression-daemon"
apps = ['^scottland-regression-daemon$']
exec = "./start"
TOML
printf '#!/bin/sh\nsetsid -f foot -T regression-forked-widget sh -c "exec sleep 600"\nsleep 6\n' >"$fixtures/daemon/start"
chmod +x "$fixtures/daemon/start"
cat >"$fixtures/slow/widget.toml" <<'TOML'
id = "regression-slow"
apps = ['^scottland-regression-slow$']
exec = "sh -c 'sleep 4; exec foot -T regression-late-widget sleep 600'"
TOML
export SCOTTLAND_HEADLESS_OUTPUTS=2
export SCOTTLAND_WIDGET_PATH=$fixtures SCOTTLAND_WIDGET_SCOPE=0
tests/headless.sh start --widgets
cleanup() {
  cp "$hdir/wayfire.log" "$hdir/../scottland-state-regressions.log" || true
  tests/headless.sh stop
  rm -rf "$fixtures"
}
trap cleanup EXIT
tests/headless.sh run python3 tests/state-regressions-test.py
