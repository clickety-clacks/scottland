#!/usr/bin/env python3
"""Focused middle-button move and adjacent touchpad gesture behavior.

Mouse actions and pointer motion use stipc. Touchpad neighbors use Scottland's
test-touchpad hook for supported gesture/button events; they do not model physical
libinput hardware.
"""
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time

assert os.environ.get("SCOTTLAND_TEST_MODEL") == "1"
artifacts = Path(sys.argv[1]).resolve()
artifacts.mkdir(parents=True, exist_ok=True)
sock = socket.socket(socket.AF_UNIX)
sock.settimeout(15)
sock.connect(os.environ["WAYFIRE_SOCKET"])


def ipc(method, data=None):
    body = json.dumps({"method": method, "data": data or {}}).encode()
    sock.sendall(struct.pack("<I", len(body)) + body)

    def read(size):
        result = b""
        while len(result) < size:
            chunk = sock.recv(size - len(result))
            if not chunk:
                raise RuntimeError("compositor disconnected")
            result += chunk
        return result

    reply = json.loads(read(struct.unpack("<I", read(4))[0]))
    if isinstance(reply, dict) and "error" in reply:
        raise RuntimeError(reply)
    return reply


def wait(predicate, timeout=8, what="state"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(.025)
    raise RuntimeError(what + " timeout")


def views():
    return ipc("window-rules/list-views")


def window():
    return next(row for row in views() if row.get("title") == "Mouse-Middle-A")


def geometry():
    return window()["geometry"]


def state():
    return ipc("scottland/test-input")


def events():
    path = artifacts / "buttons.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def clear_events():
    (artifacts / "buttons.jsonl").write_text("")


def pointer(x, y):
    ipc("stipc/move_cursor", {"x": round(x), "y": round(y)})


def mouse_button(pressed):
    ipc("stipc/feed_button", {
        "combo": "BTN_MIDDLE", "mode": "press" if pressed else "release"})


def pad(event, **data):
    return ipc("scottland/test-touchpad", {"event": event, **data})


def place(x=600, y=350, width=400, height=300):
    request = {"id": window()["id"], "geometry": {
        "x": x, "y": y, "width": width, "height": height}}
    for _ in range(8):
        ipc("window-rules/configure-view", request)
        try:
            wait(lambda: (lambda g: (g["x"], g["y"]) == (x, y) and
                         abs(g["width"] - width) <= 24 and
                         abs(g["height"] - height) <= 24)(geometry()),
                 timeout=1.5, what="fixture placement")
            break
        except RuntimeError:
            continue
    else:
        raise RuntimeError("could not place fixture window: " + str(geometry()))
    ipc("window-rules/focus-view", {"id": window()["id"]})
    wait(lambda: (ipc("window-rules/get-focused-view").get("info") or {}).get("id") ==
         window()["id"],
         what="fixture focus")
    settle()


def settle():
    previous = None
    stable = 0

    def sample():
        nonlocal previous, stable
        current = geometry()
        if current == previous:
            stable += 1
        else:
            previous, stable = current, 0
        return stable >= 3

    wait(sample, timeout=8, what="geometry settle")
    return geometry()


def check(ok, label, detail=""):
    global passed, failed
    print(("PASS " if ok else "FAIL ") + label +
          (f" [{detail}]" if detail and not ok else ""), flush=True)
    passed += bool(ok)
    failed += not bool(ok)


client = subprocess.Popen(
    [sys.executable, str(Path(__file__).with_name("mouse-middle-move-app.py")), str(artifacts)],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
passed = failed = 0
mouse_down = pad_down = swipe_active = False
try:
    wait(lambda: any(row.get("title") == "Mouse-Middle-A" for row in views()),
         what="fixture map")
    ipc("wayfire/set-config-options", {
        "output:HEADLESS-1/mode": "1600x1000@60000",
        "scottland/touchpad_gestures": True})
    place()

    # A quick mouse middle click is delivered exactly once and does not move the window.
    clear_events()
    before = geometry()
    x, y = before["x"] + before["width"] / 2, before["y"] + before["height"] / 2
    pointer(x, y)
    mouse_button(True)
    mouse_down = True
    mouse_button(False)
    mouse_down = False
    click = wait(lambda: events() if len(events()) >= 2 else None,
                 what="mouse middle-click delivery")
    check([(event["edge"], event["button"]) for event in click] ==
          [("press", 2), ("release", 2)],
          "mouse quick middle click reaches the client once, in order")
    check(geometry() == before, "mouse quick middle click leaves geometry unchanged")

    # A held middle button with pointer motion moves the window, preserves its size, and
    # withholds the button from the client.
    place()
    ipc("wayfire/set-config-options", {"scottland/touchpad_gestures": False})
    clear_events()
    before = geometry()
    x, y = before["x"] + before["width"] / 2, before["y"] + before["height"] / 2
    pointer(x, y)
    mouse_button(True)
    mouse_down = True
    pointer(x + 20, y + 10)
    wait(lambda: state()["dragging"], what="mouse middle drag start")
    for step in range(2, 11):
        pointer(x + 20 * step, y + 10 * step)
    check(not events(), "mouse middle drag sends no button event to the client")
    mouse_button(False)
    mouse_down = False
    wait(lambda: not state()["dragging"], what="mouse middle drag end")
    after = settle()
    check(after["x"] >= before["x"] + 120 and after["y"] >= before["y"] + 40 and
          (after["width"], after["height"]) == (before["width"], before["height"]),
          "mouse middle drag moves without resizing",
          f"{before} -> {after}")
    check(not events(), "mouse middle drag release stays withheld from the client")

    # The existing touchpad quick-click path remains a middle click.
    ipc("wayfire/set-config-options", {"scottland/touchpad_gestures": True})
    place()
    clear_events()
    before = geometry()
    x, y = before["x"] + before["width"] / 2, before["y"] + before["height"] / 2
    pointer(x, y)
    pad("button", button="middle", pressed=True)
    pad_down = True
    pad("button", button="middle", pressed=False)
    pad_down = False
    click = wait(lambda: events() if len(events()) >= 2 else None,
                 what="touchpad middle-click delivery")
    check([(event["edge"], event["button"]) for event in click] ==
          [("press", 2), ("release", 2)],
          "touchpad quick middle click reaches the client once, in order")
    check(geometry() == before, "touchpad quick middle click leaves geometry unchanged")

    # A touchpad click-drag still resizes about the window center.
    place()
    clear_events()
    before = geometry()
    center = (before["x"] + before["width"] / 2, before["y"] + before["height"] / 2)
    pointer(*center)
    pad("button", button="middle", pressed=True)
    pad_down = True
    # test-touchpad models buttons and gestures; stipc supplies pointer motion.
    pointer(center[0] + 80, center[1] + 60)
    wait(lambda: state()["middle_resizing"], what="touchpad resize grab start")
    pointer(center[0] + 100, center[1] + 80)
    wait(lambda: geometry() != before, what="touchpad click-drag resize")
    pad("button", button="middle", pressed=False)
    pad_down = False
    after = settle()
    before_center = (before["x"] + before["width"] / 2, before["y"] + before["height"] / 2)
    after_center = (after["x"] + after["width"] / 2, after["y"] + after["height"] / 2)
    check((after["width"], after["height"]) != (before["width"], before["height"]),
          "touchpad click-drag changes window size")
    check(max(abs(before_center[i] - after_center[i]) for i in range(2)) <= 3,
          "touchpad click-drag preserves the window center",
          f"{before_center} -> {after_center}")
    check(not events(), "touchpad resize does not deliver a middle click")

    # A three-finger swipe without a click continues to move rather than resize.
    place()
    clear_events()
    before = geometry()
    center = (before["x"] + before["width"] / 2, before["y"] + before["height"] / 2)
    pointer(*center)
    pad("swipe_begin", fingers=3)
    swipe_active = True
    wait(lambda: state()["swipe_moving"], what="three-finger swipe start")
    for _ in range(10):
        pad("swipe_update", fingers=3, dx=20, dy=10)
    pad("swipe_end")
    swipe_active = False
    wait(lambda: not state()["dragging"] and not state()["swipe_moving"] and
         geometry()["x"] >= before["x"] + 120 and
         geometry()["y"] >= before["y"] + 40,
         what="three-finger swipe movement after release")
    after = settle()
    check(after["x"] >= before["x"] + 120 and after["y"] >= before["y"] + 40 and
          (after["width"], after["height"]) == (before["width"], before["height"]),
          "three-finger swipe moves without resizing",
          f"{before} -> {after}")
    check(not events(), "three-finger swipe sends no middle-button event")
finally:
    if mouse_down:
        mouse_button(False)
    if pad_down:
        pad("button", button="middle", pressed=False)
    if swipe_active:
        pad("swipe_end")
    client.terminate()
    try:
        client.wait(timeout=3)
    except subprocess.TimeoutExpired:
        client.kill()
        client.wait()

print(f"{passed} passed, {failed} failed", flush=True)
sys.exit(1 if failed else 0)
