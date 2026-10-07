#!/usr/bin/env python3
"""Spread and solo (docs/spread.md) with real stipc input in an isolated headless session.

Fixture geometry and focus are set over IPC; every hint hold, three-finger hold, drag and drop is
real input. Usage: spread-test.py ARTIFACTS
"""
import json
import math
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time

assert os.environ.get('SCOTTLAND_TEST_MODEL') == '1'
art = Path(sys.argv[1]).resolve(); art.mkdir(parents=True, exist_ok=True)
sock = socket.socket(socket.AF_UNIX); sock.settimeout(15); sock.connect(os.environ['WAYFIRE_SOCKET'])

def ipc(method, data=None):
    body = json.dumps({'method': method, 'data': data or {}}).encode()
    sock.sendall(struct.pack('<I', len(body)) + body)
    def read(n):
        out = b''
        while len(out) < n:
            chunk = sock.recv(n - len(out))
            if not chunk: raise RuntimeError('compositor disconnected')
            out += chunk
        return out
    reply = json.loads(read(struct.unpack('<I', read(4))[0]))
    if isinstance(reply, dict) and 'error' in reply: raise RuntimeError(reply)
    return reply

def wait(predicate, timeout=10, what='state'):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        result = predicate()
        if result: return result
        time.sleep(.02)
    raise RuntimeError(what + ' timeout')

passed = failed = 0
def check(ok, name, detail=''):
    global passed, failed
    print(('PASS ' if ok else 'FAIL ') + name + (f'  [{detail}]' if detail and not ok else ''), flush=True)
    if ok: passed += 1
    else: failed += 1

def views(): return ipc('window-rules/list-views')
def geometry(id): return next(v for v in views() if v['id'] == id)['geometry']
def layout(id): return next(v for v in ipc('scottland/layout-state')['views'] if v['id'] == id)
def hints(): return ipc('scottland/hints')
def hint(id): return next(h for h in hints()['hints'] if h['window'] == id)
def spread(): return ipc('scottland/spread-state')
def key(code, down): ipc('stipc/feed_key', {'key': 'KEY_' + code, 'state': down})
def pointer(x, y): ipc('stipc/move_cursor', {'x': round(x), 'y': round(y)})
def button(down): ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'press' if down else 'release'})
def shot(name): subprocess.run(['grim', str(art / name)], check=True)
def focused_id(): return ipc('window-rules/get-focused-view').get('info', {}).get('id')

def alt(down):
    key('LEFTALT', down)
    if down: wait(lambda: hints()['active'], what='window mode')
    else: wait(lambda: not hints()['active'], what='window mode end')

def press_hint(id, hold=0.08):
    text = hint(id)['hint']
    for letter in text[:-1]:
        key(letter.upper(), True); time.sleep(.04); key(letter.upper(), False); time.sleep(.03)
    last = text[-1].upper(); key(last, True); time.sleep(hold); key(last, False)

def center(id):
    g = geometry(id); return (g['x'] + g['width'] / 2, g['y'] + g['height'] / 2)

def footprint(id):
    g = geometry(id); s = layout(id)['applied_scale']; cx, cy = center(id)
    return (cx - g['width'] * s / 2, cy - g['height'] * s / 2, cx + g['width'] * s / 2, cy + g['height'] * s / 2)

def shown(id):
    f = layout(id).get('scene_frame') or layout(id)['frame']
    return (f['x'], f['y'], f['x'] + f['width'], f['y'] + f['height'])

def overlap(a, b, tolerance=1):
    return min(a[2], b[2]) - max(a[0], b[0]) > tolerance and min(a[3], b[3]) - max(a[1], b[1]) > tolerance

def settle(ids, timeout=4):
    end = time.monotonic() + timeout; last = None
    while time.monotonic() < end:
        now = [(geometry(i)['x'], geometry(i)['y'], round(layout(i)['applied_scale'], 4), tuple(round(v) for v in shown(i)))
               for i in ids]
        if now == last: return
        last = now; time.sleep(.15)

GTK_APP = """import sys, gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk
app = Gtk.Application(application_id='org.scottland.SpreadTest.' + sys.argv[1])
def activate(a):
    w = Gtk.ApplicationWindow(application=a, title=sys.argv[1]); w.set_default_size(400, 300); w.present()
app.connect('activate', activate); app.run([])
"""
clients = []
def launch(title):
    clients.append(subprocess.Popen([sys.executable, '-c', GTK_APP, title],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    return wait(lambda: next((v['id'] for v in views() if v.get('title') == title), None), what='launch ' + title)

def place(id, x, y, w, h):
    data = {'id': id, 'geometry': {'x': x, 'y': y, 'width': w, 'height': h}}
    for attempt in range(8):
        ipc('window-rules/configure-view', data)
        try:
            wait(lambda: (lambda g: (g['x'], g['y'], g['width'], g['height']) == (x, y, w, h))(geometry(id)), 1.5)
            return
        except RuntimeError: pass
    raise RuntimeError(f'fixture placement {w}x{h}+{x}+{y} -> {geometry(id)}')

def setup(spec, focus_last):
    """spec: [(id, x, y, w, h)] back to front; focus_last ends focused and in front."""
    if hints()['active']: alt(False)
    for id, x, y, w, h in spec: place(id, x, y, w, h)
    for id, *_ in spec: ipc('window-rules/focus-view', {'id': id}); time.sleep(.05)
    ipc('window-rules/focus-view', {'id': focus_last})
    settle([s[0] for s in spec])
    # A just-mapped client can still answer late with its default size: place it again.
    for attempt in range(3):
        drifted = [(id, x, y, w, h) for id, x, y, w, h in spec
                   if (lambda g: (g['x'], g['y'], g['width'], g['height']))(geometry(id)) != (x, y, w, h)]
        if not drifted: return
        for item in drifted: place(*item)
        settle([s[0] for s in spec])
    raise RuntimeError(f'fixture drifted: {[(d[0], geometry(d[0])) for d in drifted]}')

def solves(): return spread()['solves']

def wait_solve(before, timeout=5):
    wait(lambda: solves() > before and not spread()['running'], timeout, 'solve')
    return spread()['last']

try:
    ipc('wayfire/set-config-options', {'scottland/sounds': False, 'scottland/window_hold_delay': 500,
        'scottland/window_avoidance_always': False, 'scottland/hint_avoidance_always': False,
        'output:HEADLESS-1/mode': '2560x1440@60000'})
    time.sleep(1)
    out = ipc('window-rules/list-outputs')[0]['geometry']
    W, H = out['width'], out['height']
    center_half = W * 33.333 / 200
    def zone_of(x): return 'center' if abs(x - W / 2) <= center_half else ('rail' if abs(x - W / 2) >= W / 2 - W * .02 else 'periphery')
    S, A1, A2, R = launch('SoloS'), launch('SoloA1'), launch('SoloA2'), launch('SoloR')
    ids = (S, A1, A2, R)
    scene = [(R, 60, 900, 600, 400), (A1, 700, 100, 600, 450), (A2, 1300, 600, 500, 400), (S, 830, 370, 900, 700)]

    def check_spread(name, result, solo, arrivals, resident_before):
        settle(ids)
        check(result['status'] == 'clear', f'{name}: the solve is clear', result['status'])
        check(result['complete'], f'{name}: the solve completed (slices: {result["slices"]}, longest '
              f'{result["longest_slice_ms"]:.2f} ms, solving {result["solving_ms"]:.2f} ms)')
        for a in arrivals:
            cx, _ = center(a)
            check(zone_of(cx) == 'periphery', f'{name}: arrival {a} is in the periphery', f'{cx}')
            check(abs(layout(a)['applied_scale'] - layout(a)['scale']) < .01, f'{name}: arrival {a} shows its zone scale')
        check(geometry(R) == resident_before, f'{name}: the resident nothing lands on is unmoved',
              f'{resident_before} -> {geometry(R)}')
        check(zone_of(center(solo)[0]) == 'center' and abs(layout(solo)['applied_scale'] - 1) < .01,
              f'{name}: the solo window is in the center at full scale')
        rects = {i: footprint(i) for i in ids}
        clashes = [(a, b) for a in ids for b in ids if a < b and (a in arrivals or b in arrivals or a == solo or b == solo)
                   and overlap(rects[a], rects[b])]
        check(not clashes, f'{name}: nothing overlaps the solo or an arrival', str(clashes))
        return {i: geometry(i) for i in ids}

    # 1. Keyboard solo: hold the focused window's hint (WK35). Committed outright.
    setup(scene, S)
    r_before = geometry(R); n = solves()
    alt(True); press_hint(S, hold=.75); alt(False)
    result = wait_solve(n)
    check(result['purpose'] == 'solo', 'hint hold on the focused window solos it', result['purpose'])
    after = check_spread('keyboard solo', result, S, (A1, A2), r_before)
    check(center(A1)[0] < W / 2 and center(A2)[0] > W / 2, 'keyboard solo: each arrival goes to its nearer side')
    shot('keyboard-solo.png')
    time.sleep(1.5)
    check({i: geometry(i) for i in ids} == after, 'keyboard solo: committed, nothing returns (no undo)')

    # 1b. With always-on window avoidance, the solo window in front is drawn at its own spot and
    # stays there (P14); the others peek around it.
    ipc('wayfire/set-config-options', {'scottland/window_avoidance_always': True})
    setup(scene, S)
    n = solves()
    alt(True); press_hint(S, hold=.75); alt(False)
    wait_solve(n); settle(ids, 6); time.sleep(.8)
    solo_spot = geometry(S); h = hint(S)
    check(abs(h['dx']) < .5 and abs(h['dy']) < .5,
          'peeking: the solo window has no avoidance offset (offset diagnostic)', str(h))
    others_zone = {i: zone_of(center(i)[0]) for i in (A1, A2, R)}
    shifted = {i: (lambda x1, y1, x2, y2: zone_of((x1 + x2) / 2))(*shown(i)) for i in (A1, A2, R)}
    check(others_zone == shifted, 'peeking: every other window peeks within its own zone (P13)', f'{others_zone} {shifted}')
    time.sleep(1.5)
    check(geometry(S) == solo_spot, 'peeking: the solo window stays exactly where it was put (P14)')
    ipc('wayfire/set-config-options', {'scottland/window_avoidance_always': False}); time.sleep(.5)

    # 2. Three-finger hold on the focused window (WK35 touchpad trigger).
    def pad(event, **data): return ipc('scottland/test-touchpad', dict(event=event, **data))
    setup(scene, S)
    r_before = geometry(R); n = solves()
    x1, y1, x2, y2 = footprint(S); pointer((x1 + x2) / 2, (y1 + y2) / 2); time.sleep(.1)
    pad('hold_begin', fingers=3); time.sleep(.75); pad('hold_end', cancelled=False)
    result = wait_solve(n)
    check(result['purpose'] == 'solo', 'three-finger hold on the focused window solos it', result['purpose'])
    check_spread('three-finger solo', result, S, (A1, A2), r_before)
    shot('touchpad-solo.png')

    # A Super drag of a window, for the ordinary drop below.
    def start_drag(id, to):
        x1, y1, x2, y2 = footprint(id); x, y = (x1 + x2) / 2, (y1 + y2) / 2
        pointer(x, y); time.sleep(.1); key('LEFTMETA', True); button(True); time.sleep(.1)
        for i in range(1, 21): pointer(x + (to[0] - x) * i / 20, y + (to[1] - y) * i / 20); time.sleep(.02)
    def end_drag():
        button(False); key('LEFTMETA', False); time.sleep(.6)

    # 3. Nothing else spreads (P4): present, a hint tap (zone cycling), an ordinary drop.
    setup(scene, S)
    n = solves()
    ipc('scottland/present', {'window': A1}); time.sleep(.8)
    alt(True); press_hint(A2); time.sleep(.5); alt(False); time.sleep(.5)
    start_drag(R, (500, 900)); end_drag(); time.sleep(.5)
    check(solves() == n, 'present, a hint tap and an ordinary drop never spread (P4)', f'{n} -> {solves()}')

    (art / 'spread-state.json').write_text(json.dumps(spread(), indent=2))
finally:
    for c in clients: c.terminate()
    print(f'{passed} passed, {failed} failed', flush=True)
sys.exit(1 if failed else 0)
