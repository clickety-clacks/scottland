#!/usr/bin/env python3
"""GTK clients that record real pointer button delivery for middle-press tests."""
import json
from pathlib import Path
import sys

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk

root = Path(sys.argv[1])
events = root / "buttons.jsonl"
pointer_events = root / "pointer.jsonl"
command = root / "command.json"
windows = {}
app = Gtk.Application(application_id="org.scottland.MiddlePressTest",
                      flags=Gio.ApplicationFlags.NON_UNIQUE)


def record(name, edge, gesture):
    with events.open("a") as stream:
        stream.write(json.dumps({"name": name, "edge": edge,
                                 "button": gesture.get_current_button()}) + "\n")


def record_pointer(name, edge, x, y):
    with pointer_events.open("a") as stream:
        stream.write(json.dumps({"name": name, "edge": edge, "x": x, "y": y}) + "\n")


def make(name):
    window = Gtk.ApplicationWindow(application=app, title="Middle-" + name)
    window.set_default_size(360, 260)
    window.set_decorated(False)
    area = Gtk.DrawingArea()
    def draw(_area, context, _width, _height):
        context.set_source_rgb(.12, .68, .28)
        context.paint()
    area.set_draw_func(draw)
    window.set_child(area)
    click = Gtk.GestureClick.new()
    click.set_button(0)
    click.connect("pressed", lambda gesture, _count, _x, _y: record(name, "press", gesture))
    click.connect("released", lambda gesture, _count, _x, _y: record(name, "release", gesture))
    area.add_controller(click)
    motion = Gtk.EventControllerMotion.new()
    motion.connect("enter", lambda _controller, x, y: record_pointer(name, "enter", x, y))
    motion.connect("motion", lambda _controller, x, y: record_pointer(name, "motion", x, y))
    area.add_controller(motion)
    windows[name] = window
    window.present()


def commands():
    if command.exists():
        request = json.loads(command.read_text())
        command.unlink()
        action, name = request["action"], request.get("name", "A")
        if action == "hide":
            windows[name].set_visible(False)
        elif action == "show":
            windows[name].present()
        elif action == "quit":
            app.quit()
    return GLib.SOURCE_CONTINUE


def activate(_application):
    make("A")
    make("B")
    GLib.timeout_add(25, commands)


app.connect("activate", activate)
app.run([])
