#!/usr/bin/env python3
"""GO28: one dye. Run in a private headless session on a test machine (tests/goo-one-dye-test.sh).

Scenes, each judged by the GPU dye history (goo-state samples) and by screen pixels (grim):

1. Focus colors the whole goo. A window focused and unfocused by real clicks, on plain
   yellow paper: the change reaches the shore of its band, not only the wall, at no pickup,
   at the shipped pickup and at Mike's (full pickup, density 1.5).
2. Mixing, not replacement. The dye at full pickup lies on the subtractive (absorbance)
   line between the pure focus dye and the pure picked-up dye, strictly between them, and
   closer to it than any linear RGB blend of the two. Pickup strength is set through the
   Settings Goo row with real input. The Pickup balance row moves the mix toward either side,
   live; Defaults and Cancel restore it.
3. Pickup from a window. Film over a magenta window takes magenta (green falls), where the
   old wallpaper-only pickup would take the yellow paper (green rises).
4. Spread and swirl smear. On paper half red, half blue, the dye along a band that crosses
   the boundary changes from red to blue over a short distance with swirl and spread at
   zero; set high through the Settings rows (real input, saved), the change is smeared out.
2c. Dye density scales all dye: at pure pickup, density changes how much shows, not the hue.
5. Never the goo itself. Repeated pickup coasts over an unchanged backdrop leave the dye
   where it was: no feedback from the goo's own color.
6. A real client changes once under film and the visible goo picks up its new color.
   A second change arrives after the last dye tick of a coast and must still be picked up.
   Cooldown/reset boundaries are separately checked by goo-pickup-policy-test.cpp.
Every scene ends at rest: no simulation steps, dye passes or watercolor ticks.

Test hooks used: window-rules/configure-view places windows (setup only); some settings are
set by IPC as setup; scene 5 restarts the coast with the water_coast test hook. Scene 6 freezes
coast transport, advances its elapsed time, and resets cooldown as isolated-scene setup to
inject a redraw after the last dye tick;
actual client redraws and screen pixels remain the stimulus and independently judged result.
"""
import configparser, json, math, os, signal, socket, struct, subprocess, sys, time
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
    if animate:
        # Flips green/magenta until art/stop exists, then rests on green; turns blue once
        # art/next exists.
        cmd += ['python3', '-u', '-c', "import os,time\nstop,nxt=%r,%r\ndef bg(c): print('\\033]11;'+c+'\\007',end='',flush=True)\n"
                "while not os.path.exists(stop):\n bg('#d02090'); time.sleep(.1); bg('#20c040'); time.sleep(.1)\n"
                "bg('#20c040')\nwhile not os.path.exists(nxt): time.sleep(.1)\nbg('#2040e0')\ntime.sleep(900)" % (str(art/'stop'), str(art/'next'))]
    elif background:
        # OSC 11 works with both Foot config generations; an obsolete colors section
        # otherwise produces a gray error window instead of the intended fixture.
        cmd += ['python3', '-u', '-c', "import time; print('\\033]11;#' + %r + '\\007', end='', flush=True); time.sleep(900)" % background]
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
    except subprocess.TimeoutExpired:
        try: os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        p.wait()

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
                                               'settings-test', 'snapshot'], text=True, timeout=5))
def origin(q): return ((1280 - q['panel']['width'])/2, 720 - max(24, round(720*.04)) - q['panel']['height'])
def row_y(q, name):
    g = q['goo']; return g['y'] + g['rows'].index(name)*(g['rowHeight'] + 1) + g['rowHeight']/2
def open_panel(ctl=None, initial=None):
    home = art / 'settings-home'; (home / 'scottland').mkdir(parents=True, exist_ok=True)
    if initial is not None: (home / 'scottland/layout.ini').write_text(initial)
    env = dict(os.environ, QS_DISABLE_FILE_WATCHER='1', SCOTTLAND_SETTINGS_TEST='1',
               SCOTTLAND_CTL=str(ctl or repo / 'core/libexec/scottland-ctl'), SCOTTLAND_LAYOUT_FILE=str(home / 'scottland/layout.ini'),
               SCOTTLAND_SOLAR_FILE=str(home / 'scottland/solar.ini'))
    log = open(art / 'panel.log', 'a')
    panel = subprocess.Popen(['qs', '-n', '-p', str(repo / 'core/settings')], env=env, stdout=log, stderr=log, start_new_session=True)
    log.close()
    clients.append(panel)
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

def wait_until(predicate, what, timeout=15):
    end = time.monotonic() + timeout; last = None
    while time.monotonic() < end:
        last = predicate()
        if last: return last
        time.sleep(.05)
    raise AssertionError(what + ': deadline expired; last observation ' + repr(last))

def wait_saved(path, panel, **expected):
    def ready():
        cfg = configparser.ConfigParser()
        try:
            cfg.read(path)
            return panel.poll() is not None and all(abs(cfg.getfloat('scottland', k) - v) < .001 for k, v in expected.items())
        except (configparser.Error, ValueError): return False
    wait_until(ready, 'Save wrote the requested file values')

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
    # Compatibility fixture: actual Settings Save via Enter with an old saved key and
    # a CLI read reporting density unsupported. CLI legacy-live behavior has its own IPC test.
    fixture = art / 'unsupported-density-ctl'
    fixture.write_text('#!/usr/bin/env python3\nimport json,subprocess,sys\nctl=' + repr(str(repo / 'core/libexec/scottland-ctl')) +
        '\nif sys.argv[1:] == ["get"]:\n p=subprocess.run([ctl,"get"],capture_output=True,text=True,check=True)\n d=json.loads(p.stdout)\n d.pop("goo_dye_density",None)\n d["unsupported"].append("goo_dye_density")\n print(json.dumps(d))\nelse:\n subprocess.run([ctl]+sys.argv[1:],check=True)\n')
    fixture.chmod(0o755)
    panel, layout = open_panel(fixture, '[scottland]\n  goo_dye_strength = 1.5\n')
    tap('KEY_ENTER'); wait_saved(layout, panel, goo_dye_density=1.5)
    check('Settings Save preserves legacy saved density before plugin upgrade',
          'goo_dye_density = 1.5' in layout.read_text())
    options(goo_dye_density=1.)
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

    # 2. Mixing, not replacement: pure focus dye F (no pickup), pure pickup P (Pickup balance 1),
    #    and the mix M at the default balance, with pickup set through the panel.
    options(goo_soak=0., goo_dye_density=1.); rest('pure focus')
    focus_screen = shot('2-pure-focus')
    F_px = avg([focus_screen(x, y) for x, y in band('dye-a', distances['middle'])])
    F = avg([dye(x, y) for x, y in band('dye-a', distances['middle'])])
    options(goo_soak=1., goo_pickup_balance=1.); rest('pure pickup')
    pickup_screen = shot('2-pure-pickup')
    P_px = avg([pickup_screen(x, y) for x, y in band('dye-a', distances['middle'])])
    P = avg([dye(x, y) for x, y in band('dye-a', distances['middle'])])
    options(goo_soak=0., goo_pickup_balance=.45); rest('back to no pickup')
    panel, layout = open_panel()
    set_row(panel, 'goo_soak', .995)
    soak = wait_option('goo_soak', lambda v: v > .9, 'the Wallpaper pickup row sets pickup live')
    tap('KEY_ENTER'); wait_saved(layout, panel, goo_soak=soak)
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
    M_px = avg([mixed_screen(x, y) for x, y in band('dye-a', distances['middle'])])
    record['mixing']['screen'] = {'focus': F_px, 'pickup': P_px, 'mixed': M_px}
    check('the rendered mix differs from both visible endpoint dyes',
          norm(sub(M_px, F_px)) > 6 and norm(sub(M_px, P_px)) > 6 and
          all(min(f, p) - 20 <= m <= max(f, p) + 20 for m, f, p in zip(M_px, F_px, P_px)), record['mixing']['screen'])
    quiet('mixed')

    # 2b. Pickup balance (Mike, 2026-10-05: "just give me a slider"), through its Settings row:
    #     lower favors the window's own color, higher the picked-up color, live; Defaults puts
    #     back 0.45 and Cancel the opening values, and the dye follows.
    panel, layout = open_panel()
    set_row(panel, 'goo_pickup_balance', .1)
    low = wait_option('goo_pickup_balance', lambda v: v < .2, 'the Pickup balance row')
    rest('balance low'); low_px = shot('2b-balance-low')
    M_low = avg([dye(x, y) for x, y in band('dye-a', distances['middle'])])
    set_row(panel, 'goo_pickup_balance', .95)
    high = wait_option('goo_pickup_balance', lambda v: v > .9, 'the Pickup balance row')
    rest('balance high'); high_px = shot('2b-balance-high')
    M_high = avg([dye(x, y) for x, y in band('dye-a', distances['middle'])])
    share_low, share_high = fit(K(M_low), K(F), K(P))[0], fit(K(M_high), K(F), K(P))[0]
    pts = band('dye-a', distances['middle'])
    px_low, px_high = avg([low_px(x, y) for x, y in pts]), avg([high_px(x, y) for x, y in pts])
    record['balance'] = {'low': low, 'high': high, 'focus_share': {'low': share_low, 'default': share, 'high': share_high},
                         'screen': {'low': px_low, 'high': px_high}}
    check('a low Pickup balance favors the window\'s own color', share_low > share + .1, record['balance'])
    check('a high Pickup balance favors the picked-up color', share_high < share - .1, record['balance'])
    check('on screen the band changes between them', norm(sub(px_low, px_high)) > 20, record['balance'])
    q = snapshot(panel); x0, y0 = origin(q)
    click(x0 + 86, y0 + q['panel']['height'] - 56); pointer(5, 5)   # Defaults
    check('Defaults puts Pickup balance back at 0.45', abs(wait_option('goo_pickup_balance', lambda v: abs(v - .45) < .001,
          'Defaults') - .45) < .001)
    tap('KEY_ESC')                                  # Cancel: the opening values
    wait_until(lambda: panel.poll() is not None, 'Cancel closed Settings')
    back = wait_option('goo_pickup_balance', lambda v: abs(v - .45) < .001, 'Cancel')
    soak_back = wait_option('goo_soak', lambda v: abs(v - soak) < .001, 'Cancel')
    rest('balance cancelled')
    M_back = avg([dye(x, y) for x, y in band('dye-a', distances['middle'])])
    share_back, residual_back = fit(K(M_back), K(F), K(P))
    restored_screen = shot('2b-balance-cancelled')
    px_back = avg([restored_screen(x, y) for x, y in pts])
    record['balance'].update(back=M_back, mixed=M, back_share=share_back, back_residual=residual_back,
                             restored_screen=px_back, opening_screen=M_px)
    # Restoring a setting preserves evolving dye history. Packed storage can settle
    # on either side of a quantized target; compare against the independently captured
    # opening and edited states rather than assuming bit-identical simulation history.
    check('Cancel restores the opening balance and pickup, and the dye returns to the mix',
          abs(back - .45) < .001 and abs(soak_back - soak) < .001 and residual_back < .12 and
          abs(share_back - share) < min(abs(share_back - share_low), abs(share_back - share_high)), record['balance'])
    check('Cancel visibly returns toward the opening mix rather than either edited balance',
          norm(sub(px_back, M_px)) < min(norm(sub(px_back, px_low)), norm(sub(px_back, px_high))), record['balance'])
    quiet('balance')

    # 2c. Dye density scales all dye, picked-up color included (Mike, 2026-10-05): with only
    #     picked-up color in the goo, density changes how much of it shows, not its hue.
    options(goo_pickup_balance=1., goo_dye_density=0.); rest('pure pickup, no density')
    thin_px = shot('2c-density-0'); thin = avg([dye(x, y) for x, y in pts])
    options(goo_dye_density=1.5); rest('pure pickup, density 1.5')
    dense_px = shot('2c-density-1.5'); dense = avg([dye(x, y) for x, y in pts])
    px0, px1 = avg([thin_px(x, y) for x, y in pts]), avg([dense_px(x, y) for x, y in pts])
    record['density'] = {'dye': [thin, dense], 'screen': [px0, px1]}
    check('density leaves the picked-up hue alone', norm(sub(thin, dense)) < .03, record['density'])
    check('and changes how much of it shows on screen', norm(sub(px0, px1)) > 20, record['density'])
    options(goo_pickup_balance=.45, goo_dye_density=1.); rest('density back')
    quiet('density')

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
    bare_film_px = shot('3-film-no-pickup')(*film)
    options(goo_soak=1.); rest('film, pickup'); s3 = shot('3-film-pickup')
    took = dye(*film)
    took_film_px = s3(*film)
    inside = s3(a['x'] + a['width'] + 60, a['y'] + a['height'] - 20)
    record['window-pickup'] = {'bare': bare, 'took': took, 'window_pixel': inside}
    check('the window under the film is magenta on screen (fixture)', inside[1] < 80 and inside[0] > 150 and inside[2] > 90, inside)
    check('the film dye takes the window\'s magenta: green falls, red and blue lead it',
          took[1] < bare[1] - .08 and took[0] - took[1] > bare[0] - bare[1] + .08, record['window-pickup'])
    record['window-pickup'].update(bare_film_pixel=bare_film_px, pickup_film_pixel=took_film_px)
    check('the visible film gains magenta when it picks up window content',
          took_film_px[0] - took_film_px[1] > bare_film_px[0] - bare_film_px[1] + 4 and
          norm(sub(took_film_px, bare_film_px)) > 6, record['window-pickup'])
    quiet('window pickup')

    # 5. Never the goo itself: pickup coasts over an unchanged backdrop leave the dye in place.
    feedback_points = band('dye-a', distances['middle']) + [film]
    feedback_before = shot('5-before-repeat')
    before_px = [feedback_before(x, y) for x, y in feedback_points]
    before = [dye(x, y) for x, y in feedback_points]
    for _ in range(3):
        ipc('scottland/goo-state', {'water_coast': 6})
        rest('repeated pickup')
    after = [dye(x, y) for x, y in band('dye-a', distances['middle'])] + [dye(*film)]
    drift = max(abs(x - y) for p, q in zip(before, after) for x, y in zip(p, q))
    feedback_after = shot('5-after-repeat')
    after_px = [feedback_after(x, y) for x, y in feedback_points]
    pixel_drift = max(abs(a-b) for p, q in zip(before_px, after_px) for a, b in zip(p, q))
    check('three pickup coasts over an unchanged backdrop leave the dye where it was',
          drift < .02 and pixel_drift <= 8, {'dye_drift': drift, 'pixel_drift': pixel_drift})

    # 4. Spread and swirl smear the paper: red above, blue below, along a band crossing it. At the
    # default Pickup balance (Mike's soak 1, density 1.5): at balance 1 near-instant re-pickup
    # erases most of the smear, which made a 4 pt width pass.
    stop(paper)
    for t in ('dye-a', 'dye-b', 'dye-magenta'):
        ipc('window-rules/configure-view', {'id': view(t)['id'], 'geometry': {'x': 900 + 20*len(t), 'y': 60, 'width': 200, 'height': 120}})
    paper = wallpaper('#e02020', '#2020e0')
    spawn('dye-smear', 520, 200, 300, 330)
    options(goo_soak=1., goo_pickup_balance=.45, goo_dye_density=1.5, unfocused_edge_strength=0., goo_spread=0., goo_swirl=0.)
    focus('dye-smear'); rest('smear base')
    s = view('dye-smear')['frame']; xs = s['x'] - thickness/2
    ys = list(range(round(s['y']) + 30, round(s['y'] + s['height']) - 30, 4))
    def profile():
        ps = [dye(xs, y) for y in ys]; t = [p[0] - p[2] for p in ps]
        hi, lo = max(t), min(t)
        width = 4*sum(lo + .2*(hi - lo) < v < lo + .8*(hi - lo) for v in t)
        return width, hi - lo, ps
    sharp, contrast, ps0 = profile(); still_screen = shot('4-smear-still')
    panel, layout = open_panel()
    set_row(panel, 'goo_swirl', .995); set_row(panel, 'goo_spread', .995)
    swirl = wait_option('goo_swirl', lambda v: v > 2.5, 'Dye swirl row'); spread = wait_option('goo_spread', lambda v: v > .8, 'Dye spread row')
    tap('KEY_ENTER'); wait_saved(layout, panel, goo_swirl=swirl, goo_spread=spread)
    check('the Dye swirl and Dye spread rows set both live, and Save keeps them',
          'goo_swirl = ' in layout.read_text() and 'goo_spread = ' in layout.read_text(), {'swirl': swirl, 'spread': spread})
    rest('smeared', 120); smeared_screen = shot('4-smear-swirled')
    smeared, contrast2, ps1 = profile()
    record['smear'] = {'still_width': sharp, 'swirled_width': smeared, 'contrast': [contrast, contrast2], 'still': ps0, 'swirled': ps1}
    check('with no swirl or spread the band changes from red to blue over a short distance', contrast > .3 and sharp <= 24, record['smear'])
    # 12 pt is three samples of graded color: still dye shows none (0 at the review's runs), the
    # review measured 20 pt swirled on both paths.
    check('swirl and spread smear the picked-up colors along the band', smeared >= max(12, sharp + 8) and smeared >= 2*sharp, record['smear'])
    def screen_profile(image):
        colors = [image(xs, y) for y in ys]
        differences = [c[0] - c[2] for c in colors]
        hi, lo = max(differences), min(differences)
        width = 4 * sum(lo + .2*(hi-lo) < v < lo + .8*(hi-lo) for v in differences)
        return width, hi-lo, colors
    width0, contrast0, pixels0 = screen_profile(still_screen)
    width1, contrast1, pixels1 = screen_profile(smeared_screen)
    record['smear']['screen'] = {'still_width': width0, 'swirled_width': width1, 'contrast': [contrast0, contrast1]}
    check('screen pixels show transport broadening the red/blue boundary',
          contrast0 > 30 and contrast1 > 30 and width1 >= max(12, width0 + 8) and
          max(norm(sub(a, b)) for a, b in zip(pixels0, pixels1)) > 8, record['smear']['screen'])
    quiet('smeared')
    options(goo_pickup_balance=1., goo_dye_density=1.)  # scenario 6 runs as it did before
    # 6. One real client redraw, and another after the last coast transport step.
    ipc('window-rules/configure-view', {'id': view('dye-a')['id'], 'geometry': {'x': 200, 'y': 150, 'width': 360, 'height': 240}})
    color_file = art / 'client-color'
    def client_color(color):
        temporary = art / 'client-color-new'
        temporary.write_text(color); temporary.replace(color_file)
    client_color('#20c040')
    code = ("import time\nfrom pathlib import Path\np=Path(%r)\nlast=''\nwhile True:\n"
            " c=p.read_text().strip()\n if c!=last:\n  print('\\033]11;'+c+'\\007',end='',flush=True);last=c\n time.sleep(.03)" % str(color_file))
    client = subprocess.Popen(['foot', '-c', '/dev/null', '-o', 'resize-by-cells=no', '-T', 'dye-changing',
                               'python3', '-u', '-c', code], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    clients.append(client)
    wait_until(lambda: any(v['title'] == 'dye-changing' for v in views()), 'changing client mapped')
    ipc('window-rules/configure-view', {'id': view('dye-changing')['id'], 'geometry': {'x': 120, 'y': 330, 'width': 300, 'height': 220}})
    focus('dye-a'); rest('green client')
    a = view('dye-a')['frame']; under_film = (a['x'] + 60, a['y'] + a['height'] + 4)
    green_px = shot('6-green')(*under_film); green_dye = dye(*under_film)
    ipc('scottland/goo-state', {'pickup_reset': True})  # isolate from preceding scenarios' cooldown
    before = state()
    client_color('#d02090')
    wait_until(lambda: state()['water_running'], 'natural pickup coast from client redraw')
    # Diagnostic setup: ensure a useful transport tick precedes the injected last frame.
    wait_until(lambda: norm(sub(dye(*under_film), green_dye)) > .08, 'coast advanced before late-frame injection')
    def changed_film():
        pixel = shot('6-magenta')(*under_film)
        return pixel if norm(sub(pixel, green_px)) > 8 else False
    magenta_px = wait_until(changed_film, 'first redraw reached the visible film')
    # Mostly the client beneath showing through: this proves the redraw reached the screen, not
    # pickup. The final-redraw check below judges pickup against an unchanged backdrop.
    check('one actual client redraw reaches the screen under the film (fixture)', norm(sub(magenta_px, green_px)) > 8,
          {'green': green_px, 'magenta': magenta_px})
    # Freeze after useful dye work; the blue redraw is now after the last dye tick.
    ipc('scottland/goo-state', {'water_freeze': True})
    client_color('#2040e0')
    def blue_client():
        pixel = shot('6-client-redraw')(a['x'] + 60, a['y'] + a['height'] + 60)
        return pixel[2] > 150 and pixel[0] < 80
    wait_until(blue_client, 'the actual client committed its last blue frame')
    stale_blue_px = shot('6-late-blue-before-pickup')(*under_film)
    ipc('scottland/goo-state', {'water_elapsed': 1000, 'water_freeze': False})
    rest('late blue picked up', 330)
    blue_px = shot('6-late-blue')(*under_film)
    check('a final redraw after the last dye tick still changes rendered pickup',
          norm(sub(blue_px, stale_blue_px)) > 8 and blue_px[2] - blue_px[0] > stale_blue_px[2] - stale_blue_px[0] + 4,
          {'same_blue_backdrop_before_pickup': stale_blue_px, 'after_pickup': blue_px, 'dye': dye(*under_film)})
    check('content pickup leaves waves and the liquid field asleep', state()['steps'] == before['steps'])
    quiet('at the end')

finally:
    (art / 'record.json').write_text(json.dumps(record, indent=1))
    for c in clients:
        stop(c)
    sock.close()
failed = [n for n, ok in results if not ok]
print(f'RESULT {len(results) - len(failed)} passed, {len(failed)} failed ({len(results)} checks)', flush=True)
sys.exit(1 if failed else 0)
