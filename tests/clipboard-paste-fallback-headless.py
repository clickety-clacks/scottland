#!/usr/bin/env python3
"""Exercise clipboard pasting into focused GTK and Foot targets in a private session."""

import json
import os
from pathlib import Path
import signal
import socket
import struct
import subprocess
import sys
import time


REPO = Path(__file__).resolve().parents[1]
MODE = sys.argv[1]
ART = Path(sys.argv[2]).resolve()
APP = Path(sys.argv[3]).resolve()
CLIPBOARD = REPO / "core/libexec/scottland-clipboard"
EMOJI = REPO / "core/libexec/scottland-emoji"
FOOT_TARGET = REPO / "tests/clipboard-paste-terminal-target.py"
SERVICE = REPO / "build/scottland-clipboardd"
HOOKS = REPO / "build/hooks"
ENV = os.environ.copy()
ENV["SCOTTLAND_HOOKS"] = str(HOOKS)
ENV["PATH"] = f"{ART / 'test-bin'}:{HOOKS / 'libexec'}:{ENV.get('PATH', '/usr/bin:/bin')}"
RESULTS = []
CHILDREN = []


def check(name, passed, detail=""):
    RESULTS.append({"name": name, "passed": bool(passed), "detail": detail})
    print(("PASS " if passed else "FAIL ") + name + (f" [{detail}]" if detail else ""), flush=True)
    if not passed:
        raise AssertionError(name + (f": {detail}" if detail else ""))


def ipc(method, data=None):
    path = os.environ.get("WAYFIRE_SOCKET")
    if not path:
        raise RuntimeError("the private compositor session has no IPC socket")
    body = json.dumps({"method": method, "data": data or {}}).encode()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(3)
        connection.connect(path)
        connection.sendall(struct.pack("<I", len(body)) + body)
        size = struct.unpack("<I", read_exact(connection, 4))[0]
        result = json.loads(read_exact(connection, size))
    if isinstance(result, dict) and "error" in result:
        raise RuntimeError(f"Wayfire IPC {method}: {result['error']}")
    return result


def read_exact(connection, size):
    data = bytearray()
    while len(data) < size:
        chunk = connection.recv(size - len(data))
        if not chunk:
            raise OSError("the compositor closed its IPC connection")
        data.extend(chunk)
    return bytes(data)


def focused_app(app_id):
    snapshot = ipc("scottland/desktop-model", {"slice": "desktop"})
    matches = [window for window in snapshot.get("windows", [])
               if window.get("app_id") == app_id and window.get("focused")]
    return matches[0] if len(matches) == 1 else None


def verify_process(process, identity):
    if process.poll() is not None:
        return False
    try:
        return os.getpgid(process.pid) == process.pid and \
            identity in Path(f"/proc/{process.pid}/cmdline").read_bytes().decode(errors="replace")
    except (OSError, ProcessLookupError):
        return False


def stop_owned(process, identity):
    if process.poll() is not None:
        process.wait()
        return
    if not verify_process(process, identity):
        raise RuntimeError(f"refusing to signal an unverified test process {process.pid}")
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=4)
    except subprocess.TimeoutExpired:
        if not verify_process(process, identity):
            raise RuntimeError(f"refusing to force-stop a changed test process {process.pid}")
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=3)


def launch(label, argv, identity):
    stdout = (ART / f"{label}.stdout").open("wb")
    stderr = (ART / f"{label}.stderr").open("wb")
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                               env=ENV, start_new_session=True)
    CHILDREN.append((process, identity, stdout, stderr))
    return process


def wait_for_file(path, label, process=None, identity=None, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            return
        if process is not None and process.poll() is not None:
            raise RuntimeError(f"{label} exited with {process.returncode} before {path.name}")
        if process is not None and not verify_process(process, identity):
            raise RuntimeError(f"{label} process identity changed before {path.name}")
        time.sleep(0.05)
    raise TimeoutError(f"{label} did not produce {path.name}")


def service_call(operation):
    socket_path = Path(os.environ["XDG_RUNTIME_DIR"]) / "scottland" / \
        f"clipboard-{Path(os.environ.get('WAYLAND_DISPLAY', 'wayland-0')).name}" / "service.sock"
    request = bytes([operation])
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(2)
        connection.connect(str(socket_path))
        connection.sendall(struct.pack("!I", len(request)) + request)
        size = struct.unpack("!I", read_exact(connection, 4))[0]
        response = read_exact(connection, size)
    if not response:
        raise OSError("the private clipboard service returned an empty reply")
    if response[0]:
        raise RuntimeError(response[1:].decode("utf-8", "replace"))
    return response[1:]


def wait_for_service(process, identity, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"private clipboard service exited with {process.returncode}")
        if not verify_process(process, identity):
            raise RuntimeError("private clipboard service process identity changed")
        try:
            service_call(1)
            return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError("private clipboard service socket did not answer ping")


def wait_for_focus(app_id, process, identity, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"{app_id} exited before receiving focus")
        if focused_app(app_id):
            return
        time.sleep(0.05)
    raise TimeoutError(f"{app_id} did not become the focused app")


def run_paste(label, argv, payload, expected):
    result = subprocess.run(argv, input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=ENV, timeout=12, check=False)
    check(label + " paste command", result.returncode == 0,
          f"status={result.returncode} stdout-bytes={len(result.stdout)} "
          f"stderr={result.stderr.decode('utf-8', 'replace').strip()[:180]}")
    result_path = expected[0]
    expected_bytes = expected[1]
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if result_path.is_file():
            observed = result_path.read_bytes()
            if observed == expected_bytes:
                check(label + " observed pasted bytes", True,
                      f"bytes={len(observed)} mode={MODE}")
                return
            if len(observed) >= len(expected_bytes):
                break
        time.sleep(0.05)
    observed = result_path.read_bytes() if result_path.exists() else b""
    check(label + " observed pasted bytes", False,
          f"expected={expected_bytes!r} observed={observed!r} mode={MODE}")


def gtk_case(label, app_id, payload, expected, command):
    ready = ART / f"{label}.ready"
    result = ART / f"{label}.result"
    process = launch(label, [str(APP), app_id, str(ready), str(result), expected.decode("utf-8")],
                     str(APP))
    try:
        wait_for_file(ready, label, process, str(APP))
        wait_for_focus(app_id, process, str(APP))
        run_paste(label, command, payload, (result, expected))
    finally:
        stop_owned(process, str(APP))


def foot_case(label, payload):
    ready = ART / f"{label}.ready"
    result = ART / f"{label}.result"
    process = launch(label, ["foot", "--app-id", "foot", "--title", "Paste target", "-W",
                             "48x8", "python3", "-u", str(FOOT_TARGET), str(ready), str(result)],
                     "foot")
    try:
        wait_for_file(ready, label, process, "foot")
        wait_for_focus("foot", process, "foot")
        run_paste(label, [str(CLIPBOARD), "copy-text"], payload,
                  (result, payload))
    finally:
        stop_owned(process, "foot")


def main():
    if MODE not in ("default", "fallback"):
        raise ValueError("mode must be default or fallback")
    service = launch("clipboard-service", [str(SERVICE)], str(SERVICE))
    wait_for_service(service, str(SERVICE))
    check("private clipboard service responds", True, f"pid={service.pid}")

    flag = os.environ.get("SCOTTLAND_TEST_DISABLE_PRIMARY_SELECTION") == "1"
    check("test session selects the requested paste route", flag == (MODE == "fallback"),
          f"mode={MODE} forced-fallback={flag}")

    gtk_case("gtk-text", "org.scottland.test.pastetext",
             b"gtk-text-paste-proof", b"gtk-text-paste-proof",
             [str(CLIPBOARD), "copy-text"])
    gtk_case("gtk-emoji", "org.scottland.test.pasteemoji",
             b"unused-clipboard", "😀".encode("utf-8"), [str(EMOJI)])
    foot_case("foot-text", b"foot-paste-proof\n")

    (ART / f"{MODE}-results.json").write_text(json.dumps(RESULTS, indent=2) + "\n",
                                               encoding="utf-8")
    check("all paste scenarios passed", all(row["passed"] for row in RESULTS),
          f"assertions={len(RESULTS)} mode={MODE}")


try:
    main()
finally:
    for process, identity, stdout, stderr in reversed(CHILDREN):
        try:
            stop_owned(process, identity)
        finally:
            stdout.close()
            stderr.close()
