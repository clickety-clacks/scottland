#!/usr/bin/env python3
"""Omarchy's touchpad scroll speeds carry over to Scottland (adapter-gaps AG22, AG07 scroll part).

Isolated headless --omarchy session whose HOME loads the installed, unchanged
default/hypr/input.lua: touchpad scroll_factor 0.4, scroll_touchpad 1.5 for Alacritty/kitty/foot,
0.2 for Ghostty. Input: core's virtual touchpad two-finger scroll through Wayfire's input path
(only libinput bypassed). Oracle: the wl_pointer.axis values each client received
(tests/axis-recorder.c) for app-ids foot, com.mitchellh.ghostty and chromium, and a layer-shell
panel (like the Ask plugin). Then the user's own Hyprland config changes both speeds, and the
session's config watcher (autostart.d/06-watch-config) carries the change over live.

  tests/omarchy-scroll-speed-test.py
"""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from omarchy_fixture import REPO, Checks, Fixture, Recorder, Session  # noqa: E402

SENT = 10.0
check = Checks()
fixture = Fixture(REPO / "build/omarchy-scroll-fixture", modules=["default.hypr.input"])
hypr = fixture.home / ".config/hypr/hyprland.lua"


def views(session):
    reply = session.ipc("window-rules/list-views")
    return reply if isinstance(reply, list) else []


def view(session, app_id):
    return next((v for v in views(session) if v.get("app-id") == app_id and v.get("mapped")), None)


def center(v):
    g = v["geometry"]
    return int(g["x"] + g["width"] / 2), int(g["y"] + g["height"] / 2)


def scroll_over(session, recorder, point):
    session.ipc("stipc/move_cursor", {"x": point[0], "y": point[1]})
    session.wait(lambda: recorder.lines().count("enter") > recorder.lines().count("leave"), timeout=5)
    since = recorder.mark()
    session.ipc("scottland/test-touchpad", {"event": "scroll", "dy": SENT})
    session.ipc("scottland/test-touchpad", {"event": "scroll", "dy": 0})
    session.wait(lambda: any(l.startswith("stop 0") for l in recorder.lines()[since:]), timeout=5)
    return round(recorder.vertical(since), 3)


def global_speed(session):
    return session.ipc("wayfire/get-config-option", {"option": "input/touchpad_scroll_speed"}).get("value")


with Session(fixture, "hl-omarchy-scroll") as session:
    apps = {"foot": "c03030", "com.mitchellh.ghostty": "30c030", "chromium": "3030c0"}
    recorders = {app: Recorder(session, app, color) for app, color in apps.items()}
    recorders["panel"] = Recorder(session, "ask", "c0c030", layer=True)
    ok, _ = session.wait(lambda: all(view(session, app) for app in apps) and
                         all("ready" in r.lines() for r in recorders.values()), timeout=20)
    if not check("recorders map (foot, Ghostty, Chromium, a panel)", ok):
        sys.exit(check.summary())
    for (x, y), app in zip(((20, 380), (440, 380), (860, 380)), apps):
        session.ipc("window-rules/configure-view", {"id": view(session, app)["id"], "geometry": {
            "x": x, "y": y, "width": 400, "height": 300}})
    time.sleep(1.0)  # an intended hold: moves and scaling settle
    points = {app: center(view(session, app)) for app in apps}
    points["panel"] = (640, 100)

    got = {name: scroll_over(session, recorders[name], points[name]) for name in recorders}
    check("Omarchy's global touchpad speed 0.4 applies to Chromium", got["chromium"] == 4.0, got)
    check("...and to a shell panel (layer surface, like Ask)", got["panel"] == 4.0, got)
    check("foot scrolls at Omarchy's terminal speed 1.5", got["foot"] == 15.0, got)
    check("Ghostty scrolls at Omarchy's 0.2", got["com.mitchellh.ghostty"] == 2.0, got)

    # The user's own overrides, edited while the session runs: the config watcher carries them.
    hooks = REPO / "build/hooks"
    pid_file = session.dir / "watcher.pid"
    session.run("sh", "-c", f"setsid {hooks}/autostart.d/06-watch-config >/dev/null 2>&1 </dev/null & "
                            f"echo $! >{pid_file}")
    session.wait(lambda: "watching" in ((session.dir / "state/scottland/watch-config.log").read_text()
                                        if (session.dir / "state/scottland/watch-config.log").exists() else ""),
                 timeout=10)
    time.sleep(0.5)  # an intended hold: inotifywait arms after its log line
    try:
        hypr.write_text(hypr.read_text() + '''
hl.config({ input = { touchpad = { scroll_factor = 0.8 } } })
o.window("(Alacritty|kitty|foot)", { scroll_touchpad = 2.5 })
''')
        ok, value = session.wait(lambda: abs(float(global_speed(session) or 0) - 0.8) < 1e-9, timeout=20)
        check("the user's edit reaches the running session (Wayfire's own setting is 0.8)", ok, value)
        got = {name: scroll_over(session, recorders[name], points[name]) for name in recorders}
        check("after the edit, Chromium and the panel scroll at the user's 0.8",
              got["chromium"] == 8.0 and got["panel"] == 8.0, got)
        check("after the edit, foot takes the user's later rule (2.5), Ghostty keeps 0.2",
              got["foot"] == 25.0 and got["com.mitchellh.ghostty"] == 2.0, got)
    finally:
        if pid_file.exists():
            pid = int(pid_file.read_text())
            try:
                os.killpg(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

sys.exit(check.summary())
