#!/usr/bin/env python3
"""Rehearse a reload from an older plugin build into this one while windows peek (AGENTS.md
testing step 4; WK13): widgets on both rails, a stack of overlapping windows with always-on window
avoidance (offset transformers attached), then Window mode held. Run inside a headless --widgets
session started from the OLD checkout, passing this checkout's plugin and metadata:

    python3 tests/peek-reload-rehearsal.py NEW_PLUGIN.so ARTIFACTS_DIR NEW_XML

As scottland-reload does, the new plugin's XML replaces the session's (the first directory of the
session's WAYFIRE_PLUGIN_XML_PATH) and Wayfire rereads it before the swap, under the reload mark.
"""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

spec = importlib.util.spec_from_file_location("widget_input", Path(__file__).with_name("widget-input-test.py"))
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)
new_plugin, out, new_xml = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
out.mkdir(parents=True, exist_ok=True)
mark = Path(os.environ["XDG_RUNTIME_DIR"]) / "scottland" / (os.environ["WAYLAND_DISPLAY"] + ".reloading")
xml_dir = Path(os.environ["WAYFIRE_PLUGIN_XML_PATH"].split(":")[0])
original = t.ipc.call("wayfire/get-config-option", {"option": "core/plugins"})["value"]


def reload_into(name):
    shutil.copyfile(new_xml, xml_dir / "scottland.xml")
    t.ipc.call("wayfire/reload-config-metadata")
    fresh = out / f"libscottland-{name}.so"
    shutil.copyfile(new_plugin, fresh)
    changed = " ".join(str(fresh) if p == "scottland" or "/libscottland" in p else p for p in original.split())
    mark.touch()
    try:
        t.ipc.call("wayfire/set-config-options", {"core/plugins": changed})
        time.sleep(1.5)
    finally:
        mark.unlink(missing_ok=True)


def shot(name):
    subprocess.run(["grim", str(out / (name + ".png"))], check=True)


def hints():
    return t.ipc.call("scottland/hints")


def settled(timeout=10):
    def still():
        a = hints()
        time.sleep(.2)
        b = hints()
        steady = all(abs(h["dx"] - h.get("target_dx", h["dx"])) < .1 and abs(h["dy"] - h.get("target_dy", h["dy"])) < .1
                     for h in b["hints"])
        return b if steady and not b.get("avoidance_solve_pending") and a["hints"] == b["hints"] else None
    return t.wait_for(still, timeout)


stack = ("peek-back", "peek-mid", "peek-front")
rails = ("peek-rail-left", "peek-rail-right")
try:
    t.ipc.call("wayfire/set-config-options", {"scottland/sounds": False, "scottland/window_avoidance_always": True})
    t.launch(rails[0], rail="left", y=220)
    t.launch(rails[1], rail="right", y=440)
    cx, cy = t.screen["width"] / 2, t.screen["height"] / 2
    for k, title in enumerate(stack):
        t.launch(title, rail=None)
        t.drag_begin(t.app(title), cx, cy)  # exactly stacked: the rear ones must peek
        t.drag_end()
        time.sleep(.6)
    t.move(cx, 5)
    old = settled()
    t.check("old plugin: rear windows of the stack are displaced by always-on avoidance",
            sum(abs(h["dx"]) + abs(h["dy"]) > 1 for h in old["hints"]) >= 1,
            [(h["hint"], round(h["dx"], 1), round(h["dy"], 1)) for h in old["hints"]])
    shot("old-peeking")

    reload_into("new")
    alive = all(t.app(x) for x in stack + rails) and all(t.card(x) for x in rails)
    t.check("reload old -> new with offsets attached: the compositor survives with every window and card", alive)
    new = settled()
    t.check("the new plugin runs: peek telemetry is present", all("outcome" in h for h in new["hints"]))
    rear = [h for h in new["hints"] if t.app(stack[0]) and h["window"] in (t.app(stack[0])["id"], t.app(stack[1])["id"])]
    t.check("after the swap every rear window of the stack is decided and peeks",
            rear and all(h["outcome"] in ("moved", "visible") for h in rear),
            [(h["hint"], h["outcome"], h["rung"], round(h["dx"], 1), round(h["dy"], 1)) for h in rear])
    shot("new-peeking")

    t.key("LEFTALT", True)
    t.wait_for(lambda: hints()["active"])
    time.sleep(1.5)
    held = settled()
    t.check("new plugin, Window mode: every window and card shows its hint", all(h["visible"] for h in held["hints"]),
            [(h["hint"], h["visible"]) for h in held["hints"]])
    shot("new-window-mode")
    reload_into("new-again")
    t.check("reload new -> new while Window mode is held: everything survives",
            all(t.app(x) for x in stack + rails) and all(t.card(x) for x in rails))
    t.key("LEFTALT", False)
    time.sleep(1)
    after = settled()
    t.check("after Alt release the session is back to the peek layout with hints gone",
            not after["active"] and all("outcome" in h for h in after["hints"]))
    shot("new-after")
finally:
    t.cleanup()
    print(f"peek reload rehearsal: {t.passes} passed, {t.failures} failed", flush=True)
sys.exit(bool(t.failures))
