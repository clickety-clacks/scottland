#!/usr/bin/env python3
"""Omarchy's touchpad scroll speeds carry over to Scottland (adapter-gaps AG22, AG07 scroll part).

Isolated headless --omarchy session whose HOME loads the installed, unchanged
default/hypr/input.lua: touchpad scroll_factor 0.4, scroll_touchpad 1.5 for Alacritty/kitty/foot,
0.2 for Ghostty. Input: core's virtual touchpad two-finger scroll through Wayfire's input path
(only libinput bypassed). Oracle: the wl_pointer.axis values each client received
(tests/axis-recorder.c) for app-ids foot, com.mitchellh.ghostty and chromium, and a layer-shell
panel (like the Ask plugin). A user's rules on initial_class: a client that maps as
org.example.Initial and changes its own app-id to org.example.Changed when clicked (a stipc
button press) gets the speed of the rules matching both its initial and current app-id, last rule
winning, as in Hyprland. Then the user's own Hyprland config changes both speeds, and the
session's config watcher (autostart.d/06-watch-config) carries the change over live.

  tests/omarchy-scroll-speed-test.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from omarchy_fixture import REPO, Checks, Fixture, Recorder, Session  # noqa: E402

SENT = 10.0
check = Checks()
fixture = Fixture(REPO / "build/omarchy-scroll-fixture", modules=["default.hypr.input"], lua='''
o.window({ initial_class = "org[.]example[.]Initial" }, { scroll_touchpad = 1.5 })
o.window({ initial_class = "org[.]example[.]Initial", class = "org[.]example[.]Changed" }, { scroll_touchpad = 2.5 })
''')
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
    recorders["initial"] = Recorder(session, "org.example.Initial", "c030c0", new_app_id="org.example.Changed")
    ok, _ = session.wait(lambda: all(view(session, app) for app in [*apps, "org.example.Initial"]) and
                         all("ready" in r.lines() for r in recorders.values()), timeout=20)
    if not check("recorders map (foot, Ghostty, Chromium, a panel, an app-id changer)", ok):
        sys.exit(check.summary())
    for (x, y), app in zip(((20, 380), (440, 380), (860, 380), (860, 40)), [*apps, "org.example.Initial"]):
        session.ipc("window-rules/configure-view", {"id": view(session, app)["id"], "geometry": {
            "x": x, "y": y, "width": 400, "height": 300}})
    time.sleep(1.0)  # an intended hold: moves and scaling settle
    points = {app: center(view(session, app)) for app in apps}
    points["panel"] = (640, 100)
    points["initial"] = center(view(session, "org.example.Initial"))
    initial_id = view(session, "org.example.Initial")["id"]

    got = {name: scroll_over(session, recorders[name], points[name]) for name in recorders}
    check("Omarchy's global touchpad speed 0.4 applies to Chromium", got["chromium"] == 4.0, got)
    check("...and to a shell panel (layer surface, like Ask)", got["panel"] == 4.0, got)
    check("foot scrolls at Omarchy's terminal speed 1.5", got["foot"] == 15.0, got)
    check("Ghostty scrolls at Omarchy's 0.2", got["com.mitchellh.ghostty"] == 2.0, got)
    check("a window that mapped as org.example.Initial takes the initial_class rule (1.5)",
          got["initial"] == 15.0, got)
    session.ipc("stipc/move_cursor", {"x": points["initial"][0], "y": points["initial"][1]})
    session.ipc("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "press"})
    session.ipc("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "release"})
    ok, changed = session.wait(lambda: view(session, "org.example.Changed"), timeout=10)
    check("setup: the clicked client changed its app-id (same window)",
          ok and changed["id"] == initial_id, changed)
    changed_speed = scroll_over(session, recorders["initial"], points["initial"])
    check("after its app-id changes, the later rule on initial_class + class applies (2.5)",
          changed_speed == 25.0, changed_speed)
    del recorders["initial"]

    # The user's own overrides, edited while the session runs: the config watcher carries them.
    hooks = REPO / "build/hooks"
    log = session.dir / "state/scottland/watch-config.log"
    watcher = session.spawn(f"exec {hooks}/autostart.d/06-watch-config")
    session.wait(lambda: "watching" in (log.read_text() if log.exists() else ""), timeout=10)
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
        # The watcher and the inotifywait it runs belong to this session.
        session.terminate(watcher, *session.owned("inotifywait"))

sys.exit(check.summary())
