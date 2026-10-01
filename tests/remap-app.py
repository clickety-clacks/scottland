#!/usr/bin/env python3
"""Test app for tests/widgets-test.sh: a window titled remap-app that hides itself after HIDE_AT
seconds and shows itself again a second later (an app hiding its window while widgetized)."""
import os
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib  # noqa: E402

hide_at = int(float(os.environ.get("HIDE_AT", "6")) * 1000)
app = Gtk.Application(application_id="org.scottland.TestRemap")


def activate(application):
    window = Gtk.ApplicationWindow(application=application, title="remap-app")
    window.set_default_size(300, 200)
    window.present()
    GLib.timeout_add(hide_at, lambda: window.set_visible(False) or False)
    GLib.timeout_add(hide_at + 1000, lambda: window.present() or False)


app.connect("activate", activate)
app.run()
