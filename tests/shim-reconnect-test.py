#!/usr/bin/env python3
"""Exercise the real shim and hyprctl in an isolated --omarchy headless session."""
import json
import os
from pathlib import Path
import socket
import subprocess
import time

runtime = Path(os.environ["XDG_RUNTIME_DIR"])
signature = os.environ["HYPRLAND_INSTANCE_SIGNATURE"]
assert signature.startswith("scottland_")
directory = runtime / "hypr" / signature
lock = directory / "hyprland.lock"
original_pid = int(lock.read_text().splitlines()[0])
try:
    shim_environment = dict(entry.split(b"=", 1) for entry in
                            (Path("/proc") / str(original_pid) / "environ").read_bytes().split(b"\0")
                            if b"=" in entry)
except OSError as error:
    raise AssertionError(
        f"headless run cannot read session shim /proc/{original_pid}/environ: {error}") from error
assert shim_environment.get(b"HYPRLAND_INSTANCE_SIGNATURE") == signature.encode(), \
    "headless run read an environment from the wrong session shim"
assert shim_environment.get(b"WAYLAND_DISPLAY") == os.environ["WAYLAND_DISPLAY"].encode(), \
    "headless run read a shim environment for the wrong display"
log = Path(os.environ["SCOTTLAND_TEST_STATE"]) / "scottland" / "hyprshim.log"
assert os.environ["XDG_STATE_HOME"] == os.environ["SCOTTLAND_TEST_STATE"]
assert log.is_file(), "shim log missing from the isolated state directory"


def request(text):
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(3)
        sock.connect(str(directory / ".socket.sock"))
        sock.sendall(text.encode())
        return sock.recv(65536).decode()


def clients():
    return json.loads(subprocess.check_output(["hyprctl", "-j", "clients"], text=True, timeout=5))


assert isinstance(clients(), list)
events = socket.socket(socket.AF_UNIX)
events.settimeout(8)
events.connect(str(directory / ".socket2.sock"))
assert request("scottland-test-drop-ipc") == "ok"
deadline = time.monotonic() + 5
while True:
    try:
        assert isinstance(clients(), list)
        break
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError, AssertionError):
        if time.monotonic() > deadline:
            raise
        time.sleep(.1)
assert int(lock.read_text().splitlines()[0]) == original_pid, "shim exited after Wayfire IPC closed"

# The event watcher has its own 250 ms reconnect interval; let it resubscribe before a new
# mapping produces the event whose delivery we check.
time.sleep(.4)
app = subprocess.Popen(["foot", "-T", "shim-reconnect-event", "sleep", "5"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    received = b""
    deadline = time.monotonic() + 8
    while b"shim-reconnect-event" not in received and time.monotonic() < deadline:
        try:
            received += events.recv(65536)
        except socket.timeout:
            break
    assert b"openwindow>>" in received and b"shim-reconnect-event" in received, received
finally:
    app.terminate()
    app.wait(timeout=5)
    events.close()
print("PASS shim keeps its Hyprland PID, answers hyprctl and resumes events after both Wayfire IPC clients close")
print("PASS shim log stays under the isolated session state directory")
print("PASS headless run reads the session shim's environment from /proc")
