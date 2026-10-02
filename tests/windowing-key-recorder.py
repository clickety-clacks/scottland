#!/usr/bin/env python3
"""Ordinary GTK client used to prove which physical keys reach applications."""
import json
import sys
import gi

gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, Gdk

name, path = sys.argv[1:3]
app = Gtk.Application(application_id='org.scottland.WindowingTest.' + name)

def activate(application):
    window = Gtk.ApplicationWindow(application=application, title=name)
    window.set_default_size(300, 180)
    window.set_child(Gtk.Label(label=name))
    keys = Gtk.EventControllerKey()
    def record(_controller, keyval, keycode, modifiers):
        with open(path, 'a') as stream:
            stream.write(json.dumps({'key': Gdk.keyval_name(keyval), 'code': keycode,
                                     'modifiers': int(modifiers)}) + '\n')
        return False
    keys.connect('key-pressed', record)
    window.add_controller(keys)
    window.present()

app.connect('activate', activate)
app.run([sys.argv[0]])
