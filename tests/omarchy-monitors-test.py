#!/usr/bin/env python3
"""Omarchy's monitor config and its clamshell script run unmodified in Scottland (AG03, AG04).

Isolated headless --omarchy session loading the installed, unchanged Omarchy files: the lid
bindings (default/hypr/bindings/utilities.lua), the toggles loader (default/hypr/toggles.lua) and a
user monitors.lua with the stock catch-all plus rules for a laptop panel (eDP-1: scale 1.5 at
1920x0) and an external display (DP-1 at 0x0). The lid runs the real omarchy-system-lid-close and
omarchy-hyprland-monitor-clamshell, which talk to the Hyprland shim with hyprctl.

Setup the test supplies (none of it is the code under test):
  - a laptop with a dock: the first virtual output is named eDP-1 and outputs added later DP-1
    (tests/output-names.c renames them as wlroots creates them); docking and undocking add and
    remove DP-1 through Wayfire's own headless-output IPC;
  - the lid: core's test-only virtual switch (scottland/test-switch) takes Wayfire's own switch
    path (only libinput is bypassed), and /proc/acpi/button/lid/LID0/state, which Omarchy's
    omarchy-hw-laptop-closed reads, is a file in the compositor's own mount namespace that the test
    writes with each switch event;
  - which connectors the kernel lists as connected: Omarchy's own OMARCHY_DRM_PATH override;
  - stand-ins: omarchy-system-lock and notifications record instead of acting; the two lid scripts
    are wrapped to record when they finished (the real, unchanged script runs inside).
Oracles: Wayfire's own output list (stock ipc-rules, not the shim or core's output tool) for
which outputs are on and their layout geometry (scale shows as logical size), a screencopy of the
panel (grim -o) for whether it renders, the toggle files Omarchy's scripts write, and the shim's
`monitors all` metadata compared with Wayfire's geometry.

  tests/omarchy-monitors-test.py
"""
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from omarchy_fixture import OMARCHY, REPO, Checks, Fixture, Session, read_ppm  # noqa: E402

check = Checks()
root = REPO / "build/omarchy-monitors-fixture"
MONITORS = '''local omarchy_gdk_scale = 2
local omarchy_monitor_scale = "auto"

hl.env("GDK_SCALE", tostring(omarchy_gdk_scale))
hl.monitor({ output = "", mode = "preferred", position = "auto", scale = omarchy_monitor_scale })
hl.monitor({ output = "DP-1", mode = "preferred", position = "0x0", scale = 1 })
hl.monitor({ output = "eDP-1", mode = "preferred", position = "1920x0", scale = 1.5 })
'''
PANEL = {"x": 1920, "y": 0, "width": 853, "height": 480}  # 1280x720 at scale 1.5
DOCK = {"x": 0, "y": 0, "width": 1920, "height": 1080}


def finished(name):
    """A stand-in that runs the real, unchanged Omarchy script and records that it finished."""
    return (f'"{OMARCHY}/bin/{name}" "$@"; status=$?\n'
            f"printf '%s\\t%s\\n' {name}-done \"$status\" >>'{root}/calls.log'\nexit $status")


fixture = Fixture(
    root, modules=["default.hypr.bindings.utilities"],
    recorders=["omarchy-system-lock", "omarchy-notification-send"],
    commands={"omarchy-system-lid-close": finished("omarchy-system-lid-close"),
              "omarchy-hyprland-monitor-clamshell": finished("omarchy-hyprland-monitor-clamshell")},
    lua=f'''package.path = os.getenv("HOME") .. "/.config/?.lua;" .. package.path
require("hypr.monitors")
require("default.hypr.toggles")
''')
(fixture.home / ".config/hypr/monitors.lua").write_text(MONITORS)
state = fixture.home / ".local/state/omarchy"
toggles = state / "toggles/hypr"
toggles.mkdir(parents=True)

# The laptop's hardware as Omarchy reads it: the lid state and the connectors the kernel lists.
lid_state = root / "lid-state"
lid_state.write_text("state:      open\n")
drm = root / "drm"
for connector in ("card0-eDP-1", "card0-DP-1"):
    (drm / connector).mkdir(parents=True)
(drm / "card0-eDP-1/status").write_text("connected\n")
(drm / "card0-DP-1/status").write_text("disconnected\n")
names = REPO / "build/output-names.so"
subprocess.run(["cc", "-shared", "-fPIC", "-O1", "-o", str(names), str(REPO / "tests/output-names.c"),
                "-ldl"], check=True)
wrap = root / "wrap-compositor"
wrap.write_text(f'''#!/bin/sh
# The compositor in its own mount namespace, where the lid state file is the test's.
exec bwrap --dev-bind / / --tmpfs /proc/acpi --dir /proc/acpi/button/lid/LID0 \\
  --bind '{lid_state}' /proc/acpi/button/lid/LID0/state \\
  env LD_PRELOAD='{names}' SCOTTLAND_TEST_OUTPUT_NAMES=HEADLESS-1=eDP-1,HEADLESS-2=DP-1,HEADLESS-3=DP-1 \\
  OMARCHY_DRM_PATH='{drm}' "$@"
''')
wrap.chmod(0o755)


def count(name, args=None):
    return len([c for c in fixture.calls() if c[0] == name and (args is None or c[1] == args)])


with Session(fixture, "hl-omarchy-monitors", env={"SCOTTLAND_TEST_WRAP": str(wrap)}) as session:
    # One state directory, as on a real system: the session's (where the Lua config looks for
    # toggles) is the fixture HOME's (where Omarchy's shell scripts write them).
    (session.dir / "state/omarchy").symlink_to(state)

    def outputs():
        reply = session.ipc("window-rules/list-outputs")
        return {o["name"]: {k: int(v) for k, v in o["geometry"].items()} for o in reply} \
            if isinstance(reply, list) else {}

    def monitors(*args):
        result = session.hyprctl("-j", "monitors", *args)
        try:
            return {m["name"]: m for m in json.loads(result.stdout)}
        except ValueError:
            return {}

    def capture(output, name):
        path = session.dir.parent / f"{session.dir.name}-{name}.ppm"
        path.unlink(missing_ok=True)
        result = session.run("timeout", "5", "grim", "-t", "ppm", "-o", output, str(path))
        return read_ppm(path) if result.returncode == 0 and path.exists() else None

    def lid(closed):
        """The lid as the hardware reports it: the ACPI state file, then the switch event."""
        with open(lid_state, "r+") as handle:  # in place: the compositor's namespace binds this file
            handle.write("state:      closed\n" if closed else "state:      open  \n")
        return session.ipc("scottland/test-switch", {"device": "Lid Switch", "state": closed})

    def dock(connected):
        (drm / "card0-DP-1/status").write_text("connected\n" if connected else "disconnected\n")
        if connected:
            return session.ipc("wayfire/create-headless-output", {"width": 1920, "height": 1080})
        return session.ipc("wayfire/destroy-headless-output", {"output": "DP-1"})

    check("shim answers", session.wait_shim()[0])
    config = session.config()
    check("lid open imports Omarchy's unmodified clamshell reconcile as a switch binding",
          "switch_command_omarchy_" in config and "= omarchy-hyprland-monitor-clamshell" in config,
          [line for line in config.splitlines() if "clamshell" in line])

    ok, seen = session.wait(lambda: outputs().get("eDP-1") == PANEL, timeout=10)
    check("the monitors.lua rule for eDP-1 applies: scale 1.5 at 1920x0", ok, seen)
    panel = monitors("all").get("eDP-1", {})
    check("monitors all reports the panel's real metadata (scale, mode pixels, position, refresh)",
          panel.get("scale") == 1.5 and (panel.get("width"), panel.get("height")) == (1280, 720)
          and (panel.get("x"), panel.get("y")) == (1920, 0) and panel.get("disabled") is False
          and panel.get("transform") == 0 and "refreshRate" in panel, panel)

    # A live rule (hyprctl eval) applies now and ends at the next reload, as in Hyprland.
    reply = session.hyprctl(
        "eval", 'hl.monitor({ output = "eDP-1", mode = "preferred", position = "1920x0", scale = 1.25 })')
    ok, seen = session.wait(lambda: outputs().get("eDP-1", {}).get("width") == 1024, timeout=10)
    check("hyprctl eval hl.monitor sets the panel's scale live", reply.returncode == 0 and ok,
          (reply.stdout, reply.stderr, seen))
    check("monitors reports the live scale", monitors().get("eDP-1", {}).get("scale") == 1.25,
          monitors().get("eDP-1"))
    reply = session.hyprctl("reload")
    check("hyprctl reload drops the live rule: the configured scale is back when it answers",
          reply.returncode == 0 and outputs().get("eDP-1") == PANEL, (reply.stdout, outputs()))

    dock(True)
    ok, seen = session.wait(lambda: outputs().get("DP-1") == DOCK and outputs().get("eDP-1") == PANEL,
                            timeout=10)
    check("docked: DP-1 at 0x0 by its rule, the panel unchanged", ok, seen)

    # Docked lid close: the real omarchy-system-lid-close (no lock, docked) runs the clamshell
    # script, which writes its toggle and reloads; the panel goes off.
    lid(True)
    ok, calls = fixture.wait_calls(lambda c: ("omarchy-system-lid-close-done", "0") in c, timeout=30)
    check("docked lid close runs Omarchy's lid-close and clamshell scripts", ok, calls)
    ok, seen = session.wait(lambda: "eDP-1" not in outputs(), timeout=10)
    check("docked lid close turns the laptop panel off", ok, seen)
    check("the external display stays on, in place", outputs().get("DP-1") == DOCK, outputs())
    check("a closed, docked lid does not lock the session", count("omarchy-system-lock") == 0,
          fixture.calls())
    check("the panel produces no frames (screencopy of eDP-1 fails)", capture("eDP-1", "closed") is None)
    listed = monitors("all").get("eDP-1", {})
    check("monitors all lists the panel as disabled; monitors leaves it out",
          listed.get("disabled") is True and "eDP-1" not in monitors(), (listed, list(monitors())))
    flag = toggles / "internal-monitor-clamshell.lua"
    check("Omarchy's clamshell toggle holds the panel off and the scale it had is remembered",
          flag.exists() and (toggles / "internal-monitor-scale").read_text().strip() == "1.5",
          [p.name for p in toggles.iterdir()])

    lid(False)
    ok, calls = fixture.wait_calls(
        lambda c: c.count(("omarchy-hyprland-monitor-clamshell-done", "0")) == 2, timeout=30)
    check("lid open runs the clamshell script", ok, calls)
    ok, seen = session.wait(lambda: outputs().get("eDP-1") == PANEL, timeout=10)
    check("lid open turns the panel back on at its scale (1.5) and position (1920x0)", ok, seen)
    shot = capture("eDP-1", "open")
    check("the panel produces frames again (screencopy of eDP-1 returns its 1280x720 mode)",
          shot is not None and shot[:2] == (1280, 720), shot and shot[:2])
    check("the clamshell toggle is gone", not flag.exists())
    panel = monitors("all").get("eDP-1", {})
    check("monitors all reports it enabled, at scale 1.5 and DPMS on",
          panel.get("disabled") is False and panel.get("scale") == 1.5 and panel.get("dpmsStatus") is True,
          panel)

    # A laptop display the user turned off stays off across the lid (Omarchy's manual toggle,
    # Super+Ctrl+Delete's command, run here directly as setup).
    session.run("omarchy-hyprland-monitor-internal", "off")
    ok, seen = session.wait(lambda: "eDP-1" not in outputs(), timeout=15)
    check("setup: the user turns the laptop display off", ok, seen)
    lid(True)
    ok, calls = fixture.wait_calls(lambda c: c.count(("omarchy-system-lid-close-done", "0")) == 2, timeout=30)
    check("lid close with the display turned off by hand runs the scripts", ok, calls)
    check("they leave the user's choice alone (no clamshell toggle)", not flag.exists() and "eDP-1" not in outputs(),
          ([p.name for p in toggles.iterdir()], outputs()))
    lid(False)
    ok, calls = fixture.wait_calls(
        lambda c: c.count(("omarchy-hyprland-monitor-clamshell-done", "0")) == 4, timeout=30)
    check("lid open runs the clamshell script", ok, calls)
    check("lid open keeps the display the user turned off off while docked", "eDP-1" not in outputs(), outputs())
    session.run("omarchy-hyprland-monitor-internal", "on")
    ok, seen = session.wait(lambda: outputs().get("eDP-1") == PANEL, timeout=15)
    check("setup: the user turns it back on, at its configured scale and position", ok, seen)

    # Undocked lid close: lock (a stand-in here), and the panel stays on.
    dock(False)
    ok, seen = session.wait(lambda: "DP-1" not in outputs(), timeout=10)
    check("undocked: DP-1 removed", ok, seen)
    locks = count("omarchy-system-lock")
    lid(True)
    ok, calls = fixture.wait_calls(lambda c: c.count(("omarchy-system-lid-close-done", "0")) == 3, timeout=30)
    check("undocked lid close runs the scripts and locks", ok and count("omarchy-system-lock") == locks + 1, calls)
    check("undocked lid close leaves the panel on", "eDP-1" in outputs(), outputs())
    lid(False)
    ok, calls = fixture.wait_calls(
        lambda c: c.count(("omarchy-hyprland-monitor-clamshell-done", "0")) == 6, timeout=30)
    check("undocked lid open: the panel is still on", ok and "eDP-1" in outputs(), (calls, outputs()))

sys.exit(check.summary())
