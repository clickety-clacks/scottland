#!/bin/bash
# E14: scottland-output list/set/reset and scale auto on two virtual outputs, judged by Wayfire's
# own output list and screencopy. Only a caller-owned headless compositor; logs stay beside its
# state dir.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_HEADLESS_DIR:?set an isolated headless directory under build/}"
[[ $(realpath -m "$SCOTTLAND_HEADLESS_DIR") == "$PWD"/build/* ]] || { echo 'use a directory under build/' >&2; exit 1; }
[[ ! -e $SCOTTLAND_HEADLESS_DIR ]] || { echo 'headless directory occupied' >&2; exit 1; }
artifacts=$SCOTTLAND_HEADLESS_DIR.output-control-artifacts
hooks=$(realpath -m "$SCOTTLAND_HEADLESS_DIR").hooks
[[ -d build/hooks/libexec ]] || { echo 'run make test-hooks first' >&2; exit 1; }
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

# This checkout's helpers, with two stand-ins for scale auto. A virtual output reports no physical
# size, so the compositor's real report gets the sizes named in physical-sizes.json
# ({"NAME": [width_mm, height_mm]}, stand-ins for a HiDPI panel or an ordinary monitor). And
# config.d/50-test-display prints display.ini, standing in for an integration's display settings.
cp -a build/hooks "$hooks"
real_heads=$(readlink -f build/hooks/libexec/scottland-output-heads)
rm "$hooks/libexec/scottland-output-heads"
cat >"$hooks/libexec/scottland-output-heads" <<HEADS
#!/usr/bin/env python3
import json, subprocess, sys
result = subprocess.run(['$real_heads'], capture_output=True, text=True)
sys.stderr.write(result.stderr)
if result.returncode != 0:
    sys.exit(result.returncode)
try:
    sizes = json.load(open('$hooks/physical-sizes.json'))
except FileNotFoundError:
    sizes = {}
heads = json.loads(result.stdout)
for head in heads:
    if head['name'] in sizes:
        head['physical_width'], head['physical_height'] = sizes[head['name']]
print(json.dumps(heads))
HEADS
cat >"$hooks/config.d/50-test-display" <<DISPLAY
#!/bin/sh
[ "\${1:-}" = --watch-paths ] && exit 0
cat '$hooks/display.ini' 2>/dev/null || true
DISPLAY
chmod +x "$hooks/libexec/scottland-output-heads" "$hooks/config.d/50-test-display"
# Session start: an integration asks for scale auto on a HiDPI panel (1280x720 on 120x90 mm,
# about 249 pixels per inch) before the compositor runs.
printf '[output:HEADLESS-1]\nscale = auto\n' >"$hooks/display.ini"
echo '{"HEADLESS-1": [120, 90]}' >"$hooks/physical-sizes.json"

export SCOTTLAND_TEST_HOOKS=$hooks
SCOTTLAND_TEST_OUTPUTS=2 tests/headless.sh start
ready=false
for i in $(seq 60); do
  timeout 8s tests/headless.sh ipc scottland/session-state >/dev/null 2>&1 && { ready=true; break; }
  sleep 1
done
$ready || { echo 'compositor IPC never became ready' >&2; exit 1; }
timeout --signal=TERM --kill-after=30s 5m tests/headless.sh run python3 -u "$PWD/tests/output-control-test.py" "$artifacts" \
  | tee "$artifacts/output-control.log"
exit "${PIPESTATUS[0]}"
