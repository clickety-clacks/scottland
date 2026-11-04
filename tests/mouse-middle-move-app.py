#!/usr/bin/env python3
"""Small client fixture that records middle-button delivery."""
import json
from pathlib import Path
import sys

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gio, Gtk

events = Path(sys.argv[1]) / "buttons.jsonl"
app = Gtk.Application(application_id="org.scottland.MouseMiddleMoveTest",
                      flags=Gio.ApplicationFlags.NON_UNIQUE)


def record(edge, gesture):
    with events.open("a") as stream:
        stream.write(json.dumps({"edge": edge, "button": gesture.get_current_button()}) + "\n")


def activate(application):
    window = Gtk.ApplicationWindow(application=application, title="Mouse-Middle-A")
    window.set_default_size(400, 300)
    window.set_decorated(False)
    area = Gtk.DrawingArea()

    def draw(_area, context, _width, _height):
        context.set_source_rgb(.12, .68, .28)
        context.paint()

    area.set_draw_func(draw)
    window.set_child(area)
    click = Gtk.GestureClick.new()
    click.set_button(0)
    click.connect("pressed", lambda gesture, _count, _x, _y: record("press", gesture))
    click.connect("released", lambda gesture, _count, _x, _y: record("release", gesture))
    area.add_controller(click)
    window.present()


app.connect("activate", activate)
app.run([])
