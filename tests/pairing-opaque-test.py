#!/usr/bin/env python3
"""WK36: pairing shows both windows fully opaque once; a later manipulation returns natural opacity.

Judged by pixels: each window is a solid-color GTK client, and its opacity is estimated from a
grim capture against the exposed output clear color sampled from that same capture. The compositor's
reported opacity is used only to wait for eased values to settle.

IPC config, initial sizes and the first focus are fixture setup. Peripheral placement and later
moves are real Super-drags, pairing is a real Alt + hint hold, selection and the zone cycle are
real hint keys.
"""
import json
import os
from collections import Counter
from pathlib import Path
import signal
import socket
import struct
import subprocess
import sys
import time

assert os.environ.get('SCOTTLAND_TEST_MODEL') == '1', 'caller-owned headless session required'
art = Path(sys.argv[1]).resolve(); art.mkdir(parents=True, exist_ok=True)
sock = socket.socket(socket.AF_UNIX); sock.settimeout(8); sock.connect(os.environ['WAYFIRE_SOCKET'])
clients, held, observations = [], set(), []
passed = failed = 0
signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))

OPACITY = {'center_opacity_focused': .8, 'center_opacity_unfocused': .5,
           'side_opacity_focused': .7, 'side_opacity_unfocused': .4,
           'window_mode_opacity_focused': .9, 'window_mode_opacity_unfocused': .6}
TOLERANCE = .07


def ipc(method, data=None):
    body = json.dumps(dict(method=method, data=data or {})).encode()
    sock.sendall(struct.pack('<I', len(body)) + body)
    def read(n):
        out = b''
        while len(out) < n:
            chunk = sock.recv(n - len(out))
            if not chunk: raise RuntimeError('compositor disconnected')
            out += chunk
        return out
    result = json.loads(read(struct.unpack('<I', read(4))[0]))
    if isinstance(result, dict) and 'error' in result: raise RuntimeError(result)
    return result


def wait(fn, name, timeout=12):
    end = time.monotonic() + timeout; last = None
    while time.monotonic() < end:
        last = fn()
        if last: return last
        time.sleep(.03)
    raise RuntimeError(f'{name} timed out; last={last}')


def check(ok, name, detail=None):
    global passed, failed
    passed += bool(ok); failed += not ok
    print(('PASS ' if ok else 'FAIL ') + name + ('' if detail is None else ' ' + json.dumps(detail)), flush=True)


def key(name, down):
    ipc('stipc/feed_key', dict(key='KEY_' + name, state=down))
    (held.add if down else held.discard)(name)


def button(down):
    ipc('stipc/feed_button', dict(combo='BTN_LEFT', mode='press' if down else 'release'))
    (held.add if down else held.discard)('BTN_LEFT')


def geometry(identifier):
    return next(v for v in ipc('window-rules/list-views') if v['id'] == identifier)['geometry']
def center(g): return g['x'] + g['width'] / 2, g['y'] + g['height'] / 2
def layout(identifier):
    return next(v for v in ipc('scottland/layout-state')['views'] if v['id'] == identifier)
def hint(identifier):
    return next(h for h in ipc('scottland/hints')['hints'] if h['window'] == identifier)
def focused():
    return (ipc('window-rules/get-focused-view').get('info') or {}).get('id')


def alt(down):
    key('LEFTALT', down)
    wait(lambda: ipc('scottland/hints')['active'] == down, 'Window mode transition')


def settled(ids):
    """Geometry, scale and eased opacity unchanged over several polls (readiness only)."""
    last, repeats = None, 0
    def observe():
        nonlocal last, repeats
        value = [(geometry(i), round(layout(i)['applied_scale'], 4), round(layout(i)['opacity'], 3),
                  layout(i).get('frame')) for i in ids]
        repeats = repeats + 1 if value == last else 0; last = value
        return repeats >= 5
    wait(observe, 'settled geometry/scale/opacity')


def capture(name):
    path = art / (name + '.ppm')
    subprocess.run(['grim', '-t', 'ppm', str(path)], check=True, timeout=8)
    data = path.read_bytes()
    parts, i = [], 0
    while len(parts) < 4:  # P6, width, height, maxval, separated by whitespace
        while data[i:i+1].isspace(): i += 1
        j = i
        while not data[j:j+1].isspace(): j += 1
        parts.append(data[i:j]); i = j
    i += 1
    width, height = int(parts[1]), int(parts[2])
    return dict(width=width, height=height, pixels=data[i:])


def pixel(image, x, y):
    k = (y * image['width'] + x) * 3
    return tuple(image['pixels'][k:k+3])


def clear_color(image):
    """Read the contemporaneous, exposed output background from a sparse pixel grid.
    The headless output has a flat clear color. Exclude every visible client and its halo so
    this reference is not contaminated by a window, then fail closed if the exposed pixels
    do not provide a consistent background sample.
    """
    views = ipc('scottland/layout-state')['views']
    frames = [v['frame'] for v in views if v.get('frame') and not v.get('hidden')]
    samples = []
    for y in range(4, image['height'], 8):
        for x in range(4, image['width'], 8):
            if any(f['x'] - 32 <= x <= f['x'] + f['width'] + 32 and
                   f['y'] - 32 <= y <= f['y'] + f['height'] + 32 for f in frames):
                continue
            samples.append(pixel(image, x, y))
    if len(samples) < 32:
        raise RuntimeError(f'not enough exposed output pixels for clear-color reference: {len(samples)}')
    color, count = Counter(samples).most_common(1)[0]
    if count / len(samples) < .90:
        raise RuntimeError(f'exposed output background is not flat: color={color}, support={count}/{len(samples)}')
    return color


def alpha(image, identifier, color, background_color, shift=(0, 0)):
    """Median opacity over interior samples: (shown - clear color) / (color - clear color).
    Samples another window's frame (halo included) covers are skipped: their background is not
    the exposed output's. shift: how far a held drag has moved the window from its frame."""
    views = ipc('scottland/layout-state')['views']
    f = next(v for v in views if v['id'] == identifier)['frame']
    others = [v['frame'] for v in views if v['id'] != identifier and v.get('frame') and not v.get('hidden')]
    covered = lambda x, y: any(o['x'] - 20 <= x <= o['x'] + o['width'] + 20 and
                               o['y'] - 20 <= y <= o['y'] + o['height'] + 20 for o in others)
    estimates = []
    for fx in (.25, .4, .6, .75):
        for fy in (.55, .7, .85):  # below a GTK header bar
            x, y = round(f['x'] + shift[0] + f['width'] * fx), round(f['y'] + shift[1] + f['height'] * fy)
            if covered(x, y): continue
            p, b = pixel(image, x, y), background_color
            d = [c - bb for c, bb in zip(color, b)]
            n = sum(v * v for v in d)
            if n > 60 * 60: estimates.append(sum((pp - bb) * v for pp, bb, v in zip(p, b, d)) / n)
    estimates.sort()
    return round(estimates[len(estimates) // 2], 3) if estimates else None


def natural(identifier):
    """The opacity a window would have by the ordinary rules (A14) for its zone and focus."""
    is_focused = focused() == identifier
    if ipc('scottland/hints')['active']:
        return OPACITY['window_mode_opacity_' + ('focused' if is_focused else 'unfocused')]
    zone = 'center' if layout(identifier)['zone'] == 'center' else 'side'
    return OPACITY[f'{zone}_opacity_' + ('focused' if is_focused else 'unfocused')]


def judge(step, expectations, shift=(0, 0)):
    """expectations: {label: (id, color, expected opacity)}; one capture, every window judged."""
    settled([i for i, _, _ in expectations.values()])
    image = capture(step)
    bg = clear_color(image)
    for label, (identifier, color, expected) in expectations.items():
        seen = alpha(image, identifier, color, bg, shift)
        observations.append(dict(step=step, window=label, expected=expected, pixels=seen,
            reported=round(layout(identifier)['opacity'], 3), zone=layout(identifier)['zone'],
            clear_color=bg))
        if expected is None:
            check(seen is not None and seen <= 1 - 2 * TOLERANCE, f'{step}: {label} is translucent', dict(pixels=seen))
        else:
            check(seen is not None and abs(seen - expected) <= TOLERANCE,
                  f'{step}: {label} shows opacity {expected:g}', dict(pixels=seen, expected=expected))


APP = """import sys, gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, Gdk
app = Gtk.Application(application_id='org.scottland.PairOpaque.' + sys.argv[1])
def activate(a):
 css = Gtk.CssProvider(); css.load_from_data(('window { background: %s; }' % sys.argv[3]).encode())
 Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, 800)
 w = Gtk.ApplicationWindow(application=a, title=sys.argv[1]); w.set_default_size(int(sys.argv[2]), 300); w.present()
app.connect('activate', activate); app.run([])
"""


def launch(title, width, color):
    clients.append(subprocess.Popen([sys.executable, '-c', APP, title, str(width), '#%02x%02x%02x' % color],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    return wait(lambda: next((v['id'] for v in ipc('window-rules/list-views') if v.get('title') == title), None), 'map client')


def place(identifier, x, y, width, height=300):
    ipc('window-rules/configure-view', dict(id=identifier, geometry=dict(x=round(x), y=round(y), width=width, height=height)))
    wait(lambda: geometry(identifier)['width'] == width, 'fixture width')


def drag(identifier, target, raise_first=False):
    """A real Super-drag from the window's drawn center to target, held still before the drop."""
    if raise_first:  # fixture: windows placed on top of each other at setup
        ipc('window-rules/focus-view', dict(id=identifier)); wait(lambda: focused() == identifier, 'fixture focus')
    f = layout(identifier)['frame']
    start = (f['x'] + f['width'] / 2, f['y'] + f['height'] / 2)
    ipc('stipc/move_cursor', dict(x=round(start[0]), y=round(start[1])))
    key('LEFTMETA', True); button(True)
    for i in range(1, 13):
        ipc('stipc/move_cursor', dict(x=round(start[0] + (target[0]-start[0])*i/12),
                                     y=round(start[1] + (target[1]-start[1])*i/12)))
        time.sleep(.02)  # pace a real gesture
    time.sleep(.25)  # intentional still hold so the drop has no release coast
    button(False); key('LEFTMETA', False)


def press_hint(identifier, hold_until=None):
    text = hint(identifier)['hint']
    for letter in text[:-1]: key(letter.upper(), True); key(letter.upper(), False)
    key(text[-1].upper(), True)
    if hold_until: hold_until()
    key(text[-1].upper(), False)


def pair(held_id, partner_id, scenario):
    ipc('window-rules/focus-view', dict(id=partner_id))  # fixture: the partner has focus
    wait(lambda: focused() == partner_id, 'fixture focus')
    alt(True)
    press_hint(held_id, lambda: wait(lambda: all(abs(center(geometry(i))[1] - H / 2) < 2
                                                  for i in (held_id, partner_id)), scenario + ': real hint hold pairs'))


RED, BLUE, GREEN = (225, 20, 20), (20, 40, 225), (20, 200, 40)
try:
    ipc('wayfire/set-config-options', {'scottland/sounds': False, 'scottland/alt_hold_delay': 100,
        'scottland/window_hold_delay': 350, 'scottland/window_double_tap_delay': 1,
        'scottland/window_mode_tint': 0.0,
        **{'scottland/' + k: v for k, v in OPACITY.items()}})
    output = ipc('window-rules/list-outputs')[0]['geometry']; W, H = output['width'], output['height']
    # A bystander, unfocused in the center, never paired: ordinary rules throughout.
    by = launch('Bystander', round(W * .22), GREEN)
    place(by, W / 2 - W * .11, 30, round(W * .22), 120)
    drag(by, (W / 2, 90), True)  # a real drop at the top, clear of where the pairs form
    wait(lambda: abs(center(geometry(by))[1] - 90) < 4, 'bystander dropped at the top')

    for scenario, width in (('peripheral', round(W * .74)), ('center', round(W * .36))):
        left, right = launch(scenario + 'Left', width, RED), launch(scenario + 'Right', width, BLUE)
        for identifier in (left, right): place(identifier, W / 2 - width / 2, H / 2 - 150, width)
        if scenario == 'peripheral':
            # Both start translucent in the periphery, scaled, from real drops.
            drag(left, (W * .13, H * .62), True); settled([left])
            drag(right, (W * .87, H * .8), True); settled([right])
        else:
            # Unscaled, either side of the center zone, translucent by the ordinary rules.
            drag(left, (W * .3, H * .65), True); settled([left])
            drag(right, (W * .7, H * .65), True); settled([right])
        # The last-dropped window keeps the opacity its drag motion computed after the drop, here
        # center's instead of the side's (0.8, not 0.7; the same on 230ed04, before this change),
        # so it is judged only as translucent; the others by their exact ordinary values.
        judge(scenario + ' 1 before pairing', {
            'left': (left, RED, natural(left)), 'right': (right, BLUE, None),
            'bystander': (by, GREEN, natural(by))})

        pair(left, right, scenario)
        # Alt still held: Window mode's pair (.9/.6) would apply to anything not just paired.
        judge(scenario + ' 2 paired, Alt held', {
            'left': (left, RED, 1.0), 'right': (right, BLUE, 1.0), 'bystander': (by, GREEN, natural(by))})
        alt(False)
        judge(scenario + ' 3 paired, Alt released', {
            'left': (left, RED, 1.0), 'right': (right, BLUE, 1.0), 'bystander': (by, GREEN, natural(by))})

        # A real drag of one: it returns to its natural opacity; its partner was not touched.
        paired_right = geometry(right)
        drag(left, (W * .12, H * .2))
        wait(lambda: abs(center(geometry(left))[1] - H / 2) > 20, scenario + ': drag dropped')
        judge(scenario + ' 4 left dragged', {
            'left': (left, RED, natural(left)), 'right': (right, BLUE, 1.0), 'bystander': (by, GREEN, natural(by))})

        if scenario == 'peripheral':
            # Selecting the untouched partner with its hint focuses it without moving it.
            alt(True)
            press_hint(right)
            wait(lambda: focused() == right, 'hint selection focuses the partner')
            check(geometry(right) == paired_right, 'peripheral: selection does not move the partner')
            alt(False)
            judge(scenario + ' 5 partner selected, not moved', {
                'left': (left, RED, natural(left)), 'right': (right, BLUE, 1.0)})
            # A real zone cycle of the partner (its hint again, now focused, acting on release).
            alt(True)
            press_hint(right)
            wait(lambda: geometry(right) != paired_right, 'cycle moved the partner')
            settled([right]); alt(False)
            judge(scenario + ' 6 partner cycled', {
                'left': (left, RED, natural(left)), 'right': (right, BLUE, natural(right)),
                'bystander': (by, GREEN, natural(by))})
        else:
            # While the partner is held in a real drag it is no longer opaque; Esc returns it to
            # where the move began (L27), as it was.
            f = layout(right)['frame']; start = (f['x'] + f['width'] / 2, f['y'] + f['height'] / 2)
            ipc('stipc/move_cursor', dict(x=round(start[0]), y=round(start[1])))
            key('LEFTMETA', True); button(True)
            for i in range(1, 9):
                ipc('stipc/move_cursor', dict(x=round(start[0]), y=round(start[1] - 120 * i / 8)))
                time.sleep(.02)  # pace a real gesture
            judge(scenario + ' 5 right held mid-drag', {'right': (right, BLUE, natural(right))}, shift=(0, -120))
            key('ESC', True); key('ESC', False)
            button(False); key('LEFTMETA', False)
            wait(lambda: geometry(right) == paired_right, scenario + ': Esc returned the partner')
            judge(scenario + ' 6 right returned by Esc', {'right': (right, BLUE, 1.0)})
            # A real drag of the partner too.
            drag(right, (W * .8, H * .3))
            wait(lambda: geometry(right) != paired_right, scenario + ': partner drag dropped')
            judge(scenario + ' 7 right dragged', {
                'left': (left, RED, natural(left)), 'right': (right, BLUE, natural(right)),
                'bystander': (by, GREEN, natural(by))})
        for identifier in (left, right): ipc('window-rules/close-view', dict(id=identifier))
        wait(lambda: not any(v['id'] in (left, right) for v in ipc('window-rules/list-views')), 'close fixture')
    (art / 'opacity.json').write_text(json.dumps(observations, indent=2))
    print(f'{passed} passed, {failed} failed', flush=True)
    sys.exit(1 if failed else 0)
finally:
    for name in list(held):
        try:
            if name == 'BTN_LEFT': button(False)
            else: key(name, False)
        except Exception: pass
    for p in clients:
        if p.poll() is None: p.terminate()
    for p in clients:
        try: p.wait(timeout=3)
        except subprocess.TimeoutExpired: p.kill(); p.wait()
    sock.close()
