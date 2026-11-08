#!/usr/bin/env python3
"""A transient modal toplevel and its owner for dialog window-mode tests."""
import sys
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk

name = sys.argv[1]
app = Gtk.Application(application_id='org.scottland.CycleDialog.' + name)


def activate(application):
    owner = Gtk.ApplicationWindow(application=application, title=name + 'Owner')
    owner.set_default_size(560, 360)
    owner.set_child(Gtk.Label(label=name + ' owner'))
    owner.present()

    dialog = Gtk.ApplicationWindow(application=application, title=name)
    dialog.set_transient_for(owner)
    dialog.set_modal(True)
    dialog.set_default_size(720, 480)
    dialog.set_child(Gtk.Label(label=name + ' dialog'))
    dialog.present()


app.connect('activate', activate)
app.run([sys.argv[0]])
