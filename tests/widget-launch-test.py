#!/usr/bin/env python3
"""Unit test for scottland-widget-launch's widget choice (WG6), packages (WG7) and context (WG8),
with throwaway config and data folders (never the user's).

  python3 tests/widget-launch-test.py
"""
import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile

root = tempfile.mkdtemp(prefix="scottland-launch-test-")
for sub in ("config/scottland", "data/applications", "data/scottland/widgets/mine", "hooks/widgets/card",
            "runtime", "state"):
    os.makedirs(os.path.join(root, sub), exist_ok=True)
os.environ.update({
    "XDG_CONFIG_HOME": f"{root}/config", "XDG_DATA_HOME": f"{root}/data", "XDG_DATA_DIRS": f"{root}/none",
    "XDG_RUNTIME_DIR": f"{root}/runtime", "XDG_STATE_HOME": f"{root}/state", "SCOTTLAND_HOOKS": f"{root}/hooks",
})


def write(path, text):
    with open(os.path.join(root, path), "w") as out:
        out.write(text)


write("hooks/widgets/card/widget.toml", 'id = "card"\napps = []\nexec = "card-cmd %t"\n')
write("data/scottland/widgets/mine/widget.toml", 'id = "mine"\napps = ["^org\\\\.example\\\\.Mail$"]\nexec = "mine-cmd --app %a --title %t --pid %p"\n')
write("data/applications/org.example.Viewer.desktop", "[Desktop Entry]\nName=Viewer\nIcon=viewer-icon\nExec=viewer\nX-Scottland-Widget=mine\n")
write("data/applications/Element X.desktop", '[Desktop Entry]\nName=Element X\nIcon=element-x\nExec=omarchy-launch-webapp "https://app.element.io/#/home"\n')

path = os.path.join(os.path.dirname(__file__), "..", "core", "libexec", "scottland-widget-launch")
loader = importlib.machinery.SourceFileLoader("launch", path)
spec = importlib.util.spec_from_loader("launch", loader)
launch = importlib.util.module_from_spec(spec)
loader.exec_module(launch)

fails = 0


def check(name, condition):
    global fails
    print(("PASS  " if condition else "FAIL  ") + name)
    fails += not condition


def pick(app_id):
    desktop_id, entry = launch.find_desktop(app_id)
    widget, why = launch.choose(app_id, desktop_id, entry, launch.packages())
    return widget["id"] if widget else None, desktop_id, entry


check("WG6 no widget configured: the default card", pick("org.example.Other")[0] == "card")
check("WG6 a package's apps pattern claims its app", pick("org.example.Mail")[0] == "mine")
check("WG6 the app's .desktop X-Scottland-Widget names its widget", pick("org.example.Viewer")[0] == "mine")
write("config/scottland/widgets.ini", "[widgets]\norg.example.Mail = card\norg.example.Other = nonexistent\n")
check("WG6 the user's widgets.ini wins over the app's own widget", pick("org.example.Mail")[0] == "card")
check("WG6 an unknown widget in widgets.ini falls back (and is logged)",
      pick("org.example.Other")[0] == "card" and "isn't installed" in open(f"{root}/state/scottland/widgets.log").read())
widget, desktop, entry = pick("chrome-app.element.io__-Default")
check("web apps find their .desktop entry (by site) and icon", desktop == "Element X" and entry.get("Icon") == "element-x")

write("data/applications/broken.desktop", '[Desktop Entry]\nName=Broken\nExec=broken "unclosed\n')
check("a malformed .desktop entry elsewhere doesn't stop the choice", pick("org.example.Other2")[0] == "card")
os.makedirs(f"{root}/data/scottland/widgets/my-card", exist_ok=True)
write("data/scottland/widgets/my-card/widget.toml", 'id = "card"\napps = []\nexec = "my-card"\n')
check("WG7 a user's package with the same id wins over the built-in", launch.packages()["card"]["exec"] == "my-card")
os.remove(f"{root}/data/scottland/widgets/my-card/widget.toml")

# WG8: run main() up to exec and capture what it would run.
captured = {}
real_exec = launch.os.execvpe
launch.os.execvpe = lambda prog, argv, env: captured.update(argv=argv, env=env, cwd=os.getcwd())
sys.argv = ["scottland-widget-launch", json.dumps({"id": "42", "window": 42, "app_id": "org.example.Mail2",
                                                  "title": "Inbox (3) — Mail", "pid": 1234, "rail": "left"})]
os.remove(f"{root}/config/scottland/widgets.ini")
write("data/scottland/widgets/mine/widget.toml", 'id = "mine"\napps = ["^org\\\\.example\\\\.Mail2$"]\nexec = "mine-cmd --app %a --title %t --pid %p"\n')
launch.main()
check("WG8 placeholders fill per argument (a title with spaces stays one argument)",
      captured.get("argv") == ["mine-cmd", "--app", "org.example.Mail2", "--title", "Inbox (3) — Mail", "--pid", "1234"])
env = captured.get("env", {})
check("WG8 the launch environment carries the window's identity",
      env.get("SCOTTLAND_WIDGET_ID") == "42" and env.get("SCOTTLAND_WIDGET_PID") == "1234"
      and env.get("SCOTTLAND_WIDGET_RAIL") == "left" and env.get("SCOTTLAND_WIDGET_STATE", "").endswith("/42.json"))

check("WG8 the widget runs in its package directory", captured.get("cwd") == f"{root}/data/scottland/widgets/mine")
check("the launcher leaves the .desktop id for the widget service in its own file, not the state file",
      os.path.exists(f"{launch.RUNTIME}/42.launch.json") and not os.path.exists(f"{launch.RUNTIME}/42.json"))

# WG8, for real: every placeholder, exec'd in a child process.
import subprocess
write("data/scottland/widgets/mine/widget.toml", 'id = "mine"\napps = ["^org\\\\.example\\\\.Mail2$"]\n'
      'exec = "python3 -c \'import json,os,sys; json.dump([sys.argv[1:], os.getcwd(), {k: v for k, v in os.environ.items() if k.startswith(\\"SCOTTLAND_\\")}, open(\\"/proc/self/cgroup\\").read()], open(os.environ[\\"OUT\\"], \\"w\\"))\' %a %t %i %p %w %r %d %% %z"\n')
write("data/applications/org.example.Mail2.desktop", "[Desktop Entry]\nName=Mail Two\nIcon=mail-two\nExec=mail2\n")
out = f"{root}/exec-out.json"
# Through a real systemd scope (as the compositor asks): the user manager's socket is reachable
# from the throwaway runtime folder, and the title has "$" in it, which systemd-run mustn't expand.
real_runtime = f"/run/user/{os.getuid()}"
if os.path.exists(f"{real_runtime}/systemd/private"):
    os.symlink(f"{real_runtime}/systemd", f"{root}/runtime/systemd")
unit = f"scottland-widget-launch-test-{os.getpid()}.scope"
result = subprocess.run([sys.executable, path, json.dumps({"id": "7", "window": 7, "app_id": "org.example.Mail2",
                         "title": "A b ${HOME} $$", "pid": 99, "rail": "right", "unit": unit})],
                        env={**os.environ, "OUT": out, "WAYLAND_DISPLAY": "wl-test"})
argv, cwd, wenv, cgroup = json.load(open(out)) if os.path.exists(out) else ([], "", {}, "")
check("WG8 (exec) every placeholder fills, unknown ones stay",
      argv == ["org.example.Mail2", "A b ${HOME} $$", "mail-two", "99", "7", "right", f"{root}/data/scottland/widgets/mine", "%", "%z"])
check("WG8 (exec) runs in the package directory", cwd == f"{root}/data/scottland/widgets/mine")
check("WG8 (exec) the whole launch environment",
      wenv.get("SCOTTLAND_WIDGET_APP_ID") == "org.example.Mail2" and wenv.get("SCOTTLAND_WIDGET_TITLE") == "A b ${HOME} $$"
      and wenv.get("SCOTTLAND_WIDGET_ICON") == "mail-two" and wenv.get("SCOTTLAND_WIDGET_NAME") == "Mail Two"
      and wenv.get("SCOTTLAND_WIDGET_DESKTOP") == "org.example.Mail2" and wenv.get("SCOTTLAND_WIDGET_WINDOW") == "7"
      and wenv.get("SCOTTLAND_WIDGET_BADGE") == "0"
      and wenv.get("SCOTTLAND_WIDGET_STATE") == f"{root}/runtime/scottland/widgets/wl-test/7.json"
      and wenv.get("SCOTTLAND_PALETTE") == f"{root}/runtime/scottland/wl-test.palette.json")

scoped = os.path.exists(f"{root}/runtime/systemd")
check("WG5 (exec) the widget ran in its own systemd scope" if scoped else "WG5 (exec) no user manager: the fallback ran",
      cgroup.rstrip().endswith("/" + unit) if scoped else unit not in cgroup)
check("WG8 (exec) the launch file is named by its launch",
      os.path.exists(f"{root}/runtime/scottland/widgets/wl-test/{unit}.launch.json"))

print("\nall launcher checks passed" if not fails else f"\n{fails} launcher check(s) failed")
sys.exit(1 if fails else 0)
