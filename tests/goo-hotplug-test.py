#!/usr/bin/env python3
"""Exercise goo across output add, reconfiguration and removal in a headless session.

Run with tests/headless.sh run python3 tests/goo-hotplug-test.py. The compositor,
input and screenshots all belong to that session.
"""
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import time

assert os.environ.get("SCOTTLAND_TEST_MODEL") == "1", "private headless session required"
art = Path(__file__).resolve().parents[1] / "build/goo-hotplug-evidence"
art.mkdir(parents=True, exist_ok=True)
sock = socket.socket(socket.AF_UNIX)
sock.settimeout(5)
sock.connect(os.environ["WAYFIRE_SOCKET"])
clients = []
new_output = None


def read(length):
    data = b""
    while len(data) < length:
        part = sock.recv(length - len(data))
        if not part:
            raise RuntimeError("compositor disconnected")
        data += part
    return data


def ipc(method, data=None):
    body = json.dumps({"method": method, "data": data or {}}).encode()
    sock.sendall(struct.pack("<I", len(body)) + body)
    result = json.loads(read(struct.unpack("<I", read(4))[0]))
    if isinstance(result, dict) and "error" in result:
        raise RuntimeError(f"{method}: {result}")
    return result


def wait_for(predicate, label, seconds=10):
    deadline = time.monotonic() + seconds
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(.05)
    raise AssertionError(f"timed out waiting for {label}; last={last!r}")


def view(title):
    return next((v for v in ipc("window-rules/list-views") if v.get("title") == title), None)


def output(output_id):
    return next(o for o in ipc("window-rules/list-outputs") if o["id"] == output_id)


def state(name):
    return next((s for s in ipc("scottland/goo-state")["screens"] if s["output"] == name), None)


def ready(name, sources=0):
    s = state(name)
    return s if s and s["renderer_ready"] and s["sources"] >= sources else None


def launch(title):
    clients.append(subprocess.Popen(["foot", "-c", "/dev/null", "-T", title, "sleep", "120"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    return wait_for(lambda: view(title), f"{title} mapped")


def pointer(x, y):
    ipc("stipc/move_cursor", {"x": round(x), "y": round(y)})


def key(down):
    ipc("stipc/feed_key", {"key": "KEY_LEFTMETA", "state": down})


def button(down):
    ipc("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "press" if down else "release"})


def drag_to(title, target, local_x=None):
    current = view(title)
    origin_output = output(current["output-id"])
    frame = current["bbox"]
    origin = origin_output["geometry"]
    x = origin["x"] + frame["x"] + frame["width"] / 2
    y = origin["y"] + frame["y"] + frame["height"] / 2
    tx = target["geometry"]["x"] + (local_x if local_x is not None else
                                     min(4000, target["geometry"]["width"] - 300))
    pointer(x, y)
    key(True)
    button(True)
    try:
        for step in range(1, 31):
            pointer(x + (tx - x) * step / 30, y)
            time.sleep(.02)
    finally:
        button(False)
        key(False)
    moved = wait_for(lambda: (v if (v := view(title)) and v["output-id"] == target["id"] else None),
                     f"{title} on runtime output")
    print(f"moved {title} to {target['name']}: {moved['geometry']}", flush=True)
    return moved


def place(title, target, x, y, width, height):
    current = view(title)
    ipc("window-rules/configure-view", {"id": current["id"], "geometry":
        {"x": x, "y": y, "width": width, "height": height}})
    wait_for(lambda: (v if (v := view(title)) and v["output-id"] == target["id"] and
                      abs(v["geometry"]["x"] - x) < 5 else None), f"{title} placed after mode change")


def capture(label, titles):
    path = art / f"{label}.png"
    name = new_output["name"] if label != "startup-after-remove" else startup["name"]
    ppm = art / f"{label}.ppm"
    deadline = time.monotonic() + 5
    while True:
        subprocess.run(["grim", "-t", "ppm", "-o", name, str(ppm)], check=True)
        with ppm.open("rb") as image:
            assert image.readline() == b"P6\n"
            width, height = map(int, image.readline().split())
            assert image.readline() == b"255\n"
            pixels = image.read()
        assert len(pixels) == width * height * 3
        background = pixels[:3]
        rings = {}
        for title in titles:
            source = next((r for r in state(name)["source_rects"] if r["id"] == view(title)["id"]), None)
            assert source, f"{label}: no goo source for {title}"
            frame = source
            x0, y0 = int(frame["x"]), int(frame["y"])
            x1, y1 = x0 + int(frame["width"]), y0 + int(frame["height"])
            count = 0
            for y in range(max(0, y0 - 30), min(height, y1 + 30)):
                for x in range(max(0, x0 - 30), min(width, x1 + 30)):
                    if x0 <= x < x1 and y0 <= y < y1:
                        continue
                    pixel = pixels[3 * (y * width + x):3 * (y * width + x + 1)]
                    count += max(abs(pixel[i] - background[i]) for i in range(3)) > 20
            rings[title] = count
        if all(count > 40 for count in rings.values()):
            break
        if time.monotonic() >= deadline:
            raise AssertionError(f"{label}: no rendered goo outside source bounds: {rings}; "
                                 f"sources={state(name)['source_rects']}")
        time.sleep(.05)
    subprocess.run(["grim", "-o", name, str(path)], check=True)
    ppm.unlink()
    s = state(name)
    print(f"{label}: {width}x{height}, sources={s['sources']}, steps={s['steps']}, "
          f"draws={s['draws']}, ready={s['renderer_ready']}, prepares={s['topology_prepares']}, "
          f"edge_pixels={rings}", flush=True)
    return path


try:
    startup = ipc("window-rules/list-outputs")[0]
    wait_for(lambda: ready(startup["name"]), "startup output prepared before a window")
    first = launch("hotplug-first")
    ipc("window-rules/configure-view", {"id": first["id"],
        "geometry": {"x": 250, "y": 220, "width": 360, "height": 220}})
    new_output = ipc("wayfire/create-headless-output", {"width": 5120, "height": 1440})["output"]
    wait_for(lambda: len(ipc("window-rules/list-outputs")) == 2, "runtime output")
    wait_for(lambda: (s if (s := ready(new_output["name"])) and s["sources"] == 0
                      and s["topology_prepares"] >= 1 else None),
             "runtime output prepared while empty")
    print(f"runtime output prepared before any window: {new_output}", flush=True)
    drag_to("hotplug-first", new_output, new_output["geometry"]["width"] / 2)
    wait_for(lambda: (s if (s := ready(new_output["name"], 1)) and s["steps"] > 0
                      and s["draws"] > 0 else None), "first window's goo rendered")
    first_id = view("hotplug-first")["id"]
    def full_size_first():
        source = next((r for r in state(new_output["name"])["source_rects"]
                       if r["id"] == first_id), None)
        center = source["x"] + source["width"] / 2 if source else None
        width = new_output["geometry"]["width"]
        if (source and source["width"] >= 300 and source["height"] >= 180 and
                width * .35 <= center <= width * .65):
            return source
        return None
    first_source = wait_for(full_size_first, "first window settled full-size in center")
    assert view("hotplug-first")["output-id"] == new_output["id"]
    print(f"first window settled in center: {first_source}", flush=True)
    capture("first", ["hotplug-first"])
    assert full_size_first(), "first window left full-size center placement before capture"
    pointer(startup["geometry"]["x"] + 50, startup["geometry"]["y"] + 50)
    second = launch("hotplug-second")
    if second["output-id"] != startup["id"]:
        drag_to("hotplug-second", startup)
    drag_to("hotplug-second", new_output)
    wait_for(lambda: ready(new_output["name"], 2), "later window's goo rendered")
    capture("second", ["hotplug-first", "hotplug-second"])
    before = state(new_output["name"])["topology_prepares"]
    ipc("wayfire/set-config-options", {f"output:{new_output['name']}/mode": "3840x1200@60000"})
    wait_for(lambda: (s if (s := ready(new_output["name"], 1)) and
                      s["simulation_width"] == 3840 and s["simulation_height"] == 1200 and
                      s["topology_prepares"] > before else None), "mode-sized goo")
    place("hotplug-first", new_output, 400, 200, 360, 220)
    place("hotplug-second", new_output, 1150, 350, 550, 300)
    capture("mode", ["hotplug-first"])
    before = state(new_output["name"])["topology_prepares"]
    ipc("wayfire/set-config-options", {f"output:{new_output['name']}/scale": 1.5})
    wait_for(lambda: (s if (s := ready(new_output["name"], 1)) and
                      s["simulation_width"] == 2560 and s["simulation_height"] == 800 and
                      s["topology_prepares"] > before else None), "scale-sized goo")
    capture("scale", ["hotplug-first"])
    before = state(new_output["name"])["topology_prepares"]
    x = output(new_output["id"])["geometry"]["x"]
    ipc("wayfire/set-config-options", {f"output:{new_output['name']}/position": f"{int(x + 80)}, 0"})
    wait_for(lambda: (s if (s := ready(new_output["name"], 1)) and
                      s["topology_prepares"] > before else None), "position change prepared")
    capture("position", ["hotplug-first"])
    ipc("wayfire/destroy-headless-output", {"output-id": new_output["id"]})
    new_output = None
    wait_for(lambda: len(ipc("window-rules/list-outputs")) == 1 and
                      len(ipc("scottland/goo-state")["screens"]) == 1, "removed output torn down")
    wait_for(lambda: ready(startup["name"], 1), "remaining output's goo working")
    capture("startup-after-remove", ["hotplug-first"])
    print("PASS topology goo acceptance", flush=True)
finally:
    for client in clients:
        if client.poll() is None:
            client.terminate()
            client.wait(timeout=5)
    if new_output:
        ipc("wayfire/destroy-headless-output", {"output-id": new_output["id"]})
    sock.close()
