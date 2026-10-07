#!/usr/bin/env python3
"""GO24: the dye's coast keeps the smear as it slows, Dye spread included. Run in a private
headless session on a test machine (tests/goo-coast-spread-test.sh), on each GPU path.

When the goo falls asleep with pickup on, a dye-only pass runs five times a second for 14 s,
each pass standing for less until it stands for nothing; flow, pickup and release scale with
it. Spread must scale too: a pass that stands for nothing must not still mix each texel 90 %
toward its neighbours (2026-10-06: it did, so the last seconds of every coast blurred the dye
with nothing renewing it, and the picture at rest was not the one the moving goo showed).

Fixture: Mike's goo settings with swirl 0 (no transport, so only spread can blur), a wallpaper
of strong color patches, two windows. A real click moves focus to wake the goo; the dye along
both windows' bands is read from the GPU (goo-state dye_points, one simulation step) the moment
the goo falls asleep and again when the coast has ended, at Dye spread 0.9 and at 0 (control).

Test hooks: window-rules/configure-view places windows; set-config-options sets values (setup);
goo-state dye_points reads the dye.
"""
import json, os, signal, socket, struct, subprocess, sys, time
from pathlib import Path

repo = Path(__file__).resolve().parents[1]
art = Path(sys.argv[1]).resolve(); art.mkdir(parents=True, exist_ok=True)
assert os.environ.get('SCOTTLAND_TEST_MODEL') == '1', 'private headless session required'
sock = socket.socket(socket.AF_UNIX); sock.connect(os.environ['WAYFIRE_SOCKET'])
clients = []; results = []; record = {}

MIKE = dict(goo_thickness=26, goo_reach=35, goo_thinning=.27, goo_swell=.7, goo_noise=.38, goo_lump=315,
            goo_drift=.39, goo_wave_speed=.22, goo_wave_damp=.958, goo_wave_height=.76, goo_spread=.9,
            goo_swirl=0., goo_release=.155, goo_shine=.62, goo_relief=3.5, goo_depth=4., goo_profile=.52,
            goo_soak=1., goo_pickup_balance=.47, goo_overlap_film=10., goo_hover_cloudiness=0.,
            goo_hover_emissivity=.46, goo_hover_distance=48., goo_dye_density=1.5)

def ipc(method, data=None):
    b = json.dumps({'method': method, 'data': data or {}}).encode()
    sock.sendall(struct.pack('<I', len(b)) + b)
    def read(n):
        r = b''
        while len(r) < n:
            c = sock.recv(n - len(r))
            if not c: raise RuntimeError('compositor disconnected')
            r += c
        return r
    out = json.loads(read(struct.unpack('<I', read(4))[0]))
    if isinstance(out, dict) and 'error' in out: raise RuntimeError(out)
    return out

def check(name, ok, detail=None):
    results.append((name, bool(ok)))
    print(('PASS ' if ok else 'FAIL ') + name + ('' if detail is None else ' ' + json.dumps(detail)), flush=True)
def options(**values): ipc('wayfire/set-config-options', {'scottland/' + k: v for k, v in values.items()})
def views(): return ipc('scottland/layout-state')['views']
def view(title): return next(v for v in views() if v['title'] == title)
def state(**data): return ipc('scottland/goo-state', data)['screens'][0]
def pointer(x, y): ipc('stipc/move_cursor', {'x': round(x), 'y': round(y)})
def click(x, y):
    pointer(x, y); time.sleep(.1)
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'press'}); time.sleep(.05)
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'release'})
def focus(title):
    f = view(title)['frame']; click(f['x'] + f['width']/2, f['y'] + f['height']/2); pointer(5, 5)
    for _ in range(100):
        if view(title)['frame'].get('focus', 0) > .99: return
        time.sleep(.05)
    raise AssertionError('focus did not reach ' + title)
def rest(what, deadline=120):
    end = time.monotonic() + deadline; s = state()
    while time.monotonic() < end:
        s = state()
        if s['sleeping'] and not s.get('breath_loose') and not s.get('water_running') and not s.get('pickup_pending'):
            return s
        time.sleep(.1)
    raise AssertionError('goo did not come to rest: ' + what)

def ring(f, d, step=4, margin=18):
    x, y, w, h = f['x'], f['y'], f['width'], f['height']
    pts = [(x + t, y - d) for t in range(margin, int(w) - margin, step)]
    pts += [(x + w + d, y + t) for t in range(margin, int(h) - margin, step)]
    pts += [(x + t, y + h + d) for t in range(int(w) - margin, margin, -step)]
    pts += [(x - d, y + t) for t in range(int(h) - margin, margin, -step)]
    return pts
def points(): return [p for t in ('coast-a', 'coast-b') for p in ring(view(t)['frame'], MIKE['goo_thickness']/2)]
def capture(pts):
    s = state(x=0, y=0, dye_points=[[float(x), float(y)] for x, y in pts])
    return [[255*v for v in c] for c in s['dye_field']], s
def mean_diff(a, b):
    ds = [abs(x - y) for p, q in zip(a, b) for x, y in zip(p, q)]; return sum(ds)/len(ds)
def sharpness(field):
    """Mean |difference| between neighbouring samples along the band: falls as the dye blurs."""
    ds = [abs(x - y) for p, q in zip(field, field[1:]) for x, y in zip(p, q)]; return sum(ds)/len(ds)

def spawn(title, x, y, w, h):
    clients.append(subprocess.Popen(['foot', '-c', '/dev/null', '-o', 'resize-by-cells=no', '-T', title, 'sleep', '3600'],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
    for _ in range(100):
        if any(v['title'] == title for v in views()): break
        time.sleep(.05)
    ipc('window-rules/configure-view', {'id': view(title)['id'], 'geometry': {'x': x, 'y': y, 'width': w, 'height': h}})
    for _ in range(100):
        f = view(title)['frame']
        if abs(f['x'] - x) < 2 and abs(f['width'] - w) < 2: return
        time.sleep(.05)
def wallpaper():
    changes = state()['wallpaper_node_changes']
    p = subprocess.Popen(['quickshell', '-p', str(repo / 'tests/GooWallpaper.qml')], env=dict(os.environ, GOO_WALLPAPER_PATCHES='1'),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    clients.append(p)
    for _ in range(100):
        if state()['wallpaper_node_changes'] > changes: return p
        time.sleep(.05)
    raise AssertionError('wallpaper did not map')
def stop(p):
    try: os.killpg(p.pid, signal.SIGTERM)
    except ProcessLookupError: pass
    try: p.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL); p.wait()

def coast(spread, to):
    """Wake with a real focus click, read the dye as the goo falls asleep and after its coast."""
    options(goo_spread=spread); rest('spread %g' % spread)
    focus(to); pts = points()
    for _ in range(3000):
        s = state()
        if s['sleeping'] and s['water_running']: break
        time.sleep(.005)
    asleep, s0 = capture(pts)
    s1 = rest('coast at spread %g' % spread); settled, _ = capture(pts)
    return {'spread': spread, 'coast_flows': s1['dye_flows'] - s0['dye_flows'], 'steps_during_coast': s1['steps'] - s0['steps'],
            'change': round(mean_diff(asleep, settled), 2),
            'sharpness': [round(sharpness(asleep), 3), round(sharpness(settled), 3)]}

try:
    ipc('wayfire/set-config-options', {'output:HEADLESS-1/mode': '1280x720@60000'})
    options(**MIKE)
    spawn('coast-a', 260, 170, 360, 260); spawn('coast-b', 700, 210, 340, 240)
    wallpaper(); focus('coast-a'); rest('fixture')
    print('path:', 'packed RGBA8 (GLES 2)' if state()['packed'] else 'RGBA16F', flush=True)
    runs = {}
    for i, spread in enumerate((0., .9, 0., .9)):
        r = coast(spread, 'coast-b' if i % 2 == 0 else 'coast-a'); runs.setdefault(spread, []).append(r); print(json.dumps(r), flush=True)
    record['runs'] = runs
    for r in runs[0.] + runs[.9]:
        check('spread %g: the coast ran over sleeping goo (dye passes, no simulation steps)' % r['spread'],
              r['coast_flows'] > 20 and r['steps_during_coast'] == 0, r)
    control = max(r['change'] for r in runs[0.])
    for r in runs[.9]:
        # The coast may move the dye a little (its pickup and release still act); at spread 0.9
        # it must not move it much more than with no spread at all, nor blur it.
        check('spread 0.9: the coast leaves the dye about as the moving goo left it',
              r['change'] <= max(2 * control, control + 1.5), {'change': r['change'], 'control': control})
        check('spread 0.9: the coast does not blur the dye along the band',
              r['sharpness'][1] >= .95 * r['sharpness'][0], r['sharpness'])
finally:
    (art / 'coast-spread.json').write_text(json.dumps(record, indent=1))
    for c in clients: stop(c)
failed = [n for n, ok in results if not ok]
print(f'{len(results) - len(failed)} / {len(results)} passed', flush=True)
sys.exit(1 if failed or not results else 0)
