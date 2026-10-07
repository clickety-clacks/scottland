#!/usr/bin/env python3
"""Per-window transparency IPC, rendering, lifetime, and pairing precedence."""
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time

import gi
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf

assert os.environ.get('SCOTTLAND_TEST_MODEL') == '1', 'caller-owned headless session required'
repo = Path(__file__).resolve().parents[1]
art = Path(sys.argv[1]).resolve()
art.mkdir(parents=True, exist_ok=True)
client_log = (art / 'clients.log').open('w')
sock = socket.socket(socket.AF_UNIX)
sock.settimeout(8)
sock.connect(os.environ['WAYFIRE_SOCKET'])
clients = []
passed = 0


def ipc(method, data=None):
    body = json.dumps(dict(method=method, data=data or {})).encode()
    sock.sendall(struct.pack('<I', len(body)) + body)
    def read(n):
        result = b''
        while len(result) < n:
            chunk = sock.recv(n - len(result))
            if not chunk:
                raise RuntimeError('compositor disconnected')
            result += chunk
        return result
    reply = json.loads(read(struct.unpack('<I', read(4))[0]))
    if isinstance(reply, dict) and 'error' in reply:
        raise RuntimeError(f'{method}: {reply["error"]}')
    return reply


def ipc_allow_error(method, data=None):
    body = json.dumps(dict(method=method, data=data or {})).encode()
    sock.sendall(struct.pack('<I', len(body)) + body)
    def read(n):
        result = b''
        while len(result) < n:
            chunk = sock.recv(n - len(result))
            if not chunk:
                raise RuntimeError('compositor disconnected')
            result += chunk
        return result
    return json.loads(read(struct.unpack('<I', read(4))[0]))


def wait(predicate, description, timeout=12):
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        last = predicate()
        if last:
            return last
        time.sleep(.015)
    raise RuntimeError(f'timed out waiting for {description}; last={last}')


def check(name, condition):
    global passed
    if not condition:
        raise AssertionError(name)
    passed += 1
    print('PASS ' + name, flush=True)


def views():
    return ipc('scottland/layout-state')['views']


def view(name):
    return next((v for v in views() if v.get('title') == name), None)


def raw_view(window_id):
    return next(v for v in ipc('window-rules/list-views') if v['id'] == window_id)


def focused_id():
    return ipc('window-rules/get-focused-view').get('info', {}).get('id')


def hints():
    return ipc('scottland/hints')


def opacity(window_id):
    return next(v for v in views() if v['id'] == window_id)['opacity']


def status(window_id=None):
    data = {'action': 'status'}
    if window_id is not None:
        data['window'] = int(window_id)
    return ipc('scottland/transparency', data)


def toggle(window_id):
    reply = ipc('scottland/transparency', {'action': 'toggle'})
    check('toggle targets the keyboard-focused window', reply['window'] == window_id)
    return reply


def options(**values):
    ipc('wayfire/set-config-options', {'scottland/' + key: value for key, value in values.items()})


def near(a, b, tolerance=.025):
    return abs(float(a) - float(b)) <= tolerance


def wait_opacity(window_id, target, description):
    return wait(lambda: opacity(window_id) if near(opacity(window_id), target) else None, description)


def click(x, y):
    ipc('stipc/move_cursor', {'x': round(x), 'y': round(y)})
    time.sleep(.12)
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'press'})
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'release'})


def focus_pointer(window_id, x_fraction=.5):
    g = raw_view(window_id)['geometry']
    click(g['x'] + g['width'] * x_fraction, g['y'] + g['height'] * .5)


def launch(name, width, height, color):
    path = art / (name + '.palette.json')
    path.write_text(json.dumps(dict(scheme='dark', background=color, foreground='#d8deea', accent='#81a1c1')))
    process = subprocess.Popen(['python3', str(repo / 'tests/hint-style-app.py'), name,
                                str(width), str(height), str(path)], stdout=client_log,
                               stderr=subprocess.STDOUT)
    clients.append(process)
    wait(lambda: view(name), name + ' maps')
    return process, view(name)['id']


def place(window_id, x, y, width, height):
    data = {'id': window_id, 'geometry': dict(x=round(x), y=round(y), width=round(width), height=round(height))}
    for _ in range(8):
        ipc('window-rules/configure-view', data)
        try:
            wait(lambda: (lambda g: g['x'] == data['geometry']['x'] and g['y'] == data['geometry']['y'] and
                          abs(g['width'] - data['geometry']['width']) < 24 and
                          abs(g['height'] - data['geometry']['height']) < 24)(raw_view(window_id)['geometry']),
                 'fixture geometry', timeout=1.5)
            return
        except RuntimeError:
            pass
    raise RuntimeError(f'fixture {window_id} did not take geometry: {raw_view(window_id)["geometry"]}')


class Shot:
    def __init__(self, name):
        self.path = art / (name + '.png')
        subprocess.run(['grim', str(self.path)], check=True, timeout=8)
        image = GdkPixbuf.Pixbuf.new_from_file(str(self.path))
        self.width = image.get_width()
        self.height = image.get_height()
        self.stride = image.get_rowstride()
        self.channels = image.get_n_channels()
        self.data = image.get_pixels()

    def pixel(self, x, y):
        x, y = round(x), round(y)
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise AssertionError(f'pixel ({x},{y}) outside screenshot {self.width}x{self.height}')
        offset = y * self.stride + x * self.channels
        return tuple(self.data[offset:offset + 3])


def rgb(hex_color):
    value = hex_color.removeprefix('#')
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def distance(a, b):
    return max(abs(x - y) for x, y in zip(a, b))


def blend(front, back, alpha):
    return tuple(round(alpha * a + (1 - alpha) * b) for a, b in zip(front, back))


def key(name, down):
    ipc('stipc/feed_key', {'key': 'KEY_' + name, 'state': down})


def press_hint(window_id, hold=.7):
    text = next(h['hint'] for h in hints()['hints'] if h['window'] == window_id)
    for letter in text[:-1]:
        key(letter.upper(), True); time.sleep(.04); key(letter.upper(), False); time.sleep(.03)
    last = text[-1].upper()
    key(last, True)
    time.sleep(hold)
    key(last, False)


def widgetize_by_double_tap(window_id):
    key('LEFTALT', True)
    wait(lambda: hints()['active'], 'Window mode activates for widget double tap')
    press_hint(window_id, hold=.08)
    time.sleep(.1)
    press_hint(window_id, hold=.08)
    wait(lambda: next(v for v in views() if v['id'] == window_id)['widgetized'], 'window becomes widget')
    key('LEFTALT', False)
    wait(lambda: not hints()['active'], 'Window mode exits after widget double tap')
    return wait(lambda: next((w['widget_view'] for w in ipc('scottland/widgets')['widgets']
                              if w['id'] == window_id and w['widget_view'] > 0), None), 'widget toplevel maps')


try:
    out = ipc('window-rules/list-outputs')[0]['geometry']
    ox, oy, ow, oh = out['x'], out['y'], out['width'], out['height']
    ax, bx = ox + ow * .25, ox + ow * .40
    y, width, height = oy + oh * .24, ow * .34, oh * .44
    overlap_x = (max(ax, bx) + min(ax + width, bx + width)) / 2
    overlap_y = y + height * .55
    visible_b_x = bx + width * .82
    visible_b_y = y + height * .55
    desktop = Shot('desktop-before-clients')
    desktop_b = desktop.pixel(visible_b_x, visible_b_y)

    options(sounds=False, alt_hold_delay=300, window_hold_delay=500, window_double_tap_delay=300,
            center_opacity_focused=1.0, center_opacity_unfocused=1.0,
            side_opacity_focused=1.0, side_opacity_unfocused=1.0,
            window_mode_opacity_focused=.42, window_mode_opacity_unfocused=.32)
    app_a, a = launch('TransparencyA', width, height, '#d02020')
    app_b, b = launch('TransparencyB', width, height, '#2080e0')
    check('new windows start without overrides', status(a)['override'] == 'none' and status(b)['override'] == 'none')
    place(a, ax, y, width, height)
    place(b, bx, y, width, height)
    focus_pointer(b, .82)
    wait_opacity(b, 1.0, 'focused window starts opaque')
    foreground = Shot('foreground-opaque')
    fg_overlap = foreground.pixel(overlap_x, overlap_y)
    fg_visible_b = foreground.pixel(visible_b_x, visible_b_y)
    check('solid foreground fixture is distinguishable from desktop', distance(fg_visible_b, desktop_b) > 40)

    options(center_opacity_focused=.65, side_opacity_focused=.65)
    intermediate = wait(lambda: opacity(b) if .70 < opacity(b) < .98 else None,
                        'opacity animation enters an intermediate alpha', timeout=3)
    check('focus opacity is visibly easing before toggle', .70 < intermediate < .98)
    first = toggle(b)
    check('sub-unity target toggles to opaque', first['override'] == 'opaque' and status(b)['override'] == 'opaque')
    wait_opacity(b, 1.0, 'opaque override animation settles')
    opaque = Shot('toggle-opaque')
    check('opaque override produces unblended foreground pixels',
          distance(opaque.pixel(overlap_x, overlap_y), fg_overlap) <= 3)
    second = toggle(b)
    check('next toggle clears the override', second['override'] == 'none' and status(b)['override'] == 'none')
    wait_opacity(b, .65, 'cleared override restores the focused zone target')

    options(center_opacity_focused=1.0, side_opacity_focused=1.0)
    wait_opacity(b, 1.0, 'configured target returns to fully opaque')
    ipc('wm-actions/set-minimized', {'view_id': b, 'state': True})
    time.sleep(.25)
    background = Shot('opaque-background')
    bg_overlap = background.pixel(overlap_x, overlap_y)
    ipc('wm-actions/set-minimized', {'view_id': b, 'state': False})
    wait(lambda: not raw_view(b)['minimized'], 'foreground restores')
    focus_pointer(b, .82)
    wait_opacity(b, 1.0, 'restored foreground is opaque')
    translucent = toggle(b)
    check('unity target toggles to translucent', translucent['override'] == 'translucent')
    wait_opacity(b, .985, 'focused translucent pair')
    rendered = Shot('translucent-focused')
    expected = blend(fg_overlap, bg_overlap, .985)
    check('0.985 focused override matches rendered overlap blend',
          distance(rendered.pixel(overlap_x, overlap_y), expected) <= 5)

    focus_pointer(a, .18)
    wait_opacity(b, .96, 'unfocused translucent pair')
    status_b = status(b)
    check('status remains per-window after focus moves', status_b['override'] == 'translucent' and status_b['window'] == b)
    options(center_opacity_unfocused=.75, side_opacity_unfocused=.75)
    key('LEFTALT', True)
    wait(lambda: hints()['active'], 'Window mode activates')
    wait_opacity(b, .96, 'override wins over changed zone and Window mode opacity')
    key('LEFTALT', False)
    wait(lambda: not hints()['active'], 'Window mode exits')
    rendered_unfocused = Shot('translucent-unfocused')
    expected_unfocused = blend(fg_visible_b, desktop_b, .96)
    check('0.96 unfocused override matches rendered pixel blend',
          distance(rendered_unfocused.pixel(visible_b_x, visible_b_y), expected_unfocused) <= 5)

    focus_pointer(b, .82)
    wait_opacity(b, .985, 'window regains focused translucent opacity')
    options(center_opacity_focused=.65, side_opacity_focused=.65)
    cleared = toggle(b)
    check('clearing while focused reports no override', cleared['override'] == 'none')
    wait_opacity(b, .65, 'cleared override restores the current configured opacity')

    options(center_opacity_focused=1.0, side_opacity_focused=1.0)
    wait_opacity(b, 1.0, 'fullscreen fixture is opaque before toggle')
    check('opaque target selects translucent on a fresh toggle', toggle(b)['override'] == 'translucent')
    wait_opacity(b, .985, 'translucent target before fullscreen')
    ipc('wm-actions/set-fullscreen', {'view_id': b, 'state': True})
    wait(lambda: raw_view(b).get('fullscreen'), 'window enters fullscreen')
    wait_opacity(b, 1.0, 'fullscreen remains opaque')
    check('fullscreen retains the mapped override', status(b)['override'] == 'translucent')
    ipc('wm-actions/set-fullscreen', {'view_id': b, 'state': False})
    wait(lambda: not raw_view(b).get('fullscreen'), 'window leaves fullscreen')
    wait_opacity(b, .985, 'translucent override resumes after fullscreen')

    check('clearing the fullscreen override restores the current zone target', toggle(b)['override'] == 'none')
    options(center_opacity_focused=.65, side_opacity_focused=.65)
    wait_opacity(b, .65, 'base opacity is translucent before fullscreen toggle')
    ipc('wm-actions/set-fullscreen', {'view_id': b, 'state': True})
    wait(lambda: raw_view(b).get('fullscreen'), 'window enters fullscreen with no override')
    wait_opacity(b, 1.0, 'fullscreen target is fully opaque before toggle')
    fullscreen_base = Shot('fullscreen-zone-opacity')
    check('fullscreen without an override renders the solid client pixel',
          distance(fullscreen_base.pixel(ox + ow * .5, oy + oh * .5), rgb('#2080e0')) <= 4)
    fullscreen_toggle = toggle(b)
    check('toggle samples fullscreen as shown opaque and selects translucent',
          fullscreen_toggle['override'] == 'translucent' and status(b)['override'] == 'translucent')
    wait_opacity(b, 1.0, 'translucent override remains visually opaque in fullscreen')
    fullscreen_override = Shot('fullscreen-with-translucent-override')
    check('translucent override remains fully opaque in fullscreen pixels',
          distance(fullscreen_override.pixel(ox + ow * .5, oy + oh * .5), rgb('#2080e0')) <= 4)
    ipc('wm-actions/set-fullscreen', {'view_id': b, 'state': False})
    wait(lambda: not raw_view(b).get('fullscreen'), 'window leaves fullscreen after toggle')
    wait_opacity(b, .985, 'fullscreen-selected override resumes after exit')
    check('clearing fullscreen-selected override restores the current zone target', toggle(b)['override'] == 'none')
    wait_opacity(b, .65, 'zone target resumes after fullscreen-selected override clears')

    check('other window keeps an independent empty override', status(a)['override'] == 'none')
    ipc('wm-actions/set-minimized', {'view_id': b, 'state': True})
    ipc('wm-actions/set-minimized', {'view_id': a, 'state': True})
    wait(lambda: focused_id() is None, 'no toplevel remains focused')
    no_focus = ipc_allow_error('scottland/transparency', {'action': 'toggle'})
    message = str(no_focus.get('error', ''))
    check('no focused window returns a one-line IPC error', bool(message) and '\n' not in message)
    ipc('wm-actions/set-minimized', {'view_id': a, 'state': False})
    ipc('wm-actions/set-minimized', {'view_id': b, 'state': False})

    options(widget_opacity_focused=.65, widget_opacity_unfocused=.78)
    ipc('window-rules/focus-view', {'id': a})
    widget_id = widgetize_by_double_tap(a)
    ipc('window-rules/focus-view', {'id': widget_id})
    wait_opacity(widget_id, .65, 'focused widget uses its configured opacity')
    widget_on = toggle(widget_id)
    check('focused widget accepts an independent transparency override', widget_on['override'] == 'opaque' and
          status(widget_id)['override'] == 'opaque')
    check('clearing widget override restores its configured opacity', toggle(widget_id)['override'] == 'none')
    wait_opacity(widget_id, .65, 'widget returns to its configured target')

    focus_pointer(b, .82)
    wait_opacity(b, .65, 'window has its base opacity before lifetime check')
    check('window can hold its own live override before closing', toggle(b)['override'] == 'opaque')
    app_b.terminate(); app_b.wait(timeout=5)
    wait(lambda: view('TransparencyB') is None, 'unmapped window disappears from model')
    no_window = ipc_allow_error('scottland/transparency', {'action': 'status', 'window': b})
    check('status rejects an unmapped window id', 'no such window' in str(no_window.get('error', '')))

    reopened_b, reopened_id = launch('TransparencyB', width, height, '#2080e0')
    check('reopening the same app starts with no saved override',
          reopened_id != b and status(reopened_id)['override'] == 'none')
    reopened_b.terminate(); reopened_b.wait(timeout=5)
    wait(lambda: view('TransparencyB') is None, 'reopened window unmaps')

    options(center_opacity_focused=.62, side_opacity_focused=.62,
            center_opacity_unfocused=.78, side_opacity_unfocused=.78)
    app_c, c = launch('TransparencyPairC', width, height, '#d02020')
    app_d, d = launch('TransparencyPairD', width, height, '#2080e0')
    place(c, ox + ow * .20, y, width, height)
    place(d, ox + ow * .60, y, width, height)
    ipc('window-rules/focus-view', {'id': c})
    before_pair_c = raw_view(c)['geometry']
    before_pair_d = raw_view(d)['geometry']
    key('LEFTALT', True)
    wait(lambda: hints()['active'], 'Window mode activates for pairing')
    check('pairing starts with the expected focused window', hints()['selected'] == c)
    press_hint(d)
    key('LEFTALT', False)
    wait(lambda: not hints()['active'], 'Window mode exits after pairing')
    wait(lambda: (raw_view(c)['geometry'] != before_pair_c and raw_view(d)['geometry'] != before_pair_d),
         'real hint input repositions both paired windows')
    ipc('window-rules/focus-view', {'id': c})
    wait_opacity(c, 1.0, 'freshly paired window is opaque once')
    paired_toggle = toggle(c)
    check('first transparency toggle spends pairing hold and selects translucent',
          paired_toggle['override'] == 'translucent')
    wait_opacity(c, .985, 'paired window uses focused transparency override')
    paired_clear = toggle(c)
    check('second toggle clears override without restoring pairing opacity', paired_clear['override'] == 'none')
    wait_opacity(c, .62, 'paired window returns to current zone opacity')
    check('other paired window retains its independent opaque-once hold', near(opacity(d), 1.0))

    print(f'{passed} passed, 0 failed', flush=True)
finally:
    try:
        key('LEFTALT', False)
    except Exception:
        pass
    for process in clients:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
    client_log.close()
    sock.close()
