#!/usr/bin/env python3
"""A transient modal toplevel and its owner for dialog window-mode tests."""
import sys
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib

name = sys.argv[1]
app = Gtk.Application(application_id='org.scottland.CycleDialog.' + name)


def activate(application):
    owner = Gtk.ApplicationWindow(application=application, title=name + 'Owner')
    owner.set_default_size(560, 360)
    owner.set_child(Gtk.Label(label=name + ' owner'))

    def show_dialog():
        if not owner.get_mapped():
            return GLib.SOURCE_CONTINUE
        dialog = Gtk.Dialog(title=name, transient_for=owner, modal=True)
        dialog.set_default_size(720, 480)
        dialog.get_content_area().append(Gtk.Label(label=name + ' dialog'))
        dialog.present()
        return GLib.SOURCE_REMOVE

    owner.connect('map', lambda _owner: GLib.timeout_add(100, show_dialog))
    owner.present()


app.connect('activate', activate)
app.run([sys.argv[0]])
