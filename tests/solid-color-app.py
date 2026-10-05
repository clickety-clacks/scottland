#!/usr/bin/env python3
"""A GTK window filled with one color, for pixel and picker tests: solid-color-app.py NAME #RRGGBB."""
import sys
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gdk, Gtk
name, color = sys.argv[1], sys.argv[2]
app = Gtk.Application(application_id='org.scottland.SolidColor.' + name)


def activate(application):
    css = Gtk.CssProvider()
    css.load_from_string(f"window, window * {{ background: {color}; }}")
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_USER)
    window = Gtk.ApplicationWindow(application=application, title=name)
    window.set_default_size(600, 400)
    window.set_decorated(False)
    window.present()


app.connect('activate', activate)
app.run([sys.argv[0]])
