#!/usr/bin/env python3
"""Spread and solo (docs/spread.md) with real stipc input in an isolated headless session.

Fixture geometry and focus are set over IPC; every hint hold, three-finger hold, drag, pause, drop
and Esc is real input. Usage: spread-test.py ARTIFACTS
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
        'scottland/solo_audition_delay': 3000, 'scottland/solo_audition_hotspot': 50,
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

    # 1b. With always-on window avoidance, the solo window holds still for peeking like a pair
    # member: it is anchored, never nudged off its spot (P14), and the others peek around it.
    ipc('wayfire/set-config-options', {'scottland/window_avoidance_always': True})
    setup(scene, S)
    n = solves()
    alt(True); press_hint(S, hold=.75); alt(False)
    wait_solve(n); settle(ids, 6); time.sleep(.8)
    solo_spot = geometry(S); h = hint(S)
    check(h.get('solo_anchored') and abs(h['dx']) < .5 and abs(h['dy']) < .5,
          'peeking: the solo window is anchored and drawn at its own spot', str(h))
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
    # A touchpad hold is an offer while the fingers rest (WK39, 2026-10-05); lifting takes it.
    check(result['purpose'] == 'hold offer', 'three-finger hold on the focused window offers its solo, taken on lift',
          result['purpose'])
    check_spread('three-finger solo', result, S, (A1, A2), r_before)
    shot('touchpad-solo.png')

    # 3. Drag audition, accepted: pause in the center, the offer shows, a drop inside the
    # hotspot commits it and the dropped window stays exactly where it was dropped (P14).
    drag_scene = [(R, 60, 900, 600, 400), (A1, 900, 100, 600, 450), (A2, 1300, 650, 500, 400), (S, 120, 150, 700, 500)]
    def start_drag(id, to):
        x1, y1, x2, y2 = footprint(id); x, y = (x1 + x2) / 2, (y1 + y2) / 2
        pointer(x, y); time.sleep(.1); key('LEFTMETA', True); button(True); time.sleep(.1)
        for i in range(1, 21): pointer(x + (to[0] - x) * i / 20, y + (to[1] - y) * i / 20); time.sleep(.02)
    def end_drag():
        button(False); key('LEFTMETA', False); time.sleep(.6)
    setup(drag_scene, A2)
    true_before = {i: geometry(i) for i in ids}
    n = solves()
    start_drag(S, (1280, 700))
    time.sleep(1.6)
    st = spread()['audition']
    check(st['watching'] and not st['offered'], 'audition: a pause starts timing, nothing is offered before the delay', str(st))
    time.sleep(1.7)
    st = wait(lambda: spread()['audition']['offered'] and spread()['audition'], 2, 'offer')
    check(st['offered'], 'audition: after 3 s of stillness the solo is offered')
    actors = {a['id'] for a in st['actors']}
    check({A1, A2} <= actors and R not in actors, 'audition: the offer moves the center windows, not the untouched resident', str(actors))
    time.sleep(.4)
    check(all(geometry(i) == true_before[i] for i in (A1, A2, R)), 'audition: the offer changes no true geometry')
    moved_shown = all(abs(shown(i)[0] - footprint(i)[0]) > 20 for i in (A1, A2))
    check(moved_shown, 'audition: the offered layout is shown (drawn where it would go)')
    shot('audition-offer.png')
    # Movement inside the 50 pt hotspot is not a refusal.
    pointer(1280 + 30, 700 + 20); time.sleep(.3)
    check(spread()['audition']['offered'], 'audition: moving 36 pt within the hotspot keeps the offer')
    reserved = spread()['audition']['reserved']
    dropped_at = geometry(S)
    end_drag()
    settle(ids)
    targets = {m['id']: m for m in spread()['last']['moves']}
    st = spread()['audition']
    check(st['accepts'] >= 1, 'audition: the drop accepts the offer', str(st))
    for a in (A1, A2):
        g = geometry(a); m = targets.get(a)
        check(m and abs(g['x'] + g['width'] / 2 - m['x']) < 1 and abs(g['y'] + g['height'] / 2 - m['y']) < 1,
              f'audition: accepted window {a} lands where it was shown', f'{g} vs {m}')
    g = geometry(S); time.sleep(.8)
    check(geometry(S) == g, 'audition: the dropped window stays exactly where it was dropped (no settle, no coast)')
    sx, sy = center(S)
    check(reserved[0] <= sx - g['width'] / 2 and sx + g['width'] / 2 <= reserved[2] and
          reserved[1] <= sy - g['height'] / 2 and sy + g['height'] / 2 <= reserved[3],
          'audition: the drop lies inside the reserved footprint', f'{reserved} {g}')
    check(geometry(R) == true_before[R], 'audition: the untouched resident stays put')
    shot('audition-accepted.png')

    # 4. Refused by leaving the hotspot: every window returns exactly; the drag goes on.
    setup(drag_scene, A2)
    true_before = {i: geometry(i) for i in ids}
    shown_before = {i: shown(i) for i in (A1, A2, R)}
    start_drag(S, (1280, 700)); time.sleep(3.4)
    check(wait(lambda: spread()['audition']['offered'], 2, 'offer'), 'refusal: the offer shows')
    pointer(1280 + 160, 700); time.sleep(.6)
    st = spread()['audition']
    check(not st['offered'] and st['denials'] >= 1, 'refusal: leaving the 50 pt hotspot refuses the offer', str(st))
    settle((A1, A2, R))
    check(all(geometry(i) == true_before[i] for i in (A1, A2, R)), 'refusal: true geometry never changed')
    back = all(max(abs(a - b) for a, b in zip(shown(i), shown_before[i])) < 1 for i in (A1, A2, R))
    check(back, 'refusal: every window is drawn exactly where it was', str({i: (shown(i), shown_before[i]) for i in (A1, A2)}))
    pointer(1280 + 160, 650); time.sleep(.3)
    end_drag(); settle(ids)
    check(all(geometry(i) == true_before[i] for i in (A1, A2, R)), 'refusal: a quick drop after refusing moves nothing else')

    # 5. Esc during the offer cancels the drag and the audition together.
    setup(drag_scene, A2)
    true_before = {i: geometry(i) for i in ids}
    start_drag(S, (1280, 700)); time.sleep(3.4)
    check(wait(lambda: spread()['audition']['offered'], 2, 'offer'), 'Esc: the offer shows')
    key('ESC', True); key('ESC', False); time.sleep(.1)
    end_drag(); settle(ids)
    check(all(geometry(i) == true_before[i] for i in ids), 'Esc: every window, the dragged one included, is back exactly',
          str({i: (geometry(i), true_before[i]) for i in ids if geometry(i) != true_before[i]}))

    # 6. A client resize during the offer ends it and is never rolled back.
    setup(drag_scene, A2)
    start_drag(S, (1280, 700)); time.sleep(3.4)
    check(wait(lambda: spread()['audition']['offered'], 2, 'offer'), 'invalidation: the offer shows')
    denials = spread()['audition']['denials']
    g = geometry(A1)
    ipc('window-rules/configure-view', {'id': A1, 'geometry': {'x': g['x'], 'y': g['y'], 'width': g['width'] - 60, 'height': g['height']}})
    wait(lambda: geometry(A1)['width'] == g['width'] - 60, 3, 'client resize')
    time.sleep(.4)
    st = spread()['audition']
    check(not st['offered'] and st['denials'] > denials, 'invalidation: a client resize ends the offer', str(st))
    check(geometry(A1)['width'] == g['width'] - 60, 'invalidation: the client change is kept')
    pointer(1280 + 200, 300); time.sleep(.2); end_drag(); settle(ids)

    # 6b. Shift pressed while the offer shows, without moving: the drop refuses it (L31).
    setup(drag_scene, A2)
    true_before = {i: geometry(i) for i in ids}
    start_drag(S, (1280, 700)); time.sleep(3.4)
    check(wait(lambda: spread()['audition']['offered'], 2, 'offer'), 'Shift at the drop: the offer shows')
    key('LEFTSHIFT', True); time.sleep(.3)
    check(not spread()['audition']['offered'], 'Shift without moving: the offer is refused before the drop')
    end_drag(); key('LEFTSHIFT', False); settle(ids)
    check(all(geometry(i) == true_before[i] for i in (A1, A2, R)), 'Shift at the drop: the offer is refused, nothing else moves',
          str({i: (true_before[i], geometry(i)) for i in (A1, A2, R) if geometry(i) != true_before[i]}))

    # 6c. The dragged window resizing itself voids the reservation: the offer ends.
    setup(drag_scene, A2)
    start_drag(S, (1280, 700)); time.sleep(3.4)
    check(wait(lambda: spread()['audition']['offered'], 2, 'offer'), 'dragged resize: the offer shows')
    g = geometry(S)
    ipc('window-rules/configure-view', {'id': S, 'geometry': {'x': g['x'], 'y': g['y'], 'width': g['width'] + 80, 'height': g['height']}})
    resized = True
    try: wait(lambda: geometry(S)['width'] == g['width'] + 80, 3, 'dragged resize')
    except RuntimeError: resized = False
    if resized:
        time.sleep(.3)
        check(not spread()['audition']['offered'], 'dragged resize: the offer ends', str(spread()['audition']))
    else:
        print('NOTE dragged resize: the client did not take the new size during the drag; check skipped', flush=True)
    pointer(1280 + 200, 300); time.sleep(.2); end_drag(); settle(ids)

    # 6d. A zone setting changed while the offer shows ends it; the drop commits nothing.
    setup(drag_scene, A2)
    true_before = {i: geometry(i) for i in ids}
    start_drag(S, (1280, 700)); time.sleep(3.4)
    check(wait(lambda: spread()['audition']['offered'], 2, 'offer'), 'zone change: the offer shows')
    ipc('wayfire/set-config-options', {'scottland/center_width': 40}); time.sleep(.4)
    check(not spread()['audition']['offered'], 'zone change: changing the center width ends the offer')
    end_drag(); settle(ids)
    check(all(geometry(i) == true_before[i] for i in (A1, A2, R)), 'zone change: the drop commits nothing')
    ipc('wayfire/set-config-options', {'scottland/center_width': 33.333}); time.sleep(.5)

    # 6e. A window still gliding when the offer starts: its glide is suspended, it is shown exactly
    # where the offer puts it, and a refusal resumes the glide to its own destination.
    ipc('wayfire/set-config-options', {'scottland/solo_audition_delay': 150})
    setup(drag_scene, A2)
    start_drag(S, (1280, 700)); time.sleep(.3)
    ipc('scottland/present', {'window': R})   # R glides into the center (300 ms) and joins the solve
    st = wait(lambda: (lambda a: a['offered'] and a)(spread()['audition']), 2, 'offer during a glide')
    suspended = st.get('suspended', 0)
    time.sleep(.45)
    target = {a['id']: a['to'] for a in spread()['audition']['actors']}
    def at_target(i):
        x1, y1, x2, y2 = shown(i); t = target[i]; g = geometry(i)
        return abs((x1 + x2) / 2 - t[0]) < 1.5 and abs((y1 + y2) / 2 - t[1]) < 1.5 and abs((x2 - x1) - g['width'] * t[2]) < 2
    check(suspended >= 1, 'glide: the running glide was suspended under the offer', str(st))
    check(all(at_target(i) for i in target), 'glide: every offered window is drawn exactly at its offered spot and scale',
          str({i: (shown(i), target[i]) for i in target}))
    pointer(1280 + 200, 700); time.sleep(.2)
    end_drag(); settle(ids, 6)
    x1, y1, x2, y2 = shown(R); g = geometry(R)
    check(abs((x1 + x2) / 2 - (g['x'] + g['width'] / 2)) < 1.5 and abs((y1 + y2) / 2 - (g['y'] + g['height'] / 2)) < 1.5,
          'glide: after the refusal the suspended glide finishes at its own destination')
    ipc('wayfire/set-config-options', {'scottland/solo_audition_delay': 3000})

    # 6f. With always-on window avoidance, offered windows still end exactly at their targets.
    ipc('wayfire/set-config-options', {'scottland/window_avoidance_always': True})
    setup(drag_scene, A2)
    start_drag(S, (1280, 700)); time.sleep(3.4)
    check(wait(lambda: spread()['audition']['offered'], 2, 'offer'), 'avoidance on: the offer shows')
    time.sleep(1.0)
    target = {a['id']: a['to'] for a in spread()['audition']['actors']}
    check(all(at_target(i) for i in target), 'avoidance on: offered windows are drawn exactly at their targets',
          str({i: (shown(i), target[i]) for i in target}))
    held = [hint(i).get('audition_held') for i in target]
    check(all(held), 'avoidance on: the windows the offer shows are anchored for peeking', str(held))
    pointer(1280 + 200, 300); time.sleep(.2); end_drag(); settle(ids)
    ipc('wayfire/set-config-options', {'scottland/window_avoidance_always': False}); time.sleep(.5)

    # 6g. Raising the hotspot setting during an offer cannot widen it past its reservation
    # (Astra, round 2): the offer ends, and a drop 150 pt away commits nothing.
    setup(drag_scene, A2)
    true_before = {i: geometry(i) for i in ids}
    start_drag(S, (1280, 700)); time.sleep(3.4)
    check(wait(lambda: spread()['audition']['offered'], 2, 'offer'), 'hotspot change: the offer shows')
    ipc('wayfire/set-config-options', {'scottland/solo_audition_hotspot': 200}); time.sleep(.3)
    check(not spread()['audition']['offered'], 'hotspot change: changing the hotspot ends the offer')
    pointer(1280 + 150, 700); time.sleep(.2); end_drag(); settle(ids)
    check(all(geometry(i) == true_before[i] for i in (A1, A2, R)), 'hotspot change: the drop commits nothing')
    ipc('wayfire/set-config-options', {'scottland/solo_audition_hotspot': 50}); time.sleep(.3)

    # 7. A Shift drag keeps its scale (L31) and never auditions.
    setup(drag_scene, A2)
    start_drag(S, (1280, 700)); key('LEFTSHIFT', True); pointer(1281, 700); time.sleep(3.6)
    check(not spread()['audition']['offered'], 'Shift drag: no audition')
    pointer(1281, 300); time.sleep(.1); end_drag(); key('LEFTSHIFT', False); settle(ids)

    # 8. Nothing else spreads (P4): present, a hint tap (zone cycling), an ordinary drop.
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
