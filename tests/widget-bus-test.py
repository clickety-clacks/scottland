#!/usr/bin/env python3
"""Unit test for scottland-widget-bus's bookkeeping, with a fake compositor and no D-Bus:
launch files per launch (WG10 badges), mailbox identity (WG11) without systemd.

  python3 tests/widget-bus-test.py
"""
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile

root = tempfile.mkdtemp(prefix="scottland-bus-test-")
os.environ.update({"XDG_RUNTIME_DIR": root, "XDG_STATE_HOME": f"{root}/state", "WAYLAND_DISPLAY": "wl-test"})
path = os.path.join(os.path.dirname(__file__), "..", "core", "libexec", "scottland-widget-bus")
loader = importlib.machinery.SourceFileLoader("bus", path)
bus = importlib.util.module_from_spec(importlib.util.spec_from_loader("bus", loader))
loader.exec_module(bus)
os.makedirs(bus.RUNTIME, exist_ok=True)

fails = 0


def check(name, condition):
    global fails
    print(("PASS  " if condition else "FAIL  ") + name)
    fails += not condition


class FakeIpc:
    def __init__(self):
        self.widgets = []

    def call(self, method, data=None):
        if method == "scottland/widgets":
            return {"widgets": self.widgets}
        if method == "scottland/layout-state":
            return {"views": []}
        return []


service = bus.Service.__new__(bus.Service)
service.ipc = FakeIpc()
service.widgets, service.registrations, service.badges = {}, {}, {}
service.scales, service.window_pids, service.announced = {}, {}, {}
service.bus = None


def launch_file(wid, desktop, unit):
    with open(os.path.join(bus.RUNTIME, f"{wid}.launch.json"), "w") as out:
        json.dump({"desktop": desktop, "unit": unit}, out)


def entry(wid, unit, pid=0, launcher=0):
    return {"id": wid, "window": int(wid), "app_id": "x", "title": "t", "pid": 0, "rail": "right",
            "widget_pid": pid, "launcher_pid": launcher, "widget_unit": unit}


# A crashed session left window 42's launch file for app A, whose badge is 7.
service.badges["app-a"] = (7, True)
launch_file("42", "app-a", "scottland-widget-old.scope")
service.ipc.widgets = [entry("42", "scottland-widget-new.scope")]
service.refresh()
check("WG10 another launch's file (stale) isn't taken: no desktop, no badge",
      service.widgets["42"]["_desktop"] == "" and service.widgets["42"]["Badge"] == 0)
launch_file("42", "app-b", "scottland-widget-new.scope")
service.refresh()
check("WG10 this launch's file is: app B, which has no badge",
      service.widgets["42"]["_desktop"] == "app-b" and service.widgets["42"]["Badge"] == 0)
service.badges["app-b"] = (3, True)
service.widgets["42"]["_desktop"] = ""
service.refresh()
check("WG10 ...and its badge when it has one", service.widgets["42"]["Badge"] == 3)
service.ipc.widgets = [entry("42", "scottland-widget-newer.scope")]
service.refresh()
check("WG10 a new launch with the same id starts without the old launch's badge",
      service.widgets["42"]["Badge"] == 0 and service.widgets["42"]["_desktop"] == "")

# WG11 without systemd: no systemctl on PATH; identity by process tree, and only live roots.
child = subprocess.Popen(["sleep", "30"])
os.environ["PATH"] = root  # nothing runnable: no systemctl
state = dict(service.widgets["42"], _widget_unit="scottland-widget-x.scope", _launcher_pid=os.getpid(), _widget_pid=0)
check("WG11 without systemctl, a widget's own process is recognized (no crash)",
      bus.is_widget_process(child.pid, state))
check("WG11 without systemctl, an unrelated process isn't",
      not bus.is_widget_process(1, dict(state, _launcher_pid=0)))
child.kill()

print("\nall widget service checks passed" if not fails else f"\n{fails} widget service check(s) failed")
sys.exit(1 if fails else 0)
