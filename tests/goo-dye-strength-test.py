#!/usr/bin/env python3
"""Capture and compare GO23 live state dye strength in Goo and fallback halos."""
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
baseline_mode = os.environ.get("GO23_BASELINE") == "1"
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


def wait_option(name, expected):
    if baseline_mode:
        return
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        current = ipc("wayfire/get-config-option", {"option": "scottland/" + name}).get("value")
        if abs(float(current) - expected) < .001:
            return
        time.sleep(.03)
    raise AssertionError(("setting did not apply", name, expected, current))


def set_options(options):
    if baseline_mode:
        options = {name: value for name, value in options.items() if name != "goo_dye_density"}
    ipc("wayfire/set-config-options", {"scottland/" + name: value for name, value in options.items()})


def pointer(x, y):
    ipc("stipc/move_cursor", {"x": round(x), "y": round(y)})


def button(mode):
    ipc("stipc/feed_button", {"combo": "BTN_LEFT", "mode": mode})


def focus_with_click(title):
    frame = view(title)["frame"]
    pointer(frame["x"] + frame["width"] / 2, frame["y"] + frame["height"] / 2)
    button("press")
    button("release")
    pointer(800, 950)
    time.sleep(.55)


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
    deadline = time.monotonic() + 18
    while time.monotonic() < deadline:
        if view(title).get("widgetized") and widget_view_for(title):
            time.sleep(.7)
            return
        time.sleep(.1)
    raise AssertionError(("real pointer drag did not widgetize", title, view(title), views()))


def screenshot(name):
    path = art / (name + ".png")
    subprocess.run(["grim", str(path)], check=True)
    return GdkPixbuf.Pixbuf.new_from_file(str(path))


def changed(left, right):
    a, b = left.get_pixels(), right.get_pixels()
    return len(a) != len(b) or sum(x != y for x, y in zip(a, b))


def goo_sleeping():
    return all(screen["sleeping"] for screen in ipc("scottland/goo-state")["screens"])


def differing(left, right, threshold=3):
    a, b = left.get_pixels(), right.get_pixels()
    if len(a) != len(b):
        return len(a), 255
    count = largest = 0
    n = left.get_n_channels()
    for i in range(0, len(a) - n + 1, n):
        d = max(abs(a[i+c] - b[i+c]) for c in range(3))
        largest = max(largest, d)
        count += d > threshold
    return count, largest


rest = {}  # (scheme, strength, state) -> awake/asleep comparison, goo renderer only


def compare_at_rest(scheme, strength, state, name, awake_image, awake):
    """GO23 must hold on the sleeping goo's cached path too (GO24 keeps the dye out of the
    cache and multiplies it back in): the same scene asleep looks as it did awake."""
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and not goo_sleeping():
        time.sleep(.1)
    asleep = goo_sleeping()
    time.sleep(1)
    image = screenshot(name + "-asleep")
    # Wake it with a setting that changes nothing on screen here (the pointer is far from
    # every control), so this awake picture shows the same settled scene as the asleep one.
    distance = float(ipc("wayfire/get-config-option", {"option": "scottland/goo_hover_distance"})["value"])
    set_options({"goo_hover_distance": distance + 1})
    time.sleep(.35)
    woke = not goo_sleeping()
    again = screenshot(name + "-awake-again")
    set_options({"goo_hover_distance": distance})
    count, largest = differing(again, image)
    rest[(scheme, strength, state)] = {"awake_shot_awake": awake and woke, "slept": asleep,
                                       "differing": count, "largest": largest, "image": image,
                                       "first_shot_differing": differing(awake_image, image)}
    print(json.dumps({"rest": name, "awake_shot_awake": awake, "woke_again": woke, "slept": asleep,
                      "differing_px": count, "largest": largest,
                      "first_awake_shot_differing": rest[(scheme, strength, state)]["first_shot_differing"]}), flush=True)


def wait_hints(window_ids):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        state = ipc("scottland/hints")
        visible = {h["window"] for h in state.get("hints", []) if h.get("visible")}
        if state.get("active") and window_ids <= visible:
            return
        time.sleep(.03)
    raise AssertionError(("Window mode hints did not appear", state))


try:
    ipc("wayfire/set-config-options", {"output:HEADLESS-1/mode": "1600x1000@60000"})
    set_options({"goo": True, "goo_dye_density": 1, "goo_noise": 0, "goo_drift": 0,
                 "goo_wave_height": 0, "goo_swirl": 0, "goo_spread": 0, "goo_swell": 0,
                 "attention_color_family": "theme"})
    for title in ("go23-window", "go23-widget", "go23-neutral"):
        clients.append(subprocess.Popen(["foot", "-c", "/dev/null", "-T", title, "sleep", "600"],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        wait_view(title)
    for title, geometry in (("go23-window", {"x": 1200, "y": 600, "width": 300, "height": 280}),
                            ("go23-widget", {"x": 350, "y": 300, "width": 400, "height": 340}),
                            ("go23-neutral", {"x": 600, "y": 50, "width": 240, "height": 200})):
        ipc("window-rules/configure-view", {"id": view(title)["id"], "geometry": geometry})
    time.sleep(.7)
    move_to_widget("go23-widget", 10, 500)
    surfaces = [item for item in views() if item["title"] in ("go23-window", "go23-widget")]
    assert len(surfaces) == 2 and view("go23-widget").get("widgetized") and widget_view_for("go23-widget"), surfaces
    ids = {item["id"] for item in surfaces}
    clients.append(subprocess.Popen(["foot", "-c", "/dev/null", "-T", "go23-focus-holder", "sleep", "600"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    wait_view("go23-focus-holder")
    ipc("window-rules/configure-view", {"id": view("go23-focus-holder")["id"],
        "geometry": {"x": 1410, "y": 820, "width": 150, "height": 100}})
    ipc("window-rules/focus-view", {"id": view("go23-focus-holder")["id"]})
    for window_id in ids:
        result = ipc("scottland/attention", {"window": window_id, "attention": True, "source": "go23-capture"})
        assert not result.get("in_front", False), (window_id, result)
    time.sleep(.5)
    assert view("go23-window")["frame"]["attention"]
    assert widget_view_for("go23-widget")["frame"]["attention"]
    ipc("scottland/goo-state", {"breath_hold": 1})

    strengths = (1.0,) if baseline_mode else (0.25, 1.0, 1.5)
    images = {}
    for renderer, goo in (("goo", True), ("fallback", False)):
        set_options({"goo": goo})
        for window_id in ids:
            ipc("scottland/attention", {"window": window_id, "attention": False, "source": "go23-capture"})
        focus_with_click("go23-window")
        assert view("go23-window")["frame"]["focus"] > .99
        assert view("go23-neutral")["frame"]["focus"] < .01
        for scheme in ("light", "dark"):
            set_options({"color_scheme": scheme, "attention_color": "#EBCB8BFF"})
            time.sleep(.2)
            for strength in strengths:
                set_options({"goo_dye_density": strength})
                wait_option("goo_dye_density", strength)
                time.sleep(.45)
                name = f"{renderer}-{scheme}-{strength:.2f}-focus"
                awake = goo and not goo_sleeping()
                images[(renderer, scheme, strength, "focus")] = screenshot(name)
                if goo and not baseline_mode:
                    compare_at_rest(scheme, strength, "focus", name, images[(renderer, scheme, strength, "focus")], awake)
                print(json.dumps({"screenshot": str(art / (name + ".png")), "renderer": renderer,
                                  "scheme": scheme, "strength": strength, "state": "focus accent"}), flush=True)

        ipc("window-rules/focus-view", {"id": view("go23-focus-holder")["id"]})
        time.sleep(.5)
        assert view("go23-neutral")["frame"]["focus"] < .01
        assert not view("go23-neutral")["frame"]["attention"]
        for window_id in ids:
            ipc("scottland/attention", {"window": window_id, "attention": True, "source": "go23-capture"})
        time.sleep(.5)
        for scheme in ("light", "dark"):
            set_options({"color_scheme": scheme, "attention_color": "#EBCB8BFF"})
            time.sleep(.2)
            for strength in strengths:
                set_options({"goo_dye_density": strength})
                wait_option("goo_dye_density", strength)
                time.sleep(.55)
                name = f"{renderer}-{scheme}-{strength:.2f}-attention"
                awake = goo and not goo_sleeping()
                images[(renderer, scheme, strength, "attention")] = screenshot(name)
                if goo and not baseline_mode:
                    compare_at_rest(scheme, strength, "attention", name, images[(renderer, scheme, strength, "attention")], awake)
                print(json.dumps({"screenshot": str(art / (name + ".png")), "renderer": renderer,
                                  "scheme": scheme, "strength": strength, "state": "attention"}), flush=True)
        # Isolate real Window mode hint dye in both renderers as well.
        for window_id in ids:
            ipc("scottland/attention", {"window": window_id, "attention": False, "source": "go23-capture"})
        ipc("stipc/feed_key", {"key": "KEY_LEFTALT", "state": True})
        wait_hints(ids)
        for scheme in ("light", "dark"):
            set_options({"color_scheme": scheme, "attention_color": "#EBCB8BFF"})
            time.sleep(.2)
            for strength in strengths:
                set_options({"goo_dye_density": strength})
                wait_option("goo_dye_density", strength)
                time.sleep(.45)
                name = f"{renderer}-{scheme}-{strength:.2f}-hint"
                images[(renderer, scheme, strength, "hint")] = screenshot(name)
                print(json.dumps({"screenshot": str(art / (name + ".png")), "renderer": renderer,
                                  "scheme": scheme, "strength": strength, "state": "Window mode hint"}), flush=True)
        ipc("stipc/feed_key", {"key": "KEY_LEFTALT", "state": False})
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline and ipc("scottland/hints").get("active"):
            time.sleep(.03)

    if baseline_mode:
        print("PASS captured pre-GO23 default reference screenshots", flush=True)
    else:
        for renderer in ("goo", "fallback"):
            for scheme in ("light", "dark"):
                for state in ("focus", "attention", "hint"):
                    faint = images[(renderer, scheme, .25, state)]
                    current = images[(renderer, scheme, 1.0, state)]
                    strong = images[(renderer, scheme, 1.5, state)]
                    assert changed(faint, current) and changed(current, strong), (renderer, scheme, state, "strength did not change pixels")
                    print(f"PASS {renderer} {scheme} {state}: 0.25, 1 and 1.5 produce distinct pixels", flush=True)

        for scheme in ("light", "dark"):
            for state in ("focus", "attention"):
                for strength in strengths:
                    r = rest[(scheme, strength, state)]
                    assert r["awake_shot_awake"] and r["slept"] and r["differing"] <= 50 and r["largest"] <= 6, \
                        (scheme, strength, state, {k: v for k, v in r.items() if k != "image"})
                faint, current, strong = (rest[(scheme, v, state)]["image"] for v in (.25, 1.0, 1.5))
                assert changed(faint, current) and changed(current, strong), (scheme, state, "strength did not change the sleeping goo")
                print(f"PASS goo {scheme} {state}: 0.25, 1 and 1.5 look the same asleep as awake, and stay distinct at rest", flush=True)

        # A16's separate neutral term is unit-checked in state-dye-test.cpp. Pixel identity is not
        # a valid integration assertion here: GO1's connected field may carry nearby state dye
        # across a shared Goo surface, which GO23 intentionally scales.
finally:
    try:
        ipc("stipc/feed_key", {"key": "KEY_LEFTALT", "state": False})
        ipc("wayfire/set-config-options", {"scottland/attention_color_family": "theme",
            "scottland/goo": True, "scottland/goo_dye_density": 1})
        ipc("scottland/goo-state", {"breath_hold": -1})
    except Exception:
        pass
    for client in clients:
        client.terminate()
    for client in clients:
        client.wait(timeout=5)
    sock.close()
