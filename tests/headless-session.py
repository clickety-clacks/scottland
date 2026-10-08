#!/usr/bin/env python3
"""Ownership checks and narrowly scoped cleanup for tests/headless.sh."""

import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import signal
import stat
import sys
import time


TOKEN_ENV = b"SCOTTLAND_TEST_SESSION_ID="


def fail(message):
    print(f"headless session: {message}", file=sys.stderr)
    raise SystemExit(1)


def safe_owned(path, kind):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    expected = {
        "dir": stat.S_ISDIR,
        "file": stat.S_ISREG,
        "fifo": stat.S_ISFIFO,
        "socket": stat.S_ISSOCK,
    }[kind]
    if not expected(info.st_mode) or info.st_uid != os.getuid():
        fail(f"refusing to touch unowned or unexpected path: {path}")
    return True


def checked_runtime(path):
    path = pathlib.Path(path)
    safe_owned(path, "dir")
    if path.resolve(strict=True) != path:
        fail(f"runtime path is not canonical: {path}")
    if path.stat().st_mode & 0o077:
        fail(f"runtime directory is accessible to other users: {path}")
    return path


def checked_build_dir(build, session_dir):
    build = pathlib.Path(build)
    session_dir = pathlib.Path(session_dir)
    safe_owned(build, "dir")
    if build.resolve(strict=True) != build:
        fail(f"build path is not canonical: {build}")
    safe_owned(session_dir, "dir")
    if session_dir.resolve(strict=True) != session_dir or session_dir.parent != build:
        fail(f"session directory must be a direct child of build/: {session_dir}")
    if session_dir.stat().st_mode & 0o077:
        fail(f"session directory is not private: {session_dir}")
    return session_dir


def read_session(build, session_dir):
    session_dir = checked_build_dir(build, session_dir)
    marker = session_dir / "session-id"
    runtime_file = session_dir / "runtime-path"
    safe_owned(marker, "file")
    safe_owned(runtime_file, "file")
    token = marker.read_text().strip()
    if len(token) != 64 or any(c not in "0123456789abcdef" for c in token):
        fail(f"invalid session ownership marker: {marker}")
    runtime = runtime_file.read_text().strip()
    if not runtime:
        fail(f"missing runtime path marker: {runtime_file}")
    return session_dir, token, runtime


def environment(path):
    return dict(entry.split(b"=", 1) for entry in path.read_bytes().split(b"\0") if b"=" in entry)


def owns_environment(path, token):
    path = pathlib.Path(path)
    if not safe_owned(path, "file"):
        return False
    try:
        values = environment(path)
    except OSError:
        return False
    return values.get(TOKEN_ENV[:-1]) == token.encode()


def record_environment():
    token = os.environ.get("SCOTTLAND_TEST_SESSION_ID", "")
    if not re.fullmatch(r"[0-9a-f]{64}", token):
        fail("test environment record needs a 64-character session token")
    runtime = checked_runtime(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    scottland = runtime / "scottland"
    if scottland.exists() or scottland.is_symlink():
        safe_owned(scottland, "dir")
    else:
        try:
            scottland.mkdir(mode=0o700)
        except FileExistsError:
            pass
        safe_owned(scottland, "dir")

    display = os.environ.get("WAYLAND_DISPLAY", "")
    if not re.fullmatch(r"wayland-[0-9]+", display):
        fail(f"invalid test display name: {display!r}")
    for suffix in (".lua.pid", ".color-scheme.pid", ".widget-bus.pid", ".lua.fifo"):
        path = scottland / f"{display}{suffix}"
        if path.exists() or path.is_symlink():
            fail(f"test runtime path is already occupied: {path}")

    signature = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "")
    if signature:
        if not re.fullmatch(r"scottland_[A-Za-z0-9_.-]+", signature):
            fail("invalid test Hyprland signature")
        hypr_root = runtime / "hypr"
        if hypr_root.exists() or hypr_root.is_symlink():
            safe_owned(hypr_root, "dir")
        directory = hypr_root / signature
        if directory.exists() or directory.is_symlink():
            fail(f"test Hyprland signature path is already occupied: {directory}")

    path = scottland / f"{display}.env"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        fail(f"refusing to replace existing session environment: {path}")
    try:
        data = b"\0".join(key + b"=" + value for key, value in os.environb.items()) + b"\0"
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def capture_runtime(build, session_dir, runtime, token):
    session_dir, actual_token, actual_runtime = read_session(build, session_dir)
    if actual_token != token or actual_runtime != runtime:
        fail("session markers changed before runtime ownership capture")
    runtime = checked_runtime(runtime)
    scottland = runtime / "scottland"
    matches = []
    if scottland.exists() or scottland.is_symlink():
        safe_owned(scottland, "dir")
        for path in scottland.glob("*.env"):
            try:
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                    continue
                values = environment(path)
            except OSError:
                continue
            if values.get(TOKEN_ENV[:-1]) == token.encode():
                matches.append((path, values))
    if len(matches) > 1:
        fail("multiple runtime environment files claim this session token")
    if not matches:
        return

    env_path, values = matches[0]
    display = values.get(b"WAYLAND_DISPLAY", b"").decode(errors="strict")
    signature = values.get(b"HYPRLAND_INSTANCE_SIGNATURE", b"").decode(errors="strict")
    runtime_value = values.get(b"XDG_RUNTIME_DIR", b"").decode(errors="strict")
    if not re.fullmatch(r"wayland-[0-9]+", display):
        fail("owned runtime environment has an invalid display name")
    if env_path.name != f"{display}.env":
        fail("owned runtime environment filename does not match its display")
    if pathlib.Path(runtime_value) != runtime:
        fail("owned runtime environment points at a different runtime directory")
    if (session_dir / "display").exists() and (session_dir / "display").read_text().strip() != display:
        fail("owned runtime display does not match the session directory")
    env_info = env_path.lstat()
    resources = {}
    for suffix, kind in ((".lua.pid", "file"), (".color-scheme.pid", "file"),
                         (".widget-bus.pid", "file"), (".lua.fifo", "fifo")):
        path = scottland / f"{display}{suffix}"
        if safe_owned(path, kind):
            info = path.lstat()
            resource = {"kind": kind, "dev": info.st_dev, "ino": info.st_ino}
            if kind == "file":
                content = path.read_bytes()
                first_line = content.splitlines()[0] if content.splitlines() else b""
                if not first_line.isdigit():
                    fail(f"runtime PID file is malformed: {path}")
                pid = int(first_line)
                if pathlib.Path("/proc", str(pid)).exists() and not process_has_token(pid, token):
                    fail(f"runtime PID file names a process outside this test session: {path}")
                resource["sha256"] = hashlib.sha256(content).hexdigest()
            resources[path.name] = resource

    hypr = None
    if signature:
        if not re.fullmatch(r"scottland_[A-Za-z0-9_.-]+", signature):
            fail("owned runtime environment has an invalid Hyprland signature")
        hypr_root = runtime / "hypr"
        if hypr_root.exists() or hypr_root.is_symlink():
            safe_owned(hypr_root, "dir")
        directory = hypr_root / signature
        if directory.exists() or directory.is_symlink():
            safe_owned(directory, "dir")
            lock = directory / "hyprland.lock"
            if not safe_owned(lock, "file"):
                fail("Hyprland directory has no owned lock file")
            lock_bytes = lock.read_bytes()
            lines = lock_bytes.splitlines()
            if len(lines) < 2 or not lines[0].isdigit() or lines[1].decode(errors="strict") != display:
                fail("Hyprland lock does not match this test session")
            pid = int(lines[0])
            if not process_has_token(pid, token):
                fail("Hyprland lock PID is not owned by this test session")
            try:
                proc_env = environment(pathlib.Path("/proc") / str(pid) / "environ")
                cmdline = (pathlib.Path("/proc") / str(pid) / "cmdline").read_bytes().split(b"\0")
            except OSError:
                fail("could not inspect the test-owned Hyprland shim process")
            if (proc_env.get(b"WAYLAND_DISPLAY") != display.encode() or
                    proc_env.get(b"HYPRLAND_INSTANCE_SIGNATURE") != signature.encode() or
                    not any(arg == b"scottland-hyprshim" or arg.endswith(b"/scottland-hyprshim") for arg in cmdline)):
                fail("Hyprland shim process identity does not match its runtime lock")

            sockets = {}
            for name in (".socket.sock", ".socket2.sock"):
                path = directory / name
                if not safe_owned(path, "socket"):
                    fail(f"Hyprland socket is missing: {path}")
                info = path.lstat()
                sockets[name] = {"dev": info.st_dev, "ino": info.st_ino}
            directory_info, lock_info = directory.lstat(), lock.lstat()
            hypr = {
                "signature": signature,
                "pid": pid,
                "directory": {"dev": directory_info.st_dev, "ino": directory_info.st_ino},
                "lock": {"dev": lock_info.st_dev, "ino": lock_info.st_ino,
                         "sha256": hashlib.sha256(lock_bytes).hexdigest()},
                "sockets": sockets,
            }

    record = {
        "runtime": str(runtime), "token": token, "display": display,
        "env": {"dev": env_info.st_dev, "ino": env_info.st_ino,
                "sha256": hashlib.sha256(env_path.read_bytes()).hexdigest()},
        "resources": resources, "hypr": hypr,
    }
    record_path = session_dir / "runtime-owner.json"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(record_path, flags, 0o600)
    with os.fdopen(fd, "w") as out:
        json.dump(record, out, sort_keys=True)
        out.write("\n")
        out.flush()
        os.fsync(out.fileno())


def process_has_token(pid, token):
    proc = pathlib.Path("/proc") / str(pid)
    try:
        if proc.stat().st_uid != os.getuid():
            return False
        return TOKEN_ENV + token.encode() in (proc / "environ").read_bytes().split(b"\0")
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
        return False


def session_pids(token):
    result = []
    for proc in pathlib.Path("/proc").iterdir():
        if proc.name.isdecimal():
            pid = int(proc.name)
            if pid != os.getpid() and process_has_token(pid, token):
                result.append(pid)
    return result


def signal_session(token, sig):
    for pid in session_pids(token):
        try:
            if hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"):
                fd = os.pidfd_open(pid)
                try:
                    if process_has_token(pid, token):
                        signal.pidfd_send_signal(fd, sig)
                finally:
                    os.close(fd)
            elif process_has_token(pid, token):
                os.kill(pid, sig)
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            pass


def wait_session(token, seconds):
    deadline = time.monotonic() + seconds
    while session_pids(token):
        if time.monotonic() >= deadline:
            fail("session-owned processes did not exit before the cleanup deadline")
        time.sleep(0.1)


def clean_runtime(build, session_dir, runtime, token, expected_display=None):
    runtime = checked_runtime(runtime)
    scottland = runtime / "scottland"
    if not scottland.exists() and not scottland.is_symlink():
        if expected_display:
            fail("shared runtime directory disappeared before cleanup")
        return
    safe_owned(scottland, "dir")

    matches = []
    for path in scottland.glob("*.env"):
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                continue
            values = environment(path)
            if values.get(TOKEN_ENV[:-1]) == token.encode():
                matches.append((path, values))
        except (OSError, ValueError):
            continue
    if len(matches) > 1:
        fail("multiple runtime environment files claim this session token")
    if not matches:
        if expected_display:
            fail("the recorded display environment no longer proves runtime ownership")
        return

    env_path, env = matches[0]
    display = env.get(b"WAYLAND_DISPLAY", b"").decode(errors="strict")
    runtime_value = env.get(b"XDG_RUNTIME_DIR", b"").decode(errors="strict")
    if not display.startswith("wayland-") or not display[8:].isdigit():
        fail("owned runtime environment has an invalid Wayland display name")
    if env_path.name != f"{display}.env":
        fail("owned runtime environment filename does not match its display")
    if expected_display and display != expected_display:
        fail("owned runtime environment does not match the session directory display")
    if pathlib.Path(runtime_value) != runtime:
        fail("owned runtime environment points at a different runtime directory")
    session_dir = pathlib.Path(session_dir)
    _, actual_token, actual_runtime = read_session(build, session_dir)
    if actual_token != token or actual_runtime != str(runtime):
        fail("session markers changed before runtime cleanup")
    record_path = session_dir / "runtime-owner.json"
    safe_owned(record_path, "file")
    record = json.loads(record_path.read_text())
    if record.get("runtime") != str(runtime) or record.get("token") != token or record.get("display") != display:
        fail("runtime ownership record does not match this session")
    env_info = env_path.lstat()
    env_record = record.get("env", {})
    if (env_info.st_dev != env_record.get("dev") or env_info.st_ino != env_record.get("ino") or
            hashlib.sha256(env_path.read_bytes()).hexdigest() != env_record.get("sha256")):
        fail("runtime environment record changed after ownership capture")

    for name, resource in record.get("resources", {}).items():
        expected_names = {f"{display}{suffix}" for suffix in
                          (".lua.pid", ".color-scheme.pid", ".widget-bus.pid", ".lua.fifo")}
        expected_kind = "fifo" if name.endswith(".lua.fifo") else "file"
        if name not in expected_names or resource.get("kind") != expected_kind:
            fail(f"runtime ownership record contains an unexpected path: {name}")
        path = scottland / name
        if not safe_owned(path, resource["kind"]):
            continue
        info = path.lstat()
        if info.st_dev != resource.get("dev") or info.st_ino != resource.get("ino"):
            fail(f"runtime resource identity changed after ownership capture: {path}")
        if resource["kind"] == "file" and hashlib.sha256(path.read_bytes()).hexdigest() != resource.get("sha256"):
            fail(f"runtime resource contents changed after ownership capture: {path}")
        path.unlink()

    hypr = record.get("hypr")
    signature = env.get(b"HYPRLAND_INSTANCE_SIGNATURE", b"").decode(errors="strict")
    if hypr:
        if signature != hypr.get("signature"):
            fail("Hyprland signature changed after ownership capture")
        hypr_root = runtime / "hypr"
        if hypr_root.exists() or hypr_root.is_symlink():
            safe_owned(hypr_root, "dir")
        directory = hypr_root / signature
        if directory.exists() or directory.is_symlink():
            safe_owned(directory, "dir")
            dir_info = directory.lstat()
            if dir_info.st_dev != hypr["directory"].get("dev") or dir_info.st_ino != hypr["directory"].get("ino"):
                fail("Hyprland directory identity changed after ownership capture")
            lock = directory / "hyprland.lock"
            if not safe_owned(lock, "file"):
                fail("owned Hyprland lock disappeared before cleanup")
            lock_info = lock.lstat()
            lock_bytes = lock.read_bytes()
            if (lock_info.st_dev != hypr["lock"].get("dev") or lock_info.st_ino != hypr["lock"].get("ino") or
                    hashlib.sha256(lock_bytes).hexdigest() != hypr["lock"].get("sha256")):
                fail("Hyprland lock identity changed after ownership capture")
            for name, socket_identity in hypr["sockets"].items():
                socket_path = directory / name
                if not (socket_path.exists() or socket_path.is_symlink()):
                    continue
                safe_owned(socket_path, "socket")
                info = socket_path.lstat()
                if info.st_dev != socket_identity.get("dev") or info.st_ino != socket_identity.get("ino"):
                    fail(f"Hyprland socket identity changed after ownership capture: {socket_path}")
                socket_path.unlink()
            lock.unlink()
            try:
                directory.rmdir()
            except OSError as error:
                fail(f"Hyprland directory still contains unowned entries: {error}")
    elif signature:
        hypr_root = runtime / "hypr"
        if hypr_root.exists() or hypr_root.is_symlink():
            safe_owned(hypr_root, "dir")
        directory = hypr_root / signature
        if directory.exists() or directory.is_symlink():
            fail("test runtime contains an uncaptured Hyprland signature")

    env_path.unlink()


def remove_session_dir(build, session_dir, token):
    session_dir, actual_token, _ = read_session(build, session_dir)
    if actual_token != token:
        fail("session token changed before directory cleanup")
    shutil.rmtree(session_dir)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("token")
    sub.add_parser("record-env")
    runtime_parser = sub.add_parser("canonical-runtime")
    runtime_parser.add_argument("runtime")
    verify = sub.add_parser("verify")
    verify.add_argument("build")
    verify.add_argument("session_dir")
    verify.add_argument("--field", choices=("token", "runtime"), default="token")
    signal_parser = sub.add_parser("signal")
    signal_parser.add_argument("token")
    signal_parser.add_argument("signal", choices=("TERM", "KILL"))
    wait_parser = sub.add_parser("wait")
    wait_parser.add_argument("token")
    wait_parser.add_argument("seconds", type=float)
    env_parser = sub.add_parser("owns-env")
    env_parser.add_argument("path")
    env_parser.add_argument("token")
    capture = sub.add_parser("capture-runtime")
    capture.add_argument("build")
    capture.add_argument("session_dir")
    capture.add_argument("runtime")
    capture.add_argument("token")
    clean = sub.add_parser("clean-runtime")
    clean.add_argument("build")
    clean.add_argument("session_dir")
    clean.add_argument("runtime")
    clean.add_argument("token")
    clean.add_argument("expected_display", nargs="?")
    remove = sub.add_parser("remove-dir")
    remove.add_argument("build")
    remove.add_argument("session_dir")
    remove.add_argument("token")

    args = parser.parse_args()
    if args.command == "token":
        print(os.urandom(32).hex())
    elif args.command == "record-env":
        record_environment()
    elif args.command == "canonical-runtime":
        print(checked_runtime(args.runtime))
    elif args.command == "verify":
        _, token, runtime = read_session(args.build, args.session_dir)
        print(token if args.field == "token" else runtime)
    elif args.command == "signal":
        signal_session(args.token, signal.SIGTERM if args.signal == "TERM" else signal.SIGKILL)
    elif args.command == "wait":
        wait_session(args.token, args.seconds)
    elif args.command == "owns-env":
        if not owns_environment(args.path, args.token):
            raise SystemExit(1)
    elif args.command == "capture-runtime":
        capture_runtime(args.build, args.session_dir, args.runtime, args.token)
    elif args.command == "clean-runtime":
        clean_runtime(args.build, args.session_dir, args.runtime, args.token, args.expected_display)
    elif args.command == "remove-dir":
        remove_session_dir(args.build, args.session_dir, args.token)


if __name__ == "__main__":
    main()
