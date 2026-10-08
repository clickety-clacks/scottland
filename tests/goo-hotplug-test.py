#!/usr/bin/env python3
"""Exercise goo across output add, reconfiguration and removal in a headless session.

Run with tests/headless.sh run python3 tests/goo-hotplug-test.py. Pass
--ghostty-plain-first on a runner with Ghostty to grab its halo without Super.
The compositor, input and screenshots all belong to that session.
"""
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time

assert os.environ.get("SCOTTLAND_TEST_MODEL") == "1", "private headless session required"
ghostty_first = sys.argv[1:] == ["--ghostty-plain-first"]
assert not sys.argv[1:] or ghostty_first, "unknown test arguments"
variant = "ghostty-plain" if ghostty_first else "foot-super"
art = Path(__file__).resolve().parents[1] / "build/goo-hotplug-evidence" / variant
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
    command = (["ghostty", "--config-file=/dev/null", "--window-decoration=true",
                f"--title={title}", "-e", "sleep", "120"] if ghostty_first and
               title == "hotplug-first" else
               ["foot", "-c", "/dev/null", "-T", title, "sleep", "120"])
    clients.append(subprocess.Popen(command,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    return wait_for(lambda: (v if (v := view(title)) and v["mapped"] else None),
                    f"{title} mapped")


def pointer(x, y):
    ipc("stipc/move_cursor", {"x": round(x), "y": round(y)})


def key(down):
    ipc("stipc/feed_key", {"key": "KEY_LEFTMETA", "state": down})


def button(down):
    ipc("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "press" if down else "release"})


def drag_to(title, target, local_x=None, plain=False):
    current = view(title)
    origin_output = output(current["output-id"])
    frame = current["geometry"]
    origin = origin_output["geometry"]
    if plain:
        # The map animation can offset the visible halo from the view's geometry.
        def halo_point():
            shown = next(v for v in ipc("scottland/layout-state")["views"]
                         if v["id"] == current["id"])
            drawn = shown.get("scene_frame", shown["frame"])
            point = (origin["x"] + drawn["x"] - 6,
                     origin["y"] + drawn["y"] + drawn["height"] / 2)
            pointer(*point)
            hovered = next(v for v in ipc("scottland/layout-state")["views"]
                           if v["id"] == current["id"])["frame"]["hovered"]
            return point if hovered == "halo" else None
        x, y = wait_for(halo_point, f"{title} halo handle under pointer")
    else:
        x = origin["x"] + frame["x"] + frame["width"] / 2
        y = origin["y"] + frame["y"] + frame["height"] / 2
    tx = target["geometry"]["x"] + (local_x if local_x is not None else
                                     min(4000, target["geometry"]["width"] - 300))
    pointer(x, y)
    if not plain:
        key(True)
    button(True)
    try:
        for step in range(1, 31):
            pointer(x + (tx - x) * step / 30, y)
            time.sleep(.02)
        wait_for(lambda: ipc("scottland/test-input")["dragging"],
                 f"{title} compositor drag active", seconds=2)
        # A held, deliberate drop avoids turning this placement check into an inertial fling.
        time.sleep(.15)
    finally:
        button(False)
        if not plain:
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
        snapshot = state(name)
        scale_x = width / snapshot["simulation_width"]
        scale_y = height / snapshot["simulation_height"]
        rings = {}
        for title in titles:
            source = next((r for r in snapshot["source_rects"] if r["id"] == view(title)["id"]), None)
            assert source, f"{label}: no goo source for {title}"
            frame = source
            x0, y0 = round(frame["x"] * scale_x), round(frame["y"] * scale_y)
            x1 = round((frame["x"] + frame["width"]) * scale_x)
            y1 = round((frame["y"] + frame["height"]) * scale_y)
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
    if ghostty_first:
        expected_size = first["base-geometry"]
    else:
        ipc("window-rules/configure-view", {"id": first["id"],
            "geometry": {"x": 250, "y": 220, "width": 360, "height": 220}})
        wait_for(lambda: (v if (v := view("hotplug-first")) and
                          abs(v["geometry"]["x"] - 250) < 5 and
                          abs(v["geometry"]["y"] - 220) < 5 and
                          v["geometry"]["width"] >= 300 else None),
                 "first window configured before dragging")
        expected_size = {"width": 360, "height": 220}
    new_output = ipc("wayfire/create-headless-output", {"width": 5120, "height": 1440})["output"]
    wait_for(lambda: len(ipc("window-rules/list-outputs")) == 2, "runtime output")
    wait_for(lambda: (s if (s := ready(new_output["name"])) and s["sources"] == 0
                      and s["topology_prepares"] >= 1 else None),
             "runtime output prepared while empty")
    print(f"runtime output prepared before any window: {new_output}", flush=True)
    drag_to("hotplug-first", new_output, new_output["geometry"]["width"] / 2,
            plain=ghostty_first)
    wait_for(lambda: (s if (s := ready(new_output["name"], 1)) and s["steps"] > 0
                      and s["draws"] > 0 else None), "first window's goo rendered")
    first_id = view("hotplug-first")["id"]
    def full_size_first():
        source = next((r for r in state(new_output["name"])["source_rects"]
                       if r["id"] == first_id), None)
        center = source["x"] + source["width"] / 2 if source else None
        width = new_output["geometry"]["width"]
        if (source and source["width"] >= expected_size["width"] * .85 and
                source["height"] >= expected_size["height"] * .85 and
                width * .35 <= center <= width * .65):
            return source
        return None
    first_source = wait_for(full_size_first, "first window settled full-size in center")
    assert view("hotplug-first")["output-id"] == new_output["id"]
    print(f"first window settled in center: {first_source}", flush=True)
    capture("first", ["hotplug-first"])
    assert full_size_first(), "first window left full-size center placement after capture"
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
