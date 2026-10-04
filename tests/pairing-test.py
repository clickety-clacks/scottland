#!/usr/bin/env python3
"""WK36 pairing and WK35/WK36 hint holds with real stipc keys in an isolated headless session.

Fixture geometry and focus are set up over IPC; every hint press, hold, Esc and Alt is real
keyboard input. Usage: pairing-test.py ARTIFACTS [--outputs2]
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
TWO_OUTPUTS = '--outputs2' in sys.argv
HALO = 32 / 3; PAD = HALO + 5
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
def raw(id): return next(v for v in views() if v['id'] == id)
def geometry(id): return raw(id)['geometry']
def layout(id): return next(v for v in ipc('scottland/layout-state')['views'] if v['id'] == id)
def hints(): return ipc('scottland/hints')
def hint(id): return next(h for h in hints()['hints'] if h['window'] == id)
def key(code, down): ipc('stipc/feed_key', {'key': 'KEY_' + code, 'state': down})
def outputs(): return sorted(ipc('window-rules/list-outputs'), key=lambda o: o['geometry']['x'])
def shot(name): subprocess.run(['grim', str(art / name)], check=True)
def cround(v): return math.floor(v + .5) if v >= 0 else -math.floor(-v + .5)

def alt(down):
    key('LEFTALT', down)
    if down: wait(lambda: hints()['active'], what='window mode')
    else: wait(lambda: not hints()['active'], what='window mode end')

def press_hint(id, hold=0.08, repeats=False, during=None):
    """Type a window's complete hint; the final letter stays down for `hold` seconds."""
    text = hint(id)['hint']
    for letter in text[:-1]:
        key(letter.upper(), True); time.sleep(.04); key(letter.upper(), False); time.sleep(.03)
    last = text[-1].upper(); key(last, True); start = time.monotonic()
    fired = False
    while time.monotonic() - start < hold:
        time.sleep(.05)
        if repeats: key(last, True) # stipc's stand-in for keyboard auto-repeat: presses, no release
        if during and not fired and time.monotonic() - start >= during[0]: during[1](); fired = True
    key(last, False)

def settle(ids, timeout=4):
    """Wait until geometry and drawn scale stop changing (glides and scale animations ended)."""
    end = time.monotonic() + timeout; last = None
    while time.monotonic() < end:
        now = [(geometry(i)['x'], geometry(i)['y'], round(layout(i)['applied_scale'], 4)) for i in ids]
        if now == last: return
        last = now; time.sleep(.15)

clients = []
def launch(title):
    clients.append(subprocess.Popen(['foot', '--app-id=scottland-pairing', '--title=' + title, 'sh', '-c', 'sleep 900'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    return wait(lambda: next((v['id'] for v in views() if v.get('title') == title), None), what='launch ' + title)

def place(id, x, y, w, h, output=None):
    data = {'id': id, 'geometry': {'x': x, 'y': y, 'width': w, 'height': h}}
    if output is not None: data['output_id'] = output
    ipc('window-rules/configure-view', data)
    # A client may round its size (cells); expectations use whatever size it took.
    try: wait(lambda: (lambda g: abs(g['width'] - w) <= 24 and abs(g['height'] - h) <= 24)(geometry(id)), 3)
    except RuntimeError: print('NOTE fixture size', w, h, '->', geometry(id), flush=True)

def setup(spec, focus_last):
    """spec: [(id, x, y, w, h)] in stacking order back to front; focus_last is focused, in front."""
    if hints()['active']: alt(False)
    for id, x, y, w, h, *output in spec: place(id, x, y, w, h, *(output or [None]))
    for id, *_ in spec: ipc('window-rules/focus-view', {'id': id}); time.sleep(.05)
    ipc('window-rules/focus-view', {'id': focus_last})
    settle([s[0] for s in spec])

def expected(left, right, area):
    """Mirror of fit_pair (pairing.cpp) in output-local pixels: ({id: (x, y)}, scale, gap)."""
    lw, lh = left[1]; rw, rh = right[1]
    total = lw + rw; scale = 1.0; gap = 0.0
    if total + HALO + 2 * PAD <= area['width']: gap = HALO
    elif total + 2 * PAD <= area['width']: gap = area['width'] - 2 * PAD - total
    elif total > area['width']: scale = max(.05, area['width'] / total)
    width = total * scale + gap; x0 = area['x'] + (area['width'] - width) / 2; cy = area['y'] + area['height'] / 2
    lc = x0 + lw * scale / 2; rc = x0 + lw * scale + gap + rw * scale / 2
    return ({left[0]: (cround(lc - lw / 2), cround(cy - lh / 2)), right[0]: (cround(rc - rw / 2), cround(cy - rh / 2))},
            scale, gap)

def check_pair(name, left, right, held, area, others=()):
    want, scale, gap = expected((left, (geometry(left)['width'], geometry(left)['height'])),
                                (right, (geometry(right)['width'], geometry(right)['height'])), area)
    settle([left, right])
    got = {i: (geometry(i)['x'], geometry(i)['y']) for i in (left, right)}
    check(all(abs(got[i][0] - want[i][0]) <= 1 and abs(got[i][1] - want[i][1]) <= 1 for i in want),
          f'{name}: side by side in left/right order, centered on the center line with the planned gap/scale',
          f'got {got} want {want} scale {scale:.4f} gap {gap:.2f}')
    scales = [layout(i)['applied_scale'] for i in (left, right)]
    check(all(abs(s - scale) < .01 for s in scales), f'{name}: both drawn at the shared scale {scale:.3f}', str(scales))
    check(hints()['selected'] == held, f'{name}: the held window stays selected')
    return want, scale

try:
    ipc('wayfire/set-config-options', {'scottland/sounds': False, 'scottland/alt_hold_delay': 300,
        'scottland/window_hold_delay': 500, 'scottland/window_double_tap_delay': 300,
        'scottland/window_avoidance_always': False, 'output:HEADLESS-1/mode': '1600x1000@60000'})
    if TWO_OUTPUTS:
        ipc('wayfire/set-config-options', {'output:HEADLESS-1/position': '0, 0',
            'output:HEADLESS-2/mode': '1280x800@60000', 'output:HEADLESS-2/position': '1600, 0'})
    time.sleep(1)
    outs = outputs()
    first = outs[0]; area = {'x': 0, 'y': 0, 'width': first['geometry']['width'], 'height': first['geometry']['height']}
    (art / 'outputs.json').write_text(json.dumps(outs, indent=2))
    A, B, C = launch('PairA'), launch('PairB'), launch('PairC')

    if TWO_OUTPUTS:
        second = outs[1]
        area2 = {'x': 0, 'y': 0, 'width': second['geometry']['width'], 'height': second['geometry']['height']}
        setup([(B, 300, 300, 500, 340, first['id']), (A, 300, 200, 560, 380, second['id'])], A)
        if raw(B)['output-id'] != first['id'] or raw(A)['output-id'] != second['id']:
            raise RuntimeError('two-output fixture placement failed: ' + json.dumps([raw(A), raw(B)]))
        alt(True); shot('outputs-before.png')
        press_hint(B, hold=.7)
        check(raw(B)['output-id'] == second['id'], 'two outputs: the held window joins the focused window\'s screen')
        check_pair('two outputs', B, A, B, area2)
        shot('outputs-paired.png'); alt(False)
        print(f'{passed} passed, {failed} failed'); sys.exit(1 if failed else 0)

    # 1. Fits at 100%: B was scaled in the right periphery; C stays put behind and peeks out.
    setup([(C, 480, 300, 640, 420), (B, 1060, 520, 420, 300), (A, 200, 140, 520, 360)], A)
    c_before = geometry(C); b_scale_before = layout(B)['applied_scale']
    alt(True); shot('fit-before.png')
    t0 = time.monotonic(); press_hint(B, hold=.7)
    check(b_scale_before < .99, 'fixture: the unfocused window starts scaled in the periphery', str(b_scale_before))
    check_pair('fits at 100%', A, B, B, area)
    check(geometry(C) == c_before, 'other center windows stay where they are', f'{geometry(C)} vs {c_before}')
    time.sleep(.8)
    hc, ha, hb = hint(C), hint(A), hint(B)
    check(hc['visible'] and hc.get('rendered', False), 'the window behind the pair keeps a rendered hint (peeks out)')
    check(abs(ha['dx']) + abs(ha['dy']) + abs(hb['dx']) + abs(hb['dy']) < .5,
          'window avoidance holds the pair still', json.dumps([ha['dx'], ha['dy'], hb['dx'], hb['dy']]))
    check(abs(hc['dx']) + abs(hc['dy']) > 1 or hc.get('badge') is not None,
          'the covered window peeks (offset or visible patch)', json.dumps(hc))
    shot('fit-window-mode.png')
    paired = {i: geometry(i) for i in (A, B)}
    alt(False); settle([A, B, C]); shot('fit-after-release.png')
    check(all(geometry(i) == paired[i] for i in (A, B)) and geometry(C) == c_before,
          'the pair is real geometry: it stays after Alt release; avoidance offsets return home')
    check(abs(layout(B)['applied_scale'] - 1) < .01 and abs(layout(A)['applied_scale'] - 1) < .01,
          'both stay at 100% after Window mode ends')

    # 2. Order: the held window on the left stays on the left (P1).
    setup([(B, 60, 200, 460, 320), (A, 900, 360, 520, 380)], A)
    alt(True); press_hint(B, hold=.7)
    check_pair('order kept', B, A, B, area); alt(False)

    # 3. Too wide: one shared factor, edge to edge. Then nothing is locked: an arrow push clears the pin.
    setup([(A, 100, 150, 1000, 600), (B, 900, 300, 900, 520)], A)
    alt(True); press_hint(B, hold=.7)
    want, scale = check_pair('scaled to fit', A, B, B, area)
    ga, gb = geometry(A), geometry(B)
    left_edge = ga['x'] + ga['width'] / 2 - ga['width'] * scale / 2
    right_edge = gb['x'] + gb['width'] / 2 + gb['width'] * scale / 2
    check(abs(left_edge) <= 1.5 and abs(right_edge - area['width']) <= 1.5, 'the scaled pair spans edge to edge',
          f'{left_edge:.1f}..{right_edge:.1f}')
    shot('scaled-window-mode.png')
    key('RIGHT', True); time.sleep(.05); key('RIGHT', False)
    time.sleep(1.6); settle([B])
    lb = layout(B)
    check(abs(lb['target_scale'] - lb['scale']) < .01 and abs(lb['target_scale'] - scale) > .01,
          'nothing locked: an ordinary push clears the pair scale and B follows its zone again', json.dumps(lb))
    check(abs(layout(A)['applied_scale'] - scale) < .01, 'the other window keeps its pair scale until it moves')
    alt(False); shot('scaled-after-push.png')

    # 4. A tap selects only; it never pairs.
    setup([(B, 1060, 520, 420, 300), (A, 200, 140, 520, 360)], A)
    before = {i: geometry(i) for i in (A, B)}
    alt(True); press_hint(B, hold=.12); time.sleep(.9)
    check(hints()['selected'] == B and all(geometry(i) == before[i] for i in (A, B)),
          'a tap selects the window (WK6) and does not pair')
    alt(False)

    # 5. Key auto-repeat neither restarts nor doubles the hold, and is never a double-tap.
    setup([(B, 1060, 520, 420, 300), (A, 200, 140, 520, 360)], A)
    alt(True); press_hint(B, hold=.8, repeats=True)
    check(not layout(B)['widgetized'], 'auto-repeat during a hold is not a double-tap to the rail')
    check_pair('auto-repeat hold', A, B, B, area); alt(False)

    # 6./7. Esc or Alt release during the hold cancels the pairing; the press's selection stays.
    for name, action in (('Esc', lambda: (key('ESC', True), key('ESC', False))), ('Alt release', lambda: key('LEFTALT', False))):
        setup([(B, 1060, 520, 420, 300), (A, 200, 140, 520, 360)], A)
        before = {i: geometry(i) for i in (A, B)}
        alt(True); press_hint(B, hold=.9, during=(.2, action)); time.sleep(.5)
        check(all(geometry(i) == before[i] for i in (A, B)), f'{name} during the hold cancels the pairing')
        check(raw(B)['activated'] if 'activated' in raw(B) else True, f'{name}: the press still selected/focused the window')
        key('LEFTALT', False); time.sleep(.3)

    # 8. Holding the focused window's hint is WK35's (solo, not built): the press is an ordinary tap.
    setup([(C, 480, 300, 640, 420), (B, 1060, 520, 420, 300), (A, 520, 140, 520, 360)], A)
    b_before, c_before = geometry(B), geometry(C)
    alt(True); press_hint(A, hold=.8); time.sleep(.6); settle([A])
    check(layout(A)['zone'] != 'center' and geometry(B) == b_before and geometry(C) == c_before,
          'focused hold: its press cycles as a tap; the WK35 hook moves nothing else', layout(A)['zone'])
    alt(False)

    # 9. A widget joins the pair as its app window, opened from the rail.
    setup([(B, 1060, 520, 420, 300), (A, 200, 140, 520, 360)], A)
    alt(True); press_hint(B); time.sleep(.1); press_hint(B)
    wait(lambda: layout(B)['widgetized'], what='widgetize B')
    time.sleep(1); alt(False); ipc('window-rules/focus-view', {'id': A}); time.sleep(.3)
    alt(True); shot('widget-before.png'); press_hint(B, hold=.7)
    wait(lambda: not layout(B)['widgetized'], what='restore B')
    time.sleep(.8)
    check_pair('widget pairs as its app', A, B, B, area)
    shot('widget-paired.png'); alt(False)

    # 10. Taller than the screen: no extra scaling; centered on the center line.
    setup([(B, 1060, 520, 420, 300), (A, 200, 0, 520, 1200)], A)
    if geometry(A)['height'] > area['height']:
        alt(True); press_hint(B, hold=.7)
        check_pair('taller than the screen', A, B, B, area)
        shot('tall-paired.png'); alt(False)
    else: print('SKIP taller-than-screen fixture: client refused 1200 px height', geometry(A))
finally:
    try: key('LEFTALT', False)
    except Exception: pass
    for client in clients:
        if client.poll() is None: client.terminate()
    for client in clients:
        try: client.wait(timeout=5)
        except Exception: client.kill()
print(f'{passed} passed, {failed} failed', flush=True)
sys.exit(1 if failed else 0)
