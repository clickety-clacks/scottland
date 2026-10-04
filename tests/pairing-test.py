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
    # A just-mapped client can answer an early configure with its default size: send it again
    # until the size is taken (setup only; expectations use whatever exact size results).
    for attempt in range(8):
        ipc('window-rules/configure-view', data)
        try:
            wait(lambda: (lambda g: abs(g['width'] - w) <= 24 and abs(g['height'] - h) <= 24 and (g['x'], g['y']) == (x, y))(geometry(id)), 1.5)
            return
        except RuntimeError: pass
    raise RuntimeError(f'fixture placement {w}x{h}+{x}+{y} -> {geometry(id)}')

def setup(spec, focus_last):
    """spec: [(id, x, y, w, h)] in stacking order back to front; focus_last is focused, in front."""
    if hints()['active']: alt(False)
    for id, *_ in spec: # every scenario starts from ordinary windows
        if next((v for v in ipc('scottland/layout-state')['views'] if v['id'] == id), {}).get('widgetized'):
            ipc('scottland/present', {'window': id}); wait(lambda: not layout(id)['widgetized'], what='restore fixture')
            time.sleep(.6)
    for id, x, y, w, h, *output in spec: place(id, x, y, w, h, *(output or [None]))
    for id, *_ in spec: ipc('window-rules/focus-view', {'id': id}); time.sleep(.05)
    ipc('window-rules/focus-view', {'id': focus_last})
    settle([s[0] for s in spec])
    # A client can still answer late with its own size: verify the whole fixture once more.
    # (Terminals round to their cell grid; only a larger difference is drift.)
    drifted = lambda g, x, y, w, h: (g['x'], g['y']) != (x, y) or abs(g['width'] - w) > 24 or abs(g['height'] - h) > 24
    for id, x, y, w, h, *output in spec:
        if drifted(geometry(id), x, y, w, h): place(id, x, y, w, h, *(output or [None])); settle([id])
    for id, x, y, w, h, *_ in spec:
        if drifted(geometry(id), x, y, w, h): raise RuntimeError(f'fixture {id} drifted to {geometry(id)}')

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

def check_pair(name, left, right, held, area, sizes=None):
    """sizes: {id: (w, h)} before the hold; pairing must never resize (WK36)."""
    settle([left, right])
    if sizes:
        now = {i: (geometry(i)['width'], geometry(i)['height']) for i in (left, right)}
        check(all(now[i] == sizes[i] for i in now), f'{name}: both windows keep their exact sizes', f'{now} vs {sizes}')
    want, scale, gap = expected((left, (geometry(left)['width'], geometry(left)['height'])),
                                (right, (geometry(right)['width'], geometry(right)['height'])), area)
    got = {i: (geometry(i)['x'], geometry(i)['y']) for i in (left, right)}
    check(all(abs(got[i][0] - want[i][0]) <= 1 and abs(got[i][1] - want[i][1]) <= 1 for i in want),
          f'{name}: side by side in left/right order, centered on the center line with the planned gap/scale',
          f'got {got} want {want} scale {scale:.4f} gap {gap:.2f}')
    scales = [layout(i)['applied_scale'] for i in (left, right)]
    check(all(abs(s - scale) < .01 for s in scales), f'{name}: both drawn at the shared scale {scale:.3f}', str(scales))
    check(hints()['selected'] == held, f'{name}: the held window stays selected')
    return want, scale, gap

def sizes_of(*ids): return {i: (geometry(i)['width'], geometry(i)['height']) for i in ids}
def focused_id(): return ipc('window-rules/get-focused-view').get('info', {}).get('id')
def footprint(id, dx=0, dy=0):
    g = geometry(id); s = layout(id)['applied_scale']
    cx, cy = g['x'] + g['width'] / 2 + dx, g['y'] + g['height'] / 2 + dy
    return (cx - g['width'] * s / 2, cy - g['height'] * s / 2, cx + g['width'] * s / 2, cy + g['height'] * s / 2)
def visible_area(id, front, displaced=True):
    """Area of id's (displaced) footprint not covered by the front windows (4 px sampling)."""
    e = hint(id) if displaced else {'dx': 0, 'dy': 0}; x1, y1, x2, y2 = footprint(id, e['dx'], e['dy'])
    rects = [footprint(f) for f in front]; free = 0
    for yy in range(int(y1) + 2, int(y2), 4):
        for xx in range(int(x1) + 2, int(x2), 4):
            if not any(r[0] <= xx < r[2] and r[1] <= yy < r[3] for r in rects): free += 16
    return free

GTK_APP = """import sys, gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk
app = Gtk.Application(application_id='org.scottland.PairTest.' + sys.argv[1])
def activate(a):
    w = Gtk.ApplicationWindow(application=a, title=sys.argv[1]); w.set_default_size(400, 300); w.present()
app.connect('activate', activate); app.run([])
"""
def launch_gtk(title):
    """A client that takes any size exactly (terminals round to their cell grid)."""
    clients.append(subprocess.Popen([sys.executable, '-c', GTK_APP, title],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    return wait(lambda: next((v['id'] for v in views() if v.get('title') == title), None), what='launch ' + title)

def pointer(x, y): ipc('stipc/move_cursor', {'x': round(x), 'y': round(y)})
def button(down): ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'press' if down else 'release'})
def super_drag(id, dx):
    """A real Super+drag that ends still, so it drops without a coast."""
    x1, y1, x2, y2 = footprint(id); x, y = (x1 + x2) / 2, (y1 + y2) / 2
    pointer(x, y); time.sleep(.1); key('LEFTMETA', True); button(True); time.sleep(.1)
    for i in range(1, 13): pointer(x + dx * i / 12, y); time.sleep(.02)
    time.sleep(.25); button(False); key('LEFTMETA', False); time.sleep(.5)

def widgetize_by_double_tap(id):
    press_hint(id); time.sleep(.1); press_hint(id)
    wait(lambda: layout(id)['widgetized'], what='widgetize')
    return wait(lambda: next((w['widget_view'] for w in ipc('scottland/widgets')['widgets']
        if int(w['id']) == id and w['widget_view'] > 0), None), what='widget view')

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
        # Held window on the left screen joins the focused window on the right screen, on its left.
        setup([(B, 300, 300, 500, 340, first['id']), (A, 300, 200, 560, 380, second['id'])], A)
        if raw(B)['output-id'] != first['id'] or raw(A)['output-id'] != second['id']:
            raise RuntimeError('two-output fixture placement failed: ' + json.dumps([raw(A), raw(B)]))
        before = sizes_of(A, B); alt(True); shot('outputs-before.png')
        press_hint(B, hold=.7)
        check(raw(B)['output-id'] == second['id'], 'two outputs: the held window joins the focused window\'s screen')
        check_pair('two outputs', B, A, B, area2, before)
        shot('outputs-paired.png'); alt(False)
        # Scaled pair on the smaller right screen (700 + 700 > 1280).
        setup([(B, 200, 300, 700, 420, first['id']), (A, 300, 150, 700, 460, second['id'])], A)
        before = sizes_of(A, B); alt(True); press_hint(B, hold=.7)
        check(raw(B)['output-id'] == second['id'], 'two outputs, scaled: joins the focused screen')
        _, scale, _ = check_pair('two outputs, scaled', B, A, B, area2, before)
        check(scale < .99, 'two outputs: the pair needed scaling on the smaller screen', str(scale))
        shot('outputs-scaled.png'); alt(False)
        # Held window coming from the right-hand screen goes right, scaled on the left screen.
        setup([(A, 200, 150, 1000, 600, first['id']), (B, 100, 200, 900, 520, second['id'])], A)
        before = sizes_of(A, B); alt(True); press_hint(B, hold=.7)
        check(raw(B)['output-id'] == first['id'], 'from the right screen: the held window joins the left screen')
        _, scale, _ = check_pair('from the right screen, scaled', A, B, B, area, before)
        check(scale < .99, 'from the right screen: the pair is scaled to fit', str(scale))
        shot('outputs-from-right.png'); alt(False)
        print(f'{passed} passed, {failed} failed'); sys.exit(1 if failed else 0)

    # 1. Fits at 100%. B starts scaled in the right periphery. C lies wholly inside A's coming
    # place: while Alt is still held, avoidance must move C so a real part of it shows (P12).
    setup([(C, 400, 400, 300, 200), (B, 1060, 520, 420, 300), (A, 200, 140, 520, 360)], A)
    c_before = geometry(C); b_scale_before = layout(B)['applied_scale']; before = sizes_of(A, B)
    check(b_scale_before < .99, 'fixture: the unfocused window starts scaled in the periphery', str(b_scale_before))
    alt(True); shot('fit-before.png')
    press_hint(B, hold=.7)
    check_pair('fits at 100%', A, B, B, area, before)
    check(geometry(C) == c_before, 'other center windows stay where they are', f'{geometry(C)} vs {c_before}')
    check(visible_area(C, (A, B), displaced=False) == 0, 'fixture: the pair covers the third window completely')
    moved = wait(lambda: (lambda e: abs(e['target_dx']) + abs(e['target_dy']) > 1 and
        abs(e['dx'] - e['target_dx']) + abs(e['dy'] - e['target_dy']) < .5 and e)(hint(C)), 3, 'covered window peek')
    check(abs(moved['dx']) + abs(moved['dy']) > 1, 'the covered window gets a real avoidance offset within the hold',
          json.dumps({k: moved.get(k) for k in ('dx', 'dy', 'target_dx', 'target_dy', 'clearance')}))
    area_free = visible_area(C, (A, B))
    check(area_free >= 40 * 40, 'part of the covered window is actually visible beside the pair', str(area_free))
    ha, hb = hint(A), hint(B)
    check(abs(ha['dx']) + abs(ha['dy']) + abs(hb['dx']) + abs(hb['dy']) < .5 and ha['pair_anchored'] and hb['pair_anchored'],
          'window avoidance holds the pair still', json.dumps([ha['dx'], ha['dy'], hb['dx'], hb['dy']]))
    # Its badge waits for the offset to settle (WK31), then sits on the part that shows.
    def badge_on_patch():
        e = hint(C); b = e.get('badge')
        if not (e['visible'] and e.get('rendered') and b): return None
        bx, by = b['x'] + b['size'] / 2, b['y'] + b['size'] / 2
        x1, y1, x2, y2 = footprint(C, e['dx'], e['dy'])
        inside = x1 <= bx <= x2 and y1 <= by <= y2
        uncovered = not any(r[0] <= bx < r[2] and r[1] <= by < r[3] for r in (footprint(A), footprint(B)))
        return inside and uncovered and b
    badge = None
    try: badge = wait(badge_on_patch, 2, 'covered badge')
    except RuntimeError: pass
    check(bool(badge), 'the covered window\'s badge appears on its visible part', json.dumps(hint(C)))
    shot('fit-window-mode.png')
    paired = {i: geometry(i) for i in (A, B)}
    alt(False); settle([A, B, C]); time.sleep(.6); shot('fit-after-release.png')
    check(all(geometry(i) == paired[i] for i in (A, B)) and geometry(C) == c_before,
          'the pair is real geometry: it stays after Alt release')
    check(abs(hint(C)['dx']) + abs(hint(C)['dy']) < .5, 'the peek offset returns home after Alt release (P3)')
    check(abs(layout(B)['applied_scale'] - 1) < .01 and abs(layout(A)['applied_scale'] - 1) < .01,
          'both stay at 100% after Window mode ends')

    # 2. Order: the held window on the left stays on the left (P1).
    setup([(B, 60, 200, 460, 320), (A, 900, 360, 520, 380)], A)
    before = sizes_of(A, B); alt(True); press_hint(B, hold=.7)
    check_pair('order kept', B, A, B, area, before); alt(False)

    # 3./4. The gap gives way first (P7), then the edge padding (WP7), both at 100%.
    G1, G2 = launch_gtk('PairGapLeft'), launch_gtk('PairGapRight')
    for name, lw, rw, regime in (('gap shrinks', 800, 762, 'gap'), ('padding gives way', 800, 790, 'padding')):
        setup([(G2, 900, 500, rw, 300), (G1, 100, 200, lw, 320)], G1)
        before = sizes_of(G1, G2); alt(True); press_hint(G2, hold=.7)
        _, scale, gap = check_pair(name, G1, G2, G2, area, before)
        l, r = geometry(G1), geometry(G2)
        measured_gap = r['x'] - (l['x'] + l['width']); margin = l['x']
        if regime == 'gap':
            check(scale == 1 and 0 < measured_gap < HALO and abs(margin - PAD) <= 1,
                  'gap regime: 100%, a gap narrower than a halo, full edge padding', f'gap {measured_gap} margin {margin}')
        else:
            check(scale == 1 and measured_gap == 0 and 0 < margin < PAD,
                  'padding regime: 100%, no gap, reduced edge padding', f'gap {measured_gap} margin {margin}')
        shot(f'regime-{regime}.png'); alt(False)
    for client in clients[-2:]: client.terminate()
    wait(lambda: not any(v['id'] in (G1, G2) for v in views()), what='close regime clients')

    # 5. Too wide: one shared factor, edge to edge. Nothing is locked afterwards: an arrow push
    # clears B's pair scale, and a real Super+drag clears A's.
    setup([(A, 100, 150, 1000, 600), (B, 900, 300, 900, 520)], A)
    before = sizes_of(A, B); alt(True); press_hint(B, hold=.7)
    want, scale, _ = check_pair('scaled to fit', A, B, B, area, before)
    left_edge, right_edge = footprint(A)[0], footprint(B)[2]
    check(abs(left_edge) <= 1.5 and abs(right_edge - area['width']) <= 1.5, 'the scaled pair spans edge to edge',
          f'{left_edge:.1f}..{right_edge:.1f}')
    shot('scaled-window-mode.png')
    # Inward: B already touches the right edge, where an outward push would dock it (WK20).
    key('LEFT', True); time.sleep(.05); key('LEFT', False)
    time.sleep(1.6); settle([B])
    lb = layout(B)
    check(not lb['widgetized'] and abs(lb['target_scale'] - lb['scale']) < .01 and abs(lb['target_scale'] - scale) > .01,
          'nothing locked: an arrow push clears the pair scale and B follows its zone again', json.dumps(lb))
    check(abs(layout(A)['applied_scale'] - scale) < .01, 'the other window keeps its pair scale until it moves')
    alt(False); time.sleep(.5)
    super_drag(A, 60); settle([A])
    la = layout(A)
    check(abs(la['target_scale'] - la['scale']) < .01 and abs(la['target_scale'] - scale) > .01,
          'nothing locked: a real drag clears the pair scale (L31)', json.dumps(la))
    shot('scaled-after-push-and-drag.png')

    # 6. A tap on an unfocused hint selects only (on key-down, WK6); it never pairs.
    setup([(B, 1060, 520, 420, 300), (A, 200, 140, 520, 360)], A)
    before = {i: geometry(i) for i in (A, B)}
    alt(True)
    text = hint(B)['hint']; key(text.upper(), True)
    try: wait(lambda: focused_id() == B, .4)
    except RuntimeError: pass
    check(focused_id() == B and hints()['selected'] == B, 'an unfocused hint acts on key-down: selected and focused while still held',
          f'focused {ipc("window-rules/get-focused-view").get("info")} selected {hints()["selected"]} B {B}')
    key(text.upper(), False); time.sleep(.9)
    check(hints()['selected'] == B and all(geometry(i) == before[i] for i in (A, B)), 'a tap selects and does not pair')
    alt(False)

    # 7. Key auto-repeat neither restarts nor doubles the hold, and is never a double-tap.
    setup([(B, 1060, 520, 420, 300), (A, 200, 140, 520, 360)], A)
    before = sizes_of(A, B); alt(True); press_hint(B, hold=.8, repeats=True)
    check(not layout(B)['widgetized'], 'auto-repeat during a hold is not a double-tap to the rail')
    check_pair('auto-repeat hold', A, B, B, area, before); alt(False)

    # 8./9. Esc or Alt release during the hold cancels the pairing; the press's selection stays.
    for name, action in (('Esc', lambda: (key('ESC', True), key('ESC', False))), ('Alt release', lambda: key('LEFTALT', False))):
        setup([(B, 1060, 520, 420, 300), (A, 200, 140, 520, 360)], A)
        before = {i: geometry(i) for i in (A, B)}
        alt(True); press_hint(B, hold=.9, during=(.2, action)); time.sleep(.5)
        check(all(geometry(i) == before[i] for i in (A, B)), f'{name} during the hold cancels the pairing')
        check(focused_id() == B and raw(B)['activated'], f'{name}: the press still selected and focused the window',
              f'focused {focused_id()} B {B}')
        key('LEFTALT', False); time.sleep(.3)

    # 10. The focused window's hint acts on key release (WK35, Mike 2026-10-04): a tap moves it
    # once released; a hold never moves it (the solo hook is empty).
    setup([(C, 480, 300, 640, 420), (B, 1060, 520, 420, 300), (A, 520, 140, 520, 360)], A)
    a_before, b_before, c_before = geometry(A), geometry(B), geometry(C)
    alt(True)
    text = hint(A)['hint']; key(text.upper(), True); time.sleep(.25)
    check(geometry(A) == a_before and layout(A)['zone'] == 'center', 'focused tap: nothing moves while the key is down')
    key(text.upper(), False); time.sleep(.7); settle([A])
    check(layout(A)['zone'] != 'center', 'focused tap: it takes its next zone on release', layout(A)['zone'])
    alt(False)
    setup([(C, 480, 300, 640, 420), (B, 1060, 520, 420, 300), (A, 520, 140, 520, 360)], A)
    alt(True); press_hint(A, hold=.8); time.sleep(.6); settle([A])
    check(geometry(A) == a_before and layout(A)['zone'] == 'center' and geometry(B) == b_before and geometry(C) == c_before,
          'focused hold: nothing moves (WK35 solo is an empty hook)', f"{geometry(A)} {layout(A)['zone']}")
    alt(False)

    # 11. A 450 ms near-hold, released, then a quick press is a release-timed double-tap (WK15),
    # never a pairing: unfocused (acts on key-down) and focused (acts on release).
    for name, target in (('unfocused', B), ('focused', A)):
        setup([(B, 1060, 520, 420, 300), (A, 520, 140, 520, 360)], A)
        other_before = geometry(A if target == B else B)
        alt(True); press_hint(target, hold=.45); time.sleep(.12); press_hint(target, hold=.08)
        wait(lambda: layout(target)['widgetized'], 4, f'{name} near-hold double-tap')
        check(layout(target)['widgetized'] and geometry(A if target == B else B) == other_before,
              f'{name}: 450 ms near-hold then a quick press goes to the rail, and nothing pairs')
        alt(False); ipc('scottland/present', {'window': target}); wait(lambda: not layout(target)['widgetized'], what='restore')
        time.sleep(.6)

    # 12. A held widget joins the pair as its app window, opened from the rail.
    setup([(B, 1060, 520, 420, 300), (A, 200, 140, 520, 360)], A)
    alt(True); widgetize_by_double_tap(B)
    time.sleep(1); alt(False); ipc('window-rules/focus-view', {'id': A}); time.sleep(.3)
    alt(True); shot('widget-before.png'); before = sizes_of(A, B); press_hint(B, hold=.7)
    wait(lambda: not layout(B)['widgetized'], what='restore B')
    time.sleep(.8)
    check_pair('held widget pairs as its app', A, B, B, area, before)
    shot('widget-paired.png'); alt(False)

    # 13. A focused widget is the partner: its app opens from the rail into the pair.
    setup([(B, 1060, 520, 420, 300), (A, 200, 140, 520, 360)], A)
    alt(True); widget = widgetize_by_double_tap(B); time.sleep(1); alt(False)
    ipc('window-rules/focus-view', {'id': widget}); time.sleep(.4)
    alt(True)
    check(hints()['selected'] == B, 'fixture: the focused widget represents its app', str(hints()['selected']))
    before = sizes_of(A, B); press_hint(A, hold=.7)
    wait(lambda: not layout(B)['widgetized'], what='restore partner B')
    time.sleep(.8)
    check_pair('focused widget as partner', A, B, A, area, before)
    shot('partner-widget-paired.png'); alt(False)

    # 14. A full-screen partner leaves full screen and pairs at its restored size (WK12).
    setup([(B, 1060, 520, 420, 300), (A, 200, 140, 520, 360)], A)
    before = sizes_of(A, B)
    ipc('wm-actions/set-fullscreen', {'view_id': A, 'state': True})
    wait(lambda: raw(A)['fullscreen'] and geometry(A)['width'] == area['width'], what='fullscreen A')
    ipc('window-rules/focus-view', {'id': A}); time.sleep(.5)
    alt(True); press_hint(B, hold=.7)
    wait(lambda: not raw(A)['fullscreen'], 3, 'leave full screen')
    time.sleep(.8)
    check(not raw(A)['fullscreen'], 'full screen: the partner leaves full screen')
    check_pair('full-screen partner', A, B, B, area, before)
    shot('fullscreen-paired.png'); alt(False)

    # 15. Taller than the screen: no extra scaling; centered on the center line.
    setup([(B, 1060, 520, 420, 300), (A, 200, 0, 520, 1200)], A)
    if geometry(A)['height'] > area['height']:
        before = sizes_of(A, B); alt(True); press_hint(B, hold=.7)
        check_pair('taller than the screen', A, B, B, area, before)
        shot('tall-paired.png'); alt(False)
    else: check(False, 'fixture: a window taller than the screen', str(geometry(A)))
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
