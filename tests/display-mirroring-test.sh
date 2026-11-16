#!/bin/bash
# DM1-DM7: exercise mirror selection, the existing config watcher, user-mode restoration, and
# window survival with three virtual outputs named like a panel and two external monitors.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set an isolated headless directory under build/}"
[[ $(realpath -m "$SCOTTLAND_HEADLESS_DIR") == "$PWD"/build/* ]] || {
  echo 'use a headless directory under build/' >&2; exit 1;
}
[[ ! -e $SCOTTLAND_HEADLESS_DIR && ! -L $SCOTTLAND_HEADLESS_DIR ]] || {
  echo 'headless directory is occupied' >&2; exit 1;
}
[[ $# -eq 1 ]] || { echo 'usage: tests/display-mirroring-test.sh EVIDENCE_DIR' >&2; exit 2; }
artifacts=$(realpath -m "$1")
fixture="$SCOTTLAND_HEADLESS_DIR.fixture"
hooks="$(realpath -m "$SCOTTLAND_HEADLESS_DIR").hooks"
[[ $artifacts == "$PWD"/build/* && ! -e $artifacts && ! -L $artifacts ]] || {
  echo 'evidence directory must be a new child of build/' >&2; exit 1;
}
[[ ! -e $fixture && ! -L $fixture && ! -e $hooks && ! -L $hooks ]] || {
  echo 'display-mirroring fixture path is occupied' >&2; exit 1;
}
[[ -x build/hooks/libexec/scottland-display ]] || { echo 'run make test-hooks first' >&2; exit 1; }
mkdir -m 700 -p "$artifacts" "$fixture"
started=0
cleanup() {
  status=$?
  trap - EXIT
  if ((started)); then
    cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log" 2>/dev/null || true
    tests/headless.sh stop >/dev/null || status=1
  fi
  if [[ -e $SCOTTLAND_HEADLESS_DIR || -L $SCOTTLAND_HEADLESS_DIR ]]; then
    echo "headless cleanup kept its directory: $SCOTTLAND_HEADLESS_DIR" >&2
    status=1
  fi
  rm -rf -- "$hooks" "$fixture"
  exit "$status"
}
trap cleanup EXIT
trap 'exit 143' INT TERM

cc -shared -fPIC -o "$fixture/output-names.so" tests/output-names.c -ldl
cp -a build/hooks "$hooks"
export SCOTTLAND_TEST_HOOKS=$hooks
export SCOTTLAND_TEST_PRELOAD="$fixture/output-names.so"
export SCOTTLAND_TEST_OUTPUT_NAMES='HEADLESS-1=eDP-1,HEADLESS-2=DP-1,HEADLESS-3=HDMI-A-1,HEADLESS-4=DP-1'
export SCOTTLAND_TEST_OUTPUTS=3
started=1
tests/headless.sh start --config-watcher

timeout --signal=TERM --kill-after=20s 5m \
  tests/headless.sh run python3 -u tests/display-mirroring-test.py "$artifacts" \
  | tee "$artifacts/display-mirroring.log"
