#!/usr/bin/env python3
"""Call Wayfire IPC: tests/wfipc.py METHOD [JSON]. Uses $WAYFIRE_SOCKET, else the newest socket."""
import glob, json, os, socket, struct, sys

path = os.environ.get("WAYFIRE_SOCKET") or max(
    glob.glob(f"{os.environ.get('XDG_RUNTIME_DIR', '/run/user/%d' % os.getuid())}/wayfire-*.socket"),
    key=os.path.getmtime)
sock = socket.socket(socket.AF_UNIX)
sock.connect(path)
body = json.dumps({"method": sys.argv[1], "data": json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}}).encode()
sock.sendall(struct.pack("<I", len(body)) + body)
header = b""
while len(header) < 4:
    header += sock.recv(4 - len(header))
size, reply = struct.unpack("<I", header)[0], b""
while len(reply) < size:
    reply += sock.recv(size - len(reply))
print(reply.decode())
