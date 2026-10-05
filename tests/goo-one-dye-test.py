#!/usr/bin/env python3
"""GO28: one dye. Run in a private headless session on a test machine (tests/goo-one-dye-test.sh).

Scenes, each judged by the GPU dye history (goo-state samples) and by screen pixels (grim):

1. Focus colors the whole goo. A window focused and unfocused by real clicks, on plain
   yellow paper: the change reaches the shore of its band, not only the wall, at no pickup,
   at the shipped pickup and at Mike's (full pickup, density 1.5).
2. Mixing, not replacement. The dye at full pickup lies on the subtractive (absorbance)
   line between the pure focus dye and the pure picked-up dye, strictly between them, and
   closer to it than any linear RGB blend of the two. Pickup strength is set through the
   Settings Goo row with real input.
3. Pickup from a window. Film over a magenta window takes magenta (green falls), where the
   old wallpaper-only pickup would take the yellow paper (green rises).
4. Spread and swirl smear. On paper half red, half blue, the dye along a band that crosses
   the boundary changes from red to blue over a short distance with swirl and spread at
   zero; set high through the Settings rows (real input, saved), the change is smeared out.
5. Never the goo itself. Repeated pickup coasts over an unchanged backdrop leave the dye
   where it was: no feedback from the goo's own color.
6. Bounded wakes. A window animating under the film: the waves and field never wake, pickup
   coasts are few and their cool-down doubles; when the animation stops the goo rests.
Every scene ends at rest: no simulation steps, dye passes or watercolor ticks.

Test hooks used: window-rules/configure-view places windows (setup only); some settings are
set by IPC as setup; scene 5 restarts the coast with the water_coast test hook.
"""
import json, math, os, signal, socket, struct, subprocess, sys, time
from pathlib import Path
import gi
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf

repo = Path(__file__).resolve().parents[1]
art = Path(sys.argv[1]).resolve(); art.mkdir(parents=True, exist_ok=True)
assert os.environ.get('SCOTTLAND_TEST_MODEL') == '1', 'private headless session required'
sock = socket.socket(socket.AF_UNIX); sock.connect(os.environ['WAYFIRE_SOCKET'])
clients = []; results = []; record = {}

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
def option(name): return float(ipc('wayfire/get-config-option', {'option': 'scottland/' + name})['value'])
def views(): return ipc('scottland/layout-state')['views']
def view(title): return next(v for v in views() if v['title'] == title)
def state(x=None, y=None): return ipc('scottland/goo-state', {} if x is None else {'x': x, 'y': y})['screens'][0]
def dye(x, y):
    s = state(x, y); return [s['red'], s['green'], s['blue']]
def pointer(x, y): ipc('stipc/move_cursor', {'x': round(x), 'y': round(y)})
def key(code, down): ipc('stipc/feed_key', {'key': code, 'state': down})
def tap(code): key(code, True); key(code, False)
def click(x, y):
    pointer(x, y); time.sleep(.1)
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'press'}); time.sleep(.05)
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'release'})
def drag(x, y, dx, dy):
    pointer(x, y); time.sleep(.12)
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'press'})
    for i in range(1, 13): pointer(x + dx*i/12, y + dy*i/12); time.sleep(.025)
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'release'}); time.sleep(.3)
def focus(title):
    f = view(title)['frame']; click(f['x'] + f['width']/2, f['y'] + f['height']/2); pointer(5, 5)
    for _ in range(100):
        if view(title)['frame'].get('focus', 0) > .99: return
        time.sleep(.05)
    raise AssertionError('focus did not reach ' + title)

def rest(what, deadline=90):
    """Asleep, the settled region known, no coast running, nothing pending."""
    end = time.monotonic() + deadline; s = state()
    while time.monotonic() < end:
        s = state()
        if s['sleeping'] and not s.get('breath_loose') and not s.get('water_running') and not s.get('pickup_pending'):
            return s
        time.sleep(.1)
    raise AssertionError('goo did not come to rest: ' + what + ' ' + json.dumps(
        {k: s.get(k) for k in ('sleeping', 'water_running', 'pickup_pending', 'last_wake', 'wave_energy')}))

def quiet(what, seconds=4):
    a = state(); time.sleep(seconds); b = state()
    d = {k: b[k] - a[k] for k in ('steps', 'dye_flows', 'water_ticks')}
    check(what + ': at rest, no steps, dye passes or ticks', b['sleeping'] and not any(d.values()), d)

def shot(name):
    path = art / (name + '.png'); subprocess.run(['grim', str(path)], check=True)
    pix = GdkPixbuf.Pixbuf.new_from_file(str(path)); data = pix.get_pixels()
    stride, ch = pix.get_rowstride(), pix.get_n_channels()
    return lambda x, y: list(data[round(y)*stride + round(x)*ch:round(y)*stride + round(x)*ch + 3])

def spawn(title, x, y, w, h, background=None, animate=False):
    cmd = ['foot', '-c', '/dev/null', '-o', 'resize-by-cells=no', '-T', title]
    if background: cmd[3:3] = ['-o', 'colors.background=' + background]
    if animate:
        # Flips green/magenta until art/stop exists, then rests on green; turns blue once
        # art/next exists.
        cmd += ['python3', '-u', '-c', "import os,time\nstop,nxt=%r,%r\ndef bg(c): print('\\033]11;'+c+'\\007',end='',flush=True)\n"
                "while not os.path.exists(stop):\n bg('#d02090'); time.sleep(.1); bg('#20c040'); time.sleep(.1)\n"
                "bg('#20c040')\nwhile not os.path.exists(nxt): time.sleep(.1)\nbg('#2040e0')\ntime.sleep(900)" % (str(art/'stop'), str(art/'next'))]
    else:
        cmd += ['sleep', '900']
    clients.append(subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
    for _ in range(100):
        if any(v['title'] == title for v in views()): break
        time.sleep(.05)
    ipc('window-rules/configure-view', {'id': view(title)['id'], 'geometry': {'x': x, 'y': y, 'width': w, 'height': h}})
    for _ in range(100):
        f = view(title)['frame']
        if abs(f['x'] - x) < 2 and abs(f['width'] - w) < 2: return
        time.sleep(.05)

def wallpaper(color, bottom=''):
    changes = state()['wallpaper_node_changes']
    p = subprocess.Popen(['quickshell', '-p', str(repo / 'tests/GooWallpaper.qml')],
                         env=dict(os.environ, GOO_WALLPAPER_COLOR=color, GOO_WALLPAPER_BOTTOM=bottom),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    clients.append(p)
    for _ in range(100):   # mapped: the goo hears a new background-layer client
        if state()['wallpaper_node_changes'] > changes: return p
        time.sleep(.05)
    raise AssertionError('wallpaper did not map')

def stop(p):
    try: os.killpg(p.pid, signal.SIGTERM)
    except ProcessLookupError: pass
    try: p.wait(timeout=5)
    except subprocess.TimeoutExpired: os.killpg(p.pid, signal.SIGKILL); p.wait()

# --- the Settings panel, driven with real pointer and keys (helpers as in goo-test.py) ---
def quickshell_pid(wrapper_pid):
    end = time.monotonic() + 5
    while time.monotonic() < end:
        pending, seen = [wrapper_pid], set()
        while pending:
            pid = pending.pop()
            if pid in seen: continue
            seen.add(pid); proc = Path(f'/proc/{pid}')
            try:
                comm = (proc / 'comm').read_text().strip()
                argv = (proc / 'cmdline').read_bytes().decode(errors='replace').split('\0')
            except OSError:
                continue
            if comm == 'quickshell' and str(repo / 'core/settings') in argv: return pid
            try: pending.extend(int(c) for c in (proc / 'task' / str(pid) / 'children').read_text().split())
            except (OSError, ValueError): pass
        time.sleep(.03)
    raise RuntimeError('settings panel did not start')
def snapshot(panel):
    return json.loads(subprocess.check_output(['qs', 'ipc', '--pid', str(quickshell_pid(panel.pid)), 'call',
                                               'settings-test', 'snapshot'], text=True))
def origin(q): return ((1280 - q['panel']['width'])/2, 720 - max(24, round(720*.04)) - q['panel']['height'])
def row_y(q, name):
    g = q['goo']; return g['y'] + g['rows'].index(name)*(g['rowHeight'] + 1) + g['rowHeight']/2
def open_panel():
    home = art / 'settings-home'; (home / 'scottland').mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, QS_DISABLE_FILE_WATCHER='1', SCOTTLAND_SETTINGS_TEST='1',
               SCOTTLAND_CTL=str(repo / 'core/libexec/scottland-ctl'), SCOTTLAND_LAYOUT_FILE=str(home / 'scottland/layout.ini'))
    log = open(art / 'panel.log', 'a')
    panel = subprocess.Popen(['qs', '-n', '-p', str(repo / 'core/settings')], env=env, stdout=log, stderr=log)
    clients.append(panel)
    for _ in range(100):
        try:
            q = snapshot(panel)
            if q.get('panel'): break
        except (subprocess.CalledProcessError, RuntimeError):
            pass
        time.sleep(.1)
    wrapped = q['viewport']['y'] > 190; columns = 3 if wrapped else 6
    x, y = origin(q)
    click(x + q['viewport']['x'] + q['viewport']['width']*1.5/columns, y + q['viewport']['y'] - (92 if wrapped else 44))
    for _ in range(50):
        if snapshot(panel).get('goo', {}).get('rows'): break
        time.sleep(.1)
    return panel, home / 'scottland/layout.ini'
def set_row(panel, name, fraction):
    """Scroll the row into view with the scrollbar, then press on the row at `fraction` of its width."""
    q = snapshot(panel); vp = q['viewport']; x, top = origin(q)
    if not vp['y'] + q['goo']['rowHeight'] <= row_y(q, name) <= vp['y'] + vp['height'] - q['goo']['rowHeight']:
        content = q['contentHeight']; thumb = vp['height']*vp['height']/content
        target = min(max(0, content - vp['height']), max(0, q['scroll'] + row_y(q, name) - (vp['y'] + vp['height']/2)))
        start = top + vp['y'] + q['scroll']/content*vp['height'] + thumb/2
        end = top + vp['y'] + target/content*vp['height'] + thumb/2
        drag(x + vp['x'] + vp['width'] - 5, start, 0, end - start)
        q = snapshot(panel)
    g = q['goo']
    click(x + g['x'] + g['width']*fraction, top + row_y(q, name)); pointer(5, 5)

def wait_option(name, test, what):
    for _ in range(50):
        v = option(name)
        if test(v): return v
        time.sleep(.1)
    raise AssertionError(what + ': ' + name + ' is ' + str(option(name)))

# --- colors ---
def K(c): return [-math.log(max(v, .0025)) for v in c]
def sub(a, b): return [x - y for x, y in zip(a, b)]
def norm(a): return math.sqrt(sum(x*x for x in a))
def fit(m, f, p):
    """Best mix of f and p for m; returns (share of f, residual relative to |f - p|)."""
    d = sub(f, p); t = sum(x*y for x, y in zip(sub(m, p), d)) / max(sum(x*x for x in d), 1e-9)
    r = norm(sub(m, [pp + t*dd for pp, dd in zip(p, d)]))
    return t, r / max(norm(d), 1e-9)
def avg(samples): return [sum(s[i] for s in samples)/len(samples) for i in range(3)]

try:
    ipc('wayfire/set-config-options', {'output:HEADLESS-1/mode': '1280x720@60000'})
    options(goo_noise=0., goo_drift=0., goo_wave_height=0., goo_swirl=0., goo_soak=0., goo_overlap_film=10.)
    spawn('dye-a', 200, 150, 360, 240)
    spawn('dye-b', 780, 150, 360, 240)
    paper = wallpaper('#e8c840')   # after a window: an empty desktop has no goo to hear it
    thickness = option('goo_thickness')
    def band(title, away):
        f = view(title)['frame']
        return [(f['x'] + f['width']*k/6, f['y'] - away) for k in range(1, 6)]
    distances = {'wall': 1.5, 'middle': thickness/2, 'shore': thickness - 2.5}

    # 1. Focus colors the whole goo.
    def focus_reach(label):
        focus('dye-b'); rest(label + ' unfocused'); off_screen = shot(f'1-{label}-unfocused')
        off = {k: [dye(x, y) for x, y in band('dye-a', d)] for k, d in distances.items()}
        off_px = {k: [off_screen(x, y) for x, y in band('dye-a', d)] for k, d in distances.items()}
        wet = all(state(x, y)['density'] > state(x, y)['threshold'] for x, y in band('dye-a', distances['shore']))
        focus('dye-a'); rest(label + ' focused'); on_screen = shot(f'1-{label}-focused')
        on = {k: [dye(x, y) for x, y in band('dye-a', d)] for k, d in distances.items()}
        on_px = {k: [on_screen(x, y) for x, y in band('dye-a', d)] for k, d in distances.items()}
        moved = {k: norm(sub(avg(on[k]), avg(off[k]))) for k in distances}
        moved_px = {k: norm(sub(avg(on_px[k]), avg(off_px[k]))) for k in distances}
        record['focus-' + label] = {'dye': moved, 'screen': moved_px}
        check(f'{label}: the shore samples lie in the goo', wet)
        check(f'{label}: focusing the window moves its dye at the wall', moved['wall'] > .08, moved)
        check(f'{label}: and across the band to the shore, at least half as much', moved['shore'] > .5*moved['wall']
              and moved['middle'] > .5*moved['wall'], moved)
        check(f'{label}: on screen too, at the shore', moved_px['shore'] > 8 and moved_px['shore'] > .4*moved_px['wall'], moved_px)
        quiet(label)
        return avg(on['middle']), avg(on_px['middle'])
    focus_reach('pickup-0')
    options(goo_soak=.12); focus_reach('pickup-0.12')
    options(goo_soak=1., goo_dye_density=1.5); focus_reach('pickup-1-density-1.5')

    # 2. Mixing, not replacement: pure focus dye F (no pickup), pure pickup P (density 0, so
    #    the focused window's release carries no pigment), and the mix M, through the panel.
    options(goo_soak=0., goo_dye_density=1.); rest('pure focus')
    F = avg([dye(x, y) for x, y in band('dye-a', distances['middle'])])
    options(goo_soak=1., goo_dye_density=0.); rest('pure pickup')
    P = avg([dye(x, y) for x, y in band('dye-a', distances['middle'])])
    options(goo_soak=0., goo_dye_density=1.); rest('back to no pickup')
    panel, layout = open_panel()
    set_row(panel, 'goo_soak', .995)
    soak = wait_option('goo_soak', lambda v: v > .9, 'the Wallpaper soak (pickup) row sets pickup live')
    tap('KEY_ENTER'); time.sleep(.5)
    check('Save writes the pickup strength', layout.exists() and 'goo_soak = ' in layout.read_text())
    rest('mixed'); mixed_screen = shot('2-mixed')
    M = avg([dye(x, y) for x, y in band('dye-a', distances['middle'])])
    share, residual = fit(K(M), K(F), K(P))
    lin_share, lin_residual = fit(M, F, P)
    record['mixing'] = {'F': F, 'P': P, 'M': M, 'focus_share': share, 'residual': residual,
                        'linear_share': lin_share, 'linear_residual': lin_residual, 'soak': soak}
    check('the picked-up and focus dyes differ (fixture)', norm(sub(K(F), K(P))) > .5, record['mixing'])
    check('the pickup set in the panel changed the goo without a reload', norm(sub(K(M), K(F))) > .15, record['mixing'])
    check('the mix is neither the focus dye nor the picked-up dye', .1 < share < .9, record['mixing'])
    check('it lies on the subtractive (absorbance) line between them', residual < .12, record['mixing'])
    check('closer to it than to any linear RGB blend of the two', residual < lin_residual, record['mixing'])
    quiet('mixed')

    # 3. Pickup from a window: film over a magenta window takes magenta, not the yellow paper.
    options(goo_soak=0., goo_dye_density=1.)
    spawn('dye-magenta', 500, 300, 340, 260, background='d02090')
    focus('dye-a')   # in front, its film lies over the magenta window
    rest('film, no pickup')
    a = view('dye-a')['frame']; film = (a['x'] + a['width'] + 4, a['y'] + a['height'] - 30)
    m = view('dye-magenta')['frame']
    check('the film sample lies over the magenta window, outside the front one (fixture)',
          m['x'] < film[0] < m['x'] + m['width'] and m['y'] < film[1] < m['y'] + m['height'] and
          film[0] > a['x'] + a['width'] and state(*film)['density'] > state(*film)['threshold'] and state()['overlapping'])
    bare = dye(*film)
    options(goo_soak=1.); rest('film, pickup'); s3 = shot('3-film-pickup')
    took = dye(*film)
    inside = s3(a['x'] + a['width'] + 60, a['y'] + a['height'] - 20)
    record['window-pickup'] = {'bare': bare, 'took': took, 'window_pixel': inside}
    check('the window under the film is magenta on screen (fixture)', inside[1] < 80 and inside[0] > 150 and inside[2] > 90, inside)
    check('the film dye takes the window\'s magenta: green falls, red and blue lead it',
          took[1] < bare[1] - .08 and took[0] - took[1] > bare[0] - bare[1] + .08, record['window-pickup'])
    quiet('window pickup')

    # 5. Never the goo itself: pickup coasts over an unchanged backdrop leave the dye in place.
    before = [dye(x, y) for x, y in band('dye-a', distances['middle'])] + [dye(*film)]
    for _ in range(3):
        ipc('scottland/goo-state', {'water_coast': 6})
        rest('repeated pickup')
    after = [dye(x, y) for x, y in band('dye-a', distances['middle'])] + [dye(*film)]
    drift = max(abs(x - y) for p, q in zip(before, after) for x, y in zip(p, q))
    check('three pickup coasts over an unchanged backdrop leave the dye where it was', drift < .02, {'drift': drift})

    # 4. Spread and swirl smear the paper: red above, blue below, along a band crossing it.
    stop(paper)
    for t in ('dye-a', 'dye-b', 'dye-magenta'):
        ipc('window-rules/configure-view', {'id': view(t)['id'], 'geometry': {'x': 900 + 20*len(t), 'y': 60, 'width': 200, 'height': 120}})
    paper = wallpaper('#e02020', '#2020e0')
    spawn('dye-smear', 520, 200, 300, 330)
    options(goo_soak=1., goo_dye_density=0., unfocused_edge_strength=0., goo_spread=0., goo_swirl=0.)
    focus('dye-smear'); rest('smear base')
    s = view('dye-smear')['frame']; xs = s['x'] - thickness/2
    ys = list(range(round(s['y']) + 30, round(s['y'] + s['height']) - 30, 4))
    def profile():
        ps = [dye(xs, y) for y in ys]; t = [p[0] - p[2] for p in ps]
        hi, lo = max(t), min(t)
        width = 4*sum(lo + .2*(hi - lo) < v < lo + .8*(hi - lo) for v in t)
        return width, hi - lo, ps
    sharp, contrast, ps0 = profile(); shot('4-smear-still')
    panel, layout = open_panel()
    set_row(panel, 'goo_swirl', .995); set_row(panel, 'goo_spread', .995)
    swirl = wait_option('goo_swirl', lambda v: v > 2.5, 'Dye swirl row'); spread = wait_option('goo_spread', lambda v: v > .8, 'Dye spread row')
    tap('KEY_ENTER'); time.sleep(.5)
    check('the Dye swirl and Dye spread rows set both live, and Save keeps them',
          'goo_swirl = ' in layout.read_text() and 'goo_spread = ' in layout.read_text(), {'swirl': swirl, 'spread': spread})
    rest('smeared', 120); shot('4-smear-swirled')
    smeared, contrast2, ps1 = profile()
    record['smear'] = {'still_width': sharp, 'swirled_width': smeared, 'contrast': [contrast, contrast2], 'still': ps0, 'swirled': ps1}
    check('with no swirl or spread the band changes from red to blue over a short distance', contrast > .3 and sharp <= 24, record['smear'])
    check('swirl and spread smear the picked-up colors along the band', smeared >= sharp + 16 and smeared >= 2*sharp, record['smear'])
    quiet('smeared')
    # 6. Bounded wakes: a window animating under the film (last: its backoff is meant to last).
    ipc('window-rules/configure-view', {'id': view('dye-a')['id'], 'geometry': {'x': 200, 'y': 150, 'width': 360, 'height': 240}})
    time.sleep(.5)
    spawn('dye-animated', 120, 330, 300, 220, animate=True)
    focus('dye-a')
    rest('animated, start', 120)
    a = view('dye-a')['frame']; under_film = (a['x'] + 60, a['y'] + a['height'] + 4)
    check('a film sample lies over the animated window (fixture)', state(*under_film)['density'] > state(*under_film)['threshold'])
    s0 = state(); t0 = time.monotonic(); running = samples = 0
    while time.monotonic() - t0 < 75:
        s = state(); samples += 1; running += bool(s.get('water_running')); time.sleep(.25)
    s1 = state()
    d = {k: s1[k] - s0[k] for k in ('steps', 'pickup_coasts', 'pickup_deferred', 'backdrop_changes', 'backdrop_checks')}
    record['bounded'] = dict(d, coasting_fraction=running/samples, gap=s1['pickup_gap'])
    check('content changing under the film never wakes waves or field', d['steps'] == 0, record['bounded'])
    check('it is picked up: the change is seen and coasts run', d['backdrop_changes'] >= 1 and d['pickup_coasts'] >= 1, record['bounded'])
    check('but few coasts in 75 s, the cool-down doubling', 1 <= d['pickup_coasts'] <= 4 and s1['pickup_gap'] >= 40, record['bounded'])
    check('the dye coasts well under half the time', running/samples < .4, record['bounded'])
    check('checks stay bounded (at most two a second)', d['backdrop_checks'] <= 2*75, record['bounded'])
    (art/'stop').touch()   # the animation stops on green
    rest('animation stopped', 330)
    quiet('after the animation', 8)
    time.sleep(22)         # a still backdrop for over 20 s ends the backoff
    green = dye(*under_film); before = state()
    (art/'next').touch()   # one more change: blue
    t1 = time.monotonic(); started = False
    while time.monotonic() - t1 < 10 and not started:
        started = state()['pickup_coasts'] > before['pickup_coasts']; time.sleep(.1)
    record['reset'] = {'seconds': time.monotonic() - t1, 'gap': state()['pickup_gap']}
    check('after 20 s of stillness one change is picked up at once (backoff over)', started and state()['pickup_gap'] == 20, record['reset'])
    rest('blue picked up', 60); blue = dye(*under_film)
    record['reset'].update(green=green, blue=blue)
    check('and the film dye takes the new blue', blue[2] - blue[1] > green[2] - green[1] + .08, record['reset'])
    quiet('at the end', 6)
finally:
    (art / 'record.json').write_text(json.dumps(record, indent=1))
    for c in clients:
        try: os.killpg(c.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError): pass
    sock.close()
failed = [n for n, ok in results if not ok]
print(f'RESULT {len(results) - len(failed)} passed, {len(failed)} failed ({len(results)} checks)', flush=True)
sys.exit(1 if failed else 0)
