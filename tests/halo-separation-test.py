#!/usr/bin/env python3
"""A10/GO2/GO3: independent fallback bands versus pooling, in a private headless session.
Geometry IPC arranges fixtures; pointer/button input checks the exposed band and empty gaps.
"""
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import time

import gi
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf

art = Path(__file__).resolve().parents[1] / 'build/halo-separation-evidence'
art.mkdir(exist_ok=True)
sock = socket.socket(socket.AF_UNIX)
sock.connect(os.environ['WAYFIRE_SOCKET'])
passed = failed = 0
clients = []


def ipc(method, data=None):
    body = json.dumps({'method': method, 'data': data or {}}).encode()
    sock.sendall(struct.pack('<I', len(body)) + body)
    def read(n):
        out = b''
        while len(out) < n:
            chunk = sock.recv(n - len(out))
            if not chunk:
                raise RuntimeError('compositor disconnected')
            out += chunk
        return out
    result = json.loads(read(struct.unpack('<I', read(4))[0]))
    if isinstance(result, dict) and 'error' in result:
        raise RuntimeError(result)
    return result


def check(name, ok):
    global passed, failed
    print(('PASS ' if ok else 'FAIL ') + name, flush=True)
    passed += bool(ok)
    failed += not ok


def view(title):
    return next(v for v in ipc('scottland/layout-state')['views'] if v['title'] == title)


def place(title, x, y):
    ipc('window-rules/configure-view', {'id': view(title)['id'],
        'geometry': {'x': x, 'y': y, 'width': 320, 'height': 180}})
    time.sleep(.4)


def pointer(x, y):
    ipc('stipc/move_cursor', {'x': x, 'y': y})


def drag(x, y, dx, dy):
    pointer(x, y)
    time.sleep(.1)
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'press'})
    for i in range(1, 11):
        pointer(round(x + dx*i/10), round(y + dy*i/10))
        time.sleep(.02)
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'release'})
    pointer(20, 20)
    time.sleep(.8)


def shot(name):
    path = art / (name + '.png')
    subprocess.run(['grim', str(path)], check=True)
    pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(path))
    pixels = pixbuf.get_pixels()
    def pixel(x, y):
        offset = y * pixbuf.get_rowstride() + x * pixbuf.get_n_channels()
        return tuple(pixels[offset:offset+3])
    return pixel


try:
    option = ipc('wayfire/get-config-option', {'option': 'scottland/goo'})
    check('metadata and shipped config enable goo by default',
          str(option['default']).lower() in ('true', '1') and
          str(option['value']).lower() in ('true', '1') and ipc('scottland/goo-state')['enabled'])
    ipc('wayfire/set-config-options', {'scottland/goo': False, 'scottland/center_width': 90,
        'scottland/min_scale': 1, 'scottland/max_scale': 1,
        'scottland/scale_curve': '0:1 1:1', 'scottland/sounds': False})
    for title in ('separate-a', 'separate-b'):
        clients.append(subprocess.Popen(['foot', '-c', '/dev/null', '-T', title, 'sleep', '600'],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        for _ in range(50):
            if any(v['title'] == title for v in ipc('scottland/layout-state')['views']):
                break
            time.sleep(.1)
    place('separate-a', 250, 210)
    place('separate-b', 510, 330)
    pointer(20, 20)
    time.sleep(3)
    off = shot('overlap-halo-off')
    check('overlap leaves the concave corner empty between separate halo bands',
          off(497, 403) == off(100, 100) and off(505, 420) != off(100, 100)
          and off(470, 395) != off(100, 100))
    before = [view(t)['frame'] for t in ('separate-a', 'separate-b')]
    drag(497, 403, -30, 30)
    check('empty concave corner grabs neither window', all(
        abs(view(t)['frame'][k] - f[k]) < .1
        for t, f in zip(('separate-a', 'separate-b'), before) for k in ('x', 'y')))
    ipc('wayfire/set-config-options', {'scottland/goo': True})
    time.sleep(2)
    on = shot('overlap-goo-on')
    field = ipc('scottland/goo-state', {'x': 497, 'y': 403})['screens'][0]
    check('goo pools in the same concave corner', field['density'] > field['threshold']
          and on(497, 403) != on(100, 100))
    ipc('wayfire/set-config-options', {'scottland/goo': False})
    place('separate-b', 596, 210)
    pointer(20, 20)
    time.sleep(3)
    gap = shot('gap-halo-off')
    check('nearby halo bands leave a gap without a bridge', gap(583, 300) == gap(100, 100)
          and gap(575, 300) != gap(100, 100) and gap(591, 300) != gap(100, 100))
    before = [view(t)['frame'] for t in ('separate-a', 'separate-b')]
    drag(583, 300, 0, 30)
    check('empty gap grabs neither window', all(
        abs(view(t)['frame'][k] - f[k]) < .1
        for t, f in zip(('separate-a', 'separate-b'), before) for k in ('x', 'y')))
    f = view('separate-a')['frame']
    drag(244, 300, 40, 20)
    check('independent halo band still moves its own window',
          view('separate-a')['frame']['x'] > f['x'] + 25 and view('separate-a')['frame']['y'] > f['y'] + 10)
finally:
    for v in ipc('scottland/layout-state')['views']:
        if v['title'].startswith('separate-'):
            ipc('window-rules/close-view', {'id': v['id']})
    for p in clients:
        if p.poll() is None:
            p.terminate()
    sock.close()
    print(f'RESULT {passed} passed, {failed} failed', flush=True)
raise SystemExit(bool(failed))
