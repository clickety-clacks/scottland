#!/usr/bin/env python3
"""A GTK window filled with one color, for pixel and picker tests.

  solid-color-app.py NAME #RRGGBB [--tick] [--app-id ID]

--tick redraws a small counter in the top-left corner ten times a second, so the window keeps
changing (screen-sharing tests need new frames); the rest of the window stays the solid color.
--app-id gives the mapped window another app-id (one a D-Bus application id can't be, such as
1Password), as its client would.
"""
import sys
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('GdkWayland', '4.0')
from gi.repository import Gdk, GdkWayland, GLib, Gtk
name, color = sys.argv[1], sys.argv[2]
tick = "--tick" in sys.argv[3:]
app_id = sys.argv[sys.argv.index("--app-id") + 1] if "--app-id" in sys.argv[3:] else None
app = Gtk.Application(application_id='org.scottland.SolidColor.' + name)


def activate(application):
    css = Gtk.CssProvider()
    css.load_from_string(f"window, window * {{ background: {color}; }}")
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_USER)
    window = Gtk.ApplicationWindow(application=application, title=name)
    window.set_default_size(600, 400)
    window.set_decorated(False)
    if tick:
        label = Gtk.Label(label="0", halign=Gtk.Align.START, valign=Gtk.Align.START)
        counter = [0]

        def advance():
            counter[0] += 1
            label.set_label(str(counter[0] % 10))
            return True

        GLib.timeout_add(100, advance)
        window.set_child(label)
    if app_id:
        window.connect("map", lambda w: GdkWayland.WaylandToplevel.set_application_id(w.get_surface(), app_id))
    window.present()


app.connect('activate', activate)
app.run([sys.argv[0]])
