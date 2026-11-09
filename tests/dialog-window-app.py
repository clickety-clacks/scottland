#!/usr/bin/env python3
"""A transient modal toplevel and its owner for dialog window-mode tests."""
import sys
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import GLib, Gtk

name = sys.argv[1]
app = Gtk.Application(application_id='org.scottland.CycleDialog.' + name)
windows = []


def activate(application):
    owner = Gtk.ApplicationWindow(application=application, title=name + 'Owner')
    owner.set_default_size(560, 360)
    owner.set_child(Gtk.Label(label=name + ' owner'))

    def present_dialog(_owner):
        # Wait until GTK has mapped the parent before creating the transient surface. This
        # ensures the compositor receives the xdg parent relationship with a live parent.
        dialog = Gtk.ApplicationWindow(application=application, title=name)
        dialog.set_transient_for(owner)
        dialog.set_modal(True)
        dialog.set_default_size(720, 480)
        dialog.set_child(Gtk.Label(label=name + ' dialog'))
        windows.append(dialog)
        dialog.present()
        return GLib.SOURCE_REMOVE

    windows.append(owner)
    owner.connect('map', lambda _owner: GLib.idle_add(present_dialog, _owner))
    owner.present()


app.connect('activate', activate)
app.run([sys.argv[0]])
