#!/usr/bin/env python3
"""Solid theme-following surfaces for pixel checks of compositor tint, rim and badges."""
import json
import sys
import gi

gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib, Gdk

name, width, height, palette = sys.argv[1:]
app = Gtk.Application(application_id='org.scottland.HintStyle.' + name)

def activate(application):
    window = Gtk.ApplicationWindow(application=application, title=name)
    window.set_default_size(int(width), int(height))
    css = Gtk.CssProvider()
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    previous = None
    def theme():
        nonlocal previous
        try:
            colors = json.loads(open(palette).read())
            background = colors['background']
            if background != previous:
                css.load_from_string('window { background: ' + background + '; }')
                previous = background
        except (OSError, ValueError, KeyError):
            pass
        return True
    theme()
    GLib.timeout_add(100, theme)
    window.present()

app.connect('activate', activate)
app.run([sys.argv[0]])
