#!/usr/bin/env bash
# Behavior checks for hold-forms review findings B1 and B3. Run each scenario in
# a fresh owned headless session; B2's release-timing behavior is in pairing-unit.sh.
set -euo pipefail
cd "$(dirname -- "$0")/.."

[[ $# == 1 ]] || { echo "usage: $0 ARTIFACTS_DIR" >&2; exit 2; }
repo=$(pwd -P)
build="$repo/build"
expected_head=${SCOTTLAND_REVIEW_EXPECTED_HEAD:-}
expected_tree=${SCOTTLAND_REVIEW_EXPECTED_TREE:-}
[[ $expected_head =~ ^[0-9a-f]{40}$ && $expected_tree =~ ^[0-9a-f]{40}$ ]] || {
  echo 'set the exact candidate commit and tree before running focused behavior checks' >&2
  exit 2
}
[[ $(git rev-parse HEAD) == "$expected_head" && \
   $(git rev-parse 'HEAD^{tree}') == "$expected_tree" ]] || {
  echo 'focused driver source pin mismatch; refusing to run' >&2
  exit 2
}
[[ -d $build && ! -L $build && $(realpath -e -- "$build") == "$build" ]] || {
  echo 'build directory must be a real directory in this exact checkout' >&2
  exit 2
}
artifacts=$(realpath -m -- "$1")
case "$artifacts/" in
  "$build/"*) ;;
  *) echo 'artifacts must be below this checkout build/' >&2; exit 2 ;;
esac
[[ ! -e $artifacts && ! -L $artifacts ]] || {
  echo 'artifacts directory already exists; refusing reuse' >&2
  exit 2
}
mkdir -m 700 -- "$artifacts"

# Require the runtime capabilities and helpers used by these product behaviors.
# Resolve helpers only from this candidate's build; never use a user's dev install.
command -v bwrap >/dev/null || { echo 'headless runtime isolation requires bubblewrap' >&2; exit 2; }
command -v quickshell >/dev/null || { echo 'selected widget behavior requires quickshell' >&2; exit 2; }
hooks="$build/hooks"
[[ -d $hooks && ! -L $hooks && -d $hooks/libexec && \
   $(realpath -e -- "$hooks") == "$hooks" ]] || {
  echo 'task-local build/hooks is absent or redirected; refusing host fallback' >&2
  exit 2
}
require_binding() {
  local actual=$1 source=$2 actual_real source_real
  [[ -e $actual ]] || { echo "required task-local helper is absent: $actual" >&2; return 1; }
  actual_real=$(realpath -e -- "$actual") || return 1
  source_real=$(realpath -e -- "$source") || return 1
  [[ $actual_real == "$source_real" ]] || {
    echo "task-local helper does not resolve to its pinned source: $actual" >&2
    return 1
  }
}
require_binding "$hooks/libexec/scottland-exec" "$repo/core/libexec/scottland-exec"
require_binding "$hooks/libexec/scottland-widget-bus" "$repo/core/libexec/scottland-widget-bus"
require_binding "$hooks/autostart.d/01-record-environment" "$repo/core/autostart.d/01-record-environment"
require_binding "$hooks/autostart.d/08-widget-bus" "$repo/core/autostart.d/08-widget-bus"
require_binding "$hooks/plugins/libscottland.so" "$build/libscottland.so"
export SCOTTLAND_TEST_HOOKS="$hooks"

run_scenario() (
  set -euo pipefail
  local label=$1 selector=$2 test_script=$3
  local owner headless_dir owner_dir uid owner_real started=0
  owner=$(python3 -c 'import uuid; print(uuid.uuid4())')
  [[ $owner =~ ^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$ ]] || {
    echo 'could not generate a unique UUID headless owner token' >&2
    exit 2
  }
  owner_dir="$build/hold-forms-owner-$label-$owner"
  headless_dir="$owner_dir/session-state"
  [[ ! -e $headless_dir && ! -L $headless_dir && ! -e $owner_dir && ! -L $owner_dir ]] || {
    echo 'fresh owner/session path is already occupied; refusing reuse' >&2
    exit 2
  }
  mkdir -m 700 -- "$owner_dir"
  uid=$(id -u)
  owner_real=$(realpath -e -- "$owner_dir")
  [[ -d $owner_dir && ! -L $owner_dir && $owner_real == "$owner_dir" && \
     $(stat -c %u:%a -- "$owner_dir") == "$uid:700" ]] || {
    echo 'task-owned headless owner directory failed canonical path, owner, or mode validation' >&2
    exit 2
  }
  export SCOTTLAND_HEADLESS_OWNER="$owner"
  export SCOTTLAND_HEADLESS_DIR="$headless_dir"
  export SCOTTLAND_HEADLESS_OWNER_DIR="$owner_dir"
  export SCOTTLAND_HEADLESS_ISOLATION=1
  export SCOTTLAND_TEST_SCRATCH="$owner_dir"
  export SCOTTLAND_SESSION_DIR="$headless_dir/session"

  finish() {
    local result=$?
    trap - EXIT INT TERM
    if ((started)); then
      if ! cp -- "$headless_dir/wayfire.log" "$artifacts/$label-wayfire.log"; then
        echo 'could not preserve the Wayfire log; retaining scratch and refusing retry' >&2
        result=125
      fi
      if ((result == 0)); then
        if ! tests/headless.sh stop; then
          echo 'headless stop failed; preserving scratch and refusing retry' >&2
          result=125
        elif [[ -e $headless_dir || -L $headless_dir ]]; then
          echo 'headless stop left its session directory; preserving scratch and refusing retry' >&2
          result=125
        elif [[ -d $owner_dir && ! -L $owner_dir && \
                $(stat -c %u:%a -- "$owner_dir") == "$uid:700" ]] && \
             [[ -z $(find "$owner_dir" -mindepth 1 -print -quit) ]] && rmdir -- "$owner_dir"; then
          :
        else
          echo 'cannot verify or remove the empty run-owned owner directory; preserving scratch' >&2
          result=125
        fi
      else
        if ! tests/headless.sh stop --preserve-scratch; then
          echo 'headless stop failed after a scenario failure; scratch preserved' >&2
          result=125
        fi
      fi
    fi
    exit "$result"
  }
  trap finish EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM

  if ! SCOTTLAND_TEST_OUTPUTS=1 tests/headless.sh start --widgets; then
    echo "headless start failed for $label; wrapper owns any partial-start cleanup" >&2
    exit 1
  fi
  started=1
  tests/headless.sh run env SCOTTLAND_SCENARIOS="$selector" \
    python3 -u "tests/$test_script" "$artifacts/$label"
)

# Each input sequence gets its own compositor, widget bus and owner-scoped runtime.
run_scenario b1-touchpad-release unrelated_pointer_release_cannot_take_touchpad_offer hold-forms-test.py
run_scenario b3-double-tap double_tap_goes_to_rail hint-hold-double-tap-test.py
run_scenario b3-widget-hold selected_widget_tap_then_hold_solos hint-hold-double-tap-test.py
