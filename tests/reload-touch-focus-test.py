#!/usr/bin/env python3
"""A touch drag's input grab stays Wayfire's pointer focus after the finger lifts: touch turns
pointer focus off, so removing the grab can't refocus the pointer. The first mouse motion after a
plugin reload then sends that grab a pointer leave. Before the fix its interaction object was freed
and its code unmapped, and the compositor crashed (seed 271828 of tests/state-model-test.sh: a card
finger drag, a reload, then an Esc drag with the mouse). Invoked by reload-touch-focus-test.sh."""
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import tempfile
import time


class Ipc:
    def __init__(self):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.settimeout(10)
        self.sock.connect(os.environ['WAYFIRE_SOCKET'])

    def exactly(self, n):
        data = b''
        while len(data) < n:
            chunk = self.sock.recv(n - len(data))
            if not chunk: raise ConnectionError('compositor disconnected')
            data += chunk
        return data

    def call(self, method, data=None):
        body = json.dumps({'method': method, 'data': data or {}}).encode()
        self.sock.sendall(struct.pack('<I', len(body)) + body)
        reply = json.loads(self.exactly(struct.unpack('<I', self.exactly(4))[0]))
        assert not isinstance(reply, dict) or not reply.get('error'), f'{method}: {reply}'
        return reply


ipc = Ipc()
work = tempfile.TemporaryDirectory(prefix='scottland-reload-touch-')
clients = []


def key(code, state): ipc.call('stipc/feed_key', {'key': code, 'state': state})


def frame(view_id):
    return next(v for v in ipc.call('scottland/layout-state')['views'] if v['id'] == view_id)['frame']


def center(view_id):
    f = frame(view_id)
    return f['x'] + f['width'] / 2, f['y'] + f['height'] / 2


def open_app(app_id):
    clients.append(subprocess.Popen(['foot', '--app-id', app_id, '-T', app_id, '-W', '35x8', 'sleep', '600'],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    time.sleep(.8)
    return next(v['id'] for v in ipc.call('window-rules/list-views') if v['app-id'] == app_id)


def finger_drag(view_id, dx, dy, hold):
    x, y = center(view_id)
    ipc.call('stipc/touch', {'finger': 0, 'x': round(x), 'y': round(y)})
    if hold: time.sleep(.5)  # a finger held still lifts a window into a live drag
    for i in range(1, 13):
        ipc.call('stipc/touch', {'finger': 0, 'x': round(x + dx * i / 12), 'y': round(y + dy * i / 12)})
        time.sleep(.025)
    ipc.call('stipc/touch_release', {'finger': 0})
    time.sleep(.7)


def reload_plugin():
    fresh = Path(work.name) / f'libscottland-{time.monotonic_ns()}.so'
    shutil.copyfile('build/libscottland.so', fresh)
    plugins = ipc.call('wayfire/get-config-option', {'option': 'core/plugins'})['value']
    changed = ' '.join(str(fresh) if p == 'scottland' or '/libscottland-' in p else p for p in plugins.split())
    mark = Path(os.environ['XDG_RUNTIME_DIR']) / 'scottland' / (os.environ['WAYLAND_DISPLAY'] + '.reloading')
    mark.touch()
    try:
        ipc.call('wayfire/set-config-options', {'core/plugins': changed})
        time.sleep(.8)
    finally:
        mark.unlink(missing_ok=True)


def mouse_after_reload(view_id, label):
    x, y = center(view_id)
    reload_plugin()
    ipc.call('stipc/move_cursor', {'x': round(x), 'y': round(y + 30)})  # leaves touch mode
    time.sleep(.2)
    ipc.call('stipc/move_cursor', {'x': round(x + 10), 'y': round(y + 30)})
    assert any(v['id'] == view_id for v in ipc.call('scottland/layout-state')['views'])
    print(f'PASS  {label}', flush=True)


try:
    ipc.call('wayfire/set-config-options', {'scottland/sounds': False})
    width = ipc.call('window-rules/list-outputs')[0]['geometry']['width']

    window = open_app('scottland-reload-touch-window')
    before = frame(window)
    finger_drag(window, 60, 36, hold=True)
    assert abs(frame(window)['x'] - before['x']) > 20, 'fixture must move the window with a touch drag'
    mouse_after_reload(window, 'mouse motion after a window touch drag and a reload')

    # The original sequence: a card moved by finger, a reload, then a mouse drag.
    card_window = open_app('scottland-reload-touch-card')
    x, y = center(card_window)
    ipc.call('stipc/move_cursor', {'x': round(x), 'y': round(y)})
    time.sleep(.1)
    key('KEY_LEFTMETA', True)
    ipc.call('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'press'})
    for i in range(1, 13):
        ipc.call('stipc/move_cursor', {'x': round(x + (width - 6 - x) * i / 12), 'y': round(y)})
        time.sleep(.025)
    time.sleep(.4)
    ipc.call('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'release'})
    key('KEY_LEFTMETA', False)
    time.sleep(1)
    link = next(w for w in ipc.call('scottland/desktop-model')['widgets'] if w['window'] == card_window)
    card = link['widget_view']
    before = frame(card)
    finger_drag(card, 0, 120, hold=False)
    assert abs(frame(card)['y'] - before['y']) > 40, 'fixture must move the card with a finger'
    mouse_after_reload(card, 'mouse motion after a card finger drag and a reload')

    # Reloads with input or animation still in flight, then both kinds of input.
    def alive(label):
        x, y = center(window)
        finger_drag(window, -40, 20, hold=True)
        ipc.call('stipc/move_cursor', {'x': round(x), 'y': round(y)})
        assert any(v['id'] == window for v in ipc.call('scottland/layout-state')['views'])
        print(f'PASS  {label}', flush=True)

    x, y = center(window)
    ipc.call('stipc/touch', {'finger': 0, 'x': round(x), 'y': round(y)})
    time.sleep(.5)
    ipc.call('stipc/touch', {'finger': 0, 'x': round(x + 30), 'y': round(y)})
    assert ipc.call('scottland/desktop-model')['drag']['started'], 'fixture must hold a finger drag'
    reload_plugin()
    ipc.call('stipc/touch', {'finger': 0, 'x': round(x + 60), 'y': round(y)})
    ipc.call('stipc/touch_release', {'finger': 0})
    time.sleep(.5)
    alive('reload with a finger still dragging a window')

    x, y = center(window)
    ipc.call('stipc/move_cursor', {'x': round(x), 'y': round(y)})
    key('KEY_LEFTMETA', True)
    ipc.call('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'press'})
    ipc.call('stipc/move_cursor', {'x': round(width - 40), 'y': round(y)})  # morph toward a card
    time.sleep(.1)
    assert ipc.call('scottland/desktop-model')['drag']['started'], 'fixture must hold a mouse drag'
    reload_plugin()
    ipc.call('stipc/move_cursor', {'x': round(width / 2), 'y': round(y)})
    ipc.call('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'release'})
    key('KEY_LEFTMETA', False)
    time.sleep(.5)
    alive('reload with a mouse drag morphing a window')

    for change in ('collapse', 'expand'):  # reload mid-animation each time
        key('KEY_LEFTMETA', True); key('KEY_M', True); key('KEY_M', False); key('KEY_LEFTMETA', False)
        time.sleep(.05)
        reload_plugin()
        alive(f'reload during the cards\' {change} animation')

    key('KEY_LEFTMETA', True); key('KEY_M', True); key('KEY_M', False); key('KEY_LEFTMETA', False)
    time.sleep(.8)
    x, y = center(card)
    ipc.call('stipc/move_cursor', {'x': round(x), 'y': round(y)})
    deadline = time.monotonic() + 3  # hover a collapsed card: it peeks open
    while not next(w for w in ipc.call('scottland/widgets')['widgets'] if w['window'] == card_window)['peek']:
        assert time.monotonic() < deadline, 'fixture must open a peek'
        time.sleep(.05)
    reload_plugin()
    ipc.call('stipc/move_cursor', {'x': round(x - 200), 'y': round(y)})
    time.sleep(.3)
    alive('reload while a collapsed card peeks open')

    log = Path(os.environ['SCOTTLAND_TEST_STATE']).parent / 'wayfire.log'
    released = log.read_text(errors='replace').count('released stale pointer focus')
    print(f'NOTE  stale pointer focus released at {released} unload(s)', flush=True)
finally:
    for client in clients:
        if client.poll() is None: client.terminate()
    work.cleanup()
