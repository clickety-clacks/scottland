#!/usr/bin/env python3
"""A window of one solid color, so a screenshot shows exactly where it is drawn.
   grab-peek-app.py TITLE RRGGBB WIDTH HEIGHT. A press in its top 35 px starts a client move."""
import gi
import sys
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk
title, color, width, height = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
rgb = [int(color[i:i + 2], 16) / 255 for i in (0, 2, 4)]
app = Gtk.Application(application_id='org.scottland.GrabPeek' + title.replace('-', ''))
def activate(application):
    window = Gtk.ApplicationWindow(application=application, title=title)
    window.set_default_size(width, height)
    area = Gtk.DrawingArea()
    area.set_draw_func(lambda widget, context, w, h: (context.set_source_rgb(*rgb), context.paint()))
    click = Gtk.GestureClick.new()
    click.set_button(1)
    def pressed(gesture, count, x, y):
        if y < 35:
            event = gesture.get_current_event()
            window.get_surface().begin_move(event.get_device(), 1, x, y, event.get_time())
    click.connect('pressed', pressed)
    area.add_controller(click)
    window.set_child(area)
    window.present()
app.connect('activate', activate)
app.run([sys.argv[0]])
