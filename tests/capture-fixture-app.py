#!/usr/bin/env python3
"""A GTK window showing an image at its own size, or a line of text, for capture tests.

  capture-fixture-app.py NAME --image PATH
  capture-fixture-app.py NAME --text TEXT

The window is undecorated and sized to its content: the image pixel for pixel, or the text in
large black letters on white. Its app-id is org.scottland.CaptureFixture.NAME.
"""
import sys
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
from gi.repository import Gdk, Gtk
name, kind, content = sys.argv[1], sys.argv[2], sys.argv[3]
app = Gtk.Application(application_id='org.scottland.CaptureFixture.' + name)


def activate(application):
    window = Gtk.ApplicationWindow(application=application, title=name)
    window.set_decorated(False)
    css = Gtk.CssProvider()
    css.load_from_string("window, window * { background: white; color: black; }"
                         " label { font-size: 40px; font-family: sans-serif; padding: 24px; }")
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_USER)
    if kind == '--image':
        texture = Gdk.Texture.new_from_filename(content)
        picture = Gtk.Picture.new_for_paintable(texture)
        picture.set_can_shrink(False)
        picture.set_size_request(texture.get_width(), texture.get_height())
        window.set_default_size(texture.get_width(), texture.get_height())
        window.set_child(picture)
    else:
        window.set_child(Gtk.Label(label=content))
    window.present()


app.connect('activate', activate)
app.run([sys.argv[0]])
