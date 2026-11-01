#!/bin/bash
set -euo pipefail

repo=$(cd -- "$(dirname -- "$0")/.." && pwd -P)
: "${SCOTTLAND_HEADLESS_DIR:?set a fresh isolated directory under build/}"
: "${SCOTTLAND_TEST_SCRATCH:?set an isolated test scratch directory under build/}"
headless=$(realpath -m -- "$SCOTTLAND_HEADLESS_DIR")
scratch=$(realpath -m -- "$SCOTTLAND_TEST_SCRATCH")
evidence=$(realpath -m -- "${1:-$repo/build/config-watch-evidence}")
[[ $headless == "$repo"/build/* && ! -e $headless ]] || {
  echo 'headless runtime must be a fresh directory under this checkout build/' >&2; exit 1; }
[[ $scratch == "$repo"/build/* && -d $scratch ]] || {
  echo 'test scratch must be an existing directory under this checkout build/' >&2; exit 1; }
[[ $evidence == "$repo"/build/* && ! -e $evidence ]] || {
  echo 'evidence must be a fresh directory under this checkout build/' >&2; exit 1; }
command -v inotifywait >/dev/null || { echo 'this check needs inotify-tools' >&2; exit 1; }
[[ -x $repo/build/hooks/autostart.d/06-watch-config && -f $repo/build/libscottland.so ]] || {
  echo 'build the isolated headless session helpers before running this check' >&2; exit 1; }

mkdir -m 700 -p "$evidence"
tmp=$(mktemp -d "$repo/build/config-watch-missing-settings.XXXXXX")
watcher_group=
observer_pid=
session_started=0
cleanup() {
  local status=$?
  trap - EXIT
  if [[ -n $watcher_group ]]; then
    kill -TERM -- "-$watcher_group" 2>/dev/null || true
    sleep 0.1
    kill -KILL -- "-$watcher_group" 2>/dev/null || true
  fi
  if [[ -n $observer_pid ]]; then
    kill -TERM "$observer_pid" 2>/dev/null || true
    wait "$observer_pid" 2>/dev/null || true
  fi
  [[ ! -f $headless/wayfire.log ]] || cp -- "$headless/wayfire.log" "$evidence/wayfire.log"
  [[ ! -f $tmp/watcher.log ]] || cp -- "$tmp/watcher.log" "$evidence/watcher.log"
  [[ ! -f $tmp/config-writes.log ]] || cp -- "$tmp/config-writes.log" "$evidence/config-writes.log"
  [[ ! -f $tmp/builds.log ]] || cp -- "$tmp/builds.log" "$evidence/builds.log"
  [[ ! -f $tmp/state/scottland/watch-config.log ]] || cp -- "$tmp/state/scottland/watch-config.log" "$evidence/watch-config.log"
  if ((session_started)); then
    tests/headless.sh stop >"$evidence/headless-stop.log" 2>&1 || status=$?
  fi
  rm -rf -- "$tmp"
  exit "$status"
}
trap cleanup EXIT

mkdir -p "$tmp/home/.config" "$tmp/state" "$tmp/hooks/libexec" "$tmp/base"
[[ ! -e $tmp/home/.config/scottland ]] || { echo 'settings directory unexpectedly exists' >&2; exit 1; }
cp -- "$repo/core/config/scottland.ini" "$tmp/base/scottland.ini"
cat >"$tmp/hooks/libexec/scottland-build-config" <<'BUILD'
#!/bin/sh
set -eu
if [ "${1:-}" = --watch-paths ]; then
  printf '%s\n' "$TEST_BASE"
  "$TEST_REPO/core/config.d/20-layout-settings" --watch-paths
  "$TEST_REPO/core/config.d/90-user-overrides" --watch-paths
  exit 0
fi
printf 'build\n' >>"$BUILD_LOG"
copy=$(mktemp "$SCOTTLAND_SESSION_DIR/wayfire.ini.test.XXXXXX")
cat "$SCOTTLAND_SESSION_DIR/wayfire.ini" >"$copy"
cat "$copy" >"$SCOTTLAND_SESSION_DIR/wayfire.ini"
rm -f -- "$copy"
BUILD
chmod 700 "$tmp/hooks/libexec/scottland-build-config"
cat >"$tmp/start-watcher.sh" <<'WATCHER'
#!/bin/sh
printf '%s\n' "$$" >"$WATCHER_PID_FILE"
exec env \
  HOME="$TEST_HOME" \
  XDG_CONFIG_HOME="$TEST_CONFIG_HOME" \
  XDG_STATE_HOME="$TEST_STATE_HOME" \
  SCOTTLAND_SESSION_DIR="$TEST_SESSION_DIR" \
  SCOTTLAND_HOOKS="$TEST_HOOKS" \
  TEST_REPO="$TEST_REPO" \
  TEST_BASE="$TEST_BASE" \
  BUILD_LOG="$BUILD_LOG" \
  HYPRLAND_INSTANCE_SIGNATURE= \
  "$TEST_REPO/core/autostart.d/06-watch-config"
WATCHER
chmod 700 "$tmp/start-watcher.sh"

export TEST_HOME="$tmp/home" TEST_CONFIG_HOME="$tmp/home/.config"
export TEST_STATE_HOME="$tmp/state" TEST_SESSION_DIR="$headless"
export TEST_HOOKS="$tmp/hooks" TEST_REPO="$repo"
export TEST_BASE="$tmp/base/scottland.ini" BUILD_LOG="$tmp/builds.log"
export WATCHER_PID_FILE="$tmp/watcher.pid"

session_started=1
tests/headless.sh start
inotifywait -q -m -e close_write --format '%e' "$headless/wayfire.ini" \
  >"$tmp/config-writes.log" 2>"$tmp/config-writes.err" &
observer_pid=$!
sleep 0.2

setsid "$tmp/start-watcher.sh" >"$tmp/watcher.log" 2>&1 </dev/null &
for _ in $(seq 50); do
  [[ -s $tmp/watcher.pid ]] && break
  sleep 0.1
done
[[ -s $tmp/watcher.pid ]] || { echo 'watcher did not start' >&2; exit 1; }
watcher_group=$(cat "$tmp/watcher.pid")
sleep 5.5

if [[ -s $BUILD_LOG || -s $tmp/config-writes.log ]]; then
  echo 'missing settings directory caused a rebuild or Wayfire config write' >&2
  exit 1
fi
kill -0 "$watcher_group" 2>/dev/null || { echo 'config watcher exited during the missing-directory interval' >&2; exit 1; }
grep -q 'watching (pid ' "$tmp/state/scottland/watch-config.log" || {
  echo 'config watcher did not stay active during the missing-directory interval' >&2; exit 1; }
tests/headless.sh run true
printf 'missing settings directory: %s\n' "$TEST_CONFIG_HOME/scottland"
printf 'absent-directory period: 5.5 seconds; rebuilds: 0; Wayfire config writes: 0\n'

mkdir -p "$TEST_CONFIG_HOME/scottland"
waiting=0
for _ in $(seq 50); do
  children=$(cat "/proc/$watcher_group/task/$watcher_group/children" 2>/dev/null || true)
  for child in $children; do
    [[ -r /proc/$child/comm ]] || continue
    [[ $(cat "/proc/$child/comm") == inotifywait ]] && waiting=1
  done
  ((waiting)) && break
  sleep 0.1
done
((waiting)) || { echo 'watcher did not remain active after the settings directory appeared' >&2; exit 1; }
for _ in $(seq 50); do
  [[ -s $BUILD_LOG ]] && break
  sleep 0.1
done
sleep 0.5
builds=$(wc -l <"$BUILD_LOG")
writes=$(wc -l <"$tmp/config-writes.log")
[[ $builds == 1 && $writes -ge 1 ]] || {
  echo "settings directory creation produced $builds rebuild(s) and $writes Wayfire config write(s), expected one rebuild and at least one write" >&2
  exit 1
}
waiting=0
for _ in $(seq 50); do
  children=$(cat "/proc/$watcher_group/task/$watcher_group/children" 2>/dev/null || true)
  for child in $children; do
    [[ -r /proc/$child/comm ]] || continue
    [[ $(cat "/proc/$child/comm") == inotifywait ]] && waiting=1
  done
  ((waiting)) && break
  sleep 0.1
done
((waiting)) || { echo 'watcher did not resume watching the settings directory after rebuilding' >&2; exit 1; }
printf '# changed setting\n' >"$TEST_CONFIG_HOME/scottland/layout.ini"
for _ in $(seq 50); do
  [[ $(wc -l <"$BUILD_LOG") -ge 2 ]] && break
  sleep 0.1
done
sleep 0.5
builds=$(wc -l <"$BUILD_LOG")
writes=$(wc -l <"$tmp/config-writes.log")
[[ $builds == 2 && $writes -ge 2 ]] || {
  echo "layout.ini change produced $builds rebuild(s) and $writes Wayfire config write(s), expected two total rebuilds and at least two writes" >&2
  exit 1
}
waiting=0
for _ in $(seq 50); do
  children=$(cat "/proc/$watcher_group/task/$watcher_group/children" 2>/dev/null || true)
  for child in $children; do
    [[ -r /proc/$child/comm ]] || continue
    [[ $(cat "/proc/$child/comm") == inotifywait ]] && waiting=1
  done
  ((waiting)) && break
  sleep 0.1
done
((waiting)) || { echo 'watcher did not resume watching after the layout setting change' >&2; exit 1; }
printf '# another changed setting\n' >"$TEST_CONFIG_HOME/scottland/overrides.ini"
for _ in $(seq 50); do
  [[ $(wc -l <"$BUILD_LOG") -ge 3 ]] && break
  sleep 0.1
done
sleep 0.5
builds=$(wc -l <"$BUILD_LOG")
writes=$(wc -l <"$tmp/config-writes.log")
[[ $builds == 3 && $writes -ge 3 ]] || {
  echo "overrides.ini change produced $builds rebuild(s) and $writes Wayfire config write(s), expected three total rebuilds and at least three writes" >&2
  exit 1
}
replacement=$(mktemp "$tmp/base/scottland.ini.replacement.XXXXXX")
cp -- "$repo/core/config/scottland.ini" "$replacement"
mv -f -- "$replacement" "$TEST_BASE"
for _ in $(seq 50); do
  [[ $(wc -l <"$BUILD_LOG") -ge 4 ]] && break
  sleep 0.1
done
sleep 0.5
builds=$(wc -l <"$BUILD_LOG")
writes=$(wc -l <"$tmp/config-writes.log")
[[ $builds == 4 && $writes -ge 4 ]] || {
  echo "base config replacement produced $builds rebuild(s) and $writes Wayfire config write(s), expected four total rebuilds and at least four writes" >&2
  exit 1
}
waiting=0
for _ in $(seq 50); do
  children=$(cat "/proc/$watcher_group/task/$watcher_group/children" 2>/dev/null || true)
  for child in $children; do
    [[ -r /proc/$child/comm ]] || continue
    [[ $(cat "/proc/$child/comm") == inotifywait ]] && waiting=1
  done
  ((waiting)) && break
  sleep 0.1
done
((waiting)) || { echo 'watcher did not resume watching after the base config was replaced' >&2; exit 1; }
tests/headless.sh run true
printf 'settings directory creation, two file changes, and base config replacement: rebuilds: %s; Wayfire config writes: %s; headless session remained reachable\n' "$builds" "$writes"
