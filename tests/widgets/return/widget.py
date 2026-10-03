#!/usr/bin/env python3
"""Custom widget fixtures for WG25's text-input and key-layer behavior."""
import json
import os
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk, Gio

with open(os.environ["SCOTTLAND_WIDGET_STATE"], encoding="utf-8") as stream:
    widget_state = json.load(stream)
mode = widget_state["app_id"].rsplit("-", 1)[-1]
marker = Path(os.environ["SCOTTLAND_TEST_STATE"]) / (
    "wg25-return-text" if mode == "text" else "wg25-return-claim")

application = Gtk.Application(application_id="org.scottland.TestWidgetReturn",
                              flags=Gio.ApplicationFlags.NON_UNIQUE)


def activate(app):
    window = Gtk.ApplicationWindow(application=app,
                                    title="Scottland widget: " + widget_state["title"])
    window.set_default_size(320, 96)

    if mode == "text":
        entry = Gtk.Entry()
        entry.set_text("type here")
        entry.connect("activate", lambda *_: marker.write_text("activated\n", encoding="utf-8"))
        window.set_child(entry)
        window.set_focus(entry)
        window.present()
        GLib.idle_add(lambda: (window.set_focus(entry), GLib.SOURCE_REMOVE)[1])
    else:
        label = Gtk.Label(label="Return is claimed by the key layer")
        label.set_focusable(True)
        window.set_child(label)
        if mode == "claim":
            controller = Gtk.EventControllerKey()

            def key_pressed(_controller, keyval, _keycode, _state):
                if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
                    marker.write_text("received\n", encoding="utf-8")
                return False

            controller.connect("key-pressed", key_pressed)
            window.add_controller(controller)
        window.present()
        if mode == "claim":
            GLib.idle_add(lambda: (window.set_focus(label), GLib.SOURCE_REMOVE)[1])


application.connect("activate", activate)
application.run([])
