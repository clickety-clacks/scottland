#!/bin/bash
# Check that a fresh session clears a stale mirror request and session exit clears its request.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SCOTTLAND_TEST_SCRATCH:?set a private scratch directory under build/}"
[[ $(realpath -m "$SCOTTLAND_TEST_SCRATCH") == "$PWD"/build/* ]] || {
  echo 'use a private scratch directory under build/' >&2; exit 1;
}
[[ $# -eq 1 ]] || { echo 'usage: tests/display-mirroring-session-end-test.sh EVIDENCE_LOG' >&2; exit 2; }
evidence=$(realpath -m "$1")
[[ $evidence == "$PWD"/build/* && ! -e $evidence && ! -L $evidence ]] || {
  echo 'evidence log must be a new file under build/' >&2; exit 1;
}
work="$SCOTTLAND_TEST_SCRATCH/session-end-fixture"
[[ ! -e $work && ! -L $work ]] || { echo 'session-end fixture path is occupied' >&2; exit 1; }
cleanup() {
  status=$?
  trap - EXIT
  rm -rf -- "$work"
  exit "$status"
}
trap cleanup EXIT
mkdir -m 700 -p "$work/bin" "$work/home" "$work/data/scottland/dev/libexec" \
  "$work/data/scottland/dev/session-env.d" "$work/runtime" "$work/session/display-mirror/wayland-test"

hooks="$work/data/scottland/dev"
marker="$work/session/display-mirror/wayland-test/request"
observation="$work/wayfire-observation"
config="$work/config.ini"
printf '%s\n' 'stale-panel' 'stale-target' >"$marker"
printf '%s\n' '# fixture config' >"$config"

cat >"$hooks/libexec/scottland-build-config" <<'SH'
#!/bin/sh
printf '%s\n' "$SCOTTLAND_TEST_MIRROR_CONFIG"
SH
cat >"$work/bin/wayfire" <<'SH'
#!/bin/sh
if [ -e "$SCOTTLAND_SESSION_DIR/display-mirror/$WAYLAND_DISPLAY/request" ]; then
  echo 'stale mirror request survived session startup' >&2
  exit 21
fi
printf '%s\n' 'startup-cleared' >"$SCOTTLAND_TEST_MIRROR_OBSERVATION"
printf '%s\n' 'panel' 'external' >"$SCOTTLAND_SESSION_DIR/display-mirror/$WAYLAND_DISPLAY/request"
SH
cat >"$work/bin/systemctl" <<'SH'
#!/bin/sh
if [ "$*" = '--user is-active --quiet scottland-session.target' ]; then
  exit 1
fi
echo 'unexpected systemctl operation' >&2
exit 2
SH
chmod 755 "$hooks/libexec/scottland-build-config" "$work/bin/wayfire" "$work/bin/systemctl"

if ! SCOTTLAND_DEV_LAUNCHER=1 \
  SCOTTLAND_SESSION_DIR="$work/session" \
  SCOTTLAND_TEST_MIRROR_CONFIG="$config" \
  SCOTTLAND_TEST_MIRROR_OBSERVATION="$observation" \
  XDG_CONFIG_HOME="$work/config-home" \
  XDG_DATA_HOME="$work/data" \
  XDG_RUNTIME_DIR="$work/runtime" \
  XDG_STATE_HOME="$work/state" \
  HOME="$work/home" \
  WAYLAND_DISPLAY=wayland-test \
  PATH="$work/bin:$PATH" \
  core/session/start-scottland >"$work/launcher.log" 2>&1; then
  cat "$work/launcher.log" >&2
  exit 1
fi

if [[ -e $marker || ! -e $observation ]] || ! grep -Fxq 'startup-cleared' "$observation"; then
  cat "$work/launcher.log" >&2
  echo 'session startup/exit did not clear the session mirror request' >&2
  exit 1
fi
mkdir -m 700 -p "$(dirname "$evidence")"
printf '%s\n' 'PASS: startup clears stale request and session exit clears the active request' >"$evidence"
cat "$evidence"
