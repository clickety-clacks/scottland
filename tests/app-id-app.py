#!/usr/bin/env python3
"""A mapped Wayland client changes only its app-ID when the test creates a trigger file."""
from pathlib import Path
import sys
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('GdkWayland', '4.0')
from gi.repository import Gtk, GLib, GdkWayland
app = Gtk.Application(application_id='org.scottland.IdentityBefore')
def activate(application):
    window = Gtk.ApplicationWindow(application=application, title='identity-fixture')
    window.set_default_size(300, 200)
    window.present()
    def change():
        if not Path(sys.argv[1]).exists(): return True
        GdkWayland.WaylandToplevel.set_application_id(window.get_surface(), 'org.scottland.IdentityAfter')
        return False
    GLib.timeout_add(50, change)
app.connect('activate', activate)
app.run([sys.argv[0]])
