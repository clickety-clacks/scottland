#!/usr/bin/env python3
"""A selected widget's next hint press can become a solo without releasing Alt.

The fixture uses compositor IPC for window placement, then real stipc key events for
the selection and hold. The second center window's geometry is the result oracle.
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
art = Path(sys.argv[1]).resolve()
art.mkdir(parents=True, exist_ok=True)
sock = socket.socket(socket.AF_UNIX)
sock.settimeout(15)
sock.connect(os.environ["WAYFIRE_SOCKET"])
clients = []
held = set()


def ipc(method, data=None):
    body = json.dumps({"method": method, "data": data or {}}).encode()
    sock.sendall(struct.pack("<I", len(body)) + body)

    def read(size):
        result = b""
        while len(result) < size:
            part = sock.recv(size - len(result))
            if not part:
                raise RuntimeError("compositor disconnected")
            result += part
        return result

    reply = json.loads(read(struct.unpack("<I", read(4))[0]))
    if isinstance(reply, dict) and "error" in reply:
        raise RuntimeError(reply)
    return reply


def wait(predicate, name, timeout=5):
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        last = predicate()
        if last:
            return last
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {name}; last={last!r}")


def key(name, down):
    ipc("stipc/feed_key", {"key": "KEY_" + name, "state": down})
    if down:
        held.add(name)
    else:
        held.discard(name)


def hint(window):
    return next(h["hint"] for h in ipc("scottland/hints")["hints"] if h["window"] == window)


def press_hint(window, dwell):
    label = hint(window).upper()
    for letter in label[:-1]:
        key(letter, True)
        time.sleep(0.035)  # physical key stroke, not a readiness wait
        key(letter, False)
    key(label[-1], True)
    pressed_at = time.monotonic()
    time.sleep(dwell)  # the actual held input
    key(label[-1], False)
    return pressed_at, time.monotonic()


def layout(window):
    return next(v for v in ipc("scottland/layout-state")["views"] if v["id"] == window)


def geometry(window):
    return next(v["geometry"] for v in ipc("window-rules/list-views") if v["id"] == window)


def launch(name):
    clients.append(subprocess.Popen(
        ["foot", "--app-id=scottland-hint-hold", "--title=" + name, "sh", "-c", "sleep 120"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    return wait(lambda: next((v["id"] for v in ipc("window-rules/list-views")
                              if v.get("title") == name), None), name)


def place(window, x, y, width, height):
    requested = {"id": window, "geometry": {"x": x, "y": y, "width": width, "height": height}}
    for _ in range(5):  # fixture: clients may answer the first configure with an older size
        ipc("window-rules/configure-view", requested)
        try:
            return wait(lambda: (g := geometry(window))["x"] == x and g["y"] == y and
                        abs(g["width"] - width) < 24 and abs(g["height"] - height) < 24,
                        "fixture geometry", timeout=1)
        except AssertionError:
            pass
    raise AssertionError(f"fixture placement failed: {geometry(window)}")


def alt(down):
    key("LEFTALT", down)
    wait(lambda: ipc("scottland/hints")["active"] == down, "Alt mode")


def capture(name, widget, other):
    state = {"hints": ipc("scottland/hints"), "widgets": ipc("scottland/widgets"),
             "spread": ipc("scottland/spread-state"),
             "widget": layout(widget), "other": layout(other),
             "widget_geometry": geometry(widget), "other_geometry": geometry(other)}
    (art / f"{name}.json").write_text(json.dumps(state, indent=2))
    return state


def main():
    widget = launch("hint-hold-widget")
    other = launch("hint-hold-other")
    place(widget, 520, 140, 520, 360)
    place(other, 530, 160, 520, 360)
    ipc("window-rules/focus-view", {"id": other})
    wait(lambda: ipc("window-rules/get-focused-view").get("info", {}).get("id") == other,
         "other window focused")

    # Establish a real rail widget through the existing double-tap input gesture.
    alt(True)
    press_hint(widget, 0.05)
    time.sleep(0.05)  # intended interval between taps
    press_hint(widget, 0.05)
    wait(lambda: layout(widget)["widgetized"], "widgetized fixture")
    alt(False)
    widget_view = wait(lambda: next((w["widget_view"] for w in
                        ipc("scottland/widgets")["widgets"] if int(w["id"]) == widget and
                        w["widget_view"] > 0), None), "widget card")
    ipc("window-rules/focus-view", {"id": other})
    wait(lambda: ipc("window-rules/get-focused-view").get("info", {}).get("id") == other,
         "other window refocused")
    before_other = geometry(other)

    alt(True)
    capture("before", widget, other)
    _, first_release = press_hint(widget, 0.05)
    wait(lambda: ipc("window-rules/get-focused-view").get("info", {}).get("id") == widget_view,
         "hint-selected widget focused", timeout=0.15)
    selected = ipc("scottland/hints")["selected"]
    second_press, _ = press_hint(widget, 0.75)
    (art / "selection.json").write_text(json.dumps({"selected": selected, "widget": widget,
                                                    "press_gap_ms": (second_press - first_release) * 1000}, indent=2))
    assert selected == widget, "widget hint selection did not take"
    assert second_press - first_release < 0.30, "fixture missed the double-tap timing window"
    wait(lambda: (s := ipc("scottland/spread-state")) and (s.get("last") or {}).get("purpose") == "solo" and
         geometry(other) != before_other, "solo after selected widget hint hold", timeout=3)
    after = capture("after", widget, other)
    assert not after["widget"]["widgetized"], "solo left its window widgetized"
    assert after["widget"]["zone"] == "center", "solo window did not reach center"
    assert after["other"]["zone"] != "center", "overlapping center window did not spread"
    print("PASS selected widget hint hold soloed within one Alt session", flush=True)


try:
    main()
finally:
    for name in tuple(held):
        try:
            key(name, False)
        except Exception:
            pass
    for child in clients:
        child.terminate()
    for child in clients:
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=3)
    sock.close()
