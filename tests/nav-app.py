#!/usr/bin/env python3
"""A window of one solid color that records what it receives, one JSON line per event: whether it
is the active window, key presses, and pointer enter/motion/leave in surface coordinates.
   nav-app.py TITLE RRGGBB WIDTH HEIGHT LOG"""
import json
import sys
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gdk, Gtk
title, color, width, height, log_path = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), sys.argv[5]
log = open(log_path, 'a', buffering=1)
def record(**event): log.write(json.dumps(event) + '\n')
app = Gtk.Application(application_id='org.scottland.Nav' + title.replace('-', ''))
def activate(application):
    css = Gtk.CssProvider()
    css.load_from_string(f"window, window * {{ background: #{color}; }}")
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_USER)
    window = Gtk.ApplicationWindow(application=application, title=title)
    window.set_default_size(width, height)
    window.set_decorated(False)
    area = Gtk.DrawingArea()
    keys = Gtk.EventControllerKey.new()
    keys.connect('key-pressed', lambda c, keyval, code, state: record(key=Gdk.keyval_name(keyval)) or False)
    window.add_controller(keys)
    motion = Gtk.EventControllerMotion.new()
    motion.connect('enter', lambda c, x, y: record(pointer='enter', x=x, y=y))
    motion.connect('motion', lambda c, x, y: record(pointer='motion', x=x, y=y))
    motion.connect('leave', lambda c: record(pointer='leave'))
    area.add_controller(motion)
    window.connect('notify::is-active', lambda w, _: record(active=w.is_active()))
    window.set_child(area)
    window.present()
app.connect('activate', activate)
app.run([sys.argv[0]])
