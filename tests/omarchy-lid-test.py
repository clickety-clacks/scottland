#!/usr/bin/env python3
"""Omarchy's lid switch bindings run in Scottland (adapter-gaps AG04).

Isolated headless --omarchy session loading the installed, unchanged utilities.lua
(switch:on:Lid Switch -> omarchy-system-lid-close, switch:off:Lid Switch ->
omarchy-hyprland-monitor-clamshell) plus a user binding on either change. Input: core's
test-only virtual switch device (scottland/test-switch), which takes Wayfire's own switch path;
only libinput is bypassed. Oracle: which stand-in commands ran. (What the clamshell reconcile does
to the displays is tests/omarchy-monitors-test.py.) The lid-close command must also
run on the lock screen (a real ext-session-lock client), as Omarchy marks it locked; a switch
binding without locked must not.

  tests/omarchy-lid-test.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from omarchy_fixture import REPO, Checks, Fixture, Session, pixel, screenshot  # noqa: E402

check = Checks()
root = REPO / "build/omarchy-lid-fixture"
fixture = Fixture(
    root, modules=["default.hypr.bindings.utilities"],
    recorders=["omarchy-system-lid-close", "omarchy-hyprland-monitor-clamshell", "mark"],
    lua=f'o.bind("switch:Lid Switch", nil, "{root}/bin/mark lid-changed")\n')


def lid(session, closed):
    return session.ipc("scottland/test-switch", {"device": "Lid Switch", "state": closed})


def count(name, args=None):
    return len([c for c in fixture.calls() if c[0] == name and (args is None or c[1] == args)])


with Session(fixture, "hl-omarchy-lid") as session:
    check("shim answers", session.wait_shim()[0])
    reply = lid(session, True)
    ok, calls = fixture.wait_calls(lambda c: ("omarchy-system-lid-close", "") in c)
    check("closing the lid runs omarchy-system-lid-close", ok, (reply, calls))
    ok, calls = fixture.wait_calls(lambda c: ("mark", "lid-changed") in c)
    check("a switch binding for either change runs on close", ok, calls)

    lid(session, False)
    ok, calls = fixture.wait_calls(lambda c: c.count(("mark", "lid-changed")) == 2)
    check("a switch binding for either change runs on open", ok, calls)
    time.sleep(0.3)  # an intended hold: a wrong extra run would show up here
    check("opening the lid does not run the lid-close command", count("omarchy-system-lid-close") == 1,
          fixture.calls())
    report = (session.dir / "state/scottland/omarchy-overrides.txt").read_text()
    ok, calls = fixture.wait_calls(lambda c: c.count(("omarchy-hyprland-monitor-clamshell", "")) == 1)
    check("opening the lid runs Omarchy's clamshell reconcile (translated since 10-06), unreported",
          ok and "Lid Switch (off)" not in report,
          (calls, [line for line in report.splitlines() if "Lid Switch" in line]))

    # Locked, as Omarchy marks it: lid close still runs on the lock screen. The user's binding
    # has no locked flag, so it does not run there (as in Hyprland).
    lock = session.spawn(f"exec qs -p {REPO}/tests/session-lock-fixture.qml")
    ok, _ = session.wait(lambda: (lambda shot: shot and pixel(shot, 10, 10) == (255, 0, 255))(
        screenshot(session, "lid-locked")), timeout=15, interval=0.3)
    check("session locked by a real lock client", ok)
    lid(session, True)
    ok, calls = fixture.wait_calls(lambda c: c.count(("omarchy-system-lid-close", "")) == 2)
    check("closing the lid on the lock screen still runs omarchy-system-lid-close", ok, calls)
    time.sleep(0.3)  # an intended hold: a wrong run would show up here
    check("the user's unlocked-only switch binding does not run on the lock screen",
          count("mark", "lid-changed") == 2, fixture.calls())
    session.tap("KEY_ENTER")
    session.terminate(lock)

sys.exit(check.summary())
