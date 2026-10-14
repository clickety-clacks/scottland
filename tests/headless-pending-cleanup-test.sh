#!/usr/bin/env bash
set -euo pipefail

repo=$(cd -- "$(dirname -- "$0")/.." && pwd -P)
build=$(realpath -m -- "$repo/build")
: "${SCOTTLAND_TEST_SCRATCH:?set a fresh run-owned scratch directory under this checkout build/}"
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
    env SCOTTLAND_HEADLESS_DIR="$active_dir" \
      SCOTTLAND_SESSION_DIR="$active_dir/session" SCOTTLAND_HEADLESS_OWNER="$active_owner" \
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
  local watcher_record="$evidence/$mode-live-before-stop.json"
  local launch_record="$session_dir/launch-owner.json"
  local terminal_record="$session_dir/launch-terminal.json"
  local owner runtime socket env_file watch_status start_status owner_socket missing_owner_dir
  [[ ! -e $session_dir && ! -L $session_dir ]] || fail "$mode session path is occupied"
  owner_socket=$session_dir/owner.sock
  [[ ${#owner_socket} -lt 108 ]] || fail "$mode owner socket path exceeds AF_UNIX limit; use a short checkout path"
  missing_owner_dir=$session_dir/record-owner-missing
  [[ ! -e $missing_owner_dir && ! -L $missing_owner_dir ]] || fail "$mode owner-failure fixture path is occupied"
  owner=$(python3 -c 'import uuid; print(uuid.uuid4())')
  runtime=$(realpath -e -- "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}")
  active_dir=$session_dir
  active_owner=$owner

  env SCOTTLAND_HEADLESS_DIR="$session_dir" \
    SCOTTLAND_SESSION_DIR="$session_dir/session" SCOTTLAND_HEADLESS_OWNER="$owner" \
    SCOTTLAND_HEADLESS_OWNER_DIR="$missing_owner_dir" \
    "$repo/tests/headless.sh" "${start_args[@]}" >"$start_log" 2>&1 &
  local start_pid=$!

  if python3 - "$session_dir" "$runtime" "$owner" "$mode" "$watcher_record" \
      "$repo/tests/headless-owner.py" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import time

scratch = Path(sys.argv[1])
runtime = os.fsencode(sys.argv[2])
owner = sys.argv[3]
mode = sys.argv[4]
record_out = Path(sys.argv[5])
owner_helper = os.path.realpath(sys.argv[6])
uid = os.getuid()


def regular_bytes(path, maximum=65536):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_size > maximum:
            raise RuntimeError(f'not a bounded, caller-owned regular file: {path}')
        if info.st_mode & 0o077:
            raise RuntimeError(f'owner receipt is not private: {path}')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            data = stream.read(maximum + 1)
        if len(data) > maximum:
            raise RuntimeError(f'oversized owner receipt: {path}')
        return data, [info.st_dev, info.st_ino, info.st_uid]
    finally:
        os.close(fd)


def directory_inode(path):
    info = os.stat(path, follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o700:
        raise RuntimeError(f'run directory is not a private caller-owned directory: {path}')
    return [info.st_dev, info.st_ino, info.st_uid]


def proc_info(pid):
    proc = Path('/proc') / str(pid)
    raw = (proc / 'stat').read_bytes()
    fields = raw.rsplit(b')', 1)[1].split()
    return {
        'pid': pid,
        'state': fields[0].decode(),
        'ppid': int(fields[1]),
        'pgid': int(fields[2]),
        'sid': int(fields[3]),
        'starttime': int(fields[19]),
        'comm': (proc / 'comm').read_bytes().strip().decode('utf-8', 'surrogateescape'),
        'argv': [os.fsdecode(part) for part in (proc / 'cmdline').read_bytes().split(b'\0') if part],
    }


def children(pid):
    values = (Path('/proc') / str(pid) / 'task' / str(pid) / 'children').read_text().split()
    return [int(value) for value in values]


def load_owner():
    scratch_inode = directory_inode(scratch)
    runtime_inode = directory_inode(scratch / 'runtime')
    marker_bytes, marker_inode = regular_bytes(scratch / '.scottland-headless-owner')
    marker_lines = marker_bytes.decode().splitlines()
    if marker_lines != [str(uid), os.fsdecode(runtime), owner]:
        raise RuntimeError('scratch marker does not name this run')
    raw, receipt_inode = regular_bytes(scratch / 'launch-owner.json')
    receipt = json.loads(raw)
    binding = receipt['binding']
    if (binding['root'] != scratch_inode or binding['runtime'] != runtime_inode or
            binding['marker'] != marker_inode or
            binding['marker_sha256'] != hashlib.sha256(marker_bytes).hexdigest() or
            binding['owner'] != owner):
        raise RuntimeError('guardian receipt is not bound to this scratch/runtime/owner')
    if receipt_inode[2] != uid:
        raise RuntimeError('guardian receipt owner changed')
    return receipt


def validate_recorded_processes(receipt):
    guardian = receipt['guardian']
    child = receipt['child']
    if not all(isinstance(item.get(key), int) and item[key] > 1
               for item in (guardian, child) for key in ('pid', 'starttime')):
        raise RuntimeError('guardian/child receipt has invalid PID birth identity')
    if child['ppid'] != guardian['pid']:
        raise RuntimeError('recorded launch child was not parented by its recorded guardian')
    observed_guardian = proc_info(guardian['pid'])
    observed_child = proc_info(child['pid'])
    if (observed_guardian['starttime'] != guardian['starttime'] or
            observed_guardian['state'] in ('Z', 'X')):
        raise RuntimeError('recorded guardian PID birth identity is no longer live')
    if (observed_child['starttime'] != child['starttime'] or
            observed_child['ppid'] != guardian['pid'] or
            observed_child['state'] in ('Z', 'X')):
        raise RuntimeError('recorded guardian child PID birth identity is no longer live')
    guardian_args = observed_guardian['argv']
    if (owner_helper not in guardian_args or 'launch' not in guardian_args or
            str(scratch) not in guardian_args):
        raise RuntimeError('recorded guardian command does not identify this headless launch')
    child_args = observed_child['argv']
    if (str(scratch / 'wayfire.ini') not in child_args or
            not any(arg.rsplit('/', 1)[-1] == 'wayfire' for arg in child_args)):
        raise RuntimeError('recorded launch child command does not identify this Wayfire config')
    if (observed_child['pgid'] != child['pid'] or observed_child['sid'] != child['pid'] or
            child['pid'] == os.getpgrp() or child['pid'] == os.getsid(0)):
        raise RuntimeError('recorded child is not the distinct isolated session/process group')

    found = []
    pending = [(pid, None) for pid in children(guardian['pid'])]
    if not any(pid == child['pid'] for pid, _ in pending):
        raise RuntimeError('recorded child is no longer a direct child of the guardian')
    seen = set()
    while pending:
        pid, parent = pending.pop()
        if pid in seen:
            raise RuntimeError('cycle while inspecting the recorded launch subtree')
        seen.add(pid)
        try:
            current = proc_info(pid)
        except FileNotFoundError:
            continue
        if parent is not None and current['ppid'] != parent:
            raise RuntimeError('launch subtree parent identity changed during inspection')
        if current['pgid'] != child['pid'] or current['sid'] != child['pid']:
            continue
        if current['state'] not in ('Z', 'X'):
            command_is_wayfire = current['comm'] == 'wayfire'
            config_is_run_owned = str(scratch / 'wayfire.ini') in current['argv']
            if command_is_wayfire:
                if not config_is_run_owned:
                    raise RuntimeError('Wayfire process in the owner group used another config')
                found.append(current)
            pending.extend((descendant, pid) for descendant in children(pid))
        if len(seen) > 128:
            raise RuntimeError('recorded launch subtree exceeded bounded inspection size')
    if len(found) != 1:
        raise RuntimeError(f'expected one live Wayfire in the recorded group, found {len(found)}')
    return observed_guardian, observed_child, found[0]


deadline = time.monotonic() + 8
last_reason = 'owner receipt not written yet'
while time.monotonic() < deadline:
    try:
        owner_receipt = load_owner()
        guardian, child, wayfire = validate_recorded_processes(owner_receipt)
        mode_record = (scratch / '.scottland-headless-mode').read_text().splitlines()
        if mode_record != [mode + '-pending']:
            raise RuntimeError('session mode marker is not the requested pending state')
        log = (scratch / 'wayfire.log').read_text(errors='replace')
        match = re.search(r'Using socket name (wayland-[0-9]+)', log)
        if not match:
            raise RuntimeError('recorded Wayfire has not published its socket')
        socket_name = match.group(1)
        endpoint = scratch / 'runtime' / socket_name
        endpoint_info = os.stat(endpoint, follow_symlinks=False)
        if not stat.S_ISSOCK(endpoint_info.st_mode) or endpoint_info.st_uid != uid:
            raise RuntimeError('Wayfire endpoint is not a run-owned socket')
        env_file = scratch / 'session' / (socket_name + '.env')
        if env_file.exists() or env_file.is_symlink():
            raise RuntimeError('session environment record was published before the pending assertion')
        record = json.dumps({
            'guardian': guardian,
            'child': child,
            'wayfire': wayfire,
            'process_group': child['pid'],
            'session_id': child['pid'],
            'socket': socket_name,
            'session_env': str(env_file),
            'owner': owner_receipt,
        }, sort_keys=True) + '\n'
        fd = os.open(record_out, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(record)
        sys.exit(0)
    except FileNotFoundError as error:
        last_reason = f'owned process/receipt disappeared during observation: {error}'
    except (KeyError, OSError, RuntimeError, ValueError) as error:
        last_reason = str(error)
        terminal = scratch / 'launch-terminal.json'
        if terminal.exists() and not (scratch / 'launch-owner.json').exists():
            try:
                info = json.loads(terminal.read_text())
                last_reason = f'guardian terminated before child launch: {info.get("error")}'
            except (OSError, ValueError):
                pass
    time.sleep(0.05)
print('FAIL: ' + last_reason, file=sys.stderr)
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
  ((watch_status == 0)) || fail "$mode did not prove the recorded guardian/child and live Wayfire group"
  ((start_status != 0)) || fail "$mode injected failure unexpectedly started a session"

  [[ -f $session_dir/.scottland-headless-owner && ! -L $session_dir/.scottland-headless-owner ]] || \
    fail "$mode owner marker was not retained for inspection"
  [[ $(sed -n '1p' "$session_dir/.scottland-headless-owner") == "$(id -u)" && \
     $(sed -n '2p' "$session_dir/.scottland-headless-owner") == "$runtime" && \
     $(sed -n '3p' "$session_dir/.scottland-headless-owner") == "$owner" ]] || \
    fail "$mode failed-start scratch marker does not match this run"
  [[ $(sed -n '1p' "$session_dir/.scottland-headless-mode") == "$mode-pending" ]] || \
    fail "$mode marker changed before its session record existed"
  [[ ! -e $missing_owner_dir && ! -L $missing_owner_dir ]] || \
    fail "$mode invalid owner-directory fixture changed during start"

  socket=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["socket"])' "$watcher_record")
  env_file="$session_dir/session/$socket.env"
  [[ ! -e $env_file && ! -L $env_file ]] || fail "$mode unexpectedly wrote a session environment record"
  pgid=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["process_group"])' "$watcher_record")
  [[ $pgid =~ ^[0-9]+$ ]] || fail "$mode recorded process group is invalid"

  python3 - "$launch_record" "$terminal_record" "$watcher_record" "$session_dir" "$runtime" "$owner" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

launch_path, terminal_path, observed_path, scratch_arg, runtime, owner = sys.argv[1:]
scratch = Path(scratch_arg)
uid = os.getuid()


def read_record(path):
    info = os.stat(path, follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_mode & 0o077:
        raise SystemExit(f'unsafe or non-private owner receipt: {path}')
    return json.loads(Path(path).read_text())


launch = read_record(launch_path)
terminal = read_record(terminal_path)
observed = read_record(observed_path)
marker = (scratch / '.scottland-headless-owner').read_bytes()
marker_info = os.stat(scratch / '.scottland-headless-owner', follow_symlinks=False)
root_info = os.stat(scratch, follow_symlinks=False)
runtime_info = os.stat(scratch / 'runtime', follow_symlinks=False)
binding = launch['binding']
if binding['owner'] != owner or binding['root'] != [root_info.st_dev, root_info.st_ino, root_info.st_uid] or \
   binding['runtime'] != [runtime_info.st_dev, runtime_info.st_ino, runtime_info.st_uid] or \
   binding['marker'] != [marker_info.st_dev, marker_info.st_ino, marker_info.st_uid] or \
   binding['marker_sha256'] != hashlib.sha256(marker).hexdigest():
    raise SystemExit('retained guardian receipt no longer matches this run-owned scratch')
if marker.decode().splitlines() != [str(uid), runtime, owner]:
    raise SystemExit('retained marker no longer identifies this run')
if terminal.get('owner') != launch or terminal.get('exit') != 0 or terminal.get('children') != []:
    raise SystemExit('guardian terminal does not prove clean finalization of the recorded launch')
if observed.get('owner') != launch or observed.get('process_group') != launch['child']['pid']:
    raise SystemExit('observed Wayfire process group is not the recorded owner child')

for key in ('guardian', 'child'):
    birth = launch[key]
    try:
        raw = Path(f'/proc/{birth["pid"]}/stat').read_bytes()
    except FileNotFoundError:
        continue
    fields = raw.rsplit(b')', 1)[1].split()
    if int(fields[19]) == birth['starttime']:
        raise SystemExit(f'recorded {key} process identity remains after cleanup')

try:
    os.killpg(launch['child']['pid'], 0)
except ProcessLookupError:
    pass
except OSError as error:
    raise SystemExit(f'cannot verify isolated Wayfire process group is gone: {error}')
else:
    raise SystemExit('recorded isolated Wayfire process group remains after cleanup')
PY

  cp -- "$session_dir/wayfire.log" "$evidence/$mode-wayfire.log"
  cp -- "$start_log" "$evidence/$mode-start-preserved.log"
  cp -- "$launch_record" "$evidence/$mode-launch-owner.json"
  cp -- "$terminal_record" "$evidence/$mode-launch-terminal.json"
  echo "PASS $mode failed-start cleanup: pending env record absent; recorded guardian finalized its child; isolated Wayfire group gone"
  active_dir=
  active_owner=
}

run_pending_case plain
run_pending_case shim
echo "PASS pending failed-start cleanup assertions; run-owned evidence retained under test scratch"
