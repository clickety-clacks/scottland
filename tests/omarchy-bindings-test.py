#!/usr/bin/env python3
"""Imported Omarchy shortcuts keep Hyprland's binding semantics (adapter-gaps AG09).

Real input: stipc key presses and releases in an isolated headless --omarchy session whose HOME
loads the installed, unchanged Omarchy binding files. Oracle: what the bound commands actually
ran, recorded by stand-ins for voxtype and friends (tests/omarchy_fixture.py).

  SCOTTLAND_HEADLESS_DIR is chosen by the test (build/hl-omarchy-bindings).
  tests/omarchy-bindings-test.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from omarchy_fixture import REPO, Checks, Fixture, Session  # noqa: E402

check = Checks()
root = REPO / "build/omarchy-bindings-fixture"
fixture = Fixture(
    root,
    modules=["default.hypr.bindings.voxtype"],
    recorders=["voxtype", "mark"],
    lua=f'''
-- A user's Lua-function pair on one key: the Lua host must keep both halves.
hl.bind("F8", function() os.execute("{root}/bin/mark fn-press") end)
hl.bind("F8", function() os.execute("{root}/bin/mark fn-release") end, {{ release = true }})
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

sys.exit(check.summary())
