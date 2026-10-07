#!/usr/bin/env python3
"""GTK clients that record real pointer button delivery for middle-press tests."""
import json
from pathlib import Path
import sys

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gio, GLib, Gtk, Gtk4LayerShell as Layer

root = Path(sys.argv[1])
events = root / "buttons.jsonl"
command = root / "command.json"
windows = {}
app = Gtk.Application(application_id="org.scottland.MiddlePressTest",
                      flags=Gio.ApplicationFlags.NON_UNIQUE)


def record(name, edge, gesture):
    with events.open("a") as stream:
        stream.write(json.dumps({"name": name, "edge": edge,
                                 "button": gesture.get_current_button()}) + "\n")


def make(name, layer=False):
    window = Gtk.ApplicationWindow(application=app, title="Middle-" + name)
    window.set_default_size(360, 260)
    window.set_decorated(False)
    area = Gtk.DrawingArea()
    def draw(_area, context, _width, _height):
        context.set_source_rgb(.12, .68, .28)
        context.paint()
    area.set_draw_func(draw)
    window.set_child(area)
    if layer:
        Layer.init_for_window(window)
        Layer.set_namespace(window, "scottland-middle-press-test")
        Layer.set_layer(window, Layer.Layer.OVERLAY)
        Layer.set_keyboard_mode(window, Layer.KeyboardMode.ON_DEMAND)
        Layer.set_anchor(window, Layer.Edge.TOP, True)
        Layer.set_anchor(window, Layer.Edge.LEFT, True)
    click = Gtk.GestureClick.new()
    click.set_button(0)
    click.connect("pressed", lambda gesture, _count, _x, _y: record(name, "press", gesture))
    click.connect("released", lambda gesture, _count, _x, _y: record(name, "release", gesture))
    area.add_controller(click)
    windows[name] = window
    window.connect("map", lambda *_args: (root / (name + ".mapped")).touch())
    window.present()


def commands():
    if command.exists():
        request = json.loads(command.read_text())
        command.unlink()
        action, name = request["action"], request.get("name", "A")
        if action == "make-layer":
            make("Layer", layer=True)
        elif action == "hide":
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
