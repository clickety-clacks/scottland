#!/usr/bin/env python3
"""Omarchy's color picker shortcut works in Scottland (adapter-gaps AG15).

Isolated headless --omarchy session loading the installed, unchanged utilities.lua (Super+Print:
`pkill hyprpicker || hyprpicker -a`). Real input: Super+Print with stipc keys, then a stipc
pointer click on a window filled with a known color. Oracle: the color hyprpicker put on the
clipboard (read back with wl-paste), which it took from the screen's captured pixels.

  tests/omarchy-color-picker-test.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from omarchy_fixture import REPO, Checks, Fixture, Session  # noqa: E402

COLOR = "#12AB34"
check = Checks()
fixture = Fixture(REPO / "build/omarchy-color-picker-fixture",
                  modules=["default.hypr.bindings.utilities"])


def views(session):
    reply = session.ipc("window-rules/list-views")
    return reply if isinstance(reply, list) else []


with Session(fixture, "hl-omarchy-color-picker") as session:
    check("shim answers", session.wait_shim()[0])
    session.run("sh", "-c", f"python3 {REPO}/tests/solid-color-app.py picker '{COLOR}' "
                            ">/dev/null 2>&1 </dev/null &")
    ok, found = session.wait(lambda: [v for v in views(session)
                                      if v.get("app-id") == "org.scottland.SolidColor.picker"
                                      and v.get("mapped")], timeout=20)
    check("solid color window maps", ok)
    if not ok:
        sys.exit(check.summary())
    box = found[0]["geometry"]
    x, y = box["x"] + box["width"] // 2, box["y"] + box["height"] // 2
    session.ipc("stipc/move_cursor", {"x": x, "y": y})

    session.key("KEY_LEFTMETA", True)
    session.tap("KEY_SYSRQ")
    session.key("KEY_LEFTMETA", False)
    ok, _ = session.wait(lambda: session.run("pgrep", "-x", "hyprpicker").returncode == 0, timeout=10)
    check("Super+Print starts hyprpicker", ok)
    # Readiness: hyprpicker's overlay surface has mapped (a non-toplevel view appears).
    ok, _ = session.wait(lambda: [v for v in views(session) if v.get("role") != "toplevel"
                                  and v.get("mapped") and "hyprpicker" in
                                  (v.get("app-id", "") + v.get("title", "")).lower()] or
                         [v for v in views(session) if v.get("layer") == "overlay"], timeout=10)
    check("hyprpicker overlay maps", ok, [(v.get("role"), v.get("app-id"), v.get("layer"))
                                         for v in views(session)])
    session.ipc("stipc/move_cursor", {"x": x + 3, "y": y + 3})
    time.sleep(0.2)  # paces the gesture: motion, then the click
    session.ipc("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "press"})
    session.ipc("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "release"})
    ok, value = session.wait(lambda: session.run("wl-paste", "--no-newline").stdout.strip()
                             .upper() == COLOR, timeout=10)
    pasted = session.run("wl-paste", "--no-newline").stdout.strip()
    check(f"picked color reaches the clipboard ({COLOR})", ok, pasted)
    session.run("pkill", "-x", "hyprpicker")

sys.exit(check.summary())
