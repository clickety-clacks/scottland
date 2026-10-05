#!/usr/bin/env python3
"""Use the desktop portal like an app does, for tests.

  portal-client.py screenshot OUT.json        org.freedesktop.portal.Screenshot (non-interactive)
  portal-client.py screencast OUT.json FRAME  org.freedesktop.portal.ScreenCast: share a monitor,
                                              then read one frame of the stream from PipeWire
                                              (gst-launch-1.0 pipewiresrc) into FRAME as raw RGB

OUT.json gets {"response": code, "results": {...}} (and the frame's width/height for screencast).
Runs inside the session (its D-Bus session bus and PipeWire).
"""
import json
import os
import subprocess
import sys

import dbus
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

DBusGMainLoop(set_as_default=True)
bus = dbus.SessionBus()
portal = bus.get_object("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop")
sender = bus.get_unique_name()[1:].replace(".", "_")
counter = 0


def request(method, *args, options=None):
    """Call a portal method that answers through a Request object's Response signal."""
    global counter
    counter += 1
    token = f"scottland_test_{os.getpid()}_{counter}"
    path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
    options = dict(options or {})
    options["handle_token"] = token
    loop, answer = GLib.MainLoop(), {}

    def on_response(code, results):
        answer["code"], answer["results"] = int(code), results
        loop.quit()

    match = bus.add_signal_receiver(on_response, "Response", "org.freedesktop.portal.Request",
                                    path=path)
    method(*args, options)
    GLib.timeout_add_seconds(60, loop.quit)
    loop.run()
    match.remove()
    if "code" not in answer:
        raise SystemExit(f"no portal response to {method}")
    return answer["code"], answer["results"]


def plain(value):
    if isinstance(value, (dbus.Dictionary, dict)):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (dbus.Array, dbus.Struct, list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, (dbus.String, dbus.ObjectPath)):
        return str(value)
    if isinstance(value, dbus.Boolean):
        return bool(value)
    if isinstance(value, (dbus.Int32, dbus.UInt32, dbus.Int64, dbus.UInt64, dbus.Byte)):
        return int(value)
    return value


def write(path, data):
    with open(path, "w") as out:
        json.dump(data, out)


def screenshot(out):
    shot = dbus.Interface(portal, "org.freedesktop.portal.Screenshot")
    code, results = request(shot.Screenshot, "", options={"interactive": False})
    write(out, {"response": code, "results": plain(results)})


def screencast(out, frame):
    cast = dbus.Interface(portal, "org.freedesktop.portal.ScreenCast")
    code, results = request(cast.CreateSession, options={"session_handle_token": "scottland_test"})
    if code != 0:
        return write(out, {"response": code, "step": "CreateSession"})
    session = results["session_handle"]
    code, results = request(cast.SelectSources, session,
                            options={"types": dbus.UInt32(1), "multiple": False})
    if code != 0:
        return write(out, {"response": code, "step": "SelectSources"})
    code, results = request(cast.Start, session, "")
    if code != 0:
        return write(out, {"response": code, "step": "Start"})
    streams = plain(results.get("streams", []))
    node = streams[0][0]
    fd = cast.OpenPipeWireRemote(session, dbus.Dictionary({}, signature="sv")).take()
    caps = subprocess.run(
        ["gst-launch-1.0", "-q", "pipewiresrc", f"fd={fd}", f"path={node}", "num-buffers=1",
         "!", "videoconvert", "!", "video/x-raw,format=RGB", "!", "filesink", f"location={frame}"],
        pass_fds=[fd], capture_output=True, text=True, timeout=30)
    write(out, {"response": 0, "streams": streams, "gst": caps.returncode,
                "gst_stderr": caps.stderr[-500:]})


if __name__ == "__main__":
    if sys.argv[1] == "screenshot":
        screenshot(sys.argv[2])
    else:
        screencast(sys.argv[2], sys.argv[3])
