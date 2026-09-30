#!/usr/bin/env python3
"""Super + right-drag resize with Wayfire's stipc virtual pointer; reports size, center and scale.

  tests/resize-test.py TITLE DX DY     drag from the window's lower-right quadrant by (DX, DY)
"""
import json, os, subprocess, sys, time

here = os.path.dirname(os.path.abspath(__file__))
title, dx, dy = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])


def ipc(method, data=None):
    reply = json.loads(subprocess.run([os.path.join(here, "wfipc.py"), method, json.dumps(data or {})],
                                      capture_output=True, text=True, check=True).stdout)
    if isinstance(reply, dict) and reply.get("error"):
        sys.exit(f"{method} failed: {reply['error']}")
    return reply


def view():
    return next(v for v in ipc("window-rules/list-views") if v.get("title") == title)


def show(label):
    g = view()["geometry"]
    s = next(x for x in ipc("scottland/layout-state")["views"] if x["title"] == title)
    print(f"{label}: size {g['width']:.0f}x{g['height']:.0f}  "
          f"center ({g['x'] + g['width'] / 2:.0f},{g['y'] + g['height'] / 2:.0f})  scale {s['applied_scale']:.3f}")


show("before")
b = view()["bbox"]
x, y = b["x"] + b["width"] * 0.8, b["y"] + b["height"] * 0.8
ipc("stipc/move_cursor", {"x": x, "y": y}); time.sleep(0.1)
ipc("stipc/feed_key", {"key": "KEY_LEFTMETA", "state": True})
ipc("stipc/feed_button", {"combo": "BTN_RIGHT", "mode": "press"}); time.sleep(0.1)
for i in range(1, 11):
    ipc("stipc/move_cursor", {"x": x + dx * i / 10, "y": y + dy * i / 10}); time.sleep(0.03)
time.sleep(0.3)
ipc("stipc/feed_button", {"combo": "BTN_RIGHT", "mode": "release"})
ipc("stipc/feed_key", {"key": "KEY_LEFTMETA", "state": False}); time.sleep(0.6)
show(f"after Super+right-drag {dx:+.0f},{dy:+.0f}")
