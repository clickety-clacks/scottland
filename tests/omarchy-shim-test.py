#!/usr/bin/env python3
"""The Hyprland shim runs what callers ask (adapter-gaps AG19).

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
    session.run("pkill", "-f", "[o]rg.omarchy.screensaver")

sys.exit(check.summary())
