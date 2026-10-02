"""A widget with no startup resizes; Space requests exactly one new client size."""
import json
import os
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Gdk

app = Gtk.Application(application_id="org.scottland.TestGravity")


def activate(application):
    with open(os.environ["SCOTTLAND_WIDGET_STATE"]) as source:
        state = json.load(source)
    window = Gtk.ApplicationWindow(application=application,
                                   title="Scottland widget: " + state["title"])
    window.set_decorated(False)
    window.set_default_size(320, 96)
    window.small = False

    def pressed(controller, key, code, modifiers):
        if key != Gdk.KEY_space:
            return False
        window.small = not window.small
        window.set_default_size(96 if window.small else 320, 96)
        return True

    controller = Gtk.EventControllerKey()
    controller.connect("key-pressed", pressed)
    window.add_controller(controller)
    window.present()


app.connect("activate", activate)
app.run()
