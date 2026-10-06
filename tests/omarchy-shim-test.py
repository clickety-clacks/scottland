#!/usr/bin/env python3
"""The Hyprland shim and Lua host do what callers ask or say they can't (adapter-gaps AG19 and
the visible-failure rule for unsupported requests).

Isolated headless --omarchy session with a fixture HOME (tests/omarchy_fixture.py). Requests go
through the real `hyprctl`, as Omarchy's scripts send them; shortcuts are pressed with stipc keys.
Oracles: files and windows the commands actually produced (Wayfire's own view list, not the
shim's), hyprctl's exit status, and what a shortcut's own Lua code observed.

  tests/omarchy-shim-test.py
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from omarchy_fixture import REPO, Checks, Fixture, Session  # noqa: E402

check = Checks()
root = REPO / "build/omarchy-shim-fixture"
marks = root / "marks"
fixture = Fixture(
    root,
    modules=["default.hypr.bindings.voxtype"],
    recorders=["mark"],
    lua=f'''
-- An unsupported call while the config loads must not stop the bindings after it.
hl.config({{ cursor = {{ zoom_factor = 1 }} }})
local mark = "{root}/bin/mark"
-- At run time the same kind of call must fail where the shortcut can see it.
hl.bind("SUPER + F6", function()
  local ok = pcall(hl.config, {{ cursor = {{ zoom_factor = 2 }} }})
  os.execute(mark .. (ok and " config-ok" or " config-failed"))
  ok = pcall(hl.get_config, "cursor.zoom_factor")
  os.execute(mark .. (ok and " get-config-ok" or " get-config-failed"))
  ok = pcall(hl.dispatch, hl.dsp.focus({{ monitor = "NOWHERE-1" }}))
  os.execute(mark .. (ok and " dispatch-ok" or " dispatch-failed"))
  ok = pcall(hl.dispatch, hl.dsp.exec_cmd(mark .. " exec-ran"))
  os.execute(mark .. (ok and " exec-ok" or " exec-failed"))
end)
''')
# Stock screensaver needs a supported terminal (foot here) and its branding text.
(fixture.home / ".config/xdg-terminals.list").write_text("foot.desktop\n")
(fixture.home / ".config/omarchy/branding").mkdir(parents=True)
(fixture.home / ".config/omarchy/branding/screensaver.txt").write_text("Scottland\n")


def views(session):
    reply = session.ipc("window-rules/list-views")
    return reply if isinstance(reply, list) else []


with Session(fixture, "hl-omarchy-shim") as session:
    check("shim answers", session.wait_shim()[0])
    check("Lua host is running", session.wait_lua_host()[0])

    # AG19: a Lua long string, as Omarchy's screensaver launcher writes it.
    target = root / "long-string-ran"
    result = session.hyprctl("dispatch", f"hl.dsp.exec_cmd([[touch {target}]])")
    ok, _ = session.wait(target.exists)
    check("exec_cmd with a [[long string]] runs its command", ok and result.returncode == 0,
          (result.returncode, result.stdout))

    # AG19: the stock screensaver launcher opens its terminal.
    launch_log = root / "launch-screensaver.log"
    session.run("sh", "-c", f"omarchy-launch-screensaver force >{launch_log} 2>&1 </dev/null &")
    ok, found = session.wait(lambda: [v for v in views(session)
                                      if v.get("app-id") == "org.omarchy.screensaver"
                                      and v.get("mapped")], timeout=20)
    check("omarchy-launch-screensaver maps an org.omarchy.screensaver window", ok,
          (launch_log.read_text()[-400:] if launch_log.exists() else "",
           [v.get("app-id") for v in views(session)]))
    session.terminate(*session.owned("org[.]omarchy[.]screensaver"))

    # Unsupported requests fail visibly: hyprctl exits non-zero with the shim's error.
    for args in (["dispatch", 'hl.dsp.focus({ monitor = "NOWHERE-1" })'],
                 ["keyword", "monitor", "NOWHERE-1,disable"],
                 ["eval", 'hl.monitor({ output = "NOWHERE-1", scale = 1.5 })'],
                 ["switchxkblayout", "all", "next"],
                 ["binds"], ["cursorpos"], ["-j", "layers"],
                 ["getoption", "cursor:zoom_factor"]):
        result = session.hyprctl(*args)
        check(f"unsupported `hyprctl {' '.join(args)}` fails visibly",
              result.returncode != 0 and result.stdout.startswith("error:"),
              (result.returncode, result.stdout[:120]))
    for args in (["-j", "monitors"], ["-j", "clients"], ["-j", "getoption", "general:gaps_out"]):
        result = session.hyprctl(*args)
        valid = False
        try:
            json.loads(result.stdout)
            valid = True
        except ValueError:
            pass
        check(f"supported `hyprctl {' '.join(args)}` still succeeds", result.returncode == 0 and valid,
              (result.returncode, result.stdout[:120]))

    # The Lua host: an unsupported call at load time didn't stop later bindings; at run time a
    # shortcut sees unsupported calls and refused dispatches fail, and supported ones succeed.
    session.key("KEY_LEFTMETA", True)
    session.tap("KEY_F6")
    session.key("KEY_LEFTMETA", False)
    expected = {("mark", "config-failed"), ("mark", "get-config-failed"),
                ("mark", "dispatch-failed"), ("mark", "exec-ok"), ("mark", "exec-ran")}
    ok, calls = fixture.wait_calls(lambda c: expected <= set(c))
    check("a binding after a load-time hl.config still loads", ("mark", "config-failed") in calls,
          calls)
    check("a shortcut sees unsupported hl.config / hl.get_config fail",
          {("mark", "config-failed"), ("mark", "get-config-failed")} <= set(calls), calls)
    check("a shortcut sees a refused dispatch fail", ("mark", "dispatch-failed") in calls, calls)
    check("a shortcut's supported exec dispatch succeeds and runs",
          {("mark", "exec-ok"), ("mark", "exec-ran")} <= set(calls), calls)

    # `hyprctl reload` re-reads the config now: Voxtype installed after login gets its F9 keys,
    # as omarchy-voxtype-install expects.
    before = len(fixture.calls())
    session.tap("KEY_F9")
    time.sleep(0.5)  # an intended hold: nothing is bound to F9 yet
    check("F9 does nothing before Voxtype is installed",
          not [c for c in fixture.calls()[before:] if c[0] == "voxtype"], fixture.calls()[before:])
    fixture.command("voxtype")
    result = session.hyprctl("reload", timeout=60)
    check("hyprctl reload succeeds", result.returncode == 0 and result.stdout.strip() == "ok",
          (result.returncode, result.stdout))
    ok, _ = session.wait(lambda: "voxtype record start" in session.config())
    check("reload rebuilt this session's config (not the machine's live one)", ok)
    # Readiness: Wayfire has applied the rebuilt file once its own copy of the option has it.
    ok, _ = session.wait(lambda: all("KEY_F9" in json.dumps(
        session.ipc("wayfire/get-config-option", {"option": option}))
        for option in ("command/bindings", "scottland/shortcuts")), timeout=10)
    session.key("KEY_F9", True)
    ok, calls = fixture.wait_calls(lambda c: ("voxtype", "record start") in c[before:])
    check("after reload, F9 runs voxtype record start", ok, calls[before:])
    session.key("KEY_F9", False)
    ok, calls = fixture.wait_calls(lambda c: ("voxtype", "record stop") in c[before:])
    check("after reload, F9 release runs voxtype record stop", ok, calls[before:])

    # A Hyprland config that no longer parses: `hyprctl reload` says so, and the session keeps
    # the shortcuts it had (the config builder keeps its last config).
    hypr = fixture.home / ".config/hypr/hyprland.lua"
    good = hypr.read_text()
    hypr.write_text("this is not valid lua !!!!\n")
    result = session.hyprctl("reload", timeout=60)
    check("hyprctl reload of a broken config fails visibly",
          result.returncode != 0 and result.stdout.startswith("error:") and "hyprland.lua" in result.stdout,
          (result.returncode, result.stdout))
    check("the session's config still has the F9 shortcuts", "voxtype record start" in session.config())
    before = len(fixture.calls())
    session.tap("KEY_F9")
    ok, calls = fixture.wait_calls(lambda c: {("voxtype", "record start"), ("voxtype", "record stop")}
                                   <= set(c[before:]))
    check("F9 push-to-talk still works after the failed reload", ok, calls[before:])
    hypr.write_text(good)
    result = session.hyprctl("reload", timeout=60)
    check("with the config fixed, hyprctl reload succeeds again",
          result.returncode == 0 and result.stdout.strip() == "ok", (result.returncode, result.stdout))

sys.exit(check.summary())
