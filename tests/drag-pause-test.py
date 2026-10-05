#!/usr/bin/env python3
"""Pausing during a drag does nothing (docs/spread.md, ruling 10-05: the drag audition is removed).

A window dragged into the center zone and held still there for longer than the removed audition's
pause (3 s by default) offers nothing: no other window is drawn anywhere but where it truly is,
and dropping it there moves nothing else. Checked with the shipped config, then again after a
stale `solo_audition_delay` from an older config is written into the running session's config
file, which Wayfire re-reads: an old value is ignored harmlessly.

Every drag, pause and drop is real stipc pointer and key input; fixture geometry and focus are set
over IPC. What is drawn is judged from captured pixels: the windows have solid colors, and a probe
patch of a neighbor's (A1's) true area, which the dragged window never covers, must stay in A1's
color. Usage: drag-pause-test.py ARTIFACTS WAYFIRE_INI
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

# The probe: part of A1's true area that the dragged window (top edge at y 450 while the pointer
# is at y 700) and its halo never reach.
PROBE = (950, 200, 500, 170)
def probe():
    """Pixels of the probe patch, every 5th pixel each way, as (r, g, b) tuples."""
    x, y, w, h = PROBE
    data = subprocess.run(['grim', '-g', f'{x},{y} {w}x{h}', '-t', 'ppm', '-'], check=True, capture_output=True).stdout
    fields, at = [], 0
    while len(fields) < 4:  # P6 width height maxval, whitespace separated
        while data[at:at + 1].isspace(): at += 1
        end = at
        while not data[end:end + 1].isspace(): end += 1
        fields.append(data[at:end]); at = end
    pw, ph = int(fields[1]), int(fields[2]); pixels = data[at + 1:]
    return [tuple(pixels[(r * pw + c) * 3:(r * pw + c) * 3 + 3]) for r in range(0, ph, 5) for c in range(0, pw, 5)]

baseline_color = None
def a1_share():
    """Share of the probe drawn in A1's own color: about 1 while A1 is drawn where it truly is."""
    px = probe()
    return sum(1 for p in px if max(abs(a - b) for a, b in zip(p, baseline_color)) <= 12) / len(px)
AT_HOME = .9  # share of the probe in A1's color while A1 is drawn where it truly is
def at_home(): return a1_share() >= AT_HOME

PAUSE = (1280, 700)  # center zone; the dragged window covers A1's lower edge, never the probe

def start_drag(id, to):
    g = geometry(id); x, y = g['x'] + g['width'] / 2, g['y'] + g['height'] / 2
    pointer(x, y); time.sleep(.1); key('LEFTMETA', True); button(True); time.sleep(.1)
    for i in range(1, 21): pointer(x + (to[0] - x) * i / 20, y + (to[1] - y) * i / 20); time.sleep(.02)

def end_drag():
    button(False); key('LEFTMETA', False)

try:
    ipc('wayfire/set-config-options', {'scottland/sounds': False,
        'scottland/window_avoidance_always': False, 'scottland/hint_avoidance_always': False,
        'output:HEADLESS-1/mode': '2560x1440@60000'})
    wait(lambda: ipc('window-rules/list-outputs')[0]['geometry']['width'] == 2560, 5, 'output mode')
    S, A1, A2, R = launch('S'), launch('A1'), launch('A2'), launch('R')
    ids = (S, A1, A2, R)
    scene = [(R, 60, 900, 600, 400), (A1, 900, 100, 600, 450), (A2, 1300, 650, 500, 400), (S, 120, 150, 700, 500)]

    def setup():
        for id, x, y, w, h in scene: place(id, x, y, w, h)
        for id, *_ in scene: ipc('window-rules/focus-view', {'id': id}); time.sleep(.05)
        ipc('window-rules/focus-view', {'id': A2})
        wait(lambda: all((lambda g: (g['x'], g['y'], g['width'], g['height']) == (x, y, w, h))(geometry(i))
                         for i, x, y, w, h in scene), 5, 'fixture')

    def scenario(name):
        global baseline_color
        setup()
        before = {i: geometry(i) for i in (A1, A2, R)}
        n = solves()
        start_drag(S, PAUSE)
        time.sleep(.3)
        px = probe(); baseline_color = max(set(px), key=px.count)
        check(at_home(), f'{name}: the drag reaches the center with A1 drawn at home', f'color {baseline_color}')
        # Hold still for 4.5 s, longer than the removed audition's 3 s default pause.
        away = []; still_until = time.monotonic() + 4.5
        while time.monotonic() < still_until:
            share = a1_share()
            if share < AT_HOME: away.append((round(4.5 - (still_until - time.monotonic()), 2), round(share, 2)))
            time.sleep(.05)
        check(not away, f'{name}: pausing 4.5 s mid-drag in the center draws no other window elsewhere',
              f'(s into the pause, share at home) {away}')
        shot(f'{name}-paused.png')
        end_drag()
        time.sleep(1)  # the drop and anything it might set off
        after = {i: geometry(i) for i in (A1, A2, R)}
        check(after == before, f'{name}: dropping where it paused moves no other window',
              str({i: (before[i], after[i]) for i in after if after[i] != before[i]}))
        check(at_home(), f'{name}: after the drop A1 is still drawn at home')
        print(f'  {name}: solves {n} -> {solves()} (diagnostic)', flush=True)

    scenario('shipped')

    # An older config still names the removed pause (300 ms would have offered fast).
    # Written with a marker value into [scottland] of the running session's config file.
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
    for c in clients: c.terminate()
    print(f'{passed} passed, {failed} failed (2 scenarios)', flush=True)
sys.exit(1 if failed else 0)
