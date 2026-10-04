#!/usr/bin/env python3
"""Native GTK data-device source/target, with observable client-local drop coordinates."""
import json
from pathlib import Path
import sys
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
from gi.repository import Gdk, GLib, GObject, Gtk

title, journal = sys.argv[1:3]
app = Gtk.Application(application_id='org.scottland.DndFixture.' + title.replace('-', ''))

def record(event, **data):
    with Path(journal).open('a') as out:
        out.write(json.dumps({'event': event, **data}) + '\n')

def activate(app):
    window = Gtk.ApplicationWindow(application=app, title=title)
    window.set_default_size(400, 300)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    source = Gtk.Label(label='Drag this text to another window')
    source.set_size_request(400, 100)
    drag = Gtk.DragSource(actions=Gdk.DragAction.COPY)
    drag.connect('prepare', lambda *_: Gdk.ContentProvider.new_for_value('scottland-dnd-payload'))
    drag.connect('drag-begin', lambda *_: record('begin'))
    drag.connect('drag-cancel', lambda _, drag, reason: record('cancel', reason=int(reason)) or False)
    drag.connect('drag-end', lambda *_: record('end'))
    source.add_controller(drag)
    box.append(source)
    target = Gtk.Label(label='Drop text here')
    target.set_vexpand(True)
    drop = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.COPY)
    drop.connect('enter', lambda _, x, y: record('enter', x=x, y=y) or Gdk.DragAction.COPY)
    def receive(_, value, x, y):
        record('drop', value=value, x=x, y=y)
        target.set_text(value)
        return True
    drop.connect('drop', receive)
    target.add_controller(drop)
    files = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
    def receive_files(_, value, x, y):
        names = [file.get_basename() for file in value.get_files()]
        record('file-drop', names=names, x=x, y=y)
        target.set_text(', '.join(names))
        return True
    files.connect('drop', receive_files)
    target.add_controller(files)
    box.append(target)
    window.set_child(box)
    window.present()

app.connect('activate', activate)
app.run([sys.argv[0]])
