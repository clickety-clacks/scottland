#!/usr/bin/env python3
"""WK41/WK13: a minimized window is no avoidance obstacle and no cover.

Run inside a caller-owned one-output headless session (tests/hint-minimized-obstacle-test.sh).
Window mode is entered with real stipc Alt input. Fixture setup over IPC (declared, bypasses
input): configure-view placement, focus-view stacking, wm-actions/set-minimized. Judged from
captured pixels: the bounding box of R's known client color. R (rear) is fully covered by W; F is
focused elsewhere.

Phase A (control, W shown): R must peek (its drawn color box leaves its true place).
Phase B (W minimized, drawn nowhere): nothing covers R, so R must be drawn at its true place.
Regression for the 10-06 review's finding 1: the placement-key sort inserted views missing from
the stacking order (minimized, or a widget slid away) at the front, so W stayed an obstacle.
Usage: hint-minimized-obstacle-test.py ARTIFACTS
"""
import json, os, signal, socket, struct, subprocess, sys, time
from pathlib import Path

art = Path(sys.argv[1]).resolve(); art.mkdir(parents=True, exist_ok=True)
here = Path(__file__).resolve().parent
sock = socket.socket(socket.AF_UNIX); sock.settimeout(8); sock.connect(os.environ['WAYFIRE_SOCKET'])
clients, held = [], set()
signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
results = []


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
    r = json.loads(read(struct.unpack('<I', read(4))[0]))
    if isinstance(r, dict) and 'error' in r: raise RuntimeError(r)
    return r


def wait(fn, what, timeout=10):
    end = time.monotonic() + timeout; last = None
    while time.monotonic() < end:
        last = fn()
        if last: return last
        time.sleep(.03)
    raise RuntimeError(f'{what}: timeout, last {last}')


def check(name, ok, detail=''):
    results.append(ok)
    print(('PASS ' if ok else 'FAIL ') + name + (f'  [{detail}]' if detail else ''), flush=True)


def key(name, down):
    ipc('stipc/feed_key', dict(key='KEY_' + name, state=down))
    (held.add if down else held.discard)(name)


def views(): return ipc('window-rules/list-views')
def hints(): return ipc('scottland/hints')


COLORS = {'R': '#2850c8', 'W': '#e02828', 'F': '#28b450'}


def launch(title, w, h):
    palette = art / (title + '.json'); palette.write_text(json.dumps(dict(background=COLORS[title])))
    clients.append(subprocess.Popen([sys.executable, str(here / 'hint-style-app.py'), 'Probe' + title,
                                     str(w), str(h), str(palette)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    return wait(lambda: next((v['id'] for v in views() if v.get('title') == 'Probe' + title), None), 'map ' + title)


def place(id, x, y, w, h):
    ipc('window-rules/configure-view', dict(id=id, geometry=dict(x=x, y=y, width=w, height=h)))
    wait(lambda: (lambda g: (g['x'], g['y'], g['width'], g['height']) == (x, y, w, h))(
        next(v for v in views() if v['id'] == id)['geometry']), f'place {id}', 5)


def color_box(name, tag):
    png = art / f'{tag}.png'
    subprocess.run(['grim', str(png)], check=True, timeout=8)
    data = subprocess.check_output(['grim', '-t', 'ppm', '-'], timeout=8)
    fields, pos = [], 0
    while len(fields) < 4:
        while data[pos:pos + 1].isspace(): pos += 1
        end = pos
        while not data[end:end + 1].isspace(): end += 1
        fields.append(data[pos:end]); pos = end
    width, height = int(fields[1]), int(fields[2]); px = data[pos + 1:]
    rgb = tuple(int(COLORS[name][i:i + 2], 16) for i in (1, 3, 5))
    xs, ys, n = [], [], 0
    for y in range(0, height, 2):
        row = y * width * 3
        for x in range(0, width, 2):
            i = row + x * 3
            if abs(px[i] - rgb[0]) <= 6 and abs(px[i + 1] - rgb[1]) <= 6 and abs(px[i + 2] - rgb[2]) <= 6:
                xs.append(x); ys.append(y); n += 1
    if not n: return None
    return dict(x1=min(xs), y1=min(ys), x2=max(xs), y2=max(ys), cx=(min(xs) + max(xs)) / 2,
                cy=(min(ys) + max(ys)) / 2, samples=n)


def row(id):
    return next((h for h in hints()['hints'] if h['window'] == id), None)


def settled_in_mode(id):
    st = hints()
    if not st.get('active'): return None
    h = next((h for h in st['hints'] if h['window'] == id), None)
    if not h or not h.get('visible') or h.get('pop', 0) < .999: return None
    if abs(h['dx'] - h['target_dx']) + abs(h['dy'] - h['target_dy']) > .2: return None
    if st.get('avoidance_solve_pending'): return None
    return h


try:
    out = ipc('window-rules/list-outputs')
    assert len(out) == 1, 'one output'
    SW, SH = out[0]['geometry']['width'], out[0]['geometry']['height']
    ipc('wayfire/set-config-options', {'scottland/sounds': False, 'scottland/window_mode_tint': 0,
        'scottland/center_width': 80.0,
        'scottland/window_avoidance_always': False, 'scottland/hint_avoidance_always': False})
    R = launch('R', 300, 200); W = launch('W', 520, 420); F = launch('F', 260, 180)
    cx, cy = SW // 2, SH // 2
    true_R = (cx - 150, cy - 100, 300, 200)
    place(R, *true_R); place(W, cx - 260, cy - 210, 520, 420); place(F, 260, 60, 260, 180)
    for id in (R, W, F):
        ipc('window-rules/focus-view', dict(id=id))
        wait(lambda: (ipc('window-rules/get-focused-view').get('info') or {}).get('id') == id, f'focus {id}', 4)
    # Phase A: W shown over R.
    key('LEFTALT', True)
    a = wait(lambda: settled_in_mode(R), 'phase A: R hint visible and settled')
    boxA = color_box('R', 'phaseA')
    shiftA = None if not boxA else (boxA['cx'] - cx, boxA['cy'] - cy)
    check('control: with W shown over R, R is drawn away from its true place (peeks)',
          boxA is not None and max(abs(shiftA[0]), abs(shiftA[1])) > 40,
          f'box {boxA}; shift {shiftA}; dx,dy {a["dx"]:.1f},{a["dy"]:.1f}; order {a.get("avoidance_order")}')
    key('LEFTALT', False)
    wait(lambda: not hints().get('active') and (lambda h: h and abs(h['dx']) + abs(h['dy']) < .2)(row(R)),
         'R back home after Window mode', 10)
    # Phase B: W minimized (fixture), drawn nowhere.
    ipc('wm-actions/set-minimized', {'view_id': W, 'state': True})
    wait(lambda: next(v for v in views() if v['id'] == W)['minimized'], 'W minimized', 4)
    # Wayfire animates the minimize; its node stays enabled until that ends. Fixture readiness:
    # the compositor reports W's root node disabled (layout-state 'hidden').
    wait(lambda: next(v for v in ipc('scottland/layout-state')['views'] if v['id'] == W).get('hidden'),
         'W hidden (node disabled)', 5)
    end = time.monotonic() + 5
    while True:
        boxW = color_box('W', 'phaseB-minimized')
        if boxW is None or time.monotonic() > end: break
        time.sleep(.1)
    check('fixture: minimized W is not drawn anywhere', boxW is None, str(boxW))
    key('LEFTALT', True)
    b = wait(lambda: settled_in_mode(R), 'phase B: R hint visible and settled')
    boxB = color_box('R', 'phaseB')
    check('fixture: W still hidden at the phase-B capture',
          next(v for v in ipc('scottland/layout-state')['views'] if v['id'] == W).get('hidden') is True)
    shiftB = None if not boxB else (boxB['cx'] - cx, boxB['cy'] - cy)
    check('minimized W covers nothing: R is drawn at its true place',
          boxB is not None and max(abs(shiftB[0]), abs(shiftB[1])) <= 4,
          f'box {boxB}; shift {shiftB}; dx,dy {b["dx"]:.1f},{b["dy"]:.1f}; target {b["target_dx"]:.1f},'
          f'{b["target_dy"]:.1f}; outcome {b.get("outcome")}; visible_fraction {b.get("visible_fraction")}; '
          f'outline {b.get("outline")}; order {b.get("avoidance_order")}')
    key('LEFTALT', False)
finally:
    for name in list(held):
        try: key(name, False)
        except Exception: pass
    for p in clients:
        if p.poll() is None: p.terminate()
    for p in clients:
        try: p.wait(timeout=3)
        except subprocess.TimeoutExpired: p.kill(); p.wait()
    sock.close()
    print(f'{sum(results)} passed, {len(results) - sum(results)} failed', flush=True)
sys.exit(0 if results and all(results) else 1)
