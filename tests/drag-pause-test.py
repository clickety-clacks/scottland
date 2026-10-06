#!/usr/bin/env python3
"""Pausing during a drag does nothing (docs/spread.md SP7, ruling 10-05: the drag audition is removed).

A window (S) dragged into the center zone and held still there for longer than the removed
audition's pause (3 s by default) offers nothing: every other window stays drawn where it truly
is, and dropping S there moves nothing else. Checked with the shipped config, then again after a
stale `solo_audition_delay` from an older config is written into the running session's config
file, which Wayfire re-reads: an old value is ignored harmlessly.

Every drag, pause and drop is real stipc pointer and key input; fixture geometry and focus are set
over IPC (configure-view and focus-view, bypassing input), and so is the output mode. The drag is
proven by pixels before the hold (S's own color drawn at the pause spot and gone from where S
started; a drag changes S's true geometry only at the drop) and by S's true geometry after the drop
(centered on the pause spot). What is drawn is judged from captured pixels against each window's known
solid color: a probe patch of each neighbor's true area that S never covers (A1, A2, R) must stay
in that neighbor's color. Usage: drag-pause-test.py ARTIFACTS WAYFIRE_INI
"""
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time

assert os.environ.get('SCOTTLAND_TEST_MODEL') == '1'
art = Path(sys.argv[1]).resolve(); art.mkdir(parents=True, exist_ok=True)
ini = Path(sys.argv[2])
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

passed = failed = 0
def check(ok, name, detail=''):
    global passed, failed
    print(('PASS ' if ok else 'FAIL ') + name + (f'  [{detail}]' if detail else ''), flush=True)
    if ok: passed += 1
    else: failed += 1

def views(): return ipc('window-rules/list-views')
def geometry(id): return next(v for v in views() if v['id'] == id)['geometry']
def solves(): return ipc('scottland/spread-state')['solves']  # diagnostics only
def key(code, down): ipc('stipc/feed_key', {'key': 'KEY_' + code, 'state': down})
def pointer(x, y): ipc('stipc/move_cursor', {'x': round(x), 'y': round(y)})
def button(down): ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'press' if down else 'release'})
def shot(name): subprocess.run(['grim', str(art / name)], check=True)

def wait(predicate, timeout, what):
    end = time.monotonic() + timeout; last = None
    while time.monotonic() < end:
        last = predicate()
        if last: return last
        time.sleep(.02)
    raise RuntimeError(f'{what} timeout (last: {last})')

COLORS = {'S': '#c03090', 'A1': '#20b048', 'A2': '#2050d0', 'R': '#d08020'}
RGB = {k: tuple(int(v[i:i + 2], 16) for i in (1, 3, 5)) for k, v in COLORS.items()}
GTK_APP = """import sys, gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, Gdk
app = Gtk.Application(application_id='org.scottland.DragPause.' + sys.argv[1])
def activate(a):
    css = Gtk.CssProvider(); css.load_from_string('window { background: ' + sys.argv[2] + '; }')
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    w = Gtk.ApplicationWindow(application=a, title=sys.argv[1]); w.set_default_size(400, 300); w.present()
app.connect('activate', activate); app.run([])
"""
clients = []
def launch(title):
    clients.append(subprocess.Popen([sys.executable, '-c', GTK_APP, 'Pause' + title, COLORS[title]],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    return wait(lambda: next((v['id'] for v in views() if v.get('title') == 'Pause' + title), None), 10, 'launch ' + title)

def place(id, x, y, w, h):
    data = {'id': id, 'geometry': {'x': x, 'y': y, 'width': w, 'height': h}}
    for attempt in range(8):
        ipc('window-rules/configure-view', data)
        try:
            wait(lambda: (lambda g: (g['x'], g['y'], g['width'], g['height']) == (x, y, w, h))(geometry(id)), 1.5, 'placement')
            return
        except RuntimeError: pass
    raise RuntimeError(f'fixture placement {w}x{h}+{x}+{y} -> {geometry(id)}')

def probe(region):
    """Pixels of a screen region (x, y, w, h), every 5th pixel each way, as (r, g, b) tuples."""
    x, y, w, h = region
    data = subprocess.run(['grim', '-g', f'{x},{y} {w}x{h}', '-t', 'ppm', '-'], check=True, capture_output=True).stdout
    fields, at = [], 0
    while len(fields) < 4:  # P6 width height maxval, whitespace separated
        while data[at:at + 1].isspace(): at += 1
        end = at
        while not data[end:end + 1].isspace(): end += 1
        fields.append(data[at:end]); at = end
    pw, ph = int(fields[1]), int(fields[2]); pixels = data[at + 1:]
    return [tuple(pixels[(r * pw + c) * 3:(r * pw + c) * 3 + 3]) for r in range(0, ph, 5) for c in range(0, pw, 5)]

def share(region, name):
    """Share of a region drawn in window `name`'s known color."""
    px = probe(region)
    return sum(1 for p in px if max(abs(a - b) for a, b in zip(p, RGB[name])) <= 6) / len(px)

DRAWN = .9  # share of a probe in its window's color while that window is drawn there

# Scene (2560x1440), back to front: R, A1, A2, then S (dragged). S is dragged by its center to
# PAUSE and is then drawn over x 930-1630, y 450-950 (700x500, full scale in the center zone).
SCENE = {'R': (60, 900, 600, 400), 'A1': (900, 100, 600, 450), 'A2': (1300, 650, 500, 400), 'S': (120, 150, 700, 500)}
PAUSE = (1280, 700)
# Probe patches: inside each window's own content (below its title bar), clear of S and of S's halo.
# R is in the periphery, drawn scaled down about its center (360, 1100): its patch stays near that.
PROBES = {'A1': (950, 200, 500, 170), 'A2': (1680, 720, 100, 300), 'R': (320, 1070, 80, 60),
          'S': (1080, 560, 400, 300)}  # S's patch is where S is drawn once it reaches PAUSE
S_START = (430, 370, 80, 60)  # near S's scaled center (470, 400), uncovered after it moves
NEIGHBORS = ('A1', 'A2', 'R')

def start_drag(id, to):
    g = geometry(id); x, y = g['x'] + g['width'] / 2, g['y'] + g['height'] / 2
    pointer(x, y); time.sleep(.1); key('LEFTMETA', True); button(True); time.sleep(.1)
    for i in range(1, 21): pointer(x + (to[0] - x) * i / 20, y + (to[1] - y) * i / 20); time.sleep(.02)

def end_drag():
    button(False); key('LEFTMETA', False)

def frame_signature():
    return tuple(round(share(PROBES[n], n), 3) for n in (*NEIGHBORS, 'S'))

def settled(stable=.5, timeout=5):
    """Wait until the probed frame stops changing for `stable` seconds; the last signature."""
    end = time.monotonic() + timeout
    last, since = None, time.monotonic()
    while time.monotonic() < end:
        now = frame_signature()
        if now != last: last, since = now, time.monotonic()
        elif time.monotonic() - since >= stable: return last
        time.sleep(.05)
    raise RuntimeError(f'frame never settled (last: {last})')

try:
    ipc('wayfire/set-config-options', {'scottland/sounds': False,
        'scottland/window_avoidance_always': False, 'scottland/hint_avoidance_always': False,
        'output:HEADLESS-1/mode': '2560x1440@60000'})
    wait(lambda: ipc('window-rules/list-outputs')[0]['geometry']['width'] == 2560, 5, 'output mode')
    ids = {name: launch(name) for name in ('S', 'A1', 'A2', 'R')}
    S = ids['S']
    order = ('R', 'A1', 'A2', 'S')

    def setup():
        for name in order: place(ids[name], *SCENE[name])
        for name in order: ipc('window-rules/focus-view', {'id': ids[name]}); time.sleep(.05)
        ipc('window-rules/focus-view', {'id': ids['A2']})
        # A newly mapped client may acknowledge its initial size after the first configure.
        # Reconcile fixture setup once after stacking, before sending the single tested drag.
        for name in order:
            g = geometry(ids[name])
            if (g['x'], g['y'], g['width'], g['height']) != SCENE[name]:
                place(ids[name], *SCENE[name])
        try:
            wait(lambda: all((lambda g: (g['x'], g['y'], g['width'], g['height']) == SCENE[n])(geometry(ids[n]))
                             for n in order), 5, 'fixture')
        except RuntimeError as e:
            raise RuntimeError(f'{e}; actual geometries { {n: geometry(ids[n]) for n in order} }') from e
        settled()

    def scenario(name):
        setup()
        home = {n: share(PROBES[n], n) for n in NEIGHBORS}
        check(all(v >= DRAWN for v in home.values()), f'{name}: before the drag each neighbor is drawn in its own color',
              str(home))
        before = {n: geometry(ids[n]) for n in NEIGHBORS}
        n_solves = solves()
        check(share(S_START, 'S') >= DRAWN, f'{name}: before the drag S is drawn where it starts')
        start_drag(S, PAUSE)
        # The drag happened: S is drawn at the pause spot and no longer where it started.
        try:
            wait(lambda: share(PROBES['S'], 'S') >= DRAWN and share(S_START, 'S') <= .05, 3, 'S drawn at the pause spot')
            check(True, f'{name}: the drag draws S at the pause spot, away from where it started')
        except RuntimeError as e:
            check(False, f'{name}: the drag draws S at the pause spot, away from where it started', str(e))
        # Hold still 4.5 s, longer than the removed audition's 3 s default pause (an intended hold).
        moved = []; hold_end = time.monotonic() + 4.5; samples = 0
        while time.monotonic() < hold_end:
            t = round(4.5 - (hold_end - time.monotonic()), 2)
            for n in (*NEIGHBORS, 'S'):
                s = share(PROBES[n], n)
                if s < DRAWN: moved.append((t, n, round(s, 2)))
            samples += 1
            time.sleep(.05)
        check(not moved, f'{name}: holding S still mid-drag, every neighbor stays drawn where it is and S stays put',
              f'{samples} samples; (s into the hold, window, share) {moved[:12]}')
        shot(f'{name}-paused.png')
        end_drag()
        try:
            sig = settled()
            check(all(v >= DRAWN for v in sig), f'{name}: after the drop every window is drawn where it was', str(sig))
        except RuntimeError as e:
            check(False, f'{name}: after the drop every window is drawn where it was', str(e))
        g = geometry(S)
        check(abs(g['x'] + g['width'] / 2 - PAUSE[0]) <= 1 and abs(g['y'] + g['height'] / 2 - PAUSE[1]) <= 1,
              f'{name}: the drop leaves S centered on the pause spot (true geometry)', str(g))
        after = {n: geometry(ids[n]) for n in NEIGHBORS}
        check(after == before, f'{name}: dropping where it paused moves no other window (true geometry)',
              str({n: (before[n], after[n]) for n in after if after[n] != before[n]}))
        # The drop ended the drag: moving the pointer now leaves S where it was dropped.
        dropped = geometry(S)
        pointer(PAUSE[0] + 300, PAUSE[1] - 300)
        settled()
        check(geometry(S) == dropped, f'{name}: the drag ended at the drop (S no longer follows the pointer)',
              f'{dropped} -> {geometry(S)}')
        print(f'  {name}: solves {n_solves} -> {solves()} (diagnostic)', flush=True)

    scenario('shipped')

    # An older config still names the removed pause (300 ms would have offered fast). Written with
    # a marker value into [scottland] of the running session's config file.
    text = ini.read_text()
    assert '\n[scottland]\n' in text
    stale = ('solo_audition_delay = 300\nsounds = false\n'
             'window_avoidance_always = false\nhint_avoidance_always = false\ncycle_overshoot = 7\n')
    lines = [l for l in text.split('\n') if not l.startswith(('sounds =', 'window_avoidance_always =',
             'hint_avoidance_always =', 'cycle_overshoot ='))]
    text = '\n'.join(lines).replace('\n[scottland]\n', '\n[scottland]\n' + stale, 1)
    if '[output:HEADLESS-1]' not in text: text += '\n[output:HEADLESS-1]\nmode = 2560x1440@60000\n'
    ini.write_text(text)
    try:
        wait(lambda: float(ipc('wayfire/get-config-option', {'option': 'scottland/cycle_overshoot'})['value']) == 7, 10,
             'config re-read')
        check(True, 'stale: the session re-reads a config naming the removed pause setting')
    except RuntimeError as e:
        check(False, 'stale: the session re-reads a config naming the removed pause setting', str(e))
    try: print(f"  stale: solo_audition_delay reads back {ipc('wayfire/get-config-option', {'option': 'scottland/solo_audition_delay'})} (diagnostic)", flush=True)
    except RuntimeError as e: print(f'  stale: solo_audition_delay reads back an error: {e} (diagnostic)', flush=True)
    wait(lambda: ipc('window-rules/list-outputs')[0]['geometry']['width'] == 2560, 5, 'output mode')
    scenario('stale')
finally:
    try: end_drag()
    except Exception: pass
    for c in clients:
        if c.poll() is None: c.terminate()
    for c in clients:
        try: c.wait(timeout=3)
        except subprocess.TimeoutExpired:
            c.kill(); c.wait()
    sock.close()
    print(f'{passed} passed, {failed} failed (2 scenarios)', flush=True)
sys.exit(1 if failed else 0)
