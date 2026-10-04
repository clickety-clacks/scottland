#!/usr/bin/env python3
"""A16: verify live text-scale changes reach Wayfire and app launch settings."""
import json
import os
from pathlib import Path
import signal
import socket
import struct
import subprocess
import sys
import time

root = Path(__file__).resolve().parents[1]
helper = root / "core/libexec/scottland-color-scheme"
exec_helper = root / "core/libexec/scottland-exec"
passed = failed = 0
sock = socket.socket(socket.AF_UNIX)
sock.connect(os.environ["WAYFIRE_SOCKET"])
watcher = None


def ipc(method, data=None):
    body = json.dumps({"method": method, "data": data or {}}).encode()
    sock.sendall(struct.pack("<I", len(body)) + body)
    header = sock.recv(4)
    if len(header) != 4:
        raise RuntimeError("Wayfire IPC closed before replying")
    size = struct.unpack("<I", header)[0]
    reply = b""
    while len(reply) < size:
        reply += sock.recv(size - len(reply))
    value = json.loads(reply)
    if isinstance(value, dict) and "error" in value:
        raise RuntimeError(value["error"])
    return value


def check(name, okay, detail=None):
    global passed, failed
    okay = bool(okay)
    print(("PASS " if okay else "FAIL ") + name, detail if detail is not None else "", flush=True)
    passed += okay
    failed += not okay


def settings(key):
    return subprocess.check_output(["gsettings", "get", "org.gnome.desktop.interface", key], text=True).strip()


def set_scale(value):
    subprocess.run(["gsettings", "set", "org.gnome.desktop.interface", "text-scaling-factor", str(value)],
                   check=True, capture_output=True, text=True)


def option(name):
    value = ipc("wayfire/get-config-option", {"option": name}).get("value")
    return int(value) if value is not None else None


def recorded_size():
    path = Path(os.environ["XDG_RUNTIME_DIR"]) / "scottland" / (os.environ["WAYLAND_DISPLAY"] + ".env")
    entries = path.read_bytes().split(b"\0")
    value = next(entry.split(b"=", 1)[1].decode() for entry in entries if entry.startswith(b"XCURSOR_SIZE="))
    return int(value)


def wait_until(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return True
        except (OSError, ValueError, RuntimeError, StopIteration):
            pass
        time.sleep(.1)
    return False


try:
    set_scale(1.0)
    subprocess.run(["gsettings", "set", "org.gnome.desktop.interface", "cursor-theme", "default"],
                   check=True, capture_output=True, text=True)
    watcher = subprocess.Popen([str(helper), "watch"], env=os.environ.copy(), start_new_session=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    check("A16 initial 24 px size is applied to the compositor", wait_until(lambda: option("input/cursor_size") == 24))
    check("A16 initial size is applied to GTK", wait_until(lambda: settings("cursor-size").split()[-1] == "24"))
    check("A16 initial size is recorded for new session launches", wait_until(lambda: recorded_size() == 24))
    time.sleep(0.5)  # let the gsettings monitor subscribe before changing the factor

    # Drive an actual Wayfire input event through stipc while the isolated session is running.
    ipc("stipc/move_cursor", {"x": 40, "y": 40})
    set_scale(1.6364)
    live = wait_until(lambda: option("input/cursor_size") == 48 and recorded_size() == 48
                      and settings("cursor-size").split()[-1] == "48")
    check("A16 text scale change updates the compositor and apps live", live,
          {"wayfire": option("input/cursor_size"), "gsettings": settings("cursor-size"),
           "XCURSOR_SIZE": recorded_size()})
    child_size = subprocess.check_output([str(exec_helper), "--", "python3", "-c",
                                          "import os; print(os.environ.get('XCURSOR_SIZE'))"], text=True).strip()
    check("new scottland-exec launches inherit the updated XCURSOR_SIZE", child_size == "48", child_size)
finally:
    if watcher and watcher.poll() is None:
        try:
            os.killpg(watcher.pid, signal.SIGTERM)
            watcher.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            os.killpg(watcher.pid, signal.SIGKILL)
            watcher.wait(timeout=3)
    sock.close()

print(f"{passed} passed, {failed} failed")
sys.exit(bool(failed))
