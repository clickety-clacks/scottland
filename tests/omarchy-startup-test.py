#!/usr/bin/env python3
"""Hyprland startup handlers run once per Scottland session (adapter-gaps AG20).

Isolated headless --omarchy --hyprland-start session with a fixture HOME whose config uses
Omarchy's own o.exec_on_start / o.launch_on_start helpers (installed helpers.lua, unchanged)
and a raw hl.on("hyprland.start"). Oracle: which stand-in commands actually ran, and how often,
across the session start, a `hyprctl reload` and a Lua host restart (reload.d/30-lua-host, as
scottland-reload runs it).

  tests/omarchy-startup-test.py
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from omarchy_fixture import REPO, Checks, Fixture, Session  # noqa: E402

check = Checks()
root = REPO / "build/omarchy-startup-fixture"
fixture = Fixture(
    root, recorders=["mark", "systemctl", "dbus-update-activation-environment",
                     "omarchy-launch-shell"],
    commands={"uwsm-app": 'shift; exec "$@"'},  # record, then run the app like uwsm-app does
    lua=f'''
local mark = "{root}/bin/mark"
o.exec_on_start(mark .. " exec-on-start")
o.launch_on_start(mark .. " launch-on-start")
hl.on("hyprland.start", function()
  -- The stock autostart's first lines: Scottland's own session hooks already do these.
  hl.exec_cmd("systemctl --user import-environment $(env | cut -d'=' -f 1)")
  hl.exec_cmd("dbus-update-activation-environment --systemd --all")
  hl.exec_cmd("omarchy-launch-shell")
  hl.exec_cmd(mark .. " raw-start")
end)
''')


def count(args):
    return fixture.calls().count(("mark", args))


with Session(fixture, "hl-omarchy-startup", extra_args=["--hyprland-start"]) as session:
    expected = ("exec-on-start", "launch-on-start", "raw-start")
    ok, calls = fixture.wait_calls(lambda c: all(("mark", a) in c for a in expected), timeout=15)
    check("startup handlers ran at session start (exec_on_start, launch_on_start, hl.on)", ok, calls)
    check("launch_on_start went through uwsm-app",
          ("uwsm-app", f"-- {root}/bin/mark launch-on-start") in calls, calls)
    names = [name for name, _ in calls]
    check("environment import left to Scottland's own session hooks",
          "systemctl" not in names and "dbus-update-activation-environment" not in names, calls)
    check("shell launch left to the adapter's 20-omarchy-shell", "omarchy-launch-shell" not in names,
          calls)

    result = session.hyprctl("reload", timeout=60)
    check("hyprctl reload succeeds", result.returncode == 0, result.stdout)
    hooks = REPO / "build/hooks"
    restart = session.run(str(hooks / "reload.d/30-lua-host"), timeout=30)
    check("Lua host restarts (reload.d/30-lua-host)", restart.returncode == 0, restart.stderr)
    ok, _ = session.wait_lua_host()
    time.sleep(2.0)  # an intended hold: a repeat run of the handlers would show up here
    check("handlers ran exactly once across reload and Lua host restart",
          all(count(a) == 1 for a in expected), {a: count(a) for a in expected})

sys.exit(check.summary())
