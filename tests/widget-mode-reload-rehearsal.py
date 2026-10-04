#!/usr/bin/env python3
"""Rehearse a marked reload from an older plugin build into this one with widgets collapsed and
hidden (AGENTS.md testing step 4; WG16, WG5, DM5). Run inside a headless session started from the
OLD checkout (tests/headless.sh run, with that checkout's build), passing this checkout's plugin:

    python3 tests/widget-mode-reload-rehearsal.py NEW_PLUGIN.so ARTIFACTS_DIR NEW_XML SESSION_XML_DIR

As scottland-reload does after an install, the new plugin's XML replaces the session's (in the old
checkout's metadata directory, the session's WAYFIRE_PLUGIN_XML_PATH) and Wayfire rereads it
before the swap.

Real stipc input throughout. The old plugin knows only expanded/collapsed, and full screen (FS1)
is its way of hiding widgets, so the rehearsal reloads with widgets collapsed and away behind a
full-screen window, then exercises hidden mode on the new plugin and reloads it once more.
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
new_plugin, out = Path(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
mark = Path(os.environ["XDG_RUNTIME_DIR"]) / "scottland" / (os.environ["WAYLAND_DISPLAY"] + ".reloading")
original = t.ipc.call("wayfire/get-config-option", {"option": "core/plugins"})["value"]


def reload_into(plugin, name):
    if len(sys.argv) > 4:
        shutil.copyfile(sys.argv[3], Path(sys.argv[4]) / "scottland.xml")
        t.ipc.call("wayfire/reload-config-metadata")
    fresh = out / f"libscottland-{name}.so"
    shutil.copyfile(plugin, fresh)
    changed = " ".join(str(fresh) if p == "scottland" or "/libscottland" in p else p for p in original.split())
    mark.touch()
    try:
        t.ipc.call("wayfire/set-config-options", {"core/plugins": changed})
        time.sleep(1.5)
    finally:
        mark.unlink(missing_ok=True)


def shot(name):
    subprocess.run(["grim", str(out / (name + ".png"))], check=True)


def full_screen_toggle():
    t.key("LEFTMETA", True); t.key("F", True); t.key("F", False); t.key("LEFTMETA", False)
    time.sleep(.8)


titles = ("rehearse-left", "rehearse-right")
try:
    t.ipc.call("wayfire/set-config-options", {"scottland/sounds": False})
    t.launch("rehearse-left", rail="left", y=220)
    t.launch("rehearse-right", rail="right", y=440)
    t.launch("rehearse-full", rail=None)
    t.move(t.screen["width"] / 2, 40)
    t.tap_mode_key()  # the old plugin: collapse
    time.sleep(1)
    t.check("old plugin: widgets collapsed", all(t.minimized(x) for x in titles))
    full_screen_toggle()
    t.check("old plugin: full screen sends the widgets away", all(t.card(x)["hidden"] for x in titles))
    shot("old-collapsed-fullscreen")

    reload_into(new_plugin, "new")
    t.check("reload old -> new: the compositor survives with every window",
            all(t.app(x) for x in titles + ("rehearse-full",)) and all(t.card(x) for x in titles))
    mode = t.widget_mode()
    t.check("reload old -> new: the old collapsed flag becomes collapsed mode", mode["mode"] == "collapsed", mode)
    t.check("reload old -> new: widgets stay away behind full screen", all(t.card(x)["hidden"] for x in titles))
    full_screen_toggle()
    t.wait_for(lambda: all(not t.card(x)["hidden"] for x in titles))
    time.sleep(.6)
    t.check("leaving full screen: widgets back, collapsed, in place",
            all(t.minimized(x) and abs(t.card(x)["frame"]["width"] - 96) < 1 for x in titles))
    shot("new-collapsed")

    t.tap_mode_key()  # collapsed -> hidden, new in this build
    t.wait_for(lambda: all(t.card(x)["hidden"] for x in titles))
    t.check("new plugin: a tap hides them", t.widget_mode()["mode"] == "hidden")
    reload_into(new_plugin, "new-again")
    t.check("reload new -> new while hidden: mode and places kept",
            t.widget_mode()["mode"] == "hidden" and all(t.card(x)["hidden"] for x in titles))
    t.tap_mode_key()  # hidden -> expanded
    t.wait_for(lambda: all(not t.card(x)["hidden"] and t.card(x)["frame"]["width"] > 120 for x in titles))
    time.sleep(.6)
    t.check("then a tap brings them back expanded", t.widget_mode()["mode"] == "expanded")
    shot("new-expanded")
finally:
    # The caller stops this session; reloading it back into the old plugin isn't part of it.
    t.cleanup()
    print(f"reload rehearsal: {t.passes} passed, {t.failures} failed", flush=True)
sys.exit(bool(t.failures))
