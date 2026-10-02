#!/bin/bash
# Marked upgrade from a separately built main, with its old launcher/service/card.
set -euo pipefail
cd "$(dirname "$0")/.."
branch=$PWD
previous=${1:?usage: tests/upgrade-test.sh MAIN_CHECKOUT}
hdir=${SCOTTLAND_HEADLESS_DIR:-$XDG_RUNTIME_DIR/scottland-headless-state-fixes}
[[ ! -f $hdir/pid ]] || { echo 'headless runtime occupied' >&2; exit 1; }
(cd "$previous" && tests/headless.sh start --widgets)
cleanup() {
  cp "$hdir/wayfire.log" "$hdir/../scottland-upgrade-test.log" || true
  (cd "$previous" && tests/headless.sh stop)
}
trap cleanup EXIT
(cd "$previous" && tests/headless.sh run python3 "$branch/tests/upgrade-test.py" "$branch")
