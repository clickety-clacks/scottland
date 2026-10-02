#!/usr/bin/env python3
"""One Wayland client with two toplevels and layer-shell surfaces; log real keyboard input."""
import json
import sys
from pathlib import Path
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gtk, GLib, Gio, Gtk4LayerShell as Layer

root = Path(sys.argv[1])
windows = {}
app = Gtk.Application(application_id="org.scottland.TestKeyLayer", flags=Gio.ApplicationFlags.NON_UNIQUE)


def log(name, edge, keyval, keycode, state):
    with (root / "keys.jsonl").open("a") as stream:
        stream.write(json.dumps(dict(name=name, edge=edge, keyval=keyval,
                                     keycode=keycode, mods=int(state))) + "\n")
    return False


def make(name, layer=False):
    window = Gtk.ApplicationWindow(application=app, title="KL-" + name)
    window.set_default_size(300, 180)
    window.set_child(Gtk.Label(label="Key layer test: " + name))
    if layer:
        Layer.init_for_window(window)
        Layer.set_namespace(window, "scottland-test-key-layer")
        Layer.set_layer(window, Layer.Layer.OVERLAY)
        Layer.set_keyboard_mode(window, Layer.KeyboardMode.ON_DEMAND)
        Layer.set_anchor(window, Layer.Edge.TOP, True)
        Layer.set_anchor(window, Layer.Edge.LEFT, True)
    controller = Gtk.EventControllerKey()
    controller.connect("key-pressed", lambda _, val, code, mods: log(name, "down", val, code, mods))
    controller.connect("key-released", lambda _, val, code, mods: log(name, "up", val, code, mods))
    window.add_controller(controller)
    windows[name] = window
    window.present()


def commands():
    path = root / "command.json"
    if path.exists():
        command = json.loads(path.read_text())
        path.unlink()
        name = command.get("name", "one")
        if command["action"] == "make":
            make(name, True)
        elif command["action"] == "hide":
            windows[name].set_visible(False)
        elif command["action"] == "show":
            windows[name].present()
        elif command["action"] == "close":
            windows.pop(name).close()
        elif command["action"] == "quit":
            app.quit()
    return True


def activate(_):
    make("one")
    # Leave the first window available on screen for real clicks by hiding the second.
    make("two")
    windows["two"].set_visible(False)
    make("popup", True)
    windows["popup"].set_visible(False)
    GLib.timeout_add(25, commands)


app.connect("activate", activate)
app.run([])
