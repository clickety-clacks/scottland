#!/usr/bin/env python3
"""Measure Dye spread, Dye swirl and Dye release on the current candidate, not a pass/fail test.
Run in a private headless session, on each GPU path. This probe records the candidate SHA and
reports pixel deltas against repeated Mike-value controls; it does not declare product acceptance.

Fixture: Mike's live goo settings (layout.ini, 2026-10-06), a patterned wallpaper (strong color
patches, so pickup gives the dye something to smear), two windows, the left one focused.

The dye is read from the GPU, not from the screen: `goo-state` `dye_points` returns the dye at
every point of a ring through the middle of each window's band, all read in one compositor request
at one simulation step (a "dye capture"). Each capture records the simulation step, dye-flow count
and coast-tick count. Differences are mean absolute 8-bit levels over the ring's RGB (p95 and max
too). Screen pixels along the same rings (grim) are compared the same way.

1. Settled: for each value across each slider's range (the others at Mike's), the same stimulus
   (a real Super-drag of the left window and back, a click-focus to the right one and back),
   then rest; the captured dye is compared with Mike's value. Each trial clears both dye targets
   through the isolated-test hook before the stimulus; repeated Mike runs estimate input noise.
2. Moving: in the same trials, captures while the dragged window is held away (0.3 s, 1 s) and
   screen and dye samples are taken before release, then after the focus change (0.25, 0.6, 1.5 s).
3. Coast: after the same stimulus, restart a five-second sleeping coast and sample dye, screen,
   simulation steps, dye flows and coast ticks through the coast and after rest. Spread values
   include this measurement because spread scaling is the candidate source change.
4. Settings wake: each row set with real input in Scottland Settings while the goo sleeps:
   does it wake (`wakes.settings`), step, and change the dye? Shine at its existing value is the
   no-change control; its exact selected value is checked before comparing wakes.
5. Hints: capture both the actual visible hint text exposed by the Settings test snapshot and a
   screenshot of the hovered row.

Test hooks: window-rules/configure-view places windows (setup); set-config-options sets values
for experiment 1-3 (setup; Settings wake uses real input); goo-state dye_reset clears both dye
targets only in SCOTTLAND_TEST_MODEL sessions, dye_points reads dye, and water_coast restarts
the sleeping coast.
"""
import json, math, os, signal, socket, struct, subprocess, sys, time
from pathlib import Path
import gi
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf

repo = Path(__file__).resolve().parents[1]
art = Path(sys.argv[1]).resolve(); art.mkdir(parents=True, exist_ok=True)
only = sys.argv[2].split(',') if len(sys.argv) > 2 else ['settled', 'wake', 'hints', 'cross']
assert os.environ.get('SCOTTLAND_TEST_MODEL') == '1', 'private headless session required'
sock = socket.socket(socket.AF_UNIX); sock.connect(os.environ['WAYFIRE_SOCKET'])
clients = []; record = {}
record['candidate'] = {
    'head': subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip(),
    'branch': subprocess.check_output(['git', '-C', str(repo), 'branch', '--show-current'], text=True).strip(),
    'purpose': 'current-tip slider pixel measurement; not an acceptance attestation',
}

# Mike's goo settings on the daily machine (its layout.ini), 2026-10-06.
MIKE = dict(goo_thickness=26, goo_reach=35, goo_thinning=.27, goo_swell=.7, goo_noise=.38, goo_lump=315,
            goo_drift=.39, goo_wave_speed=.22, goo_wave_damp=.958, goo_wave_height=.76, goo_spread=.9,
            goo_swirl=2.9, goo_release=.155, goo_shine=.62, goo_relief=3.5, goo_depth=4., goo_profile=.52,
            goo_soak=1., goo_pickup_balance=.47, goo_overlap_film=10., goo_hover_cloudiness=0.,
            goo_hover_emissivity=.46, goo_hover_distance=48., goo_dye_density=1.5)
MIKE_FALLOFF = ('0.000:1.000 0.063:0.779 0.125:0.607 0.188:0.472 0.250:0.368 0.313:0.287 0.375:0.223 '
                '0.395:0.223 0.500:0.135 0.569:0.113 0.625:0.082 0.688:0.064 0.750:0.050 0.813:0.039 '
                '0.875:0.030 0.938:0.024 1.000:0.018')
RANGES = {'goo_spread': [0., .225, .45, .675, .9], 'goo_swirl': [0., .5, .9, 1.8, 2.9, 3.],
          'goo_release': [.005, .03, .06, .155, .3]}

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

def log(*a): print(*a, flush=True)
def options(**values): ipc('wayfire/set-config-options', {'scottland/' + k: v for k, v in values.items()})
def option(name): return float(ipc('wayfire/get-config-option', {'option': 'scottland/' + name})['value'])
def views(): return ipc('scottland/layout-state')['views']
def view(title): return next(v for v in views() if v['title'] == title)
def state(**data): return ipc('scottland/goo-state', data)['screens'][0]
def reset_dye():
    s = state(dye_reset=True)
    if not s.get('dye_reset') or not s.get('renderer_ready'):
        raise AssertionError('isolated-test dye reset was not acknowledged by a ready renderer')
    return s
def pointer(x, y): ipc('stipc/move_cursor', {'x': round(x), 'y': round(y)})
def key(code, down): ipc('stipc/feed_key', {'key': code, 'state': down})
def tap(code): key(code, True); key(code, False)
def button(mode): ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': mode})
def click(x, y):
    pointer(x, y); time.sleep(.1); button('press'); time.sleep(.05); button('release')
def drag(x, y, dx, dy):
    pointer(x, y); time.sleep(.12); button('press')
    for i in range(1, 13): pointer(x + dx*i/12, y + dy*i/12); time.sleep(.025)
    button('release'); time.sleep(.3)
def center(title):
    f = view(title)['frame']; return f['x'] + f['width']/2, f['y'] + f['height']/2
def focus(title):
    click(*center(title)); pointer(5, 5)
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

def ring(frame, d, step=4, margin=18):
    x, y, w, h = frame['x'], frame['y'], frame['width'], frame['height']
    pts = [(x + t, y - d) for t in range(margin, int(w) - margin, step)]
    pts += [(x + w + d, y + t) for t in range(margin, int(h) - margin, step)]
    pts += [(x + t, y + h + d) for t in range(int(w) - margin, margin, -step)]
    pts += [(x - d, y + t) for t in range(int(h) - margin, margin, -step)]
    return pts
def rings(frames):
    d = MIKE['goo_thickness']/2
    return [p for f in frames for p in ring(f, d)]
def capture_sample(points):
    """The dye and counters for every point, read in one main-loop turn (one simulation step)."""
    s = state(x=0, y=0, dye_points=[[float(x), float(y)] for x, y in points])
    if len(s.get('dye_field', [])) != len(points):
        raise AssertionError('batch dye sampler returned an incomplete field')
    meta = {'simulation_step': s.get('dye_field_step'), 'steps': s.get('steps'),
            'dye_flows': s.get('dye_field_flows'), 'water_ticks': s.get('dye_field_water_ticks'),
            'sleeping': s.get('sleeping'), 'water_running': s.get('water_running')}
    return [[255*v for v in c] for c in s['dye_field']], meta
def capture(points):
    return capture_sample(points)[0]
def capture_into(captures, name, points):
    field, meta = capture_sample(points)
    captures[name] = field
    captures.setdefault('capture_meta', {})[name] = meta
def shot(name):
    path = art / (name + '.png'); subprocess.run(['grim', str(path)], check=True)
    pix = GdkPixbuf.Pixbuf.new_from_file(str(path)); data = pix.get_pixels()
    stride, ch = pix.get_rowstride(), pix.get_n_channels()
    return lambda x, y: list(data[round(y)*stride + round(x)*ch:round(y)*stride + round(x)*ch + 3])

def diff(a, b):
    """Mean, 95th percentile and max absolute difference, in 8-bit levels per channel."""
    ds = sorted(abs(x - y) for p, q in zip(a, b) for x, y in zip(p, q))
    if not ds: return {'mean': 0, 'p95': 0, 'max': 0}
    return {'mean': round(sum(ds)/len(ds), 2), 'p95': round(ds[int(.95*(len(ds)-1))], 1), 'max': round(ds[-1], 1)}
def spatial(field):
    """How much the ring's dye varies along itself: mean |neighbor difference| (sharpness)."""
    ds = [abs(x - y) for p, q in zip(field, field[1:]) for x, y in zip(p, q)]
    return round(sum(ds)/max(len(ds), 1), 3)
def spread_of(field):
    """Standard deviation of the ring's dye, mean over channels."""
    out = 0
    for c in range(3):
        v = [p[c] for p in field]; m = sum(v)/len(v); out += math.sqrt(sum((x - m)**2 for x in v)/len(v))
    return round(out/3, 2)

def spawn(title, x, y, w, h):
    cmd = ['foot', '-c', '/dev/null', '-o', 'resize-by-cells=no', '-T', title, 'sleep', '3600']
    clients.append(subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
    for _ in range(100):
        if any(v['title'] == title for v in views()): break
        time.sleep(.05)
    place(title, x, y, w, h)
def place(title, x, y, w, h):
    ipc('window-rules/configure-view', {'id': view(title)['id'], 'geometry': {'x': x, 'y': y, 'width': w, 'height': h}})
    for _ in range(100):
        f = view(title)['frame']
        if all(abs(f[k] - v) < 2 for k, v in (('x', x), ('y', y), ('width', w), ('height', h))): return
        time.sleep(.05)
    raise AssertionError('requested geometry was not applied for ' + title)

def wallpaper():
    changes = state()['wallpaper_node_changes']
    p = subprocess.Popen(['quickshell', '-p', str(repo / 'tests/GooWallpaper.qml')],
                         env=dict(os.environ, GOO_WALLPAPER_PATCHES='1'),
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


# --- the Settings panel, driven with real pointer and keys (as in goo-one-dye-test.py) ---
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
                                               'settings-test', 'snapshot'], text=True, timeout=5))
def origin(q): return ((1280 - q['panel']['width'])/2, 720 - max(24, round(720*.04)) - q['panel']['height'])
def row_y(q, name):
    g = q['goo']; return g['y'] + g['rows'].index(name)*(g['rowHeight'] + 1) + g['rowHeight']/2
def open_panel():
    home = art / 'settings-home'; (home / 'scottland').mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, QS_DISABLE_FILE_WATCHER='1', SCOTTLAND_SETTINGS_TEST='1',
               SCOTTLAND_CTL=str(repo / 'core/libexec/scottland-ctl'), SCOTTLAND_LAYOUT_FILE=str(home / 'scottland/layout.ini'),
               SCOTTLAND_SOLAR_FILE=str(home / 'scottland/solar.ini'))
    out = open(art / 'panel.log', 'a')
    panel = subprocess.Popen(['qs', '-n', '-p', str(repo / 'core/settings')], env=env, stdout=out, stderr=out, start_new_session=True)
    out.close(); clients.append(panel)
    for _ in range(100):
        try:
            q = snapshot(panel)
            if q.get('loaded') and q.get('panel'): break
        except (subprocess.CalledProcessError, RuntimeError):
            pass
        time.sleep(.1)
    wrapped = q['viewport']['y'] > 190; columns = 3 if wrapped else 6
    x, y = origin(q)
    click(x + q['viewport']['x'] + q['viewport']['width']*1.5/columns, y + q['viewport']['y'] - (92 if wrapped else 44))
    for _ in range(50):
        if snapshot(panel).get('goo', {}).get('rows'): break
        time.sleep(.1)
    return panel
def reveal_row(panel, name):
    """Scroll a Settings row fully into view without clicking or changing its value."""
    q = snapshot(panel); vp = q['viewport']; x, top = origin(q)
    if not vp['y'] + q['goo']['rowHeight'] <= row_y(q, name) <= vp['y'] + vp['height'] - q['goo']['rowHeight']:
        content = q['contentHeight']; thumb = vp['height']*vp['height']/content
        target = min(max(0, content - vp['height']), max(0, q['scroll'] + row_y(q, name) - (vp['y'] + vp['height']/2)))
        start = top + vp['y'] + q['scroll']/content*vp['height'] + thumb/2
        end = top + vp['y'] + target/content*vp['height'] + thumb/2
        drag(x + vp['x'] + vp['width'] - 5, start, 0, end - start)
        rest('scrolled')
        q = snapshot(panel); vp = q['viewport']
    if not vp['y'] + q['goo']['rowHeight'] <= row_y(q, name) <= vp['y'] + vp['height'] - q['goo']['rowHeight']:
        raise AssertionError(name + ' row did not enter the visible Settings viewport')
    return q
def set_row(panel, name, fraction):
    """Reveal a row, then press on it at `fraction` of its width."""
    q = reveal_row(panel, name); x, top = origin(q)
    g = q['goo']
    click(x + g['x'] + g['width']*fraction, top + row_y(q, name)); pointer(5, 5)

A = (260, 170, 360, 260); B = (700, 210, 340, 240)

def stimulus(tag, captures):
    """Super-drag A away and hold, captures; drop it back; focus B, captures; focus A; rest."""
    fb = view('win-b')['frame']
    x, y = center('win-a')
    pointer(x, y); time.sleep(.1); key('KEY_LEFTMETA', True); time.sleep(.05); button('press'); time.sleep(.1)
    for i in range(1, 16): pointer(x - 120*i/15, y + 60*i/15); time.sleep(.02)
    t0 = time.monotonic()
    for t in (.3, 1.):
        time.sleep(max(0, t0 + t - time.monotonic()))
        frames = [view('win-a')['frame'], fb]
        pts = rings(frames)
        name = 'held-%.1f' % t
        capture_into(captures, name, pts)
        s = shot(tag + '-held-%.1f' % t)
        captures['screen-held-%.1f' % t] = [s(px, py) for px, py in pts]
    for i in range(1, 16): pointer(x - 120 + 120*i/15, y + 60 - 60*i/15); time.sleep(.02)
    button('release'); time.sleep(.05); key('KEY_LEFTMETA', False)
    place('win-a', *A)   # back exactly where it was (the drop is within a pixel or two)
    focus('win-b'); t0 = time.monotonic()
    for t in (.25, .6, 1.5):
        time.sleep(max(0, t0 + t - time.monotonic()))
        frames = [view('win-a')['frame'], view('win-b')['frame']]
        pts = rings(frames)
        name = 'focus-%.2f' % t
        capture_into(captures, name, pts)
        s = shot(tag + '-focus-%.2f' % t)
        captures['screen-focus-%.2f' % t] = [s(px, py) for px, py in pts]
    focus('win-a')
    rest(tag)
    pts = rings([view('win-a')['frame'], view('win-b')['frame']])
    capture_into(captures, 'settled', pts)
    s = shot(tag + '-settled')
    captures['screen-settled'] = [s(px, py) for px, py in pts]
    return captures

def coast_measure(tag, duration=5.):
    """Sample same-step dye and visible pixels through a sleeping coast and at convergence."""
    pts = rings([view('win-a')['frame'], view('win-b')['frame']])
    initial, initial_meta = capture_sample(pts)
    initial_shot = shot(tag + '-coast-before')
    initial_screen = [initial_shot(px, py) for px, py in pts]
    before = state()
    started = state(water_coast=duration)
    t0 = time.monotonic(); prev_field = initial; prev_screen = initial_screen
    rows = []; snapshots = []
    for target in (.4, 1.2, 2.5, 4.5, duration + .5):
        time.sleep(max(0, t0 + target - time.monotonic()))
        field, meta = capture_sample(pts)
        image = shot(tag + '-coast-%.1f' % target)
        screen = [image(px, py) for px, py in pts]
        rows.append({'elapsed_s': round(time.monotonic() - t0, 3), **meta,
                     'dye_from_start': diff(field, initial), 'dye_from_previous': diff(field, prev_field),
                     'screen_from_start': diff(screen, initial_screen), 'screen_from_previous': diff(screen, prev_screen)})
        snapshots.append((field, screen))
        prev_field, prev_screen = field, screen
    final = rest(tag + ' coast')
    field, meta = capture_sample(pts)
    image = shot(tag + '-coast-final')
    screen = [image(px, py) for px, py in pts]
    rows.append({'elapsed_s': round(time.monotonic() - t0, 3), **meta,
                 'dye_from_start': diff(field, initial), 'dye_from_previous': diff(field, prev_field),
                 'screen_from_start': diff(screen, initial_screen), 'screen_from_previous': diff(screen, prev_screen)})
    snapshots.append((field, screen))
    return {'duration_s': duration, 'start': initial_meta,
            'start_state': {'sleeping': before['sleeping'], 'water_running': before['water_running']},
            'coast_state': {'sleeping': started['sleeping'], 'water_running': started['water_running']},
            'final_state': {'sleeping': final['sleeping'], 'water_running': final['water_running']},
            'samples': rows}, snapshots

def trial(name, value, include_coast=False):
    settings = dict(MIKE); settings[name] = value
    options(**settings); rest('apply ' + name)
    reset_dye(); rest('fresh dye baseline')
    captures = stimulus('%s-%g' % (name, value), {})
    coast, snapshots = coast_measure('%s-%g' % (name, value)) if include_coast else (None, None)
    return captures, coast, snapshots

try:
    ipc('wayfire/set-config-options', {'output:HEADLESS-1/mode': '1280x720@60000'})
    options(**MIKE); ipc('wayfire/set-config-options', {'scottland/goo_falloff': MIKE_FALLOFF})
    spawn('win-a', *A); spawn('win-b', *B)
    paper = wallpaper(); focus('win-a'); rest('fixture')
    s0 = state(); record['path'] = {'packed': s0['packed']}
    log('path:', 'packed RGBA8 (GLES 2)' if s0['packed'] else 'RGBA16F')
    t = time.monotonic(); capture(rings([view('win-a')['frame'], view('win-b')['frame']]))
    record['capture_ms'] = round(1000*(time.monotonic() - t)); log('capture ms', record['capture_ms'])

    if 'settled' in only:
        base = [trial('goo_spread', MIKE['goo_spread'], include_coast=True) for _ in range(3)]
        # Every repeated Mike run starts with both GPU dye targets cleared.
        keys = [k for k in base[0][0] if k != 'capture_meta']
        record['noise'] = {k: [diff(base[0][0][k], base[i][0][k]) for i in (1, 2)] for k in keys}
        log('noise floor', json.dumps({k: v for k, v in record['noise'].items()}))
        record['base_capture_meta'] = base[0][0]['capture_meta']
        record['base_spatial'] = {k: spatial(base[0][0][k]) for k in keys
                                  if k in ('settled', 'focus-0.25', 'held-1.0')}
        record['coast_noise'] = [
            {'sample': i, 'dye': diff(base[0][2][i][0], base[j][2][i][0]),
             'screen': diff(base[0][2][i][1], base[j][2][i][1])}
            for i in range(len(base[0][2])) for j in (1, 2)]
        record['sliders'] = {}
        for name, values in RANGES.items():
            rows = []
            for v in values:
                c, coast, snapshots = trial(name, v, include_coast=(name == 'goo_spread'))
                row = {'value': v, 'vs_mike': {k: diff(c[k], base[0][0][k]) for k in keys},
                       'sharpness': {k: spatial(c[k]) for k in ('settled', 'focus-0.25', 'held-1.0')},
                       'variation': {k: spread_of(c[k]) for k in ('settled', 'focus-0.25', 'held-1.0')},
                       'capture_meta': c['capture_meta']}
                if coast:
                    row['coast'] = coast
                    row['coast_vs_mike'] = [
                        {'sample': i, 'dye': diff(snapshots[i][0], base[0][2][i][0]),
                         'screen': diff(snapshots[i][1], base[0][2][i][1])}
                        for i in range(len(snapshots))]
                rows.append(row)
                log(name, v, json.dumps(row))
            record['sliders'][name] = rows
        options(**MIKE); rest('back to Mike')

    if 'hints' in only:
        # The rows' help, as Settings shows it when the pointer rests on a row.
        panel = open_panel()
        record['help'] = {}
        for name in ('goo_spread', 'goo_swirl', 'goo_release'):
            q = reveal_row(panel, name); x, top = origin(q); g = q['goo']
            pointer(x + g['x'] + g['width']*.3, top + row_y(q, name))
            expected_index = g['rows'].index(name)
            visible = {}
            for _ in range(30):
                time.sleep(.05)
                visible = snapshot(panel)['goo']
                if visible.get('hinted') == expected_index and visible.get('hintText'):
                    break
            if visible.get('hinted') != expected_index or not visible.get('hintText'):
                raise AssertionError('Settings did not expose visible help for ' + name)
            record['help'][name] = {'label': visible.get('hint'), 'text': visible['hintText'],
                                    'screenshot': 'hint-' + name + '.png'}
            shot('hint-' + name)
        pointer(5, 5); tap('KEY_ESC'); time.sleep(.5)
    if 'cross' in only:
        # 4. Is transport hidden by fast renewal? Swirl and spread at both ends of their range,
        # at Mike's release and at a slow one; Mike's swirl/spread run twice for the noise floor.
        record['cross'] = {}
        for release in (.155, .03):
            out = {}
            for label, extra in (('mike', {}), ('mike-again', {}), ('swirl0', {'goo_swirl': 0.}),
                                 ('spread0', {'goo_spread': 0.}), ('both0', {'goo_swirl': 0., 'goo_spread': 0.})):
                settings = dict(MIKE); settings.update(extra); settings['goo_release'] = release
                options(**settings); rest('cross settings')
                reset_dye(); rest('cross fresh dye')
                out[label] = stimulus('cross-%g-%s' % (release, label), {})
            keys = [k for k in out['mike'] if k != 'capture_meta' and not k.startswith('screen')]
            screen_keys = [k for k in out['mike'] if k.startswith('screen')]
            rows = {label: {k: diff(out[label][k], out['mike'][k]) for k in keys + screen_keys}
                    for label in out if label != 'mike'}
            for label in out:
                rows.setdefault(label, {})['sharpness'] = {k: spatial(out[label][k]) for k in ('settled', 'focus-0.25', 'held-1.0')}
                rows[label]['capture_meta'] = out[label]['capture_meta']
            record['cross']['release %g' % release] = rows
            log('cross release', release, json.dumps(rows))
        options(**MIKE); rest('back to Mike')
    if 'wake' in only:
        # Each row set with real input while the goo sleeps; Shine at its existing value is the
        # no-change control. Check that selecting any row preserves its starting value.
        record['wake'] = {}
        LOW_HIGH = {'goo_spread': (0., .9), 'goo_swirl': (0., 3.), 'goo_release': (.005, .3),
                    'goo_shine': (0., 1.5)}
        STEPS = {'goo_spread': .01, 'goo_swirl': .05, 'goo_release': .005, 'goo_shine': .01}
        # (row, Shift+arrow presses); Shine with no arrow is the control.
        for name, presses in (('goo_shine', 0), ('goo_spread', -9), ('goo_swirl', -6), ('goo_release', 3),
                              ('goo_spread', -4), ('goo_swirl', -3), ('goo_release', -2)):
            options(**MIKE); rest('wake reset'); reset_dye(); rest('wake fresh dye')
            pts = rings([view('win-a')['frame'], view('win-b')['frame']])
            f0, meta0 = capture_sample(pts); px0 = shot('wake-%s-%d-before' % (name, presses))
            panel = open_panel(); rest('panel open')
            lo, hi = LOW_HIGH[name]
            target = MIKE[name]
            if target >= hi: target -= STEPS[name] * .25
            elif target <= lo: target += STEPS[name] * .25
            before_select = state()
            set_row(panel, name, (target - lo)/(hi - lo))
            selected_row = snapshot(panel)['goo']
            expected_index = selected_row['rows'].index(name)
            if selected_row.get('selected') != expected_index:
                raise AssertionError('Settings selected row %s instead of %s' %
                                     (selected_row.get('selected'), expected_index))
            selected = option(name); after_select = state()
            if abs(selected - MIKE[name]) > STEPS[name] * .5 + 1e-6:
                raise AssertionError('%s row selection changed the control value: %g to %g' %
                                     (name, MIKE[name], selected))
            selection_wakes = (after_select['wakes'].get('settings', 0)
                               - before_select['wakes'].get('settings', 0))
            before = rest('row selected')
            if presses == 0:
                time.sleep(3); idle = state()
                record['idle_with_panel'] = {'sleeping': idle['sleeping'], 'wakes': idle['wakes'],
                                             'water_ticks': idle['water_ticks'], 'steps': idle['steps']}
                # Sleep/step counters remain diagnostics; the product check is the visible state.
            t0 = time.monotonic(); woke = None; seen = None
            for _ in range(abs(presses)):
                key('KEY_LEFTSHIFT', True); tap('KEY_RIGHT' if presses > 0 else 'KEY_LEFT'); key('KEY_LEFTSHIFT', False)
                time.sleep(.03)
            while presses and time.monotonic() - t0 < 2:
                s1 = state()
                if not s1['sleeping']:
                    woke = round(time.monotonic() - t0, 3); seen = s1; break
                time.sleep(.01)
            value = option(name)
            mid = rest('after ' + name)
            expected = min(hi, max(lo, selected + presses * STEPS[name] * 10))
            expected = math.floor(expected / STEPS[name] + .5) * STEPS[name]
            if abs(value - expected) > 1e-5:
                raise AssertionError('%s key input produced %g, expected %g' % (name, value, expected))
            after_keys = snapshot(panel)['goo']
            if after_keys.get('selected') != expected_index:
                raise AssertionError('Settings focus moved away from %s during key input' % name)
            # Wake/step counters are retained below as diagnostics, not product pass criteria.
            tap('KEY_ENTER')   # Save closes the panel; the value stays
            for _ in range(100):
                if panel.poll() is not None: break
                time.sleep(.05)
            after = rest('panel closed'); f1, meta1 = capture_sample(pts); px1 = shot('wake-%s-%d-after' % (name, presses))
            row = {'value': [selected, value], 'woke_after_s': woke, 'wake_reason': seen and seen['last_wake'],
                   'expected_value': expected, 'selection_wakes': selection_wakes,
                   'capture_meta': {'before': meta0, 'after': meta1},
                   'settings_wakes': [before['wakes'].get('settings', 0), mid['wakes'].get('settings', 0)],
                   'other_wakes': [sum(v for k, v in before['wakes'].items() if k != 'settings'),
                                   sum(v for k, v in mid['wakes'].items() if k != 'settings')],
                   'steps': mid['steps'] - before['steps'], 'dye_flows': mid['dye_flows'] - before['dye_flows'],
                   'dye': diff(f0, f1), 'screen': diff([px0(*p) for p in pts], [px1(*p) for p in pts])}
            record['wake']['%s %+d: %g->%g' % (name, presses, selected, value)] = row
            log('wake', name, presses, json.dumps(row))
            if panel in clients: stop(panel); clients.remove(panel)
        options(**MIKE); rest('back to Mike')
    (art / 'probe.json').write_text(json.dumps(record, indent=1))
finally:
    (art / 'probe.json').write_text(json.dumps(record, indent=1))
    for c in clients: stop(c)
