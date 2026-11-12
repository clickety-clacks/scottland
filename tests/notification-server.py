#!/usr/bin/env python3
"""A desktop notification server for tests: logs every notice it receives, shows nothing.

  notification-server.py LOG [--click]

Owns org.freedesktop.Notifications on the session bus and appends one JSON line per Notify call to
LOG: app name, summary, body, urgency, actions, and the icon (the app_icon or image-path given,
whether that file exists when the notice arrives and whether it is a PNG). Prints "ready" once it
owns the name. A notice closes on its own after a moment; with --click, a notice offering the
default action is clicked instead, as a user would.
"""
import json
import sys
import gi
gi.require_version('Gio', '2.0')
from gi.repository import Gio, GLib

log_path = sys.argv[1]
click = '--click' in sys.argv[2:]
XML = '''
<node>
  <interface name="org.freedesktop.Notifications">
    <method name="GetCapabilities"><arg type="as" direction="out"/></method>
    <method name="GetServerInformation">
      <arg type="s" direction="out"/><arg type="s" direction="out"/>
      <arg type="s" direction="out"/><arg type="s" direction="out"/>
    </method>
    <method name="Notify">
      <arg type="s" direction="in"/><arg type="u" direction="in"/><arg type="s" direction="in"/>
      <arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="as" direction="in"/>
      <arg type="a{sv}" direction="in"/><arg type="i" direction="in"/>
      <arg type="u" direction="out"/>
    </method>
    <method name="CloseNotification"><arg type="u" direction="in"/></method>
    <signal name="NotificationClosed"><arg type="u"/><arg type="u"/></signal>
    <signal name="ActionInvoked"><arg type="u"/><arg type="s"/></signal>
  </interface>
</node>'''
PATH = '/org/freedesktop/Notifications'
INTERFACE = 'org.freedesktop.Notifications'
counter = [0]
connection = [None]


def emit(name, values):
    connection[0].emit_signal(None, PATH, INTERFACE, name, values)


def icon_facts(path):
    if path.startswith('file://'):
        path = path[len('file://'):]
    if not path.startswith('/'):
        return {'icon': path}
    try:
        with open(path, 'rb') as icon:
            head = icon.read(8)
        return {'icon': path, 'icon_exists': True, 'icon_png': head == b'\x89PNG\r\n\x1a\n'}
    except OSError:
        return {'icon': path, 'icon_exists': False, 'icon_png': False}


def call(conn, sender, path, interface, method, params, invocation):
    if method == 'GetCapabilities':
        invocation.return_value(GLib.Variant('(as)', (['actions', 'body'],)))
    elif method == 'GetServerInformation':
        invocation.return_value(GLib.Variant('(ssss)', ('test', 'scottland', '1', '1.2')))
    elif method == 'CloseNotification':
        emit('NotificationClosed', GLib.Variant('(uu)', (params[0], 3)))
        invocation.return_value(None)
    elif method == 'Notify':
        app, replaces, app_icon, summary, body, actions, hints, timeout = params.unpack()
        counter[0] += 1
        notice_id = replaces or counter[0]
        entry = {'id': notice_id, 'app_name': app, 'summary': summary, 'body': body,
                 'urgency': hints.get('urgency'), 'actions': actions[0::2]}
        icon = hints.get('image-path') or app_icon
        if icon:
            entry.update(icon_facts(icon))
        with open(log_path, 'a') as log:
            log.write(json.dumps(entry) + '\n')
        invocation.return_value(GLib.Variant('(u)', (notice_id,)))
        if click and 'default' in actions[0::2]:
            def clicked():
                emit('ActionInvoked', GLib.Variant('(us)', (notice_id, 'default')))
                emit('NotificationClosed', GLib.Variant('(uu)', (notice_id, 2)))
                return False
            GLib.timeout_add(200, clicked)
        else:
            def expired():
                emit('NotificationClosed', GLib.Variant('(uu)', (notice_id, 1)))
                return False
            GLib.timeout_add(200, expired)


def bus_acquired(conn, name):
    connection[0] = conn
    conn.register_object(PATH, Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0], call, None, None)


def name_acquired(conn, name):
    print('ready', flush=True)


def name_lost(conn, name):
    print(f'cannot own {name}', file=sys.stderr, flush=True)
    loop.quit()


Gio.bus_own_name(Gio.BusType.SESSION, INTERFACE, Gio.BusNameOwnerFlags.NONE,
                 bus_acquired, name_acquired, name_lost)
loop = GLib.MainLoop()
loop.run()
sys.exit(1)
