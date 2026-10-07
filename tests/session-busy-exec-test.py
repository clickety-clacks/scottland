#!/usr/bin/env python3
"""E5: scottland-exec runs commands in a session whose compositor is busy, and refuses one that is gone.

For a moment after a window maps, Wayfire can stop accepting IPC connections (about 0.6 s on the
aarch64 test machine) and its small accept queue fills. scottland-exec's liveness probe read that
as "no running Scottland session", so clients launched back to back failed to start.

1. Back to back after the first map: launch one client and, the moment it maps, six more through
   tests/headless.sh run (scottland-exec) without waiting between them.
2. A stalled compositor: SIGSTOP the session's Wayfire (fault injection standing in for a busy main
   loop; no input layer is bypassed, the launches take the normal scottland-exec path), launch
   eight clients, wait until each launch has either started its client or exited, then SIGCONT.
   Oracle for both: every client maps in the compositor (window-rules/list-views), not anything
   scottland-exec says about itself.
3. Gone sessions, in a scratch runtime directory: one whose socket file has no listener (what a
   dead compositor leaves) and one whose socket file is missing. scottland-exec --display exits
   with "no running Scottland session" and --list lists neither. The timeout is a hang deadline.

  tests/session-busy-exec-test.sh   (starts and stops the headless session around this script)
"""
import json
import os
import shutil
import signal
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

repo = Path(__file__).resolve().parent.parent
headless = str(repo / "tests/headless.sh")
exec_tool = str(repo / "build/hooks/libexec/scottland-exec")
session_dir = Path(os.environ["SCOTTLAND_HEADLESS_DIR"])
artifacts = Path(sys.argv[1])
runtime = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
display = (session_dir / "display").read_text().strip()
passed = failed = 0
launched = {}  # app-id -> (launcher process, its log file)


def check(ok, name, detail=None):
    global passed, failed
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok or detail is None else f"  ({detail})"), flush=True)
    passed += bool(ok)
    failed += not ok
    return ok


def recorded_socket():
    for entry in (runtime / "scottland" / f"{display}.env").read_bytes().split(b"\0"):
        if entry.startswith(b"WAYFIRE_SOCKET="):
            return entry.split(b"=", 1)[1].decode()
    raise RuntimeError(f"{display}.env records no WAYFIRE_SOCKET")


wayfire_socket = recorded_socket()


def ipc(method, data=None):
    with socket.socket(socket.AF_UNIX) as sock:
        # A blocking connect waits while the accept queue is full instead of failing; the socket
        # timeouts are a hang deadline only.
        deadline = struct.pack("ll", 10, 0)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDTIMEO, deadline)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVTIMEO, deadline)
        sock.connect(wayfire_socket)

        def receive(n):
            data = b""
            while len(data) < n:
                chunk = sock.recv(n - len(data))
                if not chunk:
                    raise ConnectionError("compositor disconnected")
                data += chunk
            return data
        body = json.dumps({"method": method, "data": data or {}}).encode()
        sock.sendall(struct.pack("<I", len(body)) + body)
        return json.loads(receive(struct.unpack("<I", receive(4))[0]))


def mapped():
    reply = ipc("window-rules/list-views")
    return {v.get("app-id") for v in reply if v.get("mapped")} if isinstance(reply, list) else set()


def wait_for(predicate, timeout):
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        last = predicate()
        if last:
            return True, last
        time.sleep(0.05)
    return False, last


def launch(app_id):
    log = open(artifacts / f"{app_id}.log", "wb")
    process = subprocess.Popen([headless, "run", "foot", "-c", "/dev/null", "-a", app_id, "-T", app_id,
                                "sleep", "600"], stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    launched[app_id] = (process, log)


def exited(app_id):
    process, _ = launched[app_id]
    return process.poll() is not None


def refusals(app_ids):
    """Launches that ended, with what they printed (a launched client runs until the test stops it)."""
    out = {}
    for app_id in app_ids:
        if exited(app_id):
            out[app_id] = (artifacts / f"{app_id}.log").read_text(errors="replace").strip()[-200:]
    return out


def all_mapped_or_ended(app_ids):
    seen = mapped()
    return all(a in seen or exited(a) for a in app_ids) and seen


def started_client(app_id):
    """The launch is past scottland-exec: it has exec'd its client, or it has exited."""
    process, _ = launched[app_id]
    if process.poll() is not None:
        return True
    try:
        return os.path.basename(os.readlink(f"/proc/{process.pid}/exe")) == "foot"
    except OSError:
        return process.poll() is not None


def compositor_pid():
    pid = int((session_dir / "compositor.pid").read_text())
    args = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    if str(session_dir / "wayfire.ini").encode() not in args or not any(a.endswith(b"wayfire") for a in args):
        raise RuntimeError(f"compositor.pid {pid} is not this session's Wayfire")
    return pid


def back_to_back_after_first_map():
    launch("org.scottland.BusyFirst")
    ok, seen = wait_for(lambda: "org.scottland.BusyFirst" in mapped(), 30)
    if not check(ok, "setup: the first client maps", seen):
        return
    burst = [f"org.scottland.BusyBurst{i}" for i in range(6)]
    for app_id in burst:
        launch(app_id)
    _, seen = wait_for(lambda: all_mapped_or_ended(burst), 30)
    missing = sorted(set(burst) - (seen or set()))
    check(not missing, "six clients launched back to back right after the first map all map",
          {"missing": missing, "ended": refusals(missing)})


def stalled_compositor():
    pid = compositor_pid()
    stall = [f"org.scottland.BusyStall{i}" for i in range(8)]
    os.kill(pid, signal.SIGSTOP)
    try:
        for app_id in stall:
            launch(app_id)
        # No IPC while stopped: each call would take a queue slot and wait out its deadline.
        ok, _ = wait_for(lambda: all(started_client(a) for a in stall), 30)
        check(ok, "setup: every launch got past scottland-exec while the compositor was stopped",
              [a for a in stall if not started_client(a)])
    finally:
        os.kill(pid, signal.SIGCONT)
    _, seen = wait_for(lambda: all_mapped_or_ended(stall), 30)
    missing = sorted(set(stall) - (seen or set()))
    check(not missing, "eight clients launched while the compositor was stalled all map",
          {"missing": missing, "ended": refusals(missing)})


def gone_sessions():
    scratch = artifacts / "rt"
    shutil.rmtree(scratch, ignore_errors=True)
    (scratch / "scottland").mkdir(parents=True)
    try:
        stale, missing = scratch / "s", scratch / "m"
        with socket.socket(socket.AF_UNIX) as sock:
            sock.bind(str(stale))  # the file stays after close, with nobody listening
        for name, path in (("wayland-stale", stale), ("wayland-missing", missing)):
            (scratch / "scottland" / f"{name}.env").write_bytes(f"WAYFIRE_SOCKET={path}\0".encode())
        env = dict(os.environ, XDG_RUNTIME_DIR=str(scratch))
        for name, what in (("wayland-stale", "has no listener"), ("wayland-missing", "is missing")):
            started = time.monotonic()
            try:
                result = subprocess.run([exec_tool, "--display", name, "--", "true"], env=env,
                                        capture_output=True, text=True, timeout=10)
                outcome = (result.returncode, result.stderr.strip())
            except subprocess.TimeoutExpired:
                outcome = ("hung", "")
            check(outcome[0] not in (0, "hung") and f"no running Scottland session on {name}" in outcome[1],
                  f"a session whose socket {what} is refused", outcome)
            print(f"      ({name}: {time.monotonic() - started:.2f} s)", flush=True)
        try:
            result = subprocess.run([exec_tool, "--list"], env=env, capture_output=True, text=True, timeout=10)
            outcome = (result.returncode, result.stdout)
        except subprocess.TimeoutExpired:
            outcome = ("hung", "")
        check(outcome == (0, ""), "--list lists neither gone session", outcome)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def stop_clients():
    for process, log in launched.values():
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        log.close()


signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
try:
    back_to_back_after_first_map()
    stalled_compositor()
    gone_sessions()
finally:
    stop_clients()
print(f"{passed} passed, {failed} failed (named scenario checks, including setup)")
sys.exit(1 if failed else 0)
