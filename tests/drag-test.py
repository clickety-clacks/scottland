#!/usr/bin/env python3
"""Drag a window across the zones with Wayfire's stipc virtual pointer (Super+left-drag, the move
tool's default binding), reporting the window's zone and scale at checkpoints mid-drag.

  tests/drag-test.py TITLE X1 X2 ... [--shots DIR]

Needs the stipc plugin enabled. Coordinates are output-layout pixels; the pointer starts at the
window's center, and each X is a checkpoint the drag passes through before the drop at the last X.
"""
import json, os, subprocess, sys, time

here = os.path.dirname(os.path.abspath(__file__))
args = [a for a in sys.argv[1:] if not a.startswith("--")]
shots = sys.argv[sys.argv.index("--shots") + 1] if "--shots" in sys.argv else None
if shots:
    args = [a for a in args if a != shots]
title, targets = args[0], [float(x) for x in args[1:]]


def ipc(method, data=None):
    out = subprocess.run([os.path.join(here, "wfipc.py"), method, json.dumps(data or {})],
                         capture_output=True, text=True, check=True).stdout
    reply = json.loads(out)
    if isinstance(reply, dict) and reply.get("error"):
        sys.exit(f"{method} failed: {reply['error']}")
    return reply


def state():
    for v in ipc("scottland/layout-state")["views"]:
        if v["title"] == title:
            return v


view = next(v for v in ipc("window-rules/list-views") if v.get("title") == title and v.get("role") == "toplevel")
bbox = view["bbox"]
x, y = bbox["x"] + bbox["width"] / 2, bbox["y"] + bbox["height"] / 2
print(f"start {title}: center x={x:.0f} zone={state()['zone']} scale={state()['applied_scale']:.3f}")

ipc("stipc/move_cursor", {"x": x, "y": y}); time.sleep(0.1)
ipc("stipc/feed_key", {"key": "KEY_LEFTMETA", "state": True}); time.sleep(0.05)
ipc("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "press"}); time.sleep(0.1)
for n, target in enumerate(targets):
    steps = 20
    start = x
    for i in range(1, steps + 1):
        x = start + (target - start) * i / steps
        ipc("stipc/move_cursor", {"x": x, "y": y}); time.sleep(0.015)
    time.sleep(0.15)
    s = state()
    print(f"  mid-drag pointer x={x:.0f}: zone={s['zone']:10s} scale={s['applied_scale']:.3f}")
    if shots:
        subprocess.run(["grim", os.path.join(shots, f"drag-{n}.png")], check=False)
ipc("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "release"}); time.sleep(0.05)
ipc("stipc/feed_key", {"key": "KEY_LEFTMETA", "state": False}); time.sleep(0.3)
s = state()
print(f"dropped: zone={s['zone']} scale={s['applied_scale']:.3f}")
