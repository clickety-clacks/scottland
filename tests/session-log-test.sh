#!/bin/bash
# Launcher-only fixture: no display, services or live files. Run on the test host.
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
mkdir -p "$repo/build"
work=$(mktemp -d "$repo/build/session-log-test.XXXXXX")
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/data/scottland/dev/libexec" "$work/bin" "$work/runtime"
cat > "$work/data/scottland/dev/libexec/scottland-build-config" <<'SH'
#!/bin/sh
echo /dev/null
SH
cat > "$work/bin/wayfire" <<'SH'
#!/bin/sh
if [ -n "${TEST_CALLS:-}" ]; then printf '%s\n' "$*" >> "$TEST_CALLS"; fi
if [ "$TEST_SESSION" = first ]; then yes x | head -c 1048600; fi
echo "session=$TEST_SESSION core-limit=$(ulimit -c)"
exit "${TEST_WAYFIRE_EXIT:-0}"
SH
cat > "$work/bin/systemctl" <<'SH'
#!/bin/sh
exit 1
SH
chmod +x "$work/bin/"* "$work/data/scottland/dev/libexec/"*
sessions=(first second third fourth fifth)
for ((index=0; index<${#sessions[@]}; index++)); do
  session=${sessions[index]}
  TEST_SESSION=$session SCOTTLAND_DEV_LAUNCHER=1 PATH="$work/bin:$PATH" \
    XDG_DATA_HOME="$work/data" XDG_STATE_HOME="$work/state" XDG_RUNTIME_DIR="$work/runtime" \
    "$repo/core/session/start-scottland"
  [[ $(tail -n 1 "$work/state/scottland/wayfire.log") == "session=$session core-limit=unlimited" ]]
  for ((age=1; age<=3; age++)); do
    suffix=$age
    (( age == 1 )) && suffix=previous || suffix=previous.$age
    log="$work/state/scottland/wayfire.log.$suffix"
    if (( age <= index )); then
      [[ -f $log && $(stat -c %s "$log") -le 1048576 ]]
      [[ $(tail -n 1 "$log") == "session=${sessions[index-age]} core-limit=unlimited" ]]
      if [[ ${sessions[index-age]} == first ]]; then
        [[ $(stat -c %s "$log") -eq 1048576 ]]
      fi
    else
      [[ ! -e $log ]]
    fi
  done
  [[ ! -e "$work/state/scottland/wayfire.log.previous.4" ]]
  echo "PASS launcher $session: core limit and three bounded previous logs"
done

# A failed bounded copy can retain an oversized log, but must not block login.
real_mktemp=$(command -v mktemp)
real_tail=$(command -v tail)
real_mv=$(command -v mv)
cat > "$work/bin/mktemp" <<'SH'
#!/bin/sh
if [ "${TEST_FAIL_MKTEMP:-}" = 1 ]; then exit 1; fi
exec "$TEST_REAL_MKTEMP" "$@"
SH
cat > "$work/bin/tail" <<'SH'
#!/bin/sh
if [ "${TEST_FAIL_TAIL:-}" = 1 ] && [ "$1" = -c ]; then exit 1; fi
exec "$TEST_REAL_TAIL" "$@"
SH
cat > "$work/bin/mv" <<'SH'
#!/bin/sh
if [ "${TEST_FAIL_MV:-}" = 1 ]; then exit 1; fi
exec "$TEST_REAL_MV" "$@"
SH
chmod +x "$work/bin/mktemp" "$work/bin/tail" "$work/bin/mv"
fault_state="$work/fault-state/scottland"
mkdir -p "$fault_state"
head -c 1048600 /dev/zero > "$fault_state/wayfire.log"
TEST_SESSION=mktemp-failure TEST_FAIL_MKTEMP=1 TEST_REAL_MKTEMP="$real_mktemp" \
  TEST_REAL_TAIL="$real_tail" TEST_REAL_MV="$real_mv" TEST_CALLS="$work/mktemp-calls" \
  SCOTTLAND_DEV_LAUNCHER=1 PATH="$work/bin:$PATH" XDG_DATA_HOME="$work/data" \
  XDG_STATE_HOME="$work/fault-state" XDG_RUNTIME_DIR="$work/runtime" \
  "$repo/core/session/start-scottland" --fault-case >"$work/mktemp-output" 2>&1
[[ $(wc -l < "$work/mktemp-calls") -eq 1 ]]
[[ $(cat "$work/mktemp-calls") == '-c /dev/null --fault-case' ]]
[[ $(stat -c %s "$fault_state/wayfire.log.previous") -eq 1048600 ]]
[[ $(tail -n 1 "$fault_state/wayfire.log") == 'session=mktemp-failure core-limit=unlimited' ]]
grep -q 'could not bound a Wayfire log' "$work/mktemp-output"
echo 'PASS failed temporary copy: plain rename preserves history and launches once'

printf 'copy failure source\n' > "$fault_state/wayfire.log"
TEST_SESSION=copy-failure TEST_FAIL_TAIL=1 TEST_REAL_MKTEMP="$real_mktemp" \
  TEST_REAL_TAIL="$real_tail" TEST_REAL_MV="$real_mv" TEST_CALLS="$work/copy-calls" \
  SCOTTLAND_DEV_LAUNCHER=1 PATH="$work/bin:$PATH" XDG_DATA_HOME="$work/data" \
  XDG_STATE_HOME="$work/fault-state" XDG_RUNTIME_DIR="$work/runtime" \
  "$repo/core/session/start-scottland" >"$work/copy-output" 2>&1
[[ $(wc -l < "$work/copy-calls") -eq 1 ]]
[[ $(cat "$fault_state/wayfire.log.previous") == 'copy failure source' ]]
[[ $(tail -n 1 "$fault_state/wayfire.log") == 'session=copy-failure core-limit=unlimited' ]]
[[ -z $(find "$fault_state" -maxdepth 1 -name '.wayfire.log.*' -print) ]]
grep -q 'could not bound a Wayfire log' "$work/copy-output"
echo 'PASS failed bounded copy: plain rename preserves history and launches once'

rename_state="$work/rename-failure/scottland"
mkdir -p "$rename_state"
printf 'unpreserved source\n' > "$rename_state/wayfire.log"
set +e
TEST_SESSION=rename-failure TEST_FAIL_MKTEMP=1 TEST_FAIL_MV=1 \
  TEST_WAYFIRE_EXIT=23 TEST_REAL_MKTEMP="$real_mktemp" TEST_REAL_TAIL="$real_tail" \
  TEST_REAL_MV="$real_mv" TEST_CALLS="$work/rename-calls" SCOTTLAND_DEV_LAUNCHER=1 PATH="$work/bin:$PATH" \
  XDG_DATA_HOME="$work/data" XDG_STATE_HOME="$work/rename-failure" \
  XDG_RUNTIME_DIR="$work/runtime" "$repo/core/session/start-scottland" \
  --keep-status >"$work/rename-output" 2>&1
rename_status=$?
set -e
[[ $rename_status -eq 23 && $(wc -l < "$work/rename-calls") -eq 1 ]]
[[ $(cat "$work/rename-calls") == '-c /dev/null --keep-status' ]]
[[ $(cat "$rename_state/wayfire.log") == 'unpreserved source' ]]
[[ ! -e "$rename_state/wayfire.log.previous" ]]
grep -q 'could not preserve a Wayfire log' "$work/rename-output"
grep -q 'using inherited output' "$work/rename-output"
grep -q 'session=rename-failure' "$work/rename-output"
echo 'PASS failed rename: old log intact, one launch on inherited output, exit status preserved'

open_state="$work/log-open-failure/scottland"
mkdir -p "$open_state"
ln -s "$work/no-such-directory/log" "$open_state/wayfire.log"
set +e
TEST_SESSION=log-open-failure TEST_WAYFIRE_EXIT=31 \
  TEST_REAL_MKTEMP="$real_mktemp" TEST_REAL_TAIL="$real_tail" TEST_REAL_MV="$real_mv" \
  TEST_CALLS="$work/open-calls" SCOTTLAND_DEV_LAUNCHER=1 PATH="$work/bin:$PATH" \
  XDG_DATA_HOME="$work/data" XDG_STATE_HOME="$work/log-open-failure" \
  XDG_RUNTIME_DIR="$work/runtime" "$repo/core/session/start-scottland" \
  >"$work/open-output" 2>&1
open_status=$?
set -e
[[ $open_status -eq 31 && $(wc -l < "$work/open-calls") -eq 1 ]]
[[ -L "$open_state/wayfire.log" ]]
grep -q 'could not open the Wayfire log' "$work/open-output"
grep -q 'session=log-open-failure' "$work/open-output"
echo 'PASS failed log open: one launch on inherited output, exit status preserved'

normal_exit_state="$work/normal-exit/scottland"
mkdir -p "$normal_exit_state"
set +e
TEST_SESSION=normal-nonzero TEST_WAYFIRE_EXIT=17 \
  TEST_REAL_MKTEMP="$real_mktemp" TEST_REAL_TAIL="$real_tail" TEST_REAL_MV="$real_mv" \
  TEST_CALLS="$work/normal-exit-calls" SCOTTLAND_DEV_LAUNCHER=1 PATH="$work/bin:$PATH" \
  XDG_DATA_HOME="$work/data" XDG_STATE_HOME="$work/normal-exit" \
  XDG_RUNTIME_DIR="$work/runtime" "$repo/core/session/start-scottland" \
  >"$work/normal-exit-output" 2>&1
normal_exit_status=$?
set -e
[[ $normal_exit_status -eq 17 && $(wc -l < "$work/normal-exit-calls") -eq 1 ]]
[[ $(tail -n 1 "$normal_exit_state/wayfire.log") == 'session=normal-nonzero core-limit=unlimited' ]]
echo 'PASS normal log: one launch and Wayfire exit status preserved'
