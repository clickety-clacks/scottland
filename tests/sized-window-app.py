#!/usr/bin/env python3
"""Plain GTK window of a requested size, for placement tests: sized-window-app.py NAME WIDTH HEIGHT."""
import sys
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk
name, w, h = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
app = Gtk.Application(application_id='org.scottland.CycleTest.' + name)
def activate(application):
    window = Gtk.ApplicationWindow(application=application, title=name)
    window.set_default_size(w, h)
    window.set_child(Gtk.Label(label=name))
    window.present()
app.connect('activate', activate)
app.run([sys.argv[0]])
