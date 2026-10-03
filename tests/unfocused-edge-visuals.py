#!/usr/bin/env python3
"""Capture the live goo at four strengths under both shipped color schemes.
Run inside a fresh tests/headless.sh session; screenshots stay in build/ on the test host.
"""
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time

art = Path(sys.argv[1])
render = sys.argv[2] if len(sys.argv) > 2 else "goo"
if render not in ("goo", "halo"):
    raise ValueError("render must be 'goo' or 'halo'")
legacy = len(sys.argv) > 3 and sys.argv[3] == "legacy"
art.mkdir(parents=True, exist_ok=True)
sock = socket.socket(socket.AF_UNIX)
sock.connect(os.environ["WAYFIRE_SOCKET"])
clients = []


def ipc(method, data=None):
    body = json.dumps({"method": method, "data": data or {}}).encode()
    sock.sendall(struct.pack("<I", len(body)) + body)

    def read(n):
        value = b""
        while len(value) < n:
            part = sock.recv(n - len(value))
            if not part:
                raise RuntimeError("Wayfire IPC disconnected")
            value += part
        return value

    result = json.loads(read(struct.unpack("<I", read(4))[0]))
    if isinstance(result, dict) and "error" in result:
        raise RuntimeError(f"{method}: {result}")
    return result


def options(**values):
    ipc("wayfire/set-config-options", {"scottland/" + key: value for key, value in values.items()})


def views():
    return ipc("scottland/layout-state")["views"]


def place(title, x, y):
    view = next(view for view in views() if view["title"] == title)
    ipc("window-rules/configure-view", {"id": view["id"],
        "geometry": {"x": x, "y": y, "width": 330, "height": 220}})


try:
    settings = dict(center_width=90, rail_width=0, min_scale=1, max_scale=1,
                    scale_curve="0:1 1:1", goo_noise=0, goo_drift=0,
                    goo_wave_height=0, goo_swirl=0, goo_release=1, goo=(render == "goo"))
    if not legacy:
        settings.update(unfocused_edge_tone_light=.08, unfocused_edge_tone_dark=.92)
    options(**settings)
    for title in ("edge-sample-a", "edge-sample-b"):
        client = subprocess.Popen(["foot", "-c", "/dev/null", "-T", title, "sleep", "600"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        clients.append(client)
        for _ in range(100):
            if any(view["title"] == title for view in views()):
                break
            time.sleep(.05)
        else:
            raise RuntimeError(f"fixture window did not map: {title}")
        time.sleep(.2)
    place("edge-sample-a", 280, 245)
    place("edge-sample-b", 610, 245)
    ipc("stipc/move_cursor", {"x": 20, "y": 20})
    strengths = (1,) if legacy else (0, .25, .5, 1)
    for scheme in ("light", "dark"):
        options(color_scheme=scheme)
        time.sleep(.5)
        for strength in strengths:
            if not legacy:
                options(unfocused_edge_strength=strength)
            time.sleep(.5)
            name = f"{'legacy-' if legacy else ''}{render}-{scheme}-{strength:g}"
            subprocess.run(["grim", str(art / f"{name}.png")], check=True)
            (art / f"{name}.json").write_text(json.dumps({"render": render, "scheme": scheme,
                "strength": strength, "views": views()}, indent=2))
            print(f"saved {name}.png", flush=True)
finally:
    for client in clients:
        if client.poll() is None:
            client.terminate()
            try:
                client.wait(timeout=2)
            except subprocess.TimeoutExpired:
                client.kill()
                client.wait()
    sock.close()
