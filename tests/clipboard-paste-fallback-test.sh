#!/bin/bash
# Exercise GTK and terminal paste paths in the existing isolated headless session harness.
set -euo pipefail
cd "$(dirname "$0")/.."
repo=$PWD
[[ ${SCOTTLAND_HEADLESS_ISOLATION:-} == 1 ]] || {
  echo 'clipboard paste fallback checks require the existing isolated headless runner' >&2
  exit 2
}

artifacts="$repo/build/clipboard-paste-fallback-evidence"
[[ ! -e $artifacts && ! -L $artifacts ]] || {
  echo "clipboard paste evidence path is occupied: $artifacts" >&2
  exit 2
}
mkdir -m 700 -- "$artifacts"
mkdir -m 700 -- "$artifacts/test-bin"
cat >"$artifacts/test-bin/scottland-list-picker" <<'PICKER'
#!/bin/sh
cat >/dev/null
printf '%s\n' '😀 grinning face'
PICKER
chmod 700 -- "$artifacts/test-bin/scottland-list-picker"

g++ -std=c++17 -Wall -Wextra -Werror \
  $(pkg-config --cflags gtk4) tests/clipboard-paste-target.cpp \
  $(pkg-config --libs gtk4) -o "$artifacts/clipboard-paste-target"

started=0
scratch=
headless_dir=
cleanup() {
  status=$?
  trap - EXIT
  if ((started)); then
    if [[ -f $headless_dir/display ]]; then
      cp "$headless_dir/wayfire.log" "$artifacts/$(basename "$scratch")-wayfire.log" 2>/dev/null || true
      if ! tests/headless.sh stop; then
        echo 'headless postflight could not prove owned cleanup; preserving its scratch' >&2
        exit 2
      fi
    else
      if ! tests/headless.sh stop --preserve-scratch; then
        echo 'partial headless startup cleanup was not proven; preserving its scratch' >&2
        exit 2
      fi
    fi
    started=0
  fi
  if [[ -n $scratch && -d $scratch ]]; then
    if ! rmdir -- "$scratch"; then
      echo "headless scratch is not empty after owned cleanup; preserving $scratch" >&2
      exit 2
    fi
  fi
  exit "$status"
}
trap cleanup EXIT

run_mode() {
  local mode=$1
  scratch="$repo/build/clipboard-paste-$mode-scratch"
  headless_dir="$scratch/headless"
  [[ ! -e $scratch && ! -L $scratch ]] || {
    echo "headless scratch path is occupied: $scratch" >&2
    return 2
  }
  mkdir -m 700 -- "$scratch"
  export SCOTTLAND_TEST_SCRATCH=$scratch SCOTTLAND_HEADLESS_DIR=$headless_dir
  export SCOTTLAND_HEADLESS_OWNER="clipboard-paste-$mode"
  if [[ $mode == fallback ]]; then
    export SCOTTLAND_TEST_DISABLE_PRIMARY_SELECTION=1
  else
    unset SCOTTLAND_TEST_DISABLE_PRIMARY_SELECTION || true
  fi
  started=1
  tests/headless.sh start
  local console="$artifacts/$mode.log"
  if ! tests/headless.sh run python3 -u tests/clipboard-paste-fallback-headless.py \
    "$mode" "$artifacts" "$artifacts/clipboard-paste-target" >"$console" 2>&1; then
    cat "$console"
    return 1
  fi
  cat "$console"
  cp "$headless_dir/wayfire.log" "$artifacts/$mode-wayfire.log"
  if ! tests/headless.sh stop; then
    started=0
    echo "headless stop failed for $mode; preserving its scratch" >&2
    return 2
  fi
  started=0
  rmdir -- "$scratch"
  scratch=
  headless_dir=
}

run_mode default
run_mode fallback
printf 'PASS GTK text and emoji plus Foot text in default and fallback sessions\n'
