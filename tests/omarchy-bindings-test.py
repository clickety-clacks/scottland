#!/usr/bin/env python3
"""Imported Omarchy shortcuts keep Hyprland's binding semantics (adapter-gaps AG09, AG10).

Real input: stipc key presses and releases in an isolated headless --omarchy session whose HOME
loads the installed, unchanged Omarchy binding files (voxtype.lua, media.lua). Oracle: what the
bound commands actually ran, recorded by stand-ins for voxtype and friends
(tests/omarchy_fixture.py). The lock-screen cases use a real ext-session-lock client
(tests/session-lock-fixture.qml); the lock is confirmed by its magenta surface in a screencopy.

  SCOTTLAND_HEADLESS_DIR is chosen by the test (build/hl-omarchy-bindings).
  tests/omarchy-bindings-test.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from omarchy_fixture import REPO, Checks, Fixture, Session, pixel, screenshot  # noqa: E402

check = Checks()
root = REPO / "build/omarchy-bindings-fixture"
fixture = Fixture(
    root,
    modules=["default.hypr.bindings.voxtype", "default.hypr.bindings.media"],
    recorders=["voxtype", "mark", "omarchy-audio-output-volume"],
    lua=f'''
-- A user's Lua-function pair on one key: the Lua host must keep both halves.
hl.bind("F8", function() os.execute("{root}/bin/mark fn-press") end)
hl.bind("F8", function() os.execute("{root}/bin/mark fn-release") end, {{ release = true }})
-- A release chord with a modifier (Hyprland bindr on SUPER + F7).
o.bind("SUPER + F7", "Modified release", "mark mod-release", {{ release = true }})
''')


def ran(name, args):
    return lambda calls: (name, args) in calls


with Session(fixture, "hl-omarchy-bindings") as session:
    check("Lua host is running", session.wait_lua_host()[0])

    # AG09: stock F9 push-to-talk, press then release.
    session.key("KEY_F9", True)
    ok, calls = fixture.wait_calls(ran("voxtype", "record start"))
    check("F9 press runs voxtype record start", ok, calls)
    check("F9 press does not stop recording", ("voxtype", "record stop") not in calls, calls)
    session.key("KEY_F9", False)
    ok, calls = fixture.wait_calls(ran("voxtype", "record stop"))
    check("F9 release runs voxtype record stop", ok, calls)
    start, stop = ("voxtype", "record start"), ("voxtype", "record stop")
    check("start comes before stop",
          start in calls and stop in calls and calls.index(start) < calls.index(stop), calls)

    # AG09 in the Lua host: a function press and a function release on one key.
    session.key("KEY_F8", True)
    ok, calls = fixture.wait_calls(ran("mark", "fn-press"))
    check("Lua-function press runs its own function", ok, calls)
    session.key("KEY_F8", False)
    ok, calls = fixture.wait_calls(ran("mark", "fn-release"))
    check("Lua-function release runs its own function", ok, calls)

    # AG10: a release chord with a modifier runs on release, not on press.
    session.key("KEY_LEFTMETA", True)
    session.key("KEY_F7", True)
    ok, calls = fixture.wait_calls(ran("mark", "mod-release"), timeout=1.0)
    check("Super+F7 release binding does not run on press", not ok, calls)
    session.key("KEY_F7", False)
    ok, calls = fixture.wait_calls(ran("mark", "mod-release"))
    check("Super+F7 release binding runs on release", ok, calls)
    session.key("KEY_LEFTMETA", False)
    time.sleep(0.3)  # an intended hold: a late duplicate would show up here
    check("Super+F7 release runs once", fixture.calls().count(("mark", "mod-release")) == 1,
          fixture.calls())

    # AG10: Omarchy's media keys are repeating and locked. Held, a repeating key repeats.
    volume_up = ("omarchy-audio-output-volume", "raise")
    mute = ("omarchy-audio-output-volume", "mute-toggle")
    session.key("KEY_VOLUMEUP", True)
    ok, calls = fixture.wait_calls(lambda c: c.count(volume_up) >= 3, timeout=5)
    check("held volume-up repeats", ok, calls.count(volume_up))
    session.key("KEY_VOLUMEUP", False)
    settled = fixture.calls().count(volume_up)
    time.sleep(0.5)  # an intended hold: repeats must stop with the key
    check("volume-up stops repeating on release", fixture.calls().count(volume_up) == settled,
          (settled, fixture.calls().count(volume_up)))
    session.key("KEY_MUTE", True)
    ok, calls = fixture.wait_calls(lambda c: mute in c)
    time.sleep(1.0)  # an intended hold past the repeat delay
    session.key("KEY_MUTE", False)
    check("held mute (not repeating) runs once", fixture.calls().count(mute) == 1,
          fixture.calls().count(mute))

    # Lock the session with a real lock client; the lock surface must cover the screen.
    lock = session.harness("run", "sh", "-c",
                           f"exec qs -p {REPO}/tests/session-lock-fixture.qml >/dev/null 2>&1 &",
                           check=False)
    locked_ok, image = session.wait(
        lambda: (lambda shot: shot if shot and pixel(shot, 10, 10) == (255, 0, 255) else None)(
            screenshot(session, "locked")), timeout=15, interval=0.3)
    check("lock client covers the screen (magenta screencopy)", locked_ok,
          pixel(image, 10, 10) if image else "no capture")

    before = len(fixture.calls())
    session.tap("KEY_F9")
    session.key("KEY_LEFTMETA", True)
    session.tap("KEY_F7")
    session.key("KEY_LEFTMETA", False)
    session.tap("KEY_F8")
    session.key("KEY_VOLUMEUP", True)
    ok, calls = fixture.wait_calls(lambda c: volume_up in c[before:])
    check("locked volume-up runs on the lock screen", ok, calls[before:])
    time.sleep(1.0)  # an intended hold past the repeat delay
    session.key("KEY_VOLUMEUP", False)
    time.sleep(0.3)  # an intended hold: anything late shows up here
    after = fixture.calls()[before:]
    check("locked volume-up runs once while locked (no repeat there)",
          after.count(volume_up) == 1, after)
    check("unlocked-only shortcuts do not run on the lock screen (F9, Super+F7, F8)",
          not [c for c in after if c[0] in ("voxtype", "mark")], after)

    session.tap("KEY_ENTER")
    unlocked, image = session.wait(
        lambda: (lambda shot: shot if shot and pixel(shot, 10, 10) != (255, 0, 255) else None)(
            screenshot(session, "unlocked")), timeout=10, interval=0.3)
    check("Return on the lock surface unlocks", unlocked)
    before = len(fixture.calls())
    session.tap("KEY_F9")
    ok, calls = fixture.wait_calls(lambda c: ("voxtype", "record stop") in c[before:])
    check("shortcuts run again after unlock", ok, calls[before:])

sys.exit(check.summary())
