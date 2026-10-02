#!/usr/bin/env python3
"""Unit test for scottland-widget-bus's bookkeeping, with a fake compositor and no D-Bus:
full snapshots and service-owned data (WG10 badges), mailbox identity (WG11) without systemd.

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
        self.snapshot = {"version": 0, "collapsed": False, "windows": [], "widgets": []}

    def call(self, method, data=None):
        assert method == "scottland/desktop-model" and data == {"slice": "widgets"}
        return self.snapshot


service = bus.Service.__new__(bus.Service)
service.ipc = FakeIpc()
service.widgets, service.registrations, service.badges = {}, {}, {}
service.scales, service.window_pids, service.announced = {}, {}, {}
service.data, service.rendered = {}, {}
service.model_version, service.model_snapshot = -1, {}
service.session = "unit-test"
service.bus = None


def entry(wid, unit, desktop="", title="t", collapsed=False):
    return {"id": wid, "window": int(wid), "app_id": "x", "title": title, "pid": 0, "rail": "right",
            "widget_pid": 0, "launcher_pid": 0, "widget_unit": unit, "desktop": desktop,
            "name": "X", "icon": "x", "focused": False, "urgent": False, "minimized": collapsed,
            "lifecycle": "docked", "card": True}


def replace(entries):
    service.ipc.snapshot = {"version": service.model_version + 1, "session": "unit-test", "collapsed": False,
                            "windows": [], "widgets": entries}
    service.refresh()


# A late subscriber gets the complete identity without launch files or captured defaults.
service.badges["app-a"] = (7, True)
replace([entry("42", "scottland-widget-new.scope", "app-a", "Inbox", True)])
state = json.load(open(service.state_path("42")))
check("late subscriber gets title, collapsed mode and badge from a full snapshot",
      state["title"] == "Inbox" and state["minimized"] and state["badge"] == 7)
check("the first state file includes resolved name, icon and model version",
      state["name"] == "X" and state["icon"] == "x" and state["version"] == service.model_version)

# D-Bus full replacements carry the version and the revision of the file already written.
class FakeBus:
    def emit_signal(self, _dest, _path, _iface, _signal, params):
        public = params.unpack()[1]
        written = json.load(open(service.state_path("42")))
        check("D-Bus full snapshot matches the already-written file version and revision",
              public["Version"] == written["version"] and public["Revision"] == written["revision"]
              and public["Title"] == written["title"] and public["Badge"] == written["badge"])


service.bus = FakeBus()
service.update("42", {"Badge": 8})
service.bus = None

# Removed values cannot survive a newer snapshot. Queued older updates cannot win.
old = service.ipc.snapshot
replace([entry("42", "scottland-widget-new.scope")])
service.replace_snapshot(old)
check("newer snapshots replace cleared fields; an older queued snapshot is ignored",
      service.widgets["42"]["Title"] == "t" and service.widgets["42"]["Badge"] == 0
      and not service.widgets["42"]["Minimized"] and service.widgets["42"]["_desktop"] == "")

# A launch's mailbox data is service-owned, and survives compositor snapshots only for that launch.
service.data["scottland-widget-new.scope"] = '{"unread": 3}'
replace([entry("42", "scottland-widget-new.scope", title="Changed")])
check("service-owned mailbox survives a title snapshot", service.widgets["42"]["Data"] == '{"unread": 3}')
old_path = service.state_path("42")
replace([entry("42", "scottland-widget-newer.scope")])
check("a new launch with the same window id drops the old file, badge and mailbox",
      not os.path.exists(old_path) and service.widgets["42"]["Data"] == "" and service.widgets["42"]["Badge"] == 0)

# Same public content need not rewrite a card file for unrelated window motion.
path = service.state_path("42")
revision = json.load(open(path))["revision"]
replace([entry("42", "scottland-widget-newer.scope")])
check("unchanged presentation leaves the card file revision alone",
      json.load(open(path))["revision"] == revision)
replace([])
check("removal in a full snapshot removes the D-Bus replica and state file",
      not service.widgets and not os.path.exists(path))
replace([entry("42", "scottland-widget-newest.scope", "app-c")])
check("a later launch learns identity directly from its snapshot", service.widgets["42"]["_desktop"] == "app-c")

# The badge/mailbox owner restarts without turning a current value into a default.
service.badges["app-c"] = (9, True)
service.data["scottland-widget-newest.scope"] = '{"unread": 2}'
service.write_owned()
service.session = None
service.badges, service.data = {}, {}
service.refresh()
check("service restart reconstructs its owned badge and mailbox snapshot",
      service.widgets["42"]["Badge"] == 9 and service.widgets["42"]["Data"] == '{"unread": 2}')
service.ipc.snapshot = {**service.ipc.snapshot, "session": "a-different-session", "version": 1}
service.replace_snapshot(service.ipc.snapshot)
check("a new compositor session accepts its version and never inherits old badges or data",
      service.model_version == 1 and service.widgets["42"]["Badge"] == 0 and service.widgets["42"]["Data"] == "")

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
