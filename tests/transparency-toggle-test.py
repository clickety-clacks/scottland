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
    info = ipc('window-rules/get-focused-view').get('info')
    return info.get('id') if isinstance(info, dict) else None


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


def wait_opacity(window_id, target, description, tolerance=.005):
    return wait(lambda: opacity(window_id) if near(opacity(window_id), target, tolerance) else None, description)


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


def filtered_tint_delta(name, x, y):
    options(window_mode_tint=0.0)
    time.sleep(.12)
    without_tint = Shot(name + '-without-tint').pixel(x, y)
    options(window_mode_tint=100.0)
    time.sleep(.12)
    with_tint = Shot(name + '-with-tint').pixel(x, y)
    return distance(with_tint, without_tint)


def key(name, down):
    ipc('stipc/feed_key', {'key': 'KEY_' + name, 'state': down})


def tap_key(name):
    key(name, True)
    key(name, False)


def type_filter(query):
    for letter in query:
        tap_key(letter)


def press_hint(window_id, hold=.7):
    text = next(h['hint'] for h in hints()['hints'] if h['window'] == window_id)
    for letter in text[:-1]:
        key(letter.upper(), True); time.sleep(.04); key(letter.upper(), False); time.sleep(.03)
    last = text[-1].upper()
    key(last, True)
    time.sleep(hold)
    key(last, False)


def capture_widget_map_failure(window_id, error):
    snapshot = {
        'window': window_id,
        'error': str(error),
        'wayland_display': os.environ.get('WAYLAND_DISPLAY'),
        'widget_scope': os.environ.get('SCOTTLAND_WIDGET_SCOPE'),
        'ipc': {},
    }
    for method, data in (
        ('scottland/widgets', {}),
        ('scottland/desktop-model', {'slice': 'widgets'}),
        ('scottland/layout-state', {}),
        ('window-rules/list-views', {}),
    ):
        try:
            snapshot['ipc'][method] = ipc(method, data)
        except Exception as capture_error:
            snapshot['ipc'][method] = {'capture_error': str(capture_error)}

    try:
        result = subprocess.run(['ps', '-eo', 'pid,ppid,cgroup,args'], capture_output=True,
                                text=True, timeout=3, check=False)
        snapshot['processes'] = [
            line for line in result.stdout.splitlines()
            if str(repo) in line or 'scottland-widget' in line
        ]
        snapshot['ps_exit'] = result.returncode
        snapshot['ps_stderr'] = result.stderr
    except (OSError, subprocess.SubprocessError) as capture_error:
        snapshot['process_capture_error'] = str(capture_error)

    try:
        (art / 'widget-map-timeout.json').write_text(json.dumps(snapshot, indent=2, sort_keys=True) + '\n')
    except OSError as capture_error:
        print(f'could not retain widget mapping diagnostics: {capture_error}', file=sys.stderr, flush=True)


def widgetize_by_double_tap(window_id):
    key('LEFTALT', True)
    wait(lambda: hints()['active'], 'Window mode activates for widget double tap')
    press_hint(window_id, hold=.08)
    time.sleep(.1)
    press_hint(window_id, hold=.08)
    wait(lambda: next(v for v in views() if v['id'] == window_id)['widgetized'], 'window becomes widget')
    key('LEFTALT', False)
    wait(lambda: not hints()['active'], 'Window mode exits after widget double tap')
    try:
        return wait(lambda: next((w['widget_view'] for w in ipc('scottland/widgets')['widgets']
                                  if w['window'] == window_id and w['widget_view'] > 0), None),
                    'widget toplevel maps')
    except RuntimeError as error:
        capture_widget_map_failure(window_id, error)
        raise


try:
    out = ipc('window-rules/list-outputs')[0]['geometry']
    ox, oy, ow, oh = out['x'], out['y'], out['width'], out['height']
    ax, bx = ox + ow * .25, ox + ow * .40
    y, width, height = oy + oh * .24, ow * .34, oh * .44
    overlap_x = (max(ax, bx) + min(ax + width, bx + width)) / 2
    overlap_y = y + height * .55
    visible_b_x = bx + width * .82
    visible_b_y = y + height * .55
    desktop_x = ox + ow * .05
    desktop_y = oy + oh * .05

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
    # The empty headless output is black before clients map. Sample the uncovered
    # desktop after setup so pixel checks use the same settled desktop as the windows.
    desktop_b = foreground.pixel(desktop_x, desktop_y)
    fg_visible_b = foreground.pixel(visible_b_x, visible_b_y)
    check('solid foreground fixture is distinguishable from desktop', distance(fg_visible_b, desktop_b) > 40)

    options(center_opacity_focused=.65, side_opacity_focused=.65)
    intermediate = wait(lambda: opacity(b) if .70 < opacity(b) < .98 else None,
                        'opacity animation enters an intermediate alpha', timeout=3)
    check('focus opacity is visibly easing before toggle', .70 < intermediate < .98)
    first = toggle(b)
    check('sub-unity target toggles to opaque', first['override'] == 'opaque' and status(b)['override'] == 'opaque')
    # opacity_mix animates for 180 ms. Require exact unity across reads for
    # 250 ms before capturing; a near-unity sample can still be blending.
    opaque_wait_start = time.monotonic()
    opaque_since = None
    while True:
        opaque_alpha = opacity(b)
        observed_at = time.monotonic()
        print(f'opaque settle t={observed_at:.6f} alpha={opaque_alpha!r}', flush=True)
        if opaque_alpha == 1.0:
            if opaque_since is None:
                opaque_since = observed_at
            if observed_at - opaque_since > .250:
                break
        else:
            opaque_since = None
        if observed_at - opaque_wait_start >= 12:
            raise RuntimeError(f'opaque override did not remain exactly 1.0 for 250 ms; '
                               f'last alpha={opaque_alpha!r}, unity since={opaque_since}')
        time.sleep(.015)
    print(f'opaque capture starts t={time.monotonic():.6f} alpha={opaque_alpha!r}', flush=True)
    opaque = Shot('toggle-opaque')
    captured_alpha = opacity(b)
    print(f'opaque capture finished t={time.monotonic():.6f} alpha={captured_alpha!r}', flush=True)
    opaque_foreground = opaque.pixel(visible_b_x, visible_b_y)
    opaque_overlap = opaque.pixel(overlap_x, overlap_y)
    check(f'opaque override produces unblended foreground pixels (alpha={opaque_alpha:.5f}, '
          f'A-alpha={opacity(a):.5f}, focus={focused_id()}, window-mode={hints()["active"]}, '
          f'goo={ipc("scottland/goo-state")["enabled"]}, overlap={opaque_overlap}, visible-B={opaque_foreground})',
          distance(opaque_overlap, opaque_foreground) <= 3)

    # Window-mode filter exclusion controls rendered visibility even for an
    # explicitly opaque window. The override remains stored and is restored
    # when the transient filter clears; the overlay tint must also disappear.
    options(window_mode_tint=100.0)
    key('LEFTALT', True)
    wait(lambda: hints()['active'], 'Window mode activates for opaque filter test')
    tap_key('ENTER')
    key('LEFTALT', False)
    type_filter('TRANSPARENCYA')
    wait_opacity(b, 0.0, 'filter hides window with opaque override')
    check('filter exclusion retains the opaque override', status(b)['override'] == 'opaque')
    filtered_opaque = Shot('filter-opaque-override')
    check('filtered opaque window and tint are absent from desktop pixels',
          distance(filtered_opaque.pixel(visible_b_x, visible_b_y), desktop_b) <= 3)
    check('excluded opaque window has no above-window tint shape',
          filtered_tint_delta('filter-opaque-override-tint', visible_b_x, visible_b_y) <= 3)
    tap_key('ESC')
    wait_opacity(b, 1.0, 'clearing filter restores retained opaque override')
    check('clearing filter preserves opaque override state', status(b)['override'] == 'opaque')
    options(window_mode_tint=7.0)
    tap_key('ESC')
    wait(lambda: not hints()['active'], 'Window mode exits after opaque filter test')

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

    # The same visibility rule applies to a translucent override, including
    # the Window-mode tint layer, without clearing its per-window state.
    options(window_mode_tint=100.0)
    key('LEFTALT', True)
    wait(lambda: hints()['active'], 'Window mode activates for translucent filter test')
    tap_key('ENTER')
    key('LEFTALT', False)
    type_filter('TRANSPARENCYA')
    wait_opacity(b, 0.0, 'filter hides window with translucent override')
    check('filter exclusion retains the translucent override', status(b)['override'] == 'translucent')
    filtered_translucent = Shot('filter-translucent-override')
    check('filtered translucent window and tint are absent from desktop pixels',
          distance(filtered_translucent.pixel(visible_b_x, visible_b_y), desktop_b) <= 3)
    check('excluded translucent window has no above-window tint shape',
          filtered_tint_delta('filter-translucent-override-tint', visible_b_x, visible_b_y) <= 3)
    tap_key('ESC')
    wait_opacity(b, .985, 'clearing filter restores retained translucent override')
    options(window_mode_tint=7.0)
    tap_key('ESC')
    wait(lambda: not hints()['active'], 'Window mode exits after translucent filter test')

    rendered = Shot('translucent-focused')
    expected = blend(opaque_foreground, bg_overlap, .985)
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
    # Use the same-run solid frame validated by the opaque-override pixel check.
    expected_unfocused = blend(opaque_foreground, desktop_b, .96)
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
    widget_geometry = raw_view(widget_id)['geometry']
    widget_pixel_x = widget_geometry['x'] + widget_geometry['width'] * .8
    widget_pixel_y = widget_geometry['y'] + widget_geometry['height'] * .8
    options(window_mode_tint=100.0)
    key('LEFTALT', True)
    wait(lambda: hints()['active'], 'Window mode activates for widget filter test')
    tap_key('ENTER')
    key('LEFTALT', False)
    type_filter('TRANSPARENCYB')
    wait_opacity(widget_id, 0.0, 'filter hides widget with opaque override')
    check('filter exclusion retains widget opaque override', status(widget_id)['override'] == 'opaque')
    check('excluded widget has no above-window tint shape',
          filtered_tint_delta('filter-widget-override-tint', widget_pixel_x, widget_pixel_y) <= 3)
    tap_key('ESC')
    wait_opacity(widget_id, 1.0, 'clearing filter restores retained widget override')
    options(window_mode_tint=7.0)
    tap_key('ESC')
    wait(lambda: not hints()['active'], 'Window mode exits after widget filter test')
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
