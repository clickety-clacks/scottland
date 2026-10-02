#!/usr/bin/env python3
"""Widget regressions inside tests/headless.sh run, using real stipc input.

Run in an otherwise empty --widgets session. Optional arguments select cases.
The caller owns the headless session; this test closes only windows it launches.
"""
import argparse
import json
import os
from pathlib import Path
import select
import socket
import struct
import subprocess
import sys
import time


class Ipc:
    def __init__(self):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.connect(os.environ["WAYFIRE_SOCKET"])
        self.sock.settimeout(5)

    def receive(self):
        def read(n):
            data = b""
            while len(data) < n:
                chunk = self.sock.recv(n - len(data))
                if not chunk:
                    raise ConnectionError("compositor disconnected")
                data += chunk
            return data
        return json.loads(read(struct.unpack("<I", read(4))[0]))

    def call(self, method, data=None):
        body = json.dumps({"method": method, "data": data or {}}).encode()
        self.sock.sendall(struct.pack("<I", len(body)) + body)
        reply = self.receive()
        if isinstance(reply, dict) and "error" in reply:
            raise RuntimeError(reply)
        return reply


ipc = Ipc()
passes = failures = 0
owned = []


def check(name, ok, detail=""):
    global passes, failures
    if ok:
        passes += 1
        print(f"PASS  {name}", flush=True)
    else:
        failures += 1
        print(f"FAIL  {name}: {detail}", flush=True)


def wait_for(fn, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = fn()
        if result:
            return result
        time.sleep(0.025)
    raise AssertionError("timed out waiting for " + repr(fn))


def views():
    return ipc.call("scottland/layout-state")["views"]


def app(title):
    return next((v for v in views() if v["title"] == title and not v["widget"]), None)


def card(title):
    return next((v for v in views() if v["widget"] and v["title"].endswith(": " + title)), None)


def widgets():
    return ipc.call("scottland/widgets")["widgets"]


def minimized(title):
    return next(w["minimized"] for w in widgets() if w["title"] == title)


def key(name, down):
    ipc.call("stipc/feed_key", {"key": "KEY_" + name, "state": down})


def toggle():
    key("LEFTMETA", True)
    key("M", True)
    key("M", False)
    key("LEFTMETA", False)


def move(x, y):
    ipc.call("stipc/move_cursor", {"x": round(x), "y": round(y)})


def drag_begin(view, x, y):
    f = view["frame"]
    sx, sy = f["x"] + f["width"] / 2, f["y"] + f["height"] / 2
    move(sx, sy)
    time.sleep(0.1)
    key("LEFTMETA", True)
    ipc.call("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "press"})
    for i in range(1, 21):
        move(sx + (x - sx) * i / 20, sy + (y - sy) * i / 20)
        time.sleep(0.025)
    time.sleep(0.4)


def drag_end():
    ipc.call("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "release"})
    key("LEFTMETA", False)
    time.sleep(0.5)


screen = ipc.call("window-rules/list-outputs")[0]["geometry"]


def launch(title, rail="right", y=260, app_id="foot"):
    process = subprocess.Popen(["foot", "--app-id", app_id, "-T", title, "-W", "40x8", "sh", "-c", "exec sleep 600"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    owned.append((title, process))
    view = wait_for(lambda: app(title))
    time.sleep(0.4)
    if rail:
        drag_begin(app(title), screen["width"] - 6 if rail == "right" else 6, y)
        drag_end()
        wait_for(lambda: card(title) and not card(title)["preview"])
        time.sleep(0.7)
    return view


def cleanup():
    for name in ("M", "LEFTMETA", "LEFTSHIFT", "LEFTCTRL"):
        key(name, False)
    for title, process in owned:
        view = app(title)
        if view:
            ipc.call("window-rules/close-view", {"id": view["id"]})
        try:
            process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            process.terminate()  # only our recorded child PID
            process.wait(timeout=4)
    owned.clear()
    time.sleep(0.5)


def held_key():
    title = "press-regression"
    launch(title)
    # A fresh mode for this case, without bypassing the binding under test.
    if minimized(title):
        toggle()
    log_start = len(args.log.read_text()) if args.log else 0
    key("LEFTMETA", True)
    key("M", True)
    check("WG20 first press activates", minimized(title))
    key("M", True)  # duplicate down from the same physical/injected key
    check("WG20 duplicate down while held does not toggle", minimized(title))
    key("LEFTMETA", False)
    key("LEFTMETA", True)
    key("M", True)
    check("WG20 releasing the modifier does not rearm M", minimized(title))
    key("M", False)
    key("M", True)
    check("WG20 releasing M rearms immediately", not minimized(title))
    key("M", False)
    # No sleeps: separate presses must survive even well below an animation duration.
    states = []
    for _ in range(4):
        key("M", True)
        states.append(minimized(title))
        key("M", False)
    check("WG20 four rapid intentional presses all activate", states == [True, False, True, False], states)
    key("LEFTMETA", False)
    if args.log:
        trace = args.log.read_text()[log_start:]
        edges = [line for line in trace.splitlines() if "minimize-key edge=" in line]
        check("WG20 diagnostics record every bound-key edge with device, time and state",
              len(edges) == 14 and all(all(field in line for field in
                  ("device=stipc_keyboard@", "time_msec=", "received_msec=", "key=50", "state=",
                   "held_devices=", "activated=", "collapsed=")) for line in edges), edges)
        check("WG20 diagnostics distinguish six activations and two ignored duplicates",
              trace.count("minimize-key activation ") == 6 and
              trace.count("minimize-key ignored-duplicate ") == 2, trace)


def gravity():
    title = "gravity-regression"
    events = Ipc()
    events.call("window-rules/events/watch", {"events": ["view-mapped", "view-geometry-changed"]})

    def drain():
        result = []
        while select.select([events.sock], [], [], 0.05)[0]:
            result.append(events.receive())
        return result

    launch(title, app_id="scottland-test-gravity")
    widget_id = card(title)["id"]
    mapped = [e["view"]["geometry"] for e in drain()
              if e["event"] == "view-mapped" and e["view"]["id"] == widget_id]
    check("WG4 widget mapping is already against its right rail",
          len(mapped) == 1 and mapped[0]["x"] + mapped[0]["width"] == screen["width"] - 24, mapped)

    def resize(label, rail, target):
        before = card(title)["frame"]
        edge = before["x"] + (before["width"] if rail == "right" else 0)
        drain()
        key("SPACE", True)
        key("SPACE", False)
        wait_for(lambda: round(card(title)["frame"]["width"]) == target)
        time.sleep(0.2)
        changes = [e["view"]["geometry"] for e in drain()
                   if e["event"] == "view-geometry-changed" and e["view"]["id"] == widget_id]
        check(label + " keeps its edge in every applied geometry",
              bool(changes) and all(abs(g["x"] + (g["width"] if rail == "right" else 0) - edge) < 1
                                    for g in changes), changes)
        check(label + " has no follow-up corrective move",
              len(changes) == 1, changes)

    resize("WG4 first right-rail resize", "right", 96)
    drag_begin(card(title), 6, 330)
    drag_end()
    resize("WG4 first resize after moving right to left", "left", 320)
    drag_begin(card(title), screen["width"] - 6, 330)
    drag_end()
    resize("WG4 first resize after moving left to right", "right", 96)
    events.sock.close()


def previews():
    anchor = "preview-anchor"
    launch(anchor, y=180)
    if minimized(anchor):
        toggle()
    for collapsed in (True, False):
        title = "preview-collapse" if collapsed else "preview-expand"
        launch(title, rail=None)
        drag_begin(app(title), screen["width"] - 6, 420)
        wait_for(lambda: card(title) and card(title)["preview"])
        check("WG16 preview is still a window before commitment", not app(title)["widgetized"])
        check("WG16 previews stay out of the ordinary widgets listing",
              not any(w["title"] == title for w in widgets()))
        key("M", True)  # Super is still held for the drag
        key("M", False)
        time.sleep(1)
        width = card(title)["frame"]["width"]
        check(f"WG16 running preview follows mode before drop ({collapsed=})",
              (round(width) == 96) if collapsed else width > 96, width)
        drag_end()
        time.sleep(0.6)
        width = card(title)["frame"]["width"]
        check(f"WG16 committed preview has current logical mode ({collapsed=})",
              minimized(title) == collapsed, minimized(title))
        check(f"WG16 committed preview card has current presentation ({collapsed=})",
              (round(width) == 96) if collapsed else width > 96, width)
        wid = app(title)["id"]
        prop = subprocess.check_output(["busctl", "--user", "get-property", "org.scottland.Widgets",
                f"/org/scottland/widget/{wid}", "org.scottland.Widget", "Minimized"], text=True).strip()
        check(f"WG16 committed preview D-Bus mode agrees ({collapsed=})",
              prop == "b " + str(collapsed).lower(), prop)


cases = {"key": held_key, "gravity": gravity, "previews": previews}
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--log", type=Path, help="this headless session's wayfire.log")
parser.add_argument("cases", nargs="*", choices=list(cases))
args = parser.parse_args()
try:
    ipc.call("wayfire/set-config-options", {"scottland/sounds": False})
    for name in args.cases or cases:
        try:
            cases[name]()
        finally:
            cleanup()
finally:
    print(f"widget input regressions: {passes} passed, {failures} failed", flush=True)
sys.exit(bool(failures))
