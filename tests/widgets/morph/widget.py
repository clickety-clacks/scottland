"""Opaque red/blue fixture: delayed response, same-size response, or no response.

Draws a 56px green square to make unintended scaling measurable in screenshots.
Reads the real launch state. It never receives test IPC to resize its window.
"""
import json
import os
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib

path = os.environ["SCOTTLAND_WIDGET_STATE"]
state = json.load(open(path))
if state["title"].startswith("startup-delayed-"):
    import time
    time.sleep(.6)  # exercise the compositor before the destination surface exists
app = Gtk.Application(application_id="org.scottland.MorphFixture")


def activate(application):
    window = Gtk.ApplicationWindow(application=application, title="Scottland widget: " + state["title"])
    window.set_decorated(False)
    window.set_default_size(320, 96)
    drawing = Gtk.DrawingArea()
    window.set_child(drawing)
    small = False
    seen = state["minimized"]
    generation = 0

    def draw(area, cr, width, height):
        cr.set_source_rgb(0, 0, 1) if small else cr.set_source_rgb(1, 0, 0)
        cr.paint()
        cr.set_source_rgb(0, 1, 0)
        cr.rectangle(width - 76 if state["rail"] == "right" else 20, 20, 56, 56)
        cr.fill()
        cr.set_source_rgb(1, 1, 0)
        cr.rectangle(0, 8, width, 4)  # deliberately asymmetric: catches upside-down snapshots
        cr.fill()

    drawing.set_draw_func(draw)

    def poll():
        nonlocal seen, generation
        fresh = json.load(open(path))
        if fresh["minimized"] != seen:
            seen = fresh["minimized"]
            generation += 1
            current = generation
            target = seen

            def apply():
                nonlocal small
                if current != generation:
                    return False
                small = target
                if "same-size" not in state["title"]:
                    window.set_default_size(96 if small else 320, 160 if small and "taller" in state["title"] else 96)
                drawing.queue_draw()
                return False

            GLib.timeout_add(150, apply)
        return True

    if "unresponsive" not in state["title"]:
        GLib.timeout_add(10, poll)
    window.present()


app.connect("activate", activate)
app.run()
