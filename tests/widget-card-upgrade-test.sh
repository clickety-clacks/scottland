#!/bin/bash
# Upgrading the card package under a docked card leaves the card and its app open (WG5).
#   tests/widget-card-upgrade-test.sh ARTIFACTS
# Needs a fresh SCOTTLAND_HEADLESS_DIR under build/. The test upgrades a scratch copy of the card
# package inside that directory, never the checkout's.
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd -P)
cd "$repo"
: "${SCOTTLAND_HEADLESS_DIR:?set a fresh isolated headless test directory}"
dir=$(realpath -m "$SCOTTLAND_HEADLESS_DIR")
[[ $(dirname "$dir") == "$repo/build" && ! -e $dir && ! -L $dir ]] || {
  echo 'headless directory must be a fresh direct child of this checkout build/' >&2
  exit 1
}
owner_id=$(python3 -c 'import uuid; print(uuid.uuid4())')
owner_record="$dir.widget-upgrade-owner"
[[ ! -e $owner_record && ! -L $owner_record ]] || { echo 'headless owner record occupied' >&2; exit 1; }
artifacts=$(realpath -m "${1:?artifact directory}")
case $artifacts/ in "$dir"/*) echo 'artifacts cannot be inside the headless directory' >&2; exit 1 ;; esac
mkdir -p "$artifacts"
(umask 077; set -C; printf '%s\n' "$owner_id" >"$owner_record")

export SCOTTLAND_HEADLESS_DIR="$dir"
export SCOTTLAND_HEADLESS_OWNER="$owner_id"
export SCOTTLAND_HEADLESS_OWNER_DIR="$dir"
export SCOTTLAND_WIDGET_PATH="$dir/widgets"
# Package-upgrade tests exercise direct widget processes in this private session.
export SCOTTLAND_WIDGET_SCOPE=0
runtime=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}

cleanup() {
  status=$?
  trap - EXIT INT TERM
  set +e
  cp "$dir/wayfire.log" "$artifacts/wayfire.log" 2>/dev/null
  cp "$dir/state/scottland/widgets.log" "$artifacts/widgets.log" 2>/dev/null
  python3 tests/widget-card-upgrade-cleanup.py "$dir" "$owner_record" "$runtime" "$repo" >>"$artifacts/cleanup.log" 2>&1
  cleanup_status=$?
  if ((cleanup_status != 0)); then
    echo 'owned headless cleanup was incomplete; see cleanup.log' >&2
    status=1
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

tests/headless.sh start --widgets
mkdir -p "$SCOTTLAND_WIDGET_PATH"
cp -r core/widgets/card "$SCOTTLAND_WIDGET_PATH/card"
tests/headless.sh run python3 -u tests/widget-card-upgrade-test.py "$SCOTTLAND_WIDGET_PATH/card" "$artifacts" | tee "$artifacts/results.log"
