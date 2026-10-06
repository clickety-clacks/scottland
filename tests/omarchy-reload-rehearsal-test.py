#!/usr/bin/env python3
"""Reload rehearsal for the Omarchy adapter (AGENTS.md testing step 4).

A headless --omarchy --widgets session started on an OLDER checkout (its own build and harness),
with windows open, is updated the way dev-install + scottland-reload update a live session:
the session's helper links repointed to this checkout (as dev-install repoints the dev dir), the
new settings metadata registered, the config rebuilt by the new generators, a fresh copy of the
new plugin swapped in over IPC with the widget hand-over mark, and the adapter's reload.d hooks
run (10-hyprshim, 30-lua-host). Checks: it survives, keeps every window where it was, renders
them (screencopy pixels), and the new build works (session-state IPC, F9 press/release through
the regenerated config and the restarted Lua host).

The Hyprland shim is not restarted by a reload (reload.d/10-hyprshim only starts a missing one),
so the rehearsal also records that the old shim keeps serving, then restarts it as a separate
step and checks the new shim and whether a Quickshell Hyprland client keeps its event stream.

  tests/omarchy-reload-rehearsal-test.py OLD_CHECKOUT
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from omarchy_fixture import REPO, Checks, Fixture, Session, pixel, screenshot  # noqa: E402

old = Path(sys.argv[1]).resolve()
check = Checks()
root = REPO / "build/omarchy-rehearsal-fixture"
mark = f"{root}/bin/mark"
# The old harness has no SCOTTLAND_TEST_PATH, so the shortcuts name their recorder by path.
fixture = Fixture(root, recorders=["mark"], lua=f'''
o.bind("F9", "Start (push-to-talk)", "{mark} start")
o.bind("F9", "Stop (push-to-talk)", "{mark} stop", {{ release = true }})
''')
COLORS = {"one": (0xC0, 0x30, 0x30), "two": (0x30, 0xC0, 0x30), "three": (0x30, 0x30, 0xC0)}
runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
session_xml = old / "core/plugin/metadata/scottland.xml"
saved_xml = REPO / "build/rehearsal-old-scottland.xml"
shutil.copy(session_xml, saved_xml)
hooks = old / "build/hooks"


def views(session):
    reply = session.ipc("window-rules/list-views")
    return reply if isinstance(reply, list) else []


def windows(session):
    return {v["app-id"]: v for v in views(session)
            if v.get("app-id", "").startswith("org.scottland.SolidColor.") and v.get("mapped")}


def shim_pid(session):
    signature = next((line.split("=", 1)[1] for line in
                      (runtime / "scottland" / f"{session.display}.env").read_bytes().decode().split("\0")
                      if line.startswith("HYPRLAND_INSTANCE_SIGNATURE=")), "")
    lock = runtime / "hypr" / signature / "hyprland.lock"
    return lock.read_text().splitlines()[0] if lock.exists() else ""


def rendered(session, name, placed):
    shot = screenshot(session, name)
    if not shot:
        return {}
    return {app: pixel(shot, int(v["geometry"]["x"] + v["geometry"]["width"] / 2),
                       int(v["geometry"]["y"] + v["geometry"]["height"] / 2))
            for app, v in placed.items()}


def pressed_f9():
    before = len(fixture.calls())
    session.key("KEY_F9", True)
    ok_press, _ = fixture.wait_calls(lambda c: ("mark", "start") in c[before:], timeout=3)
    session.key("KEY_F9", False)
    ok_release, calls = fixture.wait_calls(lambda c: ("mark", "stop") in c[before:], timeout=5)
    return ok_press, ok_release, calls[before:]


try:
    with Session(fixture, "hl-omarchy-rehearsal", extra_args=["--widgets"], repo=old) as session:
        check("old build's session starts with its shim and Lua host",
              session.wait_shim()[0] and session.wait_lua_host()[0])
        for name, rgb in COLORS.items():
            session.run("sh", "-c", f"python3 {REPO}/tests/solid-color-app.py {name} "
                                    f"'#{bytes(rgb).hex()}' >/dev/null 2>&1 </dev/null &")
        ok, _ = session.wait(lambda: len(windows(session)) == 3, timeout=30)
        check("three windows open", ok)
        # Setup, not the behavior under test: apart, so each window's center shows its own color.
        for (x, y), view in zip(((0, 0), (640, 0), (320, 320)), windows(session).values()):
            session.ipc("window-rules/configure-view", {"id": view["id"], "geometry": {
                "x": x, "y": y, "width": 600, "height": 400}})
        time.sleep(1.0)  # an intended hold: moves and scaling settle
        before = {a: v for a, v in windows(session).items() if not a.endswith(".zero")}
        before_ids = {v["id"] for v in views(session)}
        before_pixels = rendered(session, "rehearsal-before", before)
        press, release, calls = pressed_f9()
        check("before: the old build loses F9's press half (the AG09 bug), keeps its release",
              not press and release, calls)
        old_shim = shim_pid(session)
        events = REPO / "build/rehearsal-hyprland-events.log"
        events.unlink(missing_ok=True)
        probe = session.spawn(f"exec qs -p {REPO}/tests/hyprland-events-probe.qml", events)
        ok, _ = session.wait(lambda: events.exists() and "probe ready" in events.read_text(), timeout=15)
        check("a Quickshell Hyprland client is connected to the old shim", ok)
        zero = session.spawn(f"exec python3 {REPO}/tests/solid-color-app.py zero '#404040'")
        ok, _ = session.wait(lambda: "event openwindow" in events.read_text(), timeout=10)
        check("that client receives the old shim's events (openwindow)", ok)
        session.terminate(zero)
        session.wait(lambda: len(windows(session)) == 3, timeout=20)

        # --- The reload, as dev-install + scottland-reload do it. ---
        subprocess.run(["make", "--no-print-directory", "hooks", f"HOOKS_DIR={hooks}"], cwd=REPO,
                       check=True, capture_output=True)
        shutil.copy(REPO / "core/plugin/metadata/scottland.xml", session_xml)
        session.ipc("wayfire/reload-config-metadata")
        config = session.dir / "wayfire.ini"
        hooks_line = next(l for l in config.read_text().splitlines() if l.startswith("scottland_hooks = "))
        edit = session.dir / "rehearsal-edit.sed"
        edit.write_text("s/^plugins = \\\\$/plugins = stipc \\\\/\n"
                        "s#^scottland_hooks = .*#" + hooks_line.replace("&", "\\&").replace("#", "\\#") + "#\n")
        rebuilt = session.run("env", f"SCOTTLAND_CONFIG_OUTPUT={config}", f"SCOTTLAND_CONFIG_EDIT={edit}",
                              str(hooks / "libexec/scottland-build-config"), timeout=60)
        check("the new generators rebuild the session's config", rebuilt.returncode == 0, rebuilt.stderr)
        reloading = runtime / "scottland" / f"{session.display}.reloading"
        reloading.touch()
        fresh = REPO / f"build/libscottland-rehearsal-{time.time_ns()}.so"
        shutil.copy(REPO / "build/libscottland.so", fresh)
        plugins = session.ipc("wayfire/get-config-option", {"option": "core/plugins"})["value"]
        swap = session.ipc("wayfire/set-config-options", {"core/plugins": " ".join(
            str(fresh) if p == "scottland" or "/libscottland" in p else p for p in plugins.split())})
        time.sleep(3)  # an intended hold: the plugin swap and widget hand-over settle
        reloading.unlink(missing_ok=True)
        check("plugin swap accepted", "error" not in swap, swap)
        for hook in ("10-hyprshim", "30-lua-host"):
            result = session.run(str(hooks / "reload.d" / hook), timeout=30)
            check(f"reload.d/{hook} succeeds", result.returncode == 0, result.stderr)

        # --- After. ---
        state = session.ipc("scottland/session-state")
        check("after: the new plugin answers (scottland/session-state)", state.get("locked") is False, state)
        check("after: every view is still there", {v["id"] for v in views(session)} >= before_ids,
              (before_ids, {v["id"] for v in views(session)}))
        after = {a: v for a, v in windows(session).items() if not a.endswith(".zero")}
        check("after: no window moved", {a: v["geometry"] for a, v in after.items()} ==
              {a: v["geometry"] for a, v in before.items()})
        after_pixels = rendered(session, "rehearsal-after", after)
        check("before: each window renders its color (screencopy)",
              all(before_pixels.get(f"org.scottland.SolidColor.{n}") == c for n, c in COLORS.items()),
              before_pixels)
        check("after: each window still renders its color (screencopy)",
              all(after_pixels.get(f"org.scottland.SolidColor.{n}") == c for n, c in COLORS.items()),
              after_pixels)
        session.wait_lua_host()
        press, release, calls = pressed_f9()
        check("after: F9 press and release both run (new importer and Lua host)", press and release, calls)

        # The shim: a reload keeps the old one serving.
        check("after: the reload left the old shim running", shim_pid(session) == old_shim,
              (old_shim, shim_pid(session)))
        target = REPO / "build/rehearsal-long-string"
        target.unlink(missing_ok=True)
        session.hyprctl("dispatch", f"hl.dsp.exec_cmd([[touch {target}]])")
        time.sleep(1.0)  # an intended hold: the old shim must not run it
        check("after: shim fixes wait for a shim restart (old shim ignores [[long strings]])",
              not target.exists())

        # A separate step: restart the shim, as a new session would start the new one.
        subprocess.run(["kill", old_shim])
        session.wait(lambda: shim_pid(session) != old_shim or not
                     Path(f"/proc/{old_shim}").exists(), timeout=5)
        result = session.run(str(hooks / "reload.d/10-hyprshim"), timeout=30)
        check("shim restart: reload.d/10-hyprshim starts the new shim", result.returncode == 0 and
              session.wait_shim()[0], result.stderr)
        session.hyprctl("dispatch", f"hl.dsp.exec_cmd([[touch {target}]])")
        check("shim restart: the new shim runs [[long strings]]", session.wait(target.exists)[0])
        locked = session.run("omarchy-hyprland-session-locked").returncode
        check("shim restart: lock state answers unlocked (exit 1)", locked == 1, locked)
        mark_events = len(events.read_text().splitlines())
        session.run("sh", "-c", f"python3 {REPO}/tests/solid-color-app.py four '#808080' "
                                ">/dev/null 2>&1 </dev/null &")
        ok, _ = session.wait(lambda: "event openwindow" in "\n".join(events.read_text().splitlines()[mark_events:]),
                             timeout=10)
        print(f"FINDING  Quickshell Hyprland client after a shim restart: "
              f"{'still receives events' if ok else 'receives no events (does not reconnect)'}",
              flush=True)
        (REPO / "build/rehearsal-finding.json").write_text(json.dumps({"events_after_shim_restart": ok}))
        session.terminate(probe)
finally:
    shutil.copy(saved_xml, session_xml)
    for leftover in (REPO / "build").glob("libscottland-rehearsal-*.so"):
        leftover.unlink()
    subprocess.run(["make", "--no-print-directory", "test-hooks"], cwd=old, capture_output=True)

sys.exit(check.summary())
