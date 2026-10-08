#!/usr/bin/env python3
"""Bounded compositor behavior checks in the fast-check private session."""

import json
import os
import socket
import struct
import subprocess
import time


sock = socket.socket(socket.AF_UNIX)
sock.settimeout(5)
sock.connect(os.environ["WAYFIRE_SOCKET"])


def ipc(method, data=None):
    request = json.dumps({"method": method, "data": data or {}}).encode()
    sock.sendall(struct.pack("<I", len(request)) + request)

    def read(count):
        result = bytearray()
        while len(result) < count:
            chunk = sock.recv(count - len(result))
            if not chunk:
                raise RuntimeError("compositor disconnected")
            result.extend(chunk)
        return bytes(result)

    reply = json.loads(read(struct.unpack("<I", read(4))[0]))
    if isinstance(reply, dict) and "error" in reply:
        raise RuntimeError(f"{method}: {reply['error']}")
    return reply


def wait_for(description, predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {description}")


outputs = ipc("window-rules/list-outputs")
assert isinstance(outputs, list) and outputs, "no headless output"
print("PASS output IPC", flush=True)

title = f"scottland-ci-{os.getpid()}"
client = subprocess.Popen(
    ["foot", "-T", title, "sh", "-c", "sleep 30"],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
try:
    def view():
        return next((v for v in ipc("window-rules/list-views")
                     if v.get("title") == title), None)

    item = wait_for("mapped test window", view)
    identifier = item["id"]
    print("PASS window mapping", flush=True)

    ipc("window-rules/focus-view", {"id": identifier})
    wait_for("focused test window", lambda: ipc("window-rules/get-focused-view")
             .get("info", {}).get("id") == identifier)
    print("PASS focus IPC", flush=True)

    target = {"x": 180, "y": 120, "width": 640, "height": 420}
    ipc("window-rules/configure-view", {"id": identifier, "geometry": target})
    wait_for("configured test geometry", lambda: all(
        (view() or {}).get("geometry", {}).get(key) == value
        for key, value in target.items()))
    print("PASS configure-view geometry", flush=True)
finally:
    client.terminate()
    client.wait(timeout=5)
    sock.close()
