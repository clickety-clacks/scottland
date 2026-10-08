#!/usr/bin/env bash
set -euo pipefail

repo=$(cd -- "$(dirname -- "$0")/.." && pwd -P)
build=$(realpath -m -- "$repo/build")
: "${SCOTTLAND_TEST_SCRATCH:?set a fresh run-owned scratch directory under this checkout's build/}"
test_scratch=$(realpath -e -- "$SCOTTLAND_TEST_SCRATCH")
case "$test_scratch/" in
  "$build"/*/) ;;
  *) echo 'SCOTTLAND_TEST_SCRATCH must be inside this checkout build/' >&2; exit 2 ;;
esac
[[ -d $test_scratch && ! -L $test_scratch ]] || {
  echo 'SCOTTLAND_TEST_SCRATCH must be a real directory' >&2
  exit 2
}

evidence=$test_scratch/pending-start-cleanup-evidence
[[ ! -e $evidence && ! -L $evidence ]] || {
  echo 'pending cleanup evidence path is occupied; choose fresh runner-owned scratch' >&2
  exit 2
}
mkdir -m 700 -- "$evidence"

active_dir=
active_owner=
cleanup_on_exit() {
  status=$?
  trap - EXIT
  if [[ -n $active_dir && ( -e $active_dir || -L $active_dir ) ]]; then
    env SCOTTLAND_HEADLESS_DIR="$active_dir" SCOTTLAND_HEADLESS_OWNER="$active_owner" \
      "$repo/tests/headless.sh" stop --preserve-scratch \
      >"$evidence/abort-stop.log" 2>&1 || true
  fi
  exit "$status"
}
trap cleanup_on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

check_no_owned_processes() {
  local session_dir=$1 expected_pgid=$2
  python3 - "$session_dir" "$expected_pgid" <<'PY'
import os
import pathlib
import sys

scratch = os.fsencode(sys.argv[1])
expected_pgid = int(sys.argv[2])
found = []
for entry in pathlib.Path('/proc').iterdir():
    if not entry.name.isdecimal():
        continue
    proc = entry
    try:
        raw_stat = (proc / 'stat').read_bytes()
        fields = raw_stat[raw_stat.rfind(b')') + 2:].split()
        state = fields[0]
        process_group = int(fields[2])
        env = dict(part.split(b'=', 1) for part in
                   (proc / 'environ').read_bytes().split(b'\0') if b'=' in part)
    except FileNotFoundError:
        continue
    except PermissionError as error:
        print(f'cannot verify process {entry.name}; preserving run scratch: {error}',
              file=sys.stderr)
        sys.exit(2)
    except (IndexError, ValueError, OSError) as error:
        print(f'cannot verify process {entry.name}; preserving run scratch: {error}',
              file=sys.stderr)
        sys.exit(2)
    if state in (b'Z', b'X'):
        continue
    if env.get(b'SCOTTLAND_HEADLESS_DIR') == scratch:
        found.append((entry.name, process_group))
if found:
    print(f'run-owned processes remain after failed-start stop: {found}', file=sys.stderr)
    sys.exit(1)
if expected_pgid > 0:
    group_members = []
    for entry in pathlib.Path('/proc').iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            raw_stat = (entry / 'stat').read_bytes()
            fields = raw_stat[raw_stat.rfind(b')') + 2:].split()
            state = fields[0]
            process_group = int(fields[2])
            env = dict(part.split(b'=', 1) for part in
                       (entry / 'environ').read_bytes().split(b'\0') if b'=' in part)
        except FileNotFoundError:
            continue
        except PermissionError as error:
            print(f'cannot verify process group {expected_pgid}: {error}', file=sys.stderr)
            sys.exit(2)
        except (IndexError, ValueError, OSError) as error:
            print(f'cannot verify process group {expected_pgid}: {error}', file=sys.stderr)
            sys.exit(2)
        if state not in (b'Z', b'X') and process_group == expected_pgid and \
                env.get(b'SCOTTLAND_HEADLESS_DIR') == scratch:
            group_members.append(entry.name)
    if group_members:
        print(f'run-owned process group {expected_pgid} remains: {group_members}',
              file=sys.stderr)
        sys.exit(1)
PY
}

run_pending_case() {
  local mode=$1
  local -a start_args=()
  case $mode in
    plain) start_args=(start) ;;
    shim) start_args=(start --omarchy) ;;
    *) fail "unknown pending mode: $mode" ;;
  esac

  local session_dir="$evidence/$mode-session"
  local start_log="$evidence/$mode-start.log"
  local stop_log="$evidence/$mode-stop.log"
  local watcher_record="$evidence/$mode-live-before-stop.json"
  local owner runtime status socket env_file pid pgid watch_status start_status
  [[ ! -e $session_dir && ! -L $session_dir ]] || fail "$mode session path is occupied"
  owner=$(python3 -c 'import uuid; print(uuid.uuid4())')
  runtime=$(realpath -e -- "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}")
  active_dir=$session_dir
  active_owner=$owner

  env SCOTTLAND_HEADLESS_DIR="$session_dir" SCOTTLAND_HEADLESS_OWNER="$owner" \
    SCOTTLAND_HEADLESS_OWNER_DIR="$session_dir/missing-owner-dir" \
    "$repo/tests/headless.sh" "${start_args[@]}" >"$start_log" 2>&1 &
  local start_pid=$!

  if python3 - "$session_dir" "$runtime" "$watcher_record" <<'PY'
import json
import os
import pathlib
import sys
import time

scratch = pathlib.Path(sys.argv[1])
runtime = os.fsencode(sys.argv[2])
record = pathlib.Path(sys.argv[3])
deadline = time.monotonic() + 120
while time.monotonic() < deadline:
    try:
        pid_text = (scratch / 'pid').read_text().strip()
        log = (scratch / 'wayfire.log').read_text(errors='replace')
    except (FileNotFoundError, NotADirectoryError):
        time.sleep(0.05)
        continue
    if not pid_text.isdecimal() or 'Using socket name wayland-' not in log:
        time.sleep(0.05)
        continue
    pid = int(pid_text)
    proc = pathlib.Path('/proc') / str(pid)
    try:
        raw_stat = (proc / 'stat').read_bytes()
        fields = raw_stat[raw_stat.rfind(b')') + 2:].split()
        state = fields[0].decode()
        process_group = int(fields[2])
        argv = (proc / 'cmdline').read_bytes().split(b'\0')
        env = dict(part.split(b'=', 1) for part in
                   (proc / 'environ').read_bytes().split(b'\0') if b'=' in part)
    except FileNotFoundError:
        time.sleep(0.05)
        continue
    except (IndexError, ValueError, OSError) as error:
        print(f'cannot verify the live headless launcher: {error}', file=sys.stderr)
        sys.exit(2)
    expected = {
        b'SCOTTLAND_HEADLESS_DIR': os.fsencode(str(scratch)),
        b'XDG_RUNTIME_DIR': runtime,
        b'TMPDIR': os.fsencode(str(scratch / 'tmp')),
    }
    wayfire = any(part.endswith(b'wayfire') for part in argv)
    config = os.fsencode(str(scratch / 'wayfire.ini')) in argv
    if state in ('Z', 'X') or not wayfire or not config or not all(
            env.get(key) == value for key, value in expected.items()):
        print('the failed-start process was not a live Wayfire launcher for this run',
              file=sys.stderr)
        sys.exit(1)
    record.write_text(json.dumps({
        'pid': pid,
        'process_group': process_group,
        'state': state,
        'wayfire_command': True,
        'run_environment': True,
    }, sort_keys=True) + '\n')
    sys.exit(0)
print('Wayfire did not reach a live run-owned socket before the observation deadline',
      file=sys.stderr)
sys.exit(1)
PY
  then
    watch_status=0
  else
    watch_status=$?
  fi
  if wait "$start_pid"; then
    start_status=0
  else
    start_status=$?
  fi
  ((watch_status == 0)) || fail "$mode did not prove a live run-owned Wayfire before stop"
  ((start_status != 0)) || fail "$mode injected failure unexpectedly started a session"

  grep -Fq 'headless session environment record is missing or redirected' "$start_log" || \
    fail "$mode failed for a reason other than the missing environment record"
  grep -Fq 'stopped partial headless session; retaining owned scratch for outer cleanup' \
    "$start_log" || fail "$mode start did not complete the pending-mode stop path"
  [[ -f $session_dir/.scottland-headless-owner && ! -L $session_dir/.scottland-headless-owner ]] || \
    fail "$mode owner record was not retained for inspection"
  [[ $(sed -n '1p' "$session_dir/.scottland-headless-owner") == "$(id -u)" && \
     $(sed -n '2p' "$session_dir/.scottland-headless-owner") == "$runtime" && \
     $(sed -n '3p' "$session_dir/.scottland-headless-owner") == "$owner" ]] || \
    fail "$mode failed-start scratch owner record does not match this run"
  status=$(sed -n '1p' "$session_dir/.scottland-headless-mode")
  [[ $status == "$mode-pending" ]] || fail "$mode marker changed before its session record existed"
  [[ -f $session_dir/pid && ! -e $session_dir/display && ! -e $session_dir/compositor.pid ]] || \
    fail "$mode did not leave the expected pre-publication state"
  socket=$(sed -n 's/.*Using socket name \(wayland-[0-9][0-9]*\).*/\1/p' \
    "$session_dir/wayfire.log" | head -n 1)
  [[ -n $socket ]] || fail "$mode Wayfire log has no socket publication"
  env_file="$session_dir/runtime/scottland/$socket.env"
  [[ ! -e $env_file && ! -L $env_file ]] || fail "$mode unexpectedly wrote a session environment record"
  pid=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["pid"])' "$watcher_record")
  pgid=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["process_group"])' "$watcher_record")
  [[ $pid =~ ^[0-9]+$ && $pgid =~ ^[0-9]+$ ]] || fail "$mode live process observation is invalid"
  check_no_owned_processes "$session_dir" "$pgid" || fail "$mode stop left an owned process"

  cp -- "$session_dir/wayfire.log" "$evidence/$mode-wayfire.log"
  cp -- "$start_log" "$evidence/$mode-start-preserved.log"
  env SCOTTLAND_HEADLESS_DIR="$session_dir" SCOTTLAND_HEADLESS_OWNER="$owner" \
    "$repo/tests/headless.sh" stop >"$stop_log" 2>&1 || \
    fail "$mode owner-verified scratch cleanup failed; evidence remains under runner scratch"
  [[ ! -e $session_dir && ! -L $session_dir ]] || fail "$mode owned scratch remains after stop"
  active_dir=
  active_owner=
  echo "PASS $mode failed-start cleanup: live run-owned Wayfire observed before stop, no session record, owned process group gone, own scratch removed"
}

run_pending_case plain
run_pending_case shim
echo "PASS pending failed-start cleanup fixtures; evidence retained under runner-owned build scratch"
