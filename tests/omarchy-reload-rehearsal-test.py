#!/usr/bin/env python3
"""Reload rehearsal for the Omarchy adapter (AGENTS.md testing step 4).

A headless --omarchy --widgets session started on an OLDER checkout (its own build and harness),
with windows open, is updated the way dev-install + scottland-reload update a live session:
the session's helper links repointed to this checkout (as dev-install repoints the dev dir), the
new settings metadata registered, the config rebuilt by the new generators, a fresh copy of the
new plugin swapped in over IPC with the widget hand-over mark, and the adapter's reload.d hooks
run (10-hyprshim, 30-lua-host, 40-omarchy-shell). Checks: it survives, keeps every window where it
was, renders them (screencopy pixels), the new shim serves requests, and the new build works
(session-state IPC, F9 press/release through the regenerated config and restarted Lua host).

The stock Omarchy shell restart command is a test-owned stand-in here: it records that the adapter
invokes it after the replacement shim is ready and with Scottland's libexec first on PATH. The
separate Quickshell event probe is not the Omarchy shell and records whether a generic client
reconnects after its shim socket closes.

OLD_CHECKOUT is only read: it must be built (make test-hooks), and the session runs from a copy of
it, so the reload repoints the copy's helpers and metadata, never the old checkout's. Everything
the rehearsal writes is under a new build/omarchy-rehearsal-* directory it creates, printed at
the start and kept for inspection. The old shim is signaled only after its PID is pinned and
verified as this session's.

  tests/omarchy-reload-rehearsal-test.py OLD_CHECKOUT
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from omarchy_fixture import REPO, Checks, Fixture, Session, pixel, screenshot  # noqa: E402

source = Path(sys.argv[1]).resolve()
(REPO / "build").mkdir(exist_ok=True)
scratch = Path(tempfile.mkdtemp(prefix="omarchy-rehearsal-", dir=REPO / "build"))
print(f"rehearsal scratch: {scratch}", flush=True)
# The old build, copied: its source tree (git metadata too) and the build outputs its own helper
# links point at, then its own `make hooks` links the copy's build/hooks to the copy.
old = scratch / "old"
shutil.copytree(source, old, symlinks=True, ignore=lambda d, names: ["build"] if Path(d) == source else [])
for link in (source / "build/hooks").rglob("*"):
    target = link.resolve()
    if link.is_symlink() and target.is_file() and target.is_relative_to(source / "build"):
        (old / target.relative_to(source)).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, old / target.relative_to(source))
subprocess.run(["make", "--no-print-directory", "hooks", f"HOOKS_DIR={old}/build/hooks"], cwd=old,
               check=True, capture_output=True)
hooks = old / "build/hooks"
escaped = [str(l) for l in hooks.rglob("*") if l.is_symlink() and l.resolve().is_relative_to(source)]
if not (old / "build/libscottland.so").is_file() or not (hooks / "libexec").is_dir() or escaped:
    sys.exit(f"the copy of {source} is not a built checkout of its own: {escaped or 'no plugin or helpers'}")
check = Checks()
root = scratch / "fixture"
mark = f"{root}/bin/mark"
# The old harness has no SCOTTLAND_TEST_PATH, so the shortcuts name their recorder by path.
fixture = Fixture(root, recorders=["mark"], lua=f'''
o.bind("F9", "Start (push-to-talk)", "{mark} start")
o.bind("F9", "Stop (push-to-talk)", "{mark} stop", {{ release = true }})
''')
COLORS = {"one": (0xC0, 0x30, 0x30), "two": (0x30, 0xC0, 0x30), "three": (0x30, 0x30, 0xC0)}
runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
session_xml = old / "core/plugin/metadata/scottland.xml"


def views(session):
    reply = session.ipc("window-rules/list-views")
    return reply if isinstance(reply, list) else []


def windows(session):
    return {v["app-id"]: v for v in views(session)
            if v.get("app-id", "").startswith("org.scottland.SolidColor.") and v.get("mapped")}


def shim(session):
    """(PID, start time) of the shim this session's lock file names, or None unless that process
    is this uid's scottland-hyprshim for this display and carries this session's environment."""
    signature = next((line.split("=", 1)[1] for line in
                      (runtime / "scottland" / f"{session.display}.env").read_bytes().decode().split("\0")
                      if line.startswith("HYPRLAND_INSTANCE_SIGNATURE=")), "")
    lock = runtime / "hypr" / signature / "hyprland.lock"
    try:
        pid, display = (lock.read_text().splitlines() + ["", ""])[:2]
        proc = Path(f"/proc/{pid}")
        if not pid.isdecimal() or display != session.display or proc.stat().st_uid != os.getuid():
            return None
        if b"scottland-hyprshim" not in (proc / "cmdline").read_bytes() or not session.owns(pid):
            return None
        return int(pid), (proc / "stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


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


shell_helper_dir = scratch / "shell-helper"
shell_helper_dir.mkdir()
shell_restart_log = scratch / "shell-restart.pid"
shell_restart_command = scratch / "shell-restart.command"
shell_helper = shell_helper_dir / "omarchy-restart-shell"
shell_helper.write_text(
    "#!/bin/sh\n"
    "set -eu\n"
    "lock=$XDG_RUNTIME_DIR/hypr/$HYPRLAND_INSTANCE_SIGNATURE/hyprland.lock\n"
    "pid=$(sed -n '1p' \"$lock\")\n"
    "[ \"$pid\" = \"$EXPECTED_SHIM_PID\" ] || exit 31\n"
    "grep -aq scottland-hyprshim \"/proc/$pid/cmdline\" || exit 32\n"
    "printf '%s\\n' \"$pid\" >\"$SHELL_RESTART_LOG\"\n"
    "command -v omarchy-hyprland-workspace-layout-toggle >\"$SHELL_RESTART_COMMAND\"\n"
)
shell_helper.chmod(0o755)


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
    old_shim = shim(session)
    check("the old shim is identified (lock file, uid, command, session environment)",
          old_shim is not None)
    events = scratch / "hyprland-events.log"
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
    session_xml.unlink()
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
    fresh = scratch / "libscottland-rehearsal.so"
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
    new_shim = shim(session)
    check("reload replaces the session's Hyprland shim", new_shim is not None and
          old_shim is not None and new_shim != old_shim, (old_shim, new_shim))
    if new_shim is not None:
        result = session.run(
            "env", f"PATH={shell_helper_dir}:/usr/bin:/bin",
            f"EXPECTED_SHIM_PID={new_shim[0]}", f"SHELL_RESTART_LOG={shell_restart_log}",
            f"SHELL_RESTART_COMMAND={shell_restart_command}",
            str(hooks / "reload.d" / "40-omarchy-shell"), timeout=30)
        selected = shell_restart_command.read_text().strip() if shell_restart_command.exists() else ""
        helper_pid = shell_restart_log.read_text().strip() if shell_restart_log.exists() else ""
        check("adapter calls Omarchy's shell restart helper after the new shim is ready",
              result.returncode == 0 and helper_pid == str(new_shim[0]), result.stderr)
        check("shell restart helper resolves adapter commands from libexec",
              selected == str(hooks / "libexec" / "omarchy-hyprland-workspace-layout-toggle"),
              selected)

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

    # The new shim is already active before the shell-restart hook runs.
    target = scratch / "long-string"
    session.hyprctl("dispatch", f"hl.dsp.exec_cmd([[touch {target}]])")
    check("after: the reloaded shim runs [[long strings]]", session.wait(target.exists)[0])
    locked = session.run("omarchy-hyprland-session-locked").returncode
    check("after: lock state answers unlocked (exit 1)", locked == 1, locked)
    mark_events = len(events.read_text().splitlines())
    session.run("sh", "-c", f"python3 {REPO}/tests/solid-color-app.py four '#808080' "
                            ">/dev/null 2>&1 </dev/null &")
    ok, _ = session.wait(lambda: "event openwindow" in "\n".join(events.read_text().splitlines()[mark_events:]),
                         timeout=10)
    print(f"FINDING  Quickshell Hyprland client after a shim restart: "
          f"{'still receives events' if ok else 'receives no events (does not reconnect)'}",
          flush=True)
    (scratch / "finding.json").write_text(json.dumps({"events_after_shim_restart": ok}))
    session.terminate(probe)

sys.exit(check.summary())
