#!/usr/bin/env python3
"""WK36/WP1: pairing preserves a real peripheral drop, judged by eventual geometry.

IPC config, initial placement and focus are fixture setup. Pairing, departing the pair,
and peripheral return use real stipc hint keys. Peripheral memories are
established by real Super-drags. Saved-state telemetry is recorded only as a diagnostic.
"""
import json
import os
from pathlib import Path
import signal
import shutil
import socket
import struct
import subprocess
import sys
import time

art = Path(sys.argv[1]).resolve(); art.mkdir(parents=True, exist_ok=True)
sock = socket.socket(socket.AF_UNIX); sock.settimeout(8); sock.connect(os.environ['WAYFIRE_SOCKET'])
clients, held, observations = [], set(), []
passed = failed = 0
signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))


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
    print(('PASS ' if ok else 'FAIL ') + name + ('' if detail is None else ' ' + str(detail)), flush=True)


def key(name, down):
    ipc('stipc/feed_key', dict(key='KEY_' + name, state=down))
    (held.add if down else held.discard)(name)


def button(down):
    ipc('stipc/feed_button', dict(combo='BTN_LEFT', mode='press' if down else 'release'))
    (held.add if down else held.discard)('BTN_LEFT')


def raw(identifier):
    return next(v for v in ipc('window-rules/list-views') if v['id'] == identifier)


def geometry(identifier): return raw(identifier)['geometry']
def center(g): return g['x'] + g['width'] / 2, g['y'] + g['height'] / 2

def layout(identifier):
    return next(v for v in ipc('scottland/layout-state')['views'] if v['id'] == identifier)


def hint(identifier):
    return next(h for h in ipc('scottland/hints')['hints'] if h['window'] == identifier)


def alt(down):
    key('LEFTALT', down)
    wait(lambda: ipc('scottland/hints')['active'] == down, 'Window mode transition')


def stable(identifier):
    last, repeats = None, 0
    def observe():
        nonlocal last, repeats
        g = geometry(identifier); state = layout(identifier)
        value = (g, round(state['applied_scale'], 4), state.get('frame'), state['widgetized'])
        repeats = repeats + 1 if value == last else 0; last = value
        return g if repeats >= 4 else None
    return wait(observe, 'stable geometry/scale')


def letters(identifier, after=None):
    text = hint(identifier)['hint']
    for letter in text[:-1]: key(letter.upper(), True); key(letter.upper(), False)
    key(text[-1].upper(), True)
    if after: after()
    key(text[-1].upper(), False)


APP = """import sys, gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk
app = Gtk.Application(application_id='org.scottland.PairMemory.' + sys.argv[1])
def activate(a):
 w = Gtk.ApplicationWindow(application=a, title=sys.argv[1]); w.set_default_size(int(sys.argv[2]), 300); w.present()
app.connect('activate', activate); app.run([])
"""

def launch(title, width):
    clients.append(subprocess.Popen([sys.executable, '-c', APP, title, str(width)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    return wait(lambda: next((v['id'] for v in ipc('window-rules/list-views') if v.get('title') == title), None), 'map client')


def drag(identifier, target):
    # Fixture setup may put a just-mapped client centrally; its memory comes from this real drop.
    ipc('window-rules/focus-view', dict(id=identifier))
    stable(identifier)
    start = center(geometry(identifier))
    ipc('stipc/move_cursor', dict(x=round(start[0]), y=round(start[1])))
    key('LEFTMETA', True); button(True)
    for i in range(1, 13):
        ipc('stipc/move_cursor', dict(x=round(start[0] + (target[0]-start[0])*i/12),
                                     y=round(start[1] + (target[1]-start[1])*i/12)))
        time.sleep(.02)  # pace a real gesture
    time.sleep(.25)  # intentional still hold so the drop has no release coast
    button(False); key('LEFTMETA', False)
    return stable(identifier)


def return_to_periphery(identifier):
    # Fresh holds reset the cycle to its current zone. From the fitted side placement go
    # center, then from center go periphery; no double-tap, hidden widget or test move shortcut.
    ipc('window-rules/focus-view', dict(id=identifier))  # fixture focus, not a move
    alt(True)
    before = geometry(identifier)
    letters(identifier)
    wait(lambda: geometry(identifier) != before, 'first cycle moved')
    stable(identifier); alt(False)
    alt(True)
    before = geometry(identifier)
    letters(identifier)
    wait(lambda: geometry(identifier) != before, 'peripheral return moved')
    out = stable(identifier); alt(False)
    return out


try:
    ipc('wayfire/set-config-options', {'scottland/sounds': False, 'scottland/alt_hold_delay': 100,
        'scottland/window_hold_delay': 350, 'scottland/window_double_tap_delay': 1,
        'scottland/window_avoidance_always': False, 'scottland/hint_avoidance_always': False})
    output = ipc('window-rules/list-outputs')[0]['geometry']; W, H = output['width'], output['height']
    for label, width in [('wide', round(W*.74)), ('unscaled', round(W*.43)),
                         ('reload', round(W*.74)), ('newdrop', round(W*.74))]:
        left, right = launch(label+'Left', width), launch(label+'Right', width)
        # Place and wait once; GTK keeps this size exactly. These placements do not prove memory.
        for identifier in (left, right):
            ipc('window-rules/configure-view', dict(id=identifier, geometry=dict(
                x=round(W/2-width/2), y=round(H/2-150), width=width, height=300)))
            wait(lambda i=identifier: geometry(i)['width'] == width, 'fixture width')
            stable(identifier)
        original = {left: drag(left, (W*.13, H*.28)), right: drag(right, (W*.87, H*.72))}
        check(all(abs(center(original[i])[0] - target) < 10 for i,target in
                  ((left,W*.13),(right,W*.87))), label + ': real drops reach both peripheral sides', original)
        ipc('window-rules/focus-view', dict(id=right))
        alt(True)
        letters(left, lambda: wait(lambda: all(abs(center(geometry(i))[1]-H/2) < 2
                                              for i in (left,right)), 'real hint hold pairs'))
        paired = {i: stable(i) for i in (left,right)}
        check(all(paired[i] != original[i] for i in paired) and
              center(paired[left])[0] < center(paired[right])[0], label + ': pairing really moves both windows', paired)
        subprocess.run(['grim', str(art/(label+'-paired.png'))], check=True, timeout=8)
        diagnostic = {i: hint(i).get('memories') for i in (left,right)}
        alt(False)
        if label == 'reload':
            # Only this caller-owned session is reloaded. Mirror the existing marked plugin
            # handover using a fresh copy, without a service/config/desktop install helper.
            mark = Path(os.environ['XDG_RUNTIME_DIR'])/'scottland'/(os.environ['WAYLAND_DISPLAY']+'.reloading')
            fresh = art/'libscottland-pair-memory-reload.so'
            shutil.copy(Path(__file__).resolve().parents[1]/'build/libscottland.so', fresh)
            plugins = ipc('wayfire/get-config-option', dict(option='core/plugins'))['value']
            mark.touch()
            try:
                ipc('wayfire/set-config-options', {'core/plugins': ' '.join(str(fresh) if p == 'scottland' or
                     '/libscottland' in p else p for p in plugins.split())})
                wait(lambda: str(fresh) in ipc('wayfire/get-config-option', dict(option='core/plugins'))['value'] and
                     len(ipc('scottland/desktop-model')['windows']) >= 2, 'owned reload completed')
                check(all(geometry(i) == paired[i] for i in (left,right)),
                      'reload: handover keeps the actual paired geometry')
            finally: mark.unlink(missing_ok=True)
        if label == 'newdrop':
            # A real new placement must still become the memory. Also leaves the partner
            # untouched: provenance cannot disappear merely because the pair anchor broke.
            original[left] = drag(left, (W*.18, H*.35))
        for name, identifier in (('held left', left), ('focused right', right)):
            restored = return_to_periphery(identifier)
            check(all(abs(restored[k]-original[identifier][k]) <= 1 for k in ('x','y','width','height')),
                  label + ': ' + name + ' restores the saved peripheral geometry',
                  dict(before=original[identifier], after=restored))
            observations.append(dict(scenario=label, role=name, before=original[identifier],
                                     paired=paired[identifier], restored=restored, memories_after_pair=diagnostic[identifier]))
        for identifier in (left,right): ipc('window-rules/close-view', dict(id=identifier))
        wait(lambda: not any(v['id'] in (left,right) for v in ipc('window-rules/list-views')), 'close fixture')
    (art/'geometry.json').write_text(json.dumps(observations, indent=2))
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
