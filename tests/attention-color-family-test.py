#!/usr/bin/env python3
"""Capture GO22's six color/theme choices with Goo and fallback halos on live surfaces."""
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time

import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf

assert os.environ.get("SCOTTLAND_TEST_MODEL") == "1"
art = Path(sys.argv[1]).resolve()
art.mkdir(parents=True, exist_ok=True)
sock = socket.socket(socket.AF_UNIX)
sock.connect(os.environ["WAYFIRE_SOCKET"])
clients = []


def ipc(method, data=None):
    body = json.dumps({"method": method, "data": data or {}}).encode()
    sock.sendall(struct.pack("<I", len(body)) + body)

    def read(count):
        out = b""
        while len(out) < count:
            chunk = sock.recv(count - len(out))
            if not chunk:
                raise RuntimeError("compositor disconnected")
            out += chunk
        return out

    result = json.loads(read(struct.unpack("<I", read(4))[0]))
    if "error" in result:
        raise RuntimeError(result)
    return result


def views():
    return ipc("scottland/layout-state")["views"]


def view(title):
    return next(item for item in views() if item["title"] == title)


def widget_view_for(title):
    return next((item for item in views() if item.get("widget") and title in item["title"]), None)


def wait_view(title):
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        if any(item["title"] == title for item in views()):
            return view(title)
        time.sleep(.1)
    raise AssertionError("window did not map: " + title)


def wait_family(family):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        current = ipc("wayfire/get-config-option", {"option": "scottland/attention_color_family"}).get("value")
        if current == family:
            return
        time.sleep(.03)
    raise AssertionError(("setting did not apply", family, current))


def set_options(options):
    ipc("wayfire/set-config-options", {"scottland/" + name: value for name, value in options.items()})


def pointer(x, y):
    ipc("stipc/move_cursor", {"x": round(x), "y": round(y)})


def button(mode):
    ipc("stipc/feed_button", {"combo": "BTN_LEFT", "mode": mode})


def move_to_widget(title, x, y):
    frame = view(title)["frame"]
    start_x, start_y = frame["x"] + frame["width"] / 2, frame["y"] + frame["height"] / 2
    pointer(start_x, start_y)
    ipc("stipc/feed_key", {"key": "KEY_LEFTMETA", "state": True})
    button("press")
    for step in range(1, 31):
        pointer(start_x + (x - start_x) * step / 30, start_y + (y - start_y) * step / 30)
        time.sleep(.02)
    button("release")
    ipc("stipc/feed_key", {"key": "KEY_LEFTMETA", "state": False})
    pointer(800, 950)
    time.sleep(1)
    deadline = time.monotonic() + 18
    while time.monotonic() < deadline:
        if view(title).get("widgetized") and widget_view_for(title):
            time.sleep(1)
            return
        time.sleep(.1)
    screenshot("widget-drag-failed")
    raise AssertionError(("real pointer drag did not widgetize", title, view(title), views()))


def screenshot(name):
    path = art / (name + ".png")
    subprocess.run(["grim", str(path)], check=True)
    return GdkPixbuf.Pixbuf.new_from_file(str(path))


def hue_shift(warm, cool):
    """Over the pixels where Warm and Cool differ (the attention color), the mean channel
    difference warm-minus-cool, and each image's mean red-minus-green there. Red-minus-green
    is the measure: amber against yellow-green differs little in red once composited."""
    a, b = warm.get_pixels(), cool.get_pixels()
    n = warm.get_n_channels()
    count = dr = dg = rg_warm = rg_cool = 0
    for i in range(0, min(len(a), len(b)) - n + 1, n):
        if max(abs(a[i+c] - b[i+c]) for c in range(3)) <= 8:
            continue
        count += 1
        dr += a[i] - b[i]; dg += a[i+1] - b[i+1]
        rg_warm += a[i] - a[i+1]; rg_cool += b[i] - b[i+1]
    if not count:
        return {"pixels": 0}
    return {"pixels": count, "red": dr / count, "green": dg / count,
            "warm_red_minus_green": rg_warm / count, "cool_red_minus_green": rg_cool / count}


def pixels_changed(left, right):
    a, b = left.get_pixels(), right.get_pixels()
    if len(a) != len(b):
        return True
    return sum(x != y for x, y in zip(a, b))


try:
    ipc("wayfire/set-config-options", {"output:HEADLESS-1/mode": "1600x1000@60000"})
    set_options({"goo": True, "attention_color_family": "theme"})
    for title in ("go22-window", "go22-widget"):
        clients.append(subprocess.Popen(["foot", "-c", "/dev/null", "-T", title, "sleep", "600"],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        wait_view(title)
    ipc("window-rules/configure-view", {"id": view("go22-window")["id"],
        "geometry": {"x": 900, "y": 300, "width": 400, "height": 340}})
    ipc("window-rules/configure-view", {"id": view("go22-widget")["id"],
        "geometry": {"x": 350, "y": 300, "width": 400, "height": 340}})
    time.sleep(2)
    move_to_widget("go22-widget", 10, 500)
    surfaces = [item for item in views() if item["title"] in ("go22-window", "go22-widget")]
    assert len(surfaces) == 2 and view("go22-widget").get("widgetized") and widget_view_for("go22-widget"), surfaces
    ids = {item["id"] for item in surfaces}
    clients.append(subprocess.Popen(["foot", "-c", "/dev/null", "-T", "go22-focus-holder", "sleep", "600"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    wait_view("go22-focus-holder")
    ipc("window-rules/configure-view", {"id": view("go22-focus-holder")["id"],
        "geometry": {"x": 1800, "y": 850, "width": 180, "height": 120}})
    time.sleep(.5)
    for window_id in ids:
        result = ipc("scottland/attention", {"window": window_id, "attention": True, "source": "go22-capture"})
        assert not result.get("in_front", False), (window_id, result)
    time.sleep(.6)
    assert view("go22-window")["frame"]["attention"]
    assert widget_view_for("go22-widget")["frame"]["attention"]
    ipc("scottland/goo-state", {"breath_hold": 1})
    time.sleep(.7)

    images = {}
    for renderer, goo in (("goo", True), ("fallback", False)):
        set_options({"goo": goo})
        time.sleep(.7)
        for scheme in ("light", "dark"):
            set_options({"color_scheme": scheme, "attention_color": "#EBCB8BFF"})
            time.sleep(.35)
            for family in ("theme", "warm", "cool"):
                set_options({"attention_color_family": family})
                wait_family(family)
                time.sleep(.45)
                name = f"{renderer}-{scheme}-{family}"
                images[(renderer, scheme, family)] = screenshot(name)
                print(json.dumps({"screenshot": str(art / (name + ".png")), "renderer": renderer,
                                  "scheme": scheme, "family": family, "window_and_widget_attention": True}), flush=True)

    for renderer in ("goo", "fallback"):
        for scheme in ("light", "dark"):
            theme = images[(renderer, scheme, "theme")]
            warm = images[(renderer, scheme, "warm")]
            cool = images[(renderer, scheme, "cool")]
            assert pixels_changed(theme, warm) > 100, (renderer, scheme, "theme/warm colors rendered the same")
            assert pixels_changed(warm, cool) > 100, (renderer, scheme, "warm/cool colors rendered the same")
            print(f"PASS {renderer} {scheme}: Theme, Warm and Cool render distinct attention colors", flush=True)
            # Warm is red/amber and Cool olive/yellow-green in both schemes (GO22): where the
            # two differ, Warm leans red over green, Cool green over red, by a clear margin.
            shift = hue_shift(warm, cool)
            print(json.dumps({"renderer": renderer, "scheme": scheme, "warm_vs_cool": shift}), flush=True)
            assert shift["pixels"] > 100 and shift["warm_red_minus_green"] > 8 and \
                shift["cool_red_minus_green"] < 0 and \
                shift["warm_red_minus_green"] - shift["cool_red_minus_green"] > 16, (renderer, scheme, shift)
            print(f"PASS {renderer} {scheme}: Warm reads warm (red over green) and Cool reads cool (green over red)", flush=True)
finally:
    try:
        ipc("wayfire/set-config-options", {"scottland/attention_color_family": "theme", "scottland/goo": True})
        ipc("scottland/goo-state", {"breath_hold": -1})
    except Exception:
        pass
    for client in clients:
        client.terminate()
    for client in clients:
        client.wait(timeout=5)
    sock.close()
