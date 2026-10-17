#!/usr/bin/env bash
set -euo pipefail

# The registered runner must be a dedicated test account with an existing
# private runtime. Refuse a live graphical session on that account.
[[ ${SCOTTLAND_CI_DEDICATED:-} == 1 ]] || { echo 'dedicated CI runner not configured' >&2; exit 2; }
[[ -n ${XDG_RUNTIME_DIR:-} && -d $XDG_RUNTIME_DIR && $(stat -c %u "$XDG_RUNTIME_DIR") == "$(id -u)" ]] || {
  echo 'CI runtime missing or not owned by runner account' >&2; exit 2;
}
if pgrep -u "$(id -u)" -x wayfire >/dev/null; then
  echo 'runner account already has Wayfire; refusing to touch a live session' >&2
  exit 2
fi

make test-hooks
make tools-test
for suite in inertia navigation pairing peek rail-make-room spread windowing; do
  bash "tests/${suite}-unit.sh"
done

umask 077
mkdir -p build
scratch=$(mktemp -d -p "$PWD/build" fast-check.XXXXXXXX)
export SCOTTLAND_HEADLESS_ISOLATION=1 SCOTTLAND_TEST_SCRATCH=$scratch
export SCOTTLAND_HEADLESS_DIR=$scratch/headless
export SCOTTLAND_HEADLESS_OWNER="ci-${GITHUB_RUN_ID:-manual}-${GITHUB_RUN_ATTEMPT:-1}-$$"
evidence="$PWD/build/ci-evidence/$SCOTTLAND_HEADLESS_OWNER"
mkdir -p -- "$evidence"

cleanup() {
  local result=$?
  trap - EXIT
  if [[ -d $SCOTTLAND_HEADLESS_DIR ]]; then
    if ! tests/headless.sh stop; then
      echo "headless stop failed; preserving $scratch for runner-owned inspection" >&2
      exit 2
    fi
  fi
  rmdir -- "$scratch" || { echo "scratch not empty; preserving $scratch" >&2; exit 2; }
  exit "$result"
}
trap cleanup EXIT

tests/headless.sh start
tests/headless.sh run true
tests/headless.sh ipc window-rules/list-outputs >"$scratch/outputs.json"
tests/headless.sh ipc window-rules/list-views >"$scratch/views.json"
python3 - "$scratch/outputs.json" "$scratch/views.json" <<'PY'
import json, sys
outputs = json.load(open(sys.argv[1], encoding='utf-8'))
views = json.load(open(sys.argv[2], encoding='utf-8'))
assert isinstance(outputs, list) and outputs, 'headless output missing'
assert isinstance(views, list), 'window list is not an array'
print(f'headless IPC: {len(outputs)} output(s), {len(views)} view(s)')
PY
rm -- "$scratch/outputs.json" "$scratch/views.json"
tests/headless.sh run python3 -u ci/headless-state-test.py
