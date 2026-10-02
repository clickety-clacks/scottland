#!/usr/bin/env python3
"""Each GTK frame-clock tick changes every content pixel (no wall-clock repaint timer)."""
import gi
import sys
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk
app = Gtk.Application(application_id='org.scottland.LiveDragTest')
def activate(application):
    window = Gtk.ApplicationWindow(application=application, title='LiveDrag')
    window.set_default_size(360, 240)
    area = Gtk.DrawingArea()
    count = [0]
    def tick(widget, clock):
        count[0] += 1
        widget.queue_draw()
        return True
    def draw(widget, context, width, height):
        n = count[0]
        context.set_source_rgb((n * 37 % 251) / 250, (n * 61 % 251) / 250, (n * 97 % 251) / 250)
        context.paint()
    # An explicit client-side move handle exercises the retained stock move path.
    click = Gtk.GestureClick.new()
    click.set_button(1)
    def pressed(gesture, count, x, y):
        if y < 35:
            event = gesture.get_current_event()
            window.get_surface().begin_move(event.get_device(), 1, x, y, event.get_time())
    click.connect('pressed', pressed)
    area.add_controller(click)
    area.set_draw_func(draw)
    area.add_tick_callback(tick)
    window.set_child(area)
    window.present()
app.connect('activate', activate)
app.run([sys.argv[0]])
