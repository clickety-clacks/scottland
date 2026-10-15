#!/usr/bin/env python3
"""Own one headless launch subtree, including startup before its env record exists.

Linux-only test helper. No process-group or shared-session discovery/signalling.
The guardian is a subreaper; only its direct children are eligible for pidfd stop.
"""
import argparse
from contextlib import contextmanager
import ctypes
import hashlib
import json
import os
from pathlib import Path
import select
import secrets
import signal
import socket
import stat
import struct
import sys
import time


class Refusal(RuntimeError):
    pass


@contextmanager
def owner_directory(state):
    """Resolve the guardian socket inside its verified directory without a long pathname."""
    previous = os.open('.', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fchdir(state.fd)
        yield
    finally:
        try:
            os.fchdir(previous)
        finally:
            os.close(previous)


def birth(pid):
    data = Path(f'/proc/{pid}/stat').read_bytes().rsplit(b')', 1)[1].split()
    return {'pid': pid, 'ppid': int(data[1]), 'starttime': int(data[19])}


def inode(fd):
    s = os.fstat(fd)
    return [s.st_dev, s.st_ino, s.st_uid]


def same_birth(left, right):
    # The guardian may be reparented after the starting shell exits.
    return (left['pid'], left['starttime']) == (right['pid'], right['starttime'])


class State:
    def __init__(self, directory):
        self.path = os.path.abspath(directory)
        self.fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        s = os.fstat(self.fd)
        if s.st_uid != os.getuid() or stat.S_IMODE(s.st_mode) != 0o700:
            raise Refusal('owner directory must remain private and owned')
        self.root = inode(self.fd)

    def read(self, name):
        return self.read_bound(name)[0]

    def read_bound(self, name):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.fd)
        try:
            s = os.fstat(fd)
            if not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid() or s.st_size > 65536:
                raise Refusal('invalid owner receipt')
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                data = stream.read(65537)
            if len(data) > 65536:
                raise Refusal('oversize owner receipt')
            return data, inode(fd)
        finally:
            os.close(fd)

    def binding(self):
        # Bind backing runtime and marker before any child may exec.
        fd = os.open('runtime', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.fd)
        try:
            runtime = inode(fd)
        finally:
            os.close(fd)
        marker, marker_identity = self.read_bound('.scottland-headless-owner')
        lines = marker.decode().split('\n')
        if len(lines) != 4 or lines[0] != str(os.getuid()) or lines[3]:
            raise Refusal('invalid launch owner marker')
        current = os.stat(self.path, follow_symlinks=False)
        if [current.st_dev, current.st_ino, current.st_uid] != self.root:
            raise Refusal('owner directory replaced')
        return {'root': self.root, 'runtime': runtime, 'marker': marker_identity,
                'marker_sha256': hashlib.sha256(marker).hexdigest(), 'owner': lines[2]}

    def write(self, name, value):
        temp = name + '.tmp'
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=self.fd)
        try:
            data = (json.dumps(value, sort_keys=True) + '\n').encode()
            with os.fdopen(fd, 'wb', closefd=False) as stream:
                stream.write(data); stream.flush(); os.fsync(fd)
        finally:
            os.close(fd)
        os.rename(temp, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
        try:
            os.fsync(self.fd)
        except OSError:
            # A readable success receipt must not survive a failed final fsync.
            os.unlink(name, dir_fd=self.fd)
            raise


def reap():
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if not pid:
            return


def children():
    return [int(p) for p in Path(f'/proc/self/task/{os.getpid()}/children').read_text().split()]


def stop_children(deadline):
    """Subreaper ancestry, then pidfd + birth recheck; never a numeric kill."""
    signalled = {}
    while True:
        reap()
        pending = children()
        if not pending:
            return
        if time.monotonic() >= deadline:
            raise Refusal('owned descendants remain after stop deadline')
        for pid in pending:
            try:
                first = birth(pid)
                fd = os.pidfd_open(pid)
                try:
                    if first != birth(pid) or first['ppid'] != os.getpid():
                        raise Refusal('child identity changed before stop')
                    key = (pid, first['starttime'])
                    now = time.monotonic()
                    if key not in signalled:
                        signal.pidfd_send_signal(fd, signal.SIGTERM)
                        signalled[key] = now
                    elif now - signalled[key] >= 2:
                        signal.pidfd_send_signal(fd, signal.SIGKILL)
                finally:
                    os.close(fd)
            except ProcessLookupError:
                continue
        # Killing a parent reparents its children to this subreaper. Repeat only
        # over this guardian's direct children until waitpid proves them reaped.
        time.sleep(0.01)


def read_message(peer):
    data = bytearray()
    while len(data) <= 4096:
        part = peer.recv(1)
        if not part:
            raise Refusal('finalization channel closed without a full message')
        if part == b'\n':
            return bytes(data)
        data.extend(part)
    raise Refusal('oversize finalization message')


def launch(directory, argv):
    state = State(directory)
    control = {'signal': 0}
    def interrupted(sig, frame):
        control['signal'] = control['signal'] or sig
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, interrupted)
    result = 75
    error = None
    server = None
    client = None
    nonce = None
    terminal_hash = None
    read_gate = write_gate = None
    record = None
    try:
        if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
            raise Refusal('cannot become task child subreaper')
        binding = state.binding()
        runtime = os.stat(os.environ['XDG_RUNTIME_DIR'], follow_symlinks=False)
        if [runtime.st_dev, runtime.st_ino, runtime.st_uid] != binding['runtime']:
            raise Refusal('launch is outside the task runtime mount')
        server = socket.socket(socket.AF_UNIX)
        with owner_directory(state):
            server.bind('owner.sock')
        server.listen(1)
        read_gate, write_gate = os.pipe2(os.O_CLOEXEC)
        child = os.fork()
        if child == 0:
            os.close(write_gate)
            server.close(); os.close(state.fd)
            if os.read(read_gate, 1) != b'G':
                os._exit(75)
            os.close(read_gate)
            for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                signal.signal(sig, signal.SIG_DFL)
            try:
                os.execvp(argv[0], argv)
            except BaseException:
                # Never enter the parent's terminal writer with inherited,
                # already-closed custody descriptors after an exec failure.
                os._exit(75)
        os.close(read_gate); read_gate = None
        record = {'binding': binding, 'guardian': birth(os.getpid()), 'child': birth(child),
                  'mount_namespace': os.readlink('/proc/self/ns/mnt')}
        state.write('launch-owner.json', record)
        if control['signal']:
            raise Refusal('interrupted before child release')
        os.write(write_gate, b'G'); os.close(write_gate); write_gate = None
        while not control['signal']:
            reap()
            if not children():
                raise Refusal('launch subtree exited without requested stop')
            ready, _, _ = select.select([server], [], [], 0.1)
            if not ready:
                continue
            client = server.accept()[0]
            client.settimeout(1)
            _, uid, _ = struct.unpack('3i', client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            request = json.loads(read_message(client))
            nonce = request.get('nonce')
            if (uid != os.getuid() or request.get('stop') != binding['owner'] or
                    not isinstance(nonce, str) or len(nonce) != 64 or
                    any(c not in '0123456789abcdef' for c in nonce)):
                raise Refusal('unowned stop request')
            if state.binding() != binding:
                raise Refusal('task ownership changed before stop')
            result = 0
            break
        if control['signal']:
            result = 128 + control['signal']
    except Exception as exc:
        error = str(exc)
    finally:
        for fd in (read_gate, write_gate):
            if fd is not None:
                os.close(fd)
        try:
            stop_children(time.monotonic() + 5)
        except Exception as exc:
            result = 75; error = str(exc)
        if control['signal']:
            result = 128 + control['signal']
        try:
            if record is not None and state.binding() != record['binding']:
                raise Refusal('task ownership changed during finalization')
            state.write('launch-terminal.json', {'exit': result, 'error': error, 'owner': record,
                                                'children': children(), 'signal': control['signal']})
            terminal_hash = hashlib.sha256(state.read('launch-terminal.json')).hexdigest()
        except Exception as exc:
            print('cannot persist launch terminal: ' + str(exc), file=sys.stderr)
            result = 75
        try:
            if server is not None:
                server.close()
            os.close(state.fd)
        except Exception as exc:
            print('cannot close launch custody: ' + str(exc), file=sys.stderr)
            result = 75
        # Commit boundary: finish all persistence/custody work first, then block
        # handled controls through process exit. No installed handler can set an
        # unchecked flag after this decision. A signal already delivered or
        # pending at the boundary vetoes acknowledgement. Later controls are
        # after completed finalization; this helper never restores the mask.
        controls = {signal.SIGINT, signal.SIGTERM, signal.SIGHUP}
        signal.pthread_sigmask(signal.SIG_BLOCK, controls)
        pending = signal.sigpending() & controls
        if control['signal'] or pending:
            result = 128 + (control['signal'] or min(pending))
        if client is not None:
            try:
                client.sendall((json.dumps({'nonce': nonce, 'exit': result,
                                            'terminal_sha256': terminal_hash}) + '\n').encode())
            except OSError:
                result = 75

    return result


def stop(directory):
    state = State(directory)
    guardian_fd = None
    try:
        record = json.loads(state.read('launch-owner.json'))
        if state.binding() != record['binding']:
            raise Refusal('task owner/runtime changed')
        guardian = record['guardian']
        try:
            guardian_fd = os.pidfd_open(guardian['pid'])
            if not same_birth(birth(guardian['pid']), guardian):
                raise Refusal('guardian PID birth identity changed')
        except ProcessLookupError:
            pass
        # A disk receipt alone is never an authority to remove scratch. Every
        # successful controller needs a fresh authenticated finalization reply.
        if guardian_fd is None or not same_birth(birth(guardian['pid']), guardian):
            raise Refusal('guardian is not live; preserve retained receipts')
        nonce = secrets.token_hex(32)
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(8)
            with owner_directory(state):
                client.connect('owner.sock')
            pid, uid, _ = struct.unpack('3i', client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if pid != guardian['pid'] or uid != os.getuid() or not same_birth(birth(pid), guardian):
                raise Refusal('stop socket belongs to another guardian')
            client.sendall((json.dumps({'stop': record['binding']['owner'], 'nonce': nonce}) + '\n').encode())
            answer = json.loads(read_message(client))
        if answer.get('nonce') != nonce or answer.get('exit') != 0:
            raise Refusal('guardian did not acknowledge completed finalization')
        poll = select.poll(); poll.register(guardian_fd, select.POLLIN)
        if not poll.poll(2000):
            raise Refusal('guardian has not exited after finalization')
        # Read after exit; never reuse a provisional pre-fsync snapshot.
        terminal_bytes = state.read('launch-terminal.json')
        if hashlib.sha256(terminal_bytes).hexdigest() != answer.get('terminal_sha256'):
            raise Refusal('terminal changed after finalization acknowledgement')
        terminal = json.loads(terminal_bytes)
        if terminal['owner'] != record or terminal['children'] or terminal['exit'] != 0:
            raise Refusal('guardian terminal is not a clean requested stop')
        if state.binding() != record['binding']:
            raise Refusal('task ownership changed after stop')
        return 0
    finally:
        if guardian_fd is not None:
            os.close(guardian_fd)
        os.close(state.fd)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['launch', 'stop'])
    parser.add_argument('directory')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        if args.action == 'stop':
            return stop(args.directory)
        command = args.command[1:] if args.command[:1] == ['--'] else args.command
        if not command:
            raise Refusal('missing launch command')
        return launch(args.directory, command)
    except Exception as exc:
        print('headless owner refused: ' + str(exc), file=sys.stderr)
        return 75


if __name__ == '__main__':
    sys.exit(main())
