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
from pathlib import Path
from unittest.mock import patch
import errno
import socket
import struct
import threading
import time

art = Path(__file__).resolve().parents[1] / "build"
art.mkdir(exist_ok=True)
temporary = tempfile.TemporaryDirectory(prefix="widget-bus-unit-", dir=art)
root = temporary.name
path = os.path.join(os.path.dirname(__file__), "..", "core", "libexec", "scottland-widget-bus")
loader = importlib.machinery.SourceFileLoader("bus", path)
bus = importlib.util.module_from_spec(importlib.util.spec_from_loader("bus", loader))
loader.exec_module(bus)
bus.RUNTIME = root
bus.STATE_LOG = os.path.join(root, "widgets.log")
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


service = bus.Service()
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

# A running preview needs Minimized updates without telling its app it has been widgetized.
preview = dict(entry("42", "scottland-widget-newest.scope"), pid=os.getpid(), lifecycle="previewing", minimized=True)
replace([preview])
check("WG16 the service updates a preview's presentation",
      service.widgets["42"]["Minimized"] is True)
check("WG16 a preview does not announce its app as widgetized", not service.app_state(os.getpid())[0])
preview["lifecycle"] = "docked"
replace([preview])
check("WG16 commitment announces its app as widgetized", service.app_state(os.getpid())[0])

# WG11 without systemd: no systemctl on PATH; identity by process tree, and only live roots.
child = subprocess.Popen(["sleep", "30"])
os.environ["PATH"] = root  # nothing runnable: no systemctl
state = dict(service.widgets["42"], _widget_unit="scottland-widget-x.scope", _launcher_pid=os.getpid(), _widget_pid=0)
check("WG11 without systemctl, a widget's own process is recognized (no crash)",
      bus.is_widget_process(child.pid, state))
check("WG11 without systemctl, an unrelated process isn't",
      not bus.is_widget_process(1, dict(state, _launcher_pid=0)))
child.kill()
child.wait()

# ENOSPC used to escape on_event, causing GLib to remove its watch while D-Bus
# remained alive. Drive the actual callback through GLib, with two real IPC sockets.
service.bus = None
service.ipc = None
listener = socket.socket(socket.AF_UNIX)
socket_path = os.path.join(root, "compositor.sock")
listener.bind(socket_path)
listener.listen()
listener.settimeout(5)
os.environ["WAYFIRE_SOCKET"] = socket_path
current = {"session": "recovery", "version": 1, "windows": [],
           "widgets": [entry("42", "recovery-a.scope"), entry("43", "recovery-b.scope")]}
connections = []
server_errors = []
reject_subscription = False
stop_server = threading.Event()


def send(conn, payload):
    body = json.dumps(payload).encode()
    conn.sendall(struct.pack("<I", len(body)) + body)


def serve():
    try:
        while not stop_server.is_set():
            conn, _ = listener.accept()
            header = conn.recv(4, socket.MSG_WAITALL)
            request = json.loads(conn.recv(struct.unpack("<I", header)[0], socket.MSG_WAITALL))
            assert request["method"] == "scottland/subscribe"
            if reject_subscription:
                send(conn, {"error": "No such method found!", "method": "scottland/subscribe"})
                conn.close()
            else:
                send(conn, dict(current, result="ok"))
                connections.append(conn)
    except (OSError, ValueError) as error:
        if not stop_server.is_set():
            server_errors.append(error)


thread = threading.Thread(target=serve, daemon=True)
thread.start()
context = bus.GLib.MainContext.default()


def until(predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        if predicate():
            return True
        time.sleep(.01)
    return False


def written(wid):
    with open(service.state_path(wid)) as source:
        return json.load(source)


service.watch_compositor()
check("initial subscription receives complete state", written("42")["title"] == "t")
revision = written("42")["revision"]
real_dump = bus.json.dump
signals = []


class RecoveryBus:
    def emit_signal(self, _dest, path, _iface, _signal, params):
        wid = path.rsplit("/", 1)[-1]
        public = params.unpack()[1]
        state = written(wid)
        assert public["Version"] == state["version"] and public["Revision"] == state["revision"]
        assert public["Title"] == state["title"] and public["Minimized"] == state["minimized"]
        signals.append(wid)


service.bus = RecoveryBus()


def full_disk(value, out):
    if "recovery-a.scope" in out.name:
        out.write('{"partial":')
        raise OSError(errno.ENOSPC, "simulated full state filesystem")
    return real_dump(value, out)


with patch.object(bus.json, "dump", full_disk):
    current = dict(current, version=2, widgets=[entry("42", "recovery-a.scope", title="new", collapsed=True),
                                              entry("43", "recovery-b.scope", title="also new")])
    send(connections[-1], dict(current, event="scottland-widgets#"))
    check("ENOSPC does not drop the GLib event watch or starve other widgets",
          until(lambda: service.model_version == 2) and written("43")["title"] == "also new")
    check("failed writes retain old complete file and revision, and remove partial tmp",
          written("42")["title"] == "t" and service.widgets["42"]["_revision"] == revision
          and not list(Path(root).glob("*.tmp")) and "42" not in signals)
    current = dict(current, version=3)
    send(connections[-1], dict(current, event="scottland-widgets#"))
    check("event delivery continues while storage is full", until(lambda: service.model_version == 3))
check("timer repairs latest state without another event",
      until(lambda: not service.dirty) and written("42")["minimized"] and written("42")["title"] == "new"
      and "42" in signals)

# A real permission failure also heals on the next identical snapshot.
os.chmod(root, 0o500)
try:
    current = dict(current, version=4, widgets=[entry("42", "recovery-a.scope", title="readonly")])
    service.replace_snapshot(current)
    check("read-only state directory leaves a pending write", "42" in service.dirty)
finally:
    os.chmod(root, 0o700)
service.replace_snapshot(current)
check("same snapshot repairs a failed file", not service.dirty and written("42")["title"] == "readonly")

# Prepare must fail promptly rather than launch with an old or incomplete file.
class Invocation:
    error = None
    value = None
    def return_dbus_error(self, name, message):
        self.error = name
    def return_value(self, value):
        self.value = value


invocation = Invocation()
with patch.object(service, "refresh"), patch.object(bus.json, "dump", full_disk):
    service.on_root_call(None, None, None, "org.scottland.WidgetLaunch", "Prepare",
                        bus.GLib.Variant("(ss)", ("42", "recovery-a.scope")), invocation)
check("Prepare returns an explicit error on write failure", invocation.error and invocation.value is None)
service.flush_writes()

with patch.object(bus, "write_json", side_effect=OSError(errno.ENOSPC, "full")):
    service.badges["test"] = (4, True)
    service.write_owned()
check("service-owned badge state is retried", until(lambda: not service.owned_dirty)
      and json.loads(Path(root, "service-owned.json").read_text())["badges"]["test"] == [4, True])

# Drop the real stream; reject subscribe once as during plugin reload, then restore it.
reject_subscription = True
connections[-1].shutdown(socket.SHUT_RDWR)
connections[-1].close()
check("IPC hangup schedules resubscription", until(lambda: service.reconnect_timer != 0))
check("missing subscribe method retries without exiting", until(lambda: "No such method" in Path(bus.STATE_LOG).read_text()))
reject_subscription = False
current = dict(current, version=5, widgets=[entry("42", "recovery-a.scope", title="reconnected")])
check("reconnect catches up from subscription snapshot",
      until(lambda: service.model_version == 5) and written("42")["title"] == "reconnected")
current = dict(current, version=6, widgets=[entry("42", "recovery-a.scope", title="live again")])
send(connections[-1], dict(current, event="scottland-widgets#"))
check("resubscribed stream keeps delivering updates", until(lambda: service.model_version == 6)
      and written("42")["title"] == "live again")
check("fake compositor had no server failures", not server_errors)
stop_server.set()
listener.close()
for conn in connections:
    conn.close()
if service.events:
    service.events.sock.close()
for source in (service.event_watch, service.reconnect_timer, service.write_timer):
    if source:
        bus.GLib.source_remove(source)
temporary.cleanup()

print("\nall widget service checks passed" if not fails else f"\n{fails} widget service check(s) failed")
sys.exit(1 if fails else 0)
