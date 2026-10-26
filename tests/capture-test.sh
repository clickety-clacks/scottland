#!/bin/bash
# E16: scottland-capture (region, screenshot, record, qr, text) on two virtual outputs, judged by
# Wayfire's own view and output lists, independent screencopies, the clipboard, the files written
# and the notices a stand-in notification server receives. Only a caller-owned headless
# compositor; logs stay beside its state dir.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set an isolated headless directory under build/}"
[[ $(realpath -m "$SCOTTLAND_HEADLESS_DIR") == "$PWD"/build/* ]] || { echo 'use a directory under build/' >&2; exit 1; }
[[ ! -e $SCOTTLAND_HEADLESS_DIR ]] || { echo 'headless directory occupied' >&2; exit 1; }
artifacts=$(realpath -m "$SCOTTLAND_HEADLESS_DIR").capture-artifacts
hooks=$(realpath -m "$SCOTTLAND_HEADLESS_DIR").hooks
[[ -d build/hooks/libexec ]] || { echo 'run make test-hooks first' >&2; exit 1; }
[[ -x build/hooks/libexec/scottland-capture ]] || { echo 'run make tools test-hooks first' >&2; exit 1; }
[[ ! -e $hooks ]] || { echo 'test hooks directory occupied' >&2; exit 1; }
mkdir -p "$artifacts"
# tests/headless.sh stop removes the session's scratch itself, and keeps it when that isn't safe.
cleanup() {
  status=$?
  trap - EXIT
  cp "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$artifacts/wayfire.log" 2>/dev/null || true
  tests/headless.sh stop >/dev/null || status=1
  if [[ -e $SCOTTLAND_HEADLESS_DIR || -L $SCOTTLAND_HEADLESS_DIR ]]; then
    echo "headless cleanup kept its directory: $SCOTTLAND_HEADLESS_DIR" >&2
    status=1
  fi
  rm -rf -- "$hooks"
  exit "$status"
}
trap cleanup EXIT
trap 'exit 143' INT TERM

# This checkout's helpers, with stand-ins the test switches by creating files in $hooks/switch:
# - libexec/scottland-ctl serves windows.json for `windows --json` (a window listing that says
#   which output draws each window), fails it when there is none (a listing that cannot say), and
#   runs the real one for everything else.
# - stubs/ comes first on the session's PATH: grim refuses a capture (switch/grim-refuse), the
#   screen recorder is tests/capture-recorder.py (a virtual output has no screen to record),
#   ffmpeg fails the recording's processing pass (switch/ffmpeg-fail-processing), and xdg-open
#   only logs what it was asked to open. Everything else is the real program. The Print bindings
#   run scottland-capture by name, so it is there too.
cp -a build/hooks "$hooks"
real_ctl=$(readlink -f build/hooks/libexec/scottland-ctl)
rm "$hooks/libexec/scottland-ctl"
cat >"$hooks/libexec/scottland-ctl" <<CTL
#!/bin/sh
if [ "\$1" = windows ] && [ "\${2:-}" = --json ]; then
  [ -f '$hooks/windows.json' ] || { echo 'stand-in: no window listing' >&2; exit 1; }
  exec cat '$hooks/windows.json'
fi
exec '$real_ctl' "\$@"
CTL
mkdir -p "$hooks/stubs" "$hooks/switch" "$hooks/raw"
cat >"$hooks/stubs/grim" <<GRIM
#!/bin/sh
[ -e '$hooks/switch/grim-refuse' ] && { echo 'stand-in: screen capture refused' >&2; exit 1; }
exec /usr/bin/grim "\$@"
GRIM
cat >"$hooks/stubs/gpu-screen-recorder" <<RECORDER
#!/bin/sh
exec python3 '$PWD/tests/capture-recorder.py' '$hooks' "\$@"
RECORDER
cat >"$hooks/stubs/ffmpeg" <<FFMPEG
#!/bin/sh
if [ -e '$hooks/switch/ffmpeg-fail-processing' ]; then
  for arg; do
    case \$arg in *-processed.mp4) echo 'stand-in: processing failed' >&2; exit 1 ;; esac
  done
fi
exec /usr/bin/ffmpeg "\$@"
FFMPEG
cat >"$hooks/stubs/xdg-open" <<OPEN
#!/bin/sh
printf '%s\n' "\$*" >>'$hooks/xdg-open.log'
OPEN
chmod +x "$hooks/libexec/scottland-ctl" "$hooks"/stubs/*
ln -s "$hooks/libexec/scottland-capture" "$hooks/stubs/scottland-capture"

export SCOTTLAND_TEST_HOOKS=$hooks SCOTTLAND_TEST_PATH=$hooks/stubs
SCOTTLAND_TEST_OUTPUTS=2 tests/headless.sh start
ready=false
for i in $(seq 60); do
  timeout 8s tests/headless.sh ipc scottland/session-state >/dev/null 2>&1 && { ready=true; break; }
  sleep 1
done
$ready || { echo 'compositor IPC never became ready' >&2; exit 1; }
timeout --signal=TERM --kill-after=30s 20m tests/headless.sh run python3 -u "$PWD/tests/capture-test.py" "$artifacts" \
  | tee "$artifacts/capture.log"
exit "${PIPESTATUS[0]}"
