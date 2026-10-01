#!/usr/bin/env python3
"""Observe a forking widget launcher's exit through a full model subscription."""
import json
import os
import socket
import struct

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
while True:
    snapshot = receive()
    entry = next(w for w in snapshot["widgets"] if w["widget_unit"] == unit)
    if entry["launcher_pid"] == 0:
        assert snapshot["version"] > initial["version"]
        assert entry["widget_pid"] > 0 and entry["lifecycle"] == "docked"
        break
sock.close()
