#!/usr/bin/env python3
"""Lock state and display power are real and reported truthfully (adapter-gaps AG17, AG18).

Isolated headless --omarchy session (fixture HOME). Omarchy's own scripts run unchanged against
the shim: omarchy-hyprland-session-locked (exit 0 locked, 1 unlocked) and
omarchy-brightness-display off/on (DPMS dispatches; `on` dispatches only when the shim reports a
display off). The lock is a real ext-session-lock client (tests/session-lock-fixture.qml),
unlocked by pressing Return on it (stipc key) or killed to strand the lock.

Independent oracles: a screencopy client (grim). It cannot capture an output that is powered
off and captures it again once on; while locked it sees the lock surface (magenta) or, after
the lock client died, Wayfire's stand-in, never the window underneath (#3366CC).

  tests/omarchy-lock-power-test.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from omarchy_fixture import REPO, Checks, Fixture, Session, pixel, screenshot  # noqa: E402

WINDOW = (0x33, 0x66, 0xCC)
LOCK = (0xFF, 0x00, 0xFF)
check = Checks()
fixture = Fixture(REPO / "build/omarchy-lock-power-fixture")
lock_qml = REPO / "tests/session-lock-fixture.qml"


def views(session):
    reply = session.ipc("window-rules/list-views")
    return reply if isinstance(reply, list) else []


def captured(session, name, point):
    shot = screenshot(session, name)
    return pixel(shot, *point) if shot else None


def wait_capture(session, name, point, want, timeout=15):
    """Poll screencopy until the pixel at point satisfies want(pixel or None)."""
    return session.wait(lambda: (lambda p: (p,) if want(p) else None)(captured(session, name, point)),
                        timeout=timeout, interval=0.3)


def locked_status(session):
    return session.run("omarchy-hyprland-session-locked").returncode


def start_lock(session):
    session.run("sh", "-c", f"exec qs -p {lock_qml} >/dev/null 2>&1 </dev/null &")


with Session(fixture, "hl-omarchy-lock-power") as session:
    check("shim answers", session.wait_shim()[0])
    session.run("sh", "-c", f"python3 {REPO}/tests/solid-color-app.py lockpower '#3366CC' "
                            ">/dev/null 2>&1 </dev/null &")
    ok, found = session.wait(lambda: [v for v in views(session)
                                      if v.get("app-id") == "org.scottland.SolidColor.lockpower"
                                      and v.get("mapped")], timeout=20)
    if not check("window maps", ok):
        sys.exit(check.summary())
    box = found[0]["geometry"]
    center = (int(box["x"] + box["width"] / 2), int(box["y"] + box["height"] / 2))
    check("window is captured before anything", wait_capture(
        session, "start", center, lambda p: p == WINDOW)[0])
    check("unlocked session reports unlocked (exit 1)", locked_status(session) == 1,
          locked_status(session))

    # AG18: display off and on through Omarchy's own script.
    off = session.run("omarchy-brightness-display", "off")
    ok, _ = wait_capture(session, "dpms-off", center, lambda p: p is None)
    check("omarchy-brightness-display off powers the display off (screencopy fails)",
          off.returncode == 0 and ok, off.returncode)
    on = session.run("omarchy-brightness-display", "on")
    ok, value = wait_capture(session, "dpms-on", center, lambda p: p == WINDOW)
    check("omarchy-brightness-display on powers it back on (window captured again)",
          on.returncode == 0 and ok, (on.returncode, value))

    # AG17: a real lock, reported locked; display power works while locked.
    start_lock(session)
    ok, _ = wait_capture(session, "locked", center, lambda p: p == LOCK)
    check("lock client covers the screen", ok)
    check("locked session reports locked (exit 0)", locked_status(session) == 0, locked_status(session))
    session.run("omarchy-brightness-display", "off")
    ok, _ = wait_capture(session, "locked-off", center, lambda p: p is None)
    check("display powers off while locked", ok)
    session.run("omarchy-brightness-display", "on")
    ok, _ = wait_capture(session, "locked-on", center, lambda p: p == LOCK)
    check("display powers on while locked, still showing the lock", ok)

    session.tap("KEY_ENTER")
    ok, _ = wait_capture(session, "unlocked", center, lambda p: p == WINDOW)
    check("Return unlocks; the window is visible again", ok)
    check("after unlock, reports unlocked (exit 1)", locked_status(session) == 1, locked_status(session))

    # AG17's case worth detecting: the lock client dies and the lock stays.
    start_lock(session)
    ok, _ = wait_capture(session, "locked-again", center, lambda p: p == LOCK)
    check("locked again", ok)
    session.run("pkill", "-9", "-f", "session-lock-fixture.qml")
    ok, value = wait_capture(session, "stranded", center, lambda p: p is not None and p != LOCK)
    check("lock client gone: Wayfire's stand-in replaces the lock surface", ok, value)
    check("stranded lock still hides the window", ok and value[0] != WINDOW, value)
    check("stranded lock reports locked (exit 0)", locked_status(session) == 0, locked_status(session))

    # Recovery as Omarchy does it: a new lock client takes over, then unlocks.
    start_lock(session)
    ok, _ = wait_capture(session, "relocked", center, lambda p: p == LOCK)
    check("a new lock client takes over the stranded lock", ok)
    session.tap("KEY_ENTER")
    ok, _ = wait_capture(session, "recovered", center, lambda p: p == WINDOW)
    check("unlocking it shows the window again", ok)
    check("recovered session reports unlocked (exit 1)", locked_status(session) == 1,
          locked_status(session))
    session.run("pkill", "-f", "session-lock-fixture.qml")

sys.exit(check.summary())
