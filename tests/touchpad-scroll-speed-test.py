#!/usr/bin/env python3
"""Two-finger scroll speed, globally and per app (core L17 and its per-app speeds).

Isolated headless session with core alone (no adapter). Input: core's test-only virtual touchpad
(scottland/test-touchpad "scroll", finger source), whose axis events take Wayfire's own input
path; only libinput is bypassed. Oracle: the wl_pointer.axis values each client actually
received (tests/axis-recorder.c), for toplevels with different app-ids and a layer-shell
surface. Per-app entries are then added to the running session's config file, as an edit or a
regenerated fragment would.

  tests/touchpad-scroll-speed-test.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from omarchy_fixture import REPO, Checks, Fixture, Recorder, Session  # noqa: E402

SENT = 10.0
check = Checks()
fixture = Fixture(REPO / "build/touchpad-scroll-fixture")


def views(session):
    reply = session.ipc("window-rules/list-views")
    return reply if isinstance(reply, list) else []


def view(session, app_id):
    return next((v for v in views(session) if v.get("app-id") == app_id and v.get("mapped")), None)


def scroll_over(session, recorder, point):
    """Point at the surface, scroll SENT px with two fingers; returns what it received."""
    session.ipc("stipc/move_cursor", {"x": point[0], "y": point[1]})
    session.wait(lambda: "enter" in recorder.lines()[-3:] or recorder.lines().count("enter") >
                 recorder.lines().count("leave"), timeout=5)
    since = recorder.mark()
    session.ipc("scottland/test-touchpad", {"event": "scroll", "dy": SENT})
    session.ipc("scottland/test-touchpad", {"event": "scroll", "dy": 0})
    session.wait(lambda: any(l.startswith("stop 0") for l in recorder.lines()[since:]), timeout=5)
    return round(recorder.vertical(since), 3)


def center(v):
    g = v["geometry"]
    return int(g["x"] + g["width"] / 2), int(g["y"] + g["height"] / 2)


with Session(fixture, "hl-touchpad-scroll", omarchy=False) as session:
    fast = Recorder(session, "org.example.Fast", "c03030")
    plain = Recorder(session, "org.example.Plain", "30c030")
    panel = Recorder(session, "panel", "3030c0", layer=True)
    ok, _ = session.wait(lambda: view(session, "org.example.Fast") and view(session, "org.example.Plain")
                         and all("ready" in r.lines() for r in (fast, plain, panel)), timeout=20)
    if not check("recorders map (two windows, one layer surface)", ok):
        sys.exit(check.summary())
    # Setup, not the behavior under test: windows apart, below the top-anchored panel.
    for (x, y), app in (((80, 380), "org.example.Fast"), ((800, 380), "org.example.Plain")):
        session.ipc("window-rules/configure-view", {"id": view(session, app)["id"], "geometry": {
            "x": x, "y": y, "width": 400, "height": 300}})
    time.sleep(1.0)  # an intended hold: moves and scaling settle
    points = {"fast": center(view(session, "org.example.Fast")),
              "plain": center(view(session, "org.example.Plain")), "panel": (640, 100)}

    got = {name: scroll_over(session, rec, points[name])
           for name, rec in (("fast", fast), ("plain", plain), ("panel", panel))}
    check("core alone: every surface scrolls at the shipped 0.2", all(v == 2.0 for v in got.values()), got)

    # Per-app speeds, added to the running session's config.
    config = session.dir / "wayfire.ini"
    config.write_text(config.read_text() + "\n[scottland]\n"
                      "touchpad_scroll_apps_a = org\\.example\\.Fast\ntouchpad_scroll_factor_a = 1.5\n"
                      "touchpad_scroll_apps_b = Plain\ntouchpad_scroll_factor_b = 9\n"
                      "touchpad_scroll_apps_z = org\\.example\\.Fast\ntouchpad_scroll_factor_z = 3\n")
    ok, _ = session.wait(lambda: session.ipc("wayfire/get-config-option", {
        "option": "scottland/touchpad_scroll_factor_z"}).get("value") not in (None, ""), timeout=10)
    check("the session picked up the edited config", ok)
    got = {name: scroll_over(session, rec, points[name])
           for name, rec in (("fast", fast), ("plain", plain), ("panel", panel))}
    check("a matching app scrolls at its own speed (1.5: first entry by name wins)", got["fast"] == 15.0, got)
    check("a regex must match the whole app-id ('Plain' does not match org.example.Plain)",
          got["plain"] == 2.0, got)
    check("a layer surface (panel) keeps the global speed", got["panel"] == 2.0, got)

    session.ipc("wayfire/set-config-options", {"input/touchpad_scroll_speed": 0.4})
    got = {name: scroll_over(session, rec, points[name]) for name, rec in (("fast", fast), ("plain", plain))}
    check("changing the global speed live changes unmatched apps only (4.0), not Fast (15.0)",
          got == {"fast": 15.0, "plain": 4.0}, got)

sys.exit(check.summary())
