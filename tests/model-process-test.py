#!/usr/bin/env python3
"""Observe a forking widget launcher's exit through a full model subscription."""
import sys as _sys; _sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from session_reload import reload_session
import json
import os
import socket
import struct
import sys
import shutil
import tempfile
import time
from pathlib import Path

sock = socket.socket(socket.AF_UNIX)
sock.settimeout(8)
sock.connect(os.environ["WAYFIRE_SOCKET"])


def exactly(count):
    result = b""
    while len(result) < count:
        chunk = sock.recv(count - len(result))
        if not chunk:
            raise ConnectionError("compositor disconnected")
        result += chunk
    return result


def receive():
    return json.loads(exactly(struct.unpack("<I", exactly(4))[0]))


body = json.dumps({"method": "scottland/subscribe", "data": {"slice": "widgets"}}).encode()
sock.sendall(struct.pack("<I", len(body)) + body)
initial = receive()
assert len(initial["widgets"]) == 1
unit = initial["widgets"][0]["widget_unit"]
assert initial["widgets"][0]["launcher_pid"] > 0, "fixture launcher exited before subscription"
if "--reload" in sys.argv:
    # Keep the subscription connected while replacing the plugin under a forked widget.
    def call(method, data):
        with socket.socket(socket.AF_UNIX) as request:
            request.connect(os.environ["WAYFIRE_SOCKET"])
            body = json.dumps({"method": method, "data": data}).encode()
            request.sendall(struct.pack("<I", len(body)) + body)
            size = struct.unpack("<I", request.recv(4))[0]
            result = b""
            while len(result) < size: result += request.recv(size-len(result))
            return json.loads(result)
    with tempfile.TemporaryDirectory() as directory:
        reload_session()
        assert call("scottland/desktop-model", {})["widgets"][0]["widget_unit"] == unit
        # Library is now mapped; unlinking its file cannot affect the loaded plugin.
while True:
    snapshot = receive()
    entry = next(w for w in snapshot["widgets"] if w["widget_unit"] == unit)
    if entry["launcher_pid"] == 0:
        assert snapshot["version"] > initial["version"]
        assert entry["widget_pid"] > 0 and entry["lifecycle"] == "docked"
        break
sock.close()
