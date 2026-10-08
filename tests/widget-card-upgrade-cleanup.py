#!/usr/bin/env python3
"""Fail-closed cleanup for the package-upgrade headless session.

This owns only processes carrying this wrapper's token (plus Quickshell processes whose argv names
the unique scratch card package), and only runtime files recorded or claimed by that session.
"""
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import sys
import time


def fail(message):
    raise RuntimeError(message)


def lstat(path):
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def assert_no_symlink_components(path):
    path = Path(path)
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current = current / component
        info = lstat(current)
        if info is not None and stat.S_ISLNK(info.st_mode):
            fail(f'refusing path through symlink: {current}')


def validate_runtime(runtime):
    if not runtime.is_absolute():
        fail('runtime path is not absolute; refusing runtime use')
    assert_no_symlink_components(runtime)
    try:
        canonical = runtime.resolve(strict=True)
    except (FileNotFoundError, RuntimeError, OSError) as error:
        fail(f'runtime path cannot be resolved safely: {error}')
    if canonical != runtime:
        fail('runtime path is not canonical; refusing runtime use')
    runtime_info = lstat(runtime)
    if runtime_info is None or stat.S_ISLNK(runtime_info.st_mode) or not stat.S_ISDIR(runtime_info.st_mode):
        fail('runtime path is missing or is not a real directory')
    state_dir = runtime / 'scottland'
    state_info = lstat(state_dir)
    if state_info is not None and (stat.S_ISLNK(state_info.st_mode) or not stat.S_ISDIR(state_info.st_mode)):
        fail('Scottland runtime directory is not a real directory')
    return canonical


def create_owner_record(owner_record, owner, repo):
    if not re.fullmatch(r'[0-9a-f-]{36}', owner):
        fail('owner token is not a UUID')
    build = repo / 'build'
    suffix = '.widget-upgrade-owner'
    if (not repo.is_absolute() or not owner_record.is_absolute() or
            not owner_record.name.endswith(suffix) or owner_record.parent != build):
        fail('owner record is not the expected direct build/ sidecar')
    owner_dir = owner_record.with_name(owner_record.name[:-len(suffix)])
    assert_no_symlink_components(repo)
    assert_no_symlink_components(build)
    assert_no_symlink_components(owner_dir)
    build_info = lstat(build)
    if build_info is None or stat.S_ISLNK(build_info.st_mode) or not stat.S_ISDIR(build_info.st_mode):
        fail('checkout build/ is missing or is not a real directory')
    if lstat(owner_dir) is not None:
        fail('headless owner directory appeared before setup')
    data = (owner + '\n').encode()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
    fd = None
    opened = None
    interrupted = []

    def note_interrupt(signum, _frame):
        interrupted.append(signum)

    old_int = signal.signal(signal.SIGINT, note_interrupt)
    old_term = signal.signal(signal.SIGTERM, note_interrupt)
    try:
        fd = os.open(owner_record, flags, 0o600)
        opened = os.fstat(fd)
        os.fchmod(fd, 0o600)
        offset = 0
        while offset < len(data):
            offset += os.write(fd, data[offset:])
        os.fsync(fd)
        if interrupted:
            raise InterruptedError(f'owner record setup interrupted by signal {interrupted[0]}')
        os.close(fd)
        fd = None
    except BaseException:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        current = lstat(owner_record)
        if (opened is not None and current is not None and stat.S_ISREG(current.st_mode) and
                current.st_dev == opened.st_dev and current.st_ino == opened.st_ino):
            owner_record.unlink()
        raise
    finally:
        signal.signal(signal.SIGINT, old_int)
        signal.signal(signal.SIGTERM, old_term)


def process_info(pid):
    try:
        data = Path('/proc', str(pid), 'stat').read_text()
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return None
    close = data.rfind(')')
    if close < 0:
        return None
    fields = data[close + 2:].split()
    try:
        return {'state': fields[0], 'ppid': int(fields[1]), 'start': int(fields[19])}
    except (IndexError, ValueError):
        return None


def proc_environ(pid):
    try:
        entries = Path('/proc', str(pid), 'environ').read_bytes().split(b'\0')
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return {}
    return dict(entry.split(b'=', 1) for entry in entries if b'=' in entry)


def proc_argv(pid):
    try:
        return Path('/proc', str(pid), 'cmdline').read_bytes().split(b'\0')
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return []


def ancestor_pids(pid):
    found = set()
    while pid > 1 and pid not in found:
        found.add(pid)
        info = process_info(pid)
        if info is None:
            break
        pid = info['ppid']
    return found


def target_processes(owner, owner_dir, package_shell, excluded):
    targets = {}
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid in excluded:
            continue
        info = process_info(pid)
        if info is None or info['state'] == 'Z':
            continue
        env = proc_environ(pid)
        if (env.get(b'SCOTTLAND_HEADLESS_OWNER') == owner.encode() and
                env.get(b'SCOTTLAND_HEADLESS_OWNER_DIR') == str(owner_dir).encode()):
            targets[pid] = {'pid': pid, 'start': info['start'], 'kind': 'session'}
            continue
        argv = proc_argv(pid)
        if (argv and argv[0].endswith(b'quickshell') and
                package_shell.encode() in argv):
            targets[pid] = {'pid': pid, 'start': info['start'], 'kind': 'scratch-card'}
    return targets


def still_target(ref, owner, owner_dir, package_shell):
    info = process_info(ref['pid'])
    if info is None or info['state'] == 'Z' or info['start'] != ref['start']:
        return False
    if ref['kind'] == 'session':
        env = proc_environ(ref['pid'])
        return (env.get(b'SCOTTLAND_HEADLESS_OWNER') == owner.encode() and
                env.get(b'SCOTTLAND_HEADLESS_OWNER_DIR') == str(owner_dir).encode())
    argv = proc_argv(ref['pid'])
    return bool(argv and argv[0].endswith(b'quickshell') and package_shell.encode() in argv)


def signal_target(ref, sig, owner, owner_dir, package_shell):
    if not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
        fail('pidfd signaling is unavailable; refusing PID-based cleanup')
    try:
        fd = os.pidfd_open(ref['pid'], 0)
    except ProcessLookupError:
        return
    try:
        if still_target(ref, owner, owner_dir, package_shell):
            signal.pidfd_send_signal(fd, sig)
            print(f'{signal.Signals(sig).name} {ref["kind"]} pid={ref["pid"]}', flush=True)
    except ProcessLookupError:
        pass
    finally:
        os.close(fd)


def owned_env(path, owner, owner_dir):
    assert_no_symlink_components(path)
    info = lstat(path)
    if info is None:
        return False
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        fail(f'refusing non-regular session environment record: {path}')
    fields = dict(entry.split(b'=', 1) for entry in path.read_bytes().split(b'\0') if b'=' in entry)
    if (fields.get(b'SCOTTLAND_HEADLESS_OWNER') != owner.encode() or
            fields.get(b'SCOTTLAND_HEADLESS_OWNER_DIR') != str(owner_dir).encode()):
        fail(f'session environment record has a different owner: {path}')
    return True


def runtime_view_path(name, runtime, runtime_view):
    logical = Path(name)
    try:
        relative = logical.relative_to(runtime)
    except ValueError:
        fail(f'runtime manifest path is outside XDG_RUNTIME_DIR: {name!r}')
    if not relative.parts or any(part in ('', '.', '..') for part in relative.parts):
        fail(f'runtime manifest path has an unsafe relative component: {name!r}')
    owned_path = runtime_view / relative
    assert_no_symlink_components(owned_path)
    return owned_path


def remove_recorded_paths(manifest_path, runtime, runtime_view, owner, owner_dir):
    info = lstat(manifest_path)
    if info is None:
        print('no runtime inode manifest; leaving unrecorded runtime paths untouched', flush=True)
        return False
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        fail(f'refusing non-regular runtime manifest: {manifest_path}')
    manifest = json.loads(manifest_path.read_text())
    display = manifest.get('display')
    if (manifest.get('owner') != owner or manifest.get('runtime') != str(runtime) or
            not isinstance(display, str) or not re.fullmatch(r'wayland-[0-9]+', display)):
        fail('runtime inode manifest identity does not match this session')
    env_path = runtime_view / 'scottland' / f'{display}.env'
    if not owned_env(env_path, owner, owner_dir):
        fail(f'owned environment record is missing: {env_path}')
    allowed = {
        str(runtime / 'scottland' / f'{display}.env'): stat.S_IFREG,
        str(runtime / display): stat.S_IFSOCK,
        str(runtime / f'{display}.lock'): stat.S_IFREG,
    }
    files = manifest.get('files')
    if not isinstance(files, dict) or not files or not set(files).issubset(allowed):
        fail('runtime inode manifest contains an unexpected path set')
    for name, recorded in files.items():
        path = Path(name)
        if not path.is_absolute() or name != str(path) or not isinstance(recorded, dict):
            fail(f'malformed runtime manifest entry: {name!r}')
        owned_path = runtime_view_path(name, runtime, runtime_view)
        current = lstat(owned_path)
        if current is None:
            continue
        if stat.S_ISLNK(current.st_mode):
            fail(f'refusing to unlink replaced symlink: {owned_path}')
        expected = allowed[name]
        if (current.st_dev != recorded.get('device') or current.st_ino != recorded.get('inode') or
                stat.S_IFMT(current.st_mode) != expected or recorded.get('mode') != expected):
            fail(f'runtime path identity changed; leaving it untouched: {owned_path}')
        if name == str(runtime / 'scottland' / f'{display}.env') and \
                not owned_env(owned_path, owner, owner_dir):
            fail(f'environment record owner changed: {owned_path}')
        owned_path.unlink()
        print(f'unlinked owned runtime view path {owned_path}', flush=True)
    return True


def read_manifest(manifest_path, runtime, owner):
    info = lstat(manifest_path)
    if info is None or stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        fail(f'runtime inode manifest is missing or invalid: {manifest_path}')
    manifest = json.loads(manifest_path.read_text())
    display = manifest.get('display')
    if (manifest.get('owner') != owner or manifest.get('runtime') != str(runtime) or
            not isinstance(display, str) or not re.fullmatch(r'wayland-[0-9]+', display)):
        fail('runtime inode manifest identity does not match this session')
    return manifest


def remove_widget_runtime(runtime, display, owner, owned_pids):
    parent = runtime / 'scottland' / 'widgets'
    state_dir = parent / display
    for directory in (runtime, runtime / 'scottland', parent):
        directory_info = lstat(directory)
        if directory_info is not None and (stat.S_ISLNK(directory_info.st_mode) or
                                           not stat.S_ISDIR(directory_info.st_mode)):
            fail(f'refusing unsafe widget runtime parent: {directory}')
    state_info = lstat(state_dir)
    pid_file = runtime / 'scottland' / f'{display}.widget-bus.pid'
    pid_info = lstat(pid_file)
    if state_info is None:
        if pid_info is not None:
            fail(f'widget pid file exists without its owner marker directory: {pid_file}')
        return
    if stat.S_ISLNK(state_info.st_mode) or not stat.S_ISDIR(state_info.st_mode):
        fail(f'refusing non-directory widget runtime: {state_dir}')
    marker = state_dir / '.headless-owner'
    marker_info = lstat(marker)
    if marker_info is None or stat.S_ISLNK(marker_info.st_mode) or not stat.S_ISREG(marker_info.st_mode):
        fail(f'widget runtime owner marker missing or invalid: {marker}')
    if marker.read_text().strip() != owner:
        fail(f'widget runtime belongs to a different owner: {state_dir}')
    if pid_info is not None:
        if stat.S_ISLNK(pid_info.st_mode) or not stat.S_ISREG(pid_info.st_mode):
            fail(f'refusing non-file widget pid record: {pid_file}')
        try:
            pid = int(pid_file.read_text().splitlines()[0])
        except (IndexError, ValueError):
            fail(f'widget pid record is malformed: {pid_file}')
        if pid not in owned_pids:
            fail(f'widget pid is not among this session\'s observed processes: {pid_file}')
        pid_file.unlink()
        print(f'unlinked owned widget pid record {pid_file}', flush=True)
    shutil.rmtree(state_dir)
    print(f'removed owned widget runtime {state_dir}', flush=True)


def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--validate-runtime':
        print(validate_runtime(Path(sys.argv[2])))
        return
    if len(sys.argv) == 5 and sys.argv[1] == '--create-owner-record':
        create_owner_record(Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4]))
        return
    if len(sys.argv) != 5:
        fail('usage: widget-card-upgrade-cleanup.py SESSION_DIR OWNER_RECORD RUNTIME REPO')
    owner_dir = Path(sys.argv[1])
    owner_record = Path(sys.argv[2])
    runtime = Path(sys.argv[3])
    repo = Path(sys.argv[4])
    build = repo / 'build'
    if (not owner_dir.is_absolute() or owner_dir.parent != build or not repo.is_absolute()):
        fail('session directory is not a real direct child of this checkout build/')
    assert_no_symlink_components(repo)
    assert_no_symlink_components(build)
    assert_no_symlink_components(owner_dir)
    if lstat(build) is None or not stat.S_ISDIR(lstat(build).st_mode):
        fail('checkout build/ is missing or is not a directory')
    owner_dir_info = lstat(owner_dir)
    owner_dir_identity = None
    if owner_dir_info is not None and (stat.S_ISLNK(owner_dir_info.st_mode) or
                                       not stat.S_ISDIR(owner_dir_info.st_mode)):
        fail('session directory is not a real directory')
    if owner_record != Path(str(owner_dir) + '.widget-upgrade-owner'):
        fail('owner record path does not match the session directory')
    record_info = lstat(owner_record)
    if record_info is None or stat.S_ISLNK(record_info.st_mode) or not stat.S_ISREG(record_info.st_mode):
        fail(f'owner record is missing or invalid: {owner_record}')
    owner = owner_record.read_text().strip()
    if not re.fullmatch(r'[0-9a-f-]{36}', owner):
        fail('owner record does not contain a valid UUID token')
    record_identity = (record_info.st_dev, record_info.st_ino, stat.S_IFMT(record_info.st_mode))
    runtime = validate_runtime(runtime)

    runtime_view = owner_dir / 'runtime'
    headless_owner_record = owner_dir / '.scottland-headless-owner'
    runtime_view_info = lstat(runtime_view)
    headless_owner_info = lstat(headless_owner_record)
    runtime_view_identity = None
    headless_owner_identity = None
    if owner_dir_info is not None:
        if owner_dir_info.st_uid != os.getuid() or stat.S_IMODE(owner_dir_info.st_mode) != 0o700:
            fail('session directory is not private and owned by this uid')
        owner_dir_identity = (owner_dir_info.st_dev, owner_dir_info.st_ino, stat.S_IFMT(owner_dir_info.st_mode))
        if (headless_owner_info is None or stat.S_ISLNK(headless_owner_info.st_mode) or
                not stat.S_ISREG(headless_owner_info.st_mode)):
            fail('headless owner record is missing or invalid')
        owner_lines = headless_owner_record.read_text().splitlines()
        if owner_lines != [str(os.getuid()), str(runtime), owner]:
            fail('headless owner record does not match this test token/runtime')
        if headless_owner_info.st_uid != os.getuid():
            fail('headless owner record is owned by another uid')
        headless_owner_identity = (headless_owner_info.st_dev, headless_owner_info.st_ino,
                                  stat.S_IFMT(headless_owner_info.st_mode))
        if (runtime_view_info is None or stat.S_ISLNK(runtime_view_info.st_mode) or
                not stat.S_ISDIR(runtime_view_info.st_mode)):
            fail('headless runtime view is missing or invalid')
        if runtime_view_info.st_uid != os.getuid() or stat.S_IMODE(runtime_view_info.st_mode) != 0o700:
            fail('headless runtime view is not private and owned by this uid')
        runtime_view_identity = (runtime_view_info.st_dev, runtime_view_info.st_ino,
                                 stat.S_IFMT(runtime_view_info.st_mode))
        assert_no_symlink_components(runtime_view)
        scottland_view = runtime_view / 'scottland'
        scottland_info = lstat(scottland_view)
        if scottland_info is not None and (stat.S_ISLNK(scottland_info.st_mode) or
                                           not stat.S_ISDIR(scottland_info.st_mode)):
            fail('headless Scottland runtime view is not a real directory')
    elif runtime_view_info is not None or headless_owner_info is not None:
        fail('headless runtime data exists without its session directory')

    package_shell = str(owner_dir / 'widgets' / 'card' / 'shell.qml')
    excluded = ancestor_pids(os.getpid())
    initial = target_processes(owner, owner_dir, package_shell, excluded)
    owned_pids = set(initial)
    print(f'owner={owner} observed_processes={sorted(owned_pids)}', flush=True)
    for ref in initial.values():
        signal_target(ref, signal.SIGTERM, owner, owner_dir, package_shell)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        remaining = target_processes(owner, owner_dir, package_shell, excluded)
        if not remaining:
            break
        time.sleep(0.05)
    remaining = target_processes(owner, owner_dir, package_shell, excluded)
    for ref in remaining.values():
        signal_target(ref, signal.SIGKILL, owner, owner_dir, package_shell)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        remaining = target_processes(owner, owner_dir, package_shell, excluded)
        if not remaining:
            break
        time.sleep(0.05)
    if remaining:
        fail(f'owned processes remain after exact cleanup: {sorted(remaining)}')

    manifest_path = owner_dir / 'runtime-owned.json'
    manifest_info = lstat(manifest_path)
    display = None
    if manifest_info is not None:
        manifest = read_manifest(manifest_path, runtime, owner)
        display = manifest.get('display')
    elif runtime_view_info is not None:
        # The environment record can prove its own ownership before the socket manifest exists.
        env_dir = runtime_view / 'scottland'
        assert_no_symlink_components(env_dir)
        for candidate in env_dir.glob('wayland-*.env'):
            if owned_env(candidate, owner, owner_dir):
                display = candidate.name[:-4]
                break
    if display:
        cleanup_issues = []
        if manifest_info is None:
            # Every runtime path is under this freshly-created, private owner directory because
            # headless.sh bind-mounts owner_dir/runtime at the unchanged XDG_RUNTIME_DIR. Without
            # a manifest, do not guess which individual socket/lock paths are ours; defer their
            # complete removal to the identity-checked owner_dir rmtree below.
            print('no runtime inode manifest; deferring private runtime-view cleanup to owner-tree removal',
                  flush=True)
        else:
            try:
                remove_widget_runtime(runtime_view, display, owner, owned_pids)
            except RuntimeError as error:
                cleanup_issues.append(str(error))
                print(f'left widget runtime untouched: {error}', file=sys.stderr, flush=True)
            try:
                if not remove_recorded_paths(manifest_path, runtime, runtime_view, owner, owner_dir):
                    cleanup_issues.append('runtime inode manifest vanished before cleanup')
                else:
                    current_manifest = lstat(manifest_path)
                    if (current_manifest is None or stat.S_ISLNK(current_manifest.st_mode) or
                            not stat.S_ISREG(current_manifest.st_mode) or
                            (current_manifest.st_dev, current_manifest.st_ino,
                             stat.S_IFMT(current_manifest.st_mode)) !=
                            (manifest_info.st_dev, manifest_info.st_ino,
                             stat.S_IFMT(manifest_info.st_mode))):
                        fail('runtime manifest identity changed before removal')
                    manifest_path.unlink()
            except RuntimeError as error:
                cleanup_issues.append(str(error))
                print(f'left remaining runtime paths untouched: {error}', file=sys.stderr, flush=True)
        if cleanup_issues:
            fail('; '.join(cleanup_issues))
    elif manifest_info is not None:
        fail('runtime manifest exists without a display identity')
    else:
        print('no session runtime record was published', flush=True)

    current_owner_dir = lstat(owner_dir)
    if owner_dir_identity is not None:
        if (current_owner_dir is None or stat.S_ISLNK(current_owner_dir.st_mode) or
                not stat.S_ISDIR(current_owner_dir.st_mode) or
                (current_owner_dir.st_dev, current_owner_dir.st_ino,
                 stat.S_IFMT(current_owner_dir.st_mode)) != owner_dir_identity):
            fail('session directory identity changed before cleanup')
        current_headless_owner = lstat(headless_owner_record)
        if (current_headless_owner is None or stat.S_ISLNK(current_headless_owner.st_mode) or
                not stat.S_ISREG(current_headless_owner.st_mode) or
                (current_headless_owner.st_dev, current_headless_owner.st_ino,
                 stat.S_IFMT(current_headless_owner.st_mode)) != headless_owner_identity or
                headless_owner_record.read_text().splitlines() != [str(os.getuid()), str(runtime), owner]):
            fail('headless owner record identity changed before cleanup')
        current_runtime_view = lstat(runtime_view)
        if (current_runtime_view is None or stat.S_ISLNK(current_runtime_view.st_mode) or
                not stat.S_ISDIR(current_runtime_view.st_mode) or
                (current_runtime_view.st_dev, current_runtime_view.st_ino,
                 stat.S_IFMT(current_runtime_view.st_mode)) != runtime_view_identity):
            fail('headless runtime view identity changed before cleanup')
        shutil.rmtree(owner_dir)
        print(f'removed identity-checked owner session tree and private runtime view {owner_dir}', flush=True)
    current_record = lstat(owner_record)
    if (current_record is None or stat.S_ISLNK(current_record.st_mode) or
            not stat.S_ISREG(current_record.st_mode) or
            (current_record.st_dev, current_record.st_ino,
             stat.S_IFMT(current_record.st_mode)) != record_identity):
        fail('owner record changed before final removal')
    if owner_record.read_text().strip() != owner:
        fail('owner record token changed before final removal')
    owner_record.unlink()
    print('removed owner record; exact cleanup complete', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(f'cleanup refused or incomplete: {error}', file=sys.stderr, flush=True)
        sys.exit(1)
