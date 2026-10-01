#!/usr/bin/env python3
"""Test app for tests/widgets-test.sh: a window titled notify-app that sends a desktop notification
from its own process NOTIFY_AT seconds after it starts (an app asking for attention, WG15)."""
import os
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

app = Gtk.Application(application_id="org.scottland.TestNotify")


def notify():
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    bus.call("org.freedesktop.Notifications", "/org/freedesktop/Notifications", "org.freedesktop.Notifications",
             "Notify", GLib.Variant("(susssasa{sv}i)", ("notify-app", 0, "", "Done", "Your task finished", [], {}, -1)),
             None, Gio.DBusCallFlags.NONE, 2000, None, None)
    return False


def activate(application):
    window = Gtk.ApplicationWindow(application=application, title="notify-app")
    window.set_default_size(300, 200)
    window.present()
    GLib.timeout_add(int(float(os.environ.get("NOTIFY_AT", "6")) * 1000), notify)


app.connect("activate", activate)
app.run()
