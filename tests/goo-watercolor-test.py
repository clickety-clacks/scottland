#!/usr/bin/env python3
"""GO28: one dye picks up wallpaper, rests, persists and stirs.

Run in a private headless session on the test machine:
  tests/headless.sh run python3 tests/goo-watercolor-test.py ARTIFACTS

A wallpaper of strong color patches; a full-size window, and a window dragged with real
input toward the edge so the layout scales it and its goo band is thin. Same-scene soak-zero
controls distinguish paper pickup from state pigment. At the shipped soak, dye and rendered
pixels must shift toward the wallpaper color in both bands, remain unchanged after a minute,
and still carry that paper-directed shift after a real drag away and back. This is measurement
against a matched control, not a chroma threshold or product acceptance attestation.
"""
import json, math, os, signal, socket, struct, subprocess, sys, time
from pathlib import Path

root = Path(__file__).resolve().parents[1]
out = Path(sys.argv[1]).resolve(); out.mkdir(parents=True, exist_ok=True)
assert os.environ.get('SCOTTLAND_TEST_MODEL') == '1', 'private headless session required'
sock = socket.socket(socket.AF_UNIX); sock.connect(os.environ['WAYFIRE_SOCKET'])

def ipc(method, data=None):
    payload = json.dumps({'method': method, 'data': data or {}}).encode()
    sock.sendall(struct.pack('<I', len(payload)) + payload)
    def read(n):
        b = b''
        while len(b) < n:
            chunk = sock.recv(n-len(b))
            if not chunk: raise RuntimeError('compositor disconnected')
            b += chunk
        return b
    result = json.loads(read(struct.unpack('<I', read(4))[0]))
    if isinstance(result, dict) and 'error' in result: raise RuntimeError(result)
    return result
def view(title): return next(v for v in ipc('scottland/layout-state')['views'] if v['title'] == title)
def state(x=None, y=None, **data):
    if x is not None: data.update(x=x, y=y)
    return ipc('scottland/goo-state', data)['screens'][0]
def pointer(x, y): ipc('stipc/move_cursor', {'x': round(x), 'y': round(y)})
def button(mode): ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': mode})
def key(down): ipc('stipc/feed_key', {'key': 'KEY_LEFTMETA', 'state': down})
def drag(title, dx, dy):
    f = view(title)['frame']; cx, cy = f['x']+f['width']/2, f['y']+f['height']/2
    pointer(cx, cy); key(True); button('press')
    for i in range(1, 21): pointer(cx+dx*i/20, cy+dy*i/20); time.sleep(.02)
    button('release'); key(False)
def rest(what):
    """Asleep, strips settled, and the watercolor come to a stop."""
    time.sleep(1)
    for _ in range(900):
        s = state()
        if s['sleeping'] and not s.get('breath_loose') and not s.get('water_running'): return
        time.sleep(.1)
    raise AssertionError('goo did not come to rest: ' + what + ' ' + json.dumps(
        {k: state().get(k) for k in ('sleeping', 'breath_loose', 'water_running', 'wave_energy', 'last_wake')}))

checks = []
def check(name, ok, detail=None):
    checks.append((name, bool(ok)))
    print(('PASS ' if ok else 'FAIL ') + name + ('' if ok or detail is None else ' ' + json.dumps(detail)), flush=True)

def band_points(title, away):
    """Dye sample positions along a window's top and left edges."""
    f = view(title)['frame']
    return ([(f['x']+f['width']*k/8, f['y']-away) for k in range(1, 8)] +
            [(f['x']-away, f['y']+f['height']*k/8) for k in range(1, 8)])
def dye_at(points):
    """Batch-read dye and its counters in one main-loop turn."""
    s = state(dye_points=[[float(x), float(y)] for x, y in points])
    if len(s.get('dye_field', [])) != len(points):
        raise AssertionError('batched dye sampler returned an incomplete band')
    meta = {'simulation_step': s.get('dye_field_step'), 'dye_flows': s.get('dye_field_flows'),
            'water_ticks': s.get('dye_field_water_ticks')}
    return s['dye_field'], meta
def differ(a, b): return max(abs(x-y) for p, q in zip(a, b) for x, y in zip(p, q))
def pixels(path, points):
    import gi
    gi.require_version('GdkPixbuf', '2.0')
    from gi.repository import GdkPixbuf
    image = GdkPixbuf.Pixbuf.new_from_file(str(path)); data = image.get_pixels()
    stride, channels = image.get_rowstride(), image.get_n_channels()
    width, height = image.get_width(), image.get_height()
    out = []
    for x, y in points:
        x, y = round(x), round(y)
        if not (0 <= x < width and 0 <= y < height):
            raise AssertionError('sample point lies outside screenshot: %r' % ((x, y),))
        start = y*stride + x*channels
        out.append(list(data[start:start+3]))
    return out
def wait_for_paper(path, deadline=10):
    points = [(30, 500), (550, 500), (1050, 500), (1570, 500)]
    end = time.monotonic() + deadline
    last = []
    while time.monotonic() < end:
        subprocess.run(['grim', str(path)], check=True)
        last = pixels(path, points)
        if max(abs(x-y) for p in last for q in last for x, y in zip(p, q)) > 24: return
        time.sleep(.2)
    raise AssertionError('patterned wallpaper did not become visible: ' + json.dumps(last))
def toward_paper(before, after, paper):
    """Project each measured change onto its same-position paper-color direction."""
    if not (len(before) == len(after) == len(paper)):
        raise AssertionError('paired scene samples have different lengths')
    dots = []; norms = []; magnitudes = []; shifts = []; targets = []
    for b, a, p in zip(before, after, paper):
        d = [x-y for x, y in zip(a, b)]
        t = [x-y for x, y in zip(p, b)]
        norm = sum(x*x for x in t)
        if norm <= 1e-9: continue
        dots.append(sum(x*y for x, y in zip(d, t)))
        norms.append(norm)
        magnitudes.append(sum(abs(x) for x in d)/3)
        shifts.append(d); targets.append(t)
    if not norms:
        raise AssertionError('paper control has no color direction at sampled points')
    # Remove each channel's spatial mean. A uniform RGB wash then has no spatial
    # variation to correlate with the local paper pattern.
    mean_d = [sum(sample[c] for sample in shifts)/len(shifts) for c in range(3)]
    mean_t = [sum(sample[c] for sample in targets)/len(targets) for c in range(3)]
    centered_d = [sample[c]-mean_d[c] for sample in shifts for c in range(3)]
    centered_t = [sample[c]-mean_t[c] for sample in targets for c in range(3)]
    d_energy = sum(v*v for v in centered_d); t_energy = sum(v*v for v in centered_t)
    pattern = (sum(x*y for x, y in zip(centered_d, centered_t))/math.sqrt(d_energy*t_energy)
               if d_energy > 1e-9 and t_energy > 1e-9 else 0.)
    return {'toward_fraction': round(sum(dots)/sum(norms), 6),
            'projected_shift': round(sum(d/math.sqrt(n) for d, n in zip(dots, norms))/len(norms), 6),
            'toward_points': sum(d > 0 for d in dots), 'sampled_points': len(norms),
            'mean_abs_shift': round(sum(magnitudes)/len(magnitudes), 6),
            'max_abs_shift': differ(before, after), 'local_pattern_correlation': round(pattern, 6)}

clients = []
try:
    ipc('wayfire/set-config-options', {'output:HEADLESS-1/mode': '1600x1000@60000', 'scottland/goo_soak': 0.})
    time.sleep(1)
    with (out/'wallpaper.log').open('w') as f:
        clients.append(subprocess.Popen(['quickshell', '-p', str(root/'tests/GooWallpaper.qml')],
                                        env=dict(os.environ, GOO_WALLPAPER_PATCHES='dark'),
                                        stdout=f, stderr=subprocess.STDOUT, start_new_session=True))
    wait_for_paper(out/'paper.png')
    for title in ('water-full', 'water-thin'):
        clients.append(subprocess.Popen(['foot', '-c', '/dev/null', '-o', 'resize-by-cells=no', '-T', title, 'sleep', '900'],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
        time.sleep(.5)
    time.sleep(1.5)
    ipc('window-rules/configure-view', {'id': view('water-full')['id'], 'geometry': {'x': 520, 'y': 250, 'width': 620, 'height': 460}})
    ipc('window-rules/configure-view', {'id': view('water-thin')['id'], 'geometry': {'x': 150, 'y': 120, 'width': 420, 'height': 320}})
    time.sleep(.5)
    drag('water-thin', -260, 60)   # real input: toward the left edge, where the layout scales it
    f = view('water-full')['frame']; pointer(f['x']+f['width']/2, f['y']+f['height']/2); button('press'); button('release')
    time.sleep(.5)
    thin = view('water-thin')
    check('the edge window is scaled, so its goo band is thin', thin.get('scale', 1) < .6, {'scale': thin.get('scale')})
    away_thin = 3.
    pointer(5, 5)
    rest('soak 0')
    full_points = band_points('water-full', 7)
    thin_points = band_points('water-thin', away_thin)
    full_frame = dict(view('water-full')['frame'])
    thin_frame = dict(view('water-thin')['frame'])
    bare_full, bare_full_meta = dye_at(full_points)
    bare_thin, bare_thin_meta = dye_at(thin_points)
    subprocess.run(['grim', str(out/'soak-0.png')], check=True)
    bare_full_px, bare_thin_px = pixels(out/'soak-0.png', full_points), pixels(out/'soak-0.png', thin_points)
    paper_full_px, paper_thin_px = pixels(out/'paper.png', full_points), pixels(out/'paper.png', thin_points)
    check('paper controls sample distinct local colors',
          len(set(map(tuple, paper_full_px + paper_thin_px))) > 1,
          {'full': paper_full_px, 'thin': paper_thin_px})
    paper_full_dye = [[c/255 for c in p] for p in paper_full_px]
    paper_thin_dye = [[c/255 for c in p] for p in paper_thin_px]

    # The repeated soak-zero scene estimates readback and screenshot noise without changing
    # geometry, wallpaper, focus, or pointer position.
    bare_full_repeat, bare_full_repeat_meta = dye_at(full_points)
    bare_thin_repeat, bare_thin_repeat_meta = dye_at(thin_points)
    subprocess.run(['grim', str(out/'soak-0-repeat.png')], check=True)
    bare_full_repeat_px = pixels(out/'soak-0-repeat.png', full_points)
    bare_thin_repeat_px = pixels(out/'soak-0-repeat.png', thin_points)
    noise = {'full_dye': differ(bare_full, bare_full_repeat),
             'thin_dye': differ(bare_thin, bare_thin_repeat),
             'full_pixels': differ(bare_full_px, bare_full_repeat_px),
             'thin_pixels': differ(bare_thin_px, bare_thin_repeat_px)}

    ipc('wayfire/set-config-options', {'scottland/goo_soak': 0.12})   # the shipped default
    rest('soak 0.12')
    wet_full_frame, wet_thin_frame = view('water-full')['frame'], view('water-thin')['frame']
    wet_geometry_delta = {
        'full': {k: abs(wet_full_frame[k]-full_frame[k]) for k in ('x', 'y', 'width', 'height')},
        'thin': {k: abs(wet_thin_frame[k]-thin_frame[k]) for k in ('x', 'y', 'width', 'height')}}
    check('soak-zero and shipped-soak samples use the same window scene',
          max(max(v.values()) for v in wet_geometry_delta.values()) <= 1., wet_geometry_delta)
    s0 = state()
    wet_full, wet_full_meta = dye_at(full_points)
    wet_thin, wet_thin_meta = dye_at(thin_points)
    subprocess.run(['grim', str(out/'rest-a.png')], check=True)
    wet_full_px, wet_thin_px = pixels(out/'rest-a.png', full_points), pixels(out/'rest-a.png', thin_points)
    full_dye_signal = toward_paper(bare_full, wet_full, paper_full_dye)
    thin_dye_signal = toward_paper(bare_thin, wet_thin, paper_thin_dye)
    full_pixel_signal = toward_paper(bare_full_px, wet_full_px, paper_full_px)
    thin_pixel_signal = toward_paper(bare_thin_px, wet_thin_px, paper_thin_px)
    def measurable(signal, noise_floor):
        return (signal['toward_fraction'] > 0 and
                signal['toward_points'] >= (signal['sampled_points'] + 1)//2 and
                signal['projected_shift'] > math.sqrt(3)*noise_floor)
    check('full-band dye shifts toward paper at the shipped soak',
          measurable(full_dye_signal, noise['full_dye']),
          {'signal': full_dye_signal, 'noise_max': noise['full_dye']})
    check('thin-band dye shifts toward paper at the shipped soak',
          measurable(thin_dye_signal, noise['thin_dye']),
          {'signal': thin_dye_signal, 'noise_max': noise['thin_dye']})
    check('full-band screen pixels shift toward paper at the shipped soak',
          measurable(full_pixel_signal, noise['full_pixels']),
          {'signal': full_pixel_signal, 'noise_max': noise['full_pixels']})
    check('thin-band screen pixels shift toward paper at the shipped soak',
          measurable(thin_pixel_signal, noise['thin_pixels']),
          {'signal': thin_pixel_signal, 'noise_max': noise['thin_pixels']})
    check('thin-band paper-directed dye shift remains a fair part of full band',
          thin_dye_signal['projected_shift'] > .25*full_dye_signal['projected_shift'] > 0,
          {'thin': thin_dye_signal['projected_shift'], 'full': full_dye_signal['projected_shift']})
    check('dye shift co-varies positively with local paper pattern in both bands',
          full_dye_signal['local_pattern_correlation'] > 0 and
          thin_dye_signal['local_pattern_correlation'] > 0,
          {'full': full_dye_signal['local_pattern_correlation'],
           'thin': thin_dye_signal['local_pattern_correlation']})
    check('screen pixel shift co-varies positively with local paper pattern in both bands',
          full_pixel_signal['local_pattern_correlation'] > 0 and
          thin_pixel_signal['local_pattern_correlation'] > 0,
          {'full': full_pixel_signal['local_pattern_correlation'],
           'thin': thin_pixel_signal['local_pattern_correlation']})

    def rows(path, ys):
        import gi
        gi.require_version('GdkPixbuf', '2.0')
        from gi.repository import GdkPixbuf
        image = GdkPixbuf.Pixbuf.new_from_file(str(path)); data = image.get_pixels()
        stride, ch = image.get_rowstride(), image.get_n_channels()
        f = view('water-full')['frame']
        return [[data[round(y)*stride+x*ch+c] for x in range(round(f['x'])+40, round(f['x']+f['width'])-40, 5) for c in range(3)]
                for y in ys]
    f = view('water-full')['frame']
    ys = (f['y']-2, f['y']-8)
    # GO28's same-scene point pairs measure local color pickup. Row spread is retained as
    # a descriptive diagnostic; the watercolor mixes state color with the wallpaper beneath.
    def spread(row):
        total = 0
        for c in range(3):
            v = row[c::3]; mean = sum(v)/len(v); total += (sum((x-mean)**2 for x in v)/len(v))**.5
        return total/3
    wall_row, band_row = rows(out/'rest-a.png', ys)
    check('screen-band pixels vary along the window edge',
          spread(band_row) > 5,
          {'wall spread': spread(wall_row), 'band spread': spread(band_row)})
    print(json.dumps({'path': {'packed': s0['packed']}, 'noise': noise,
                      'paper_shift': {'full_dye': full_dye_signal, 'thin_dye': thin_dye_signal,
                                      'full_pixels': full_pixel_signal, 'thin_pixels': thin_pixel_signal},
                      'capture_meta': {'bare_full': bare_full_meta, 'bare_thin': bare_thin_meta,
                                       'bare_full_repeat': bare_full_repeat_meta,
                                       'bare_thin_repeat': bare_thin_repeat_meta, 'wet_full': wet_full_meta,
                                       'wet_thin': wet_thin_meta}}), flush=True)

    # A minute idle: nothing runs and nothing changes.
    time.sleep(60)
    s1 = state()
    still_full, still_full_meta = dye_at(full_points)
    still_thin, still_thin_meta = dye_at(thin_points)
    subprocess.run(['grim', str(out/'rest-b.png')], check=True)
    still_full_px, still_thin_px = pixels(out/'rest-b.png', full_points), pixels(out/'rest-b.png', thin_points)
    idle = {k: s1[k]-s0[k] for k in ('steps', 'water_ticks', 'dye_flows', 'draws')}
    check('a minute idle: no simulation steps, no watercolor ticks, no dye passes', s1['sleeping'] and
          idle['steps'] == 0 and idle['water_ticks'] == 0 and idle['dye_flows'] == 0, idle)
    check('the goo draws nothing in that minute (bar the two screenshots)', idle['draws'] <= 4, idle)
    check('the dye is exactly as it was a minute before', differ(wet_full, still_full) == 0 and
          differ(wet_thin, still_thin) == 0, {'full': differ(wet_full, still_full), 'thin': differ(wet_thin, still_thin)})
    check('the screen is pixel-identical a minute later',
          (out/'rest-a.png').read_bytes() == (out/'rest-b.png').read_bytes() or
          subprocess.run(['cmp', '-s', str(out/'rest-a.png'), str(out/'rest-b.png')]).returncode == 0)
    still_full_signal = toward_paper(bare_full, still_full, paper_full_dye)
    still_thin_signal = toward_paper(bare_thin, still_thin, paper_thin_dye)
    still_full_pixel_signal = toward_paper(bare_full_px, still_full_px, paper_full_px)
    still_thin_pixel_signal = toward_paper(bare_thin_px, still_thin_px, paper_thin_px)
    check('paper-directed dye shift persists for one minute in both bands',
          measurable(still_full_signal, noise['full_dye']) and measurable(still_thin_signal, noise['thin_dye']),
          {'full': still_full_signal, 'thin': still_thin_signal})
    check('paper-directed pixel shift persists for one minute in both bands',
          measurable(still_full_pixel_signal, noise['full_pixels']) and
          measurable(still_thin_pixel_signal, noise['thin_pixels']),
          {'full': still_full_pixel_signal, 'thin': still_thin_pixel_signal})

    # A real drag away and back stirs the field but returns the windows to the soak-zero scene.
    drag('water-full', 60, 30)
    time.sleep(.3)
    check('a drag wakes the goo', not state()['sleeping'])
    drag('water-full', -60, -30)
    pointer(5, 5)
    rest('after the drag and return')
    after_stir = view('water-full')['frame']
    frame_delta = {k: abs(after_stir[k]-full_frame[k]) for k in ('x', 'y', 'width', 'height')}
    print('DIAGNOSTIC real-drag frame delta ' + json.dumps(frame_delta), flush=True)
    stirred, stirred_meta = dye_at(full_points)
    subprocess.run(['grim', str(out/'stirred.png')], check=True)
    stirred_px = pixels(out/'stirred.png', full_points)
    stirred_signal = toward_paper(bare_full, stirred, paper_full_dye)
    stirred_pixel_signal = toward_paper(bare_full_px, stirred_px, paper_full_px)
    check('real drag redistributes dye at the returned scene', differ(stirred, wet_full) > noise['full_dye'],
          {'after_vs_before': differ(stirred, wet_full), 'noise_max': noise['full_dye'], 'capture': stirred_meta})
    check('real drag redistributes rendered pixels at the returned scene',
          differ(stirred_px, wet_full_px) > noise['full_pixels'],
          {'after_vs_before': differ(stirred_px, wet_full_px), 'noise_max': noise['full_pixels']})
    check('stirred dye retains paper-directed color against the same-scene soak-zero control',
          measurable(stirred_signal, noise['full_dye']), stirred_signal)
    check('stirred pixels retain paper-directed color against the same-scene soak-zero control',
          measurable(stirred_pixel_signal, noise['full_pixels']), stirred_pixel_signal)
    a = state(); time.sleep(5); b = state()
    check('and it comes to rest again', b['sleeping'] and b['water_ticks'] == a['water_ticks'] and b['steps'] == a['steps'])
    (out/'samples.json').write_text(json.dumps({
        'candidate': {'head': subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip(),
                      'branch': subprocess.check_output(['git', '-C', str(root), 'branch', '--show-current'], text=True).strip()},
        'path': {'packed': s0['packed']}, 'points': {'full': full_points, 'thin': thin_points},
        'paper': {'full': paper_full_px, 'thin': paper_thin_px},
        'dye': {'bare_full': bare_full, 'bare_thin': bare_thin,
                'bare_full_repeat': bare_full_repeat, 'bare_thin_repeat': bare_thin_repeat,
                'wet_full': wet_full, 'wet_thin': wet_thin, 'still_full': still_full,
                'still_thin': still_thin, 'stirred': stirred},
        'pixels': {'bare_full': bare_full_px, 'bare_thin': bare_thin_px,
                   'bare_full_repeat': bare_full_repeat_px, 'bare_thin_repeat': bare_thin_repeat_px,
                   'wet_full': wet_full_px, 'wet_thin': wet_thin_px, 'still_full': still_full_px,
                   'still_thin': still_thin_px, 'stirred': stirred_px},
        'noise': noise, 'paper_shift': {'wet_full': full_dye_signal, 'wet_thin': thin_dye_signal,
                                        'wet_full_pixels': full_pixel_signal, 'wet_thin_pixels': thin_pixel_signal,
                                        'still_full': still_full_signal, 'still_thin': still_thin_signal,
                                        'still_full_pixels': still_full_pixel_signal,
                                        'still_thin_pixels': still_thin_pixel_signal,
                                        'stirred': stirred_signal, 'stirred_pixels': stirred_pixel_signal},
        'capture_meta': {'bare_full': bare_full_meta, 'bare_thin': bare_thin_meta,
                         'bare_full_repeat': bare_full_repeat_meta,
                         'bare_thin_repeat': bare_thin_repeat_meta, 'wet_full': wet_full_meta,
                         'wet_thin': wet_thin_meta, 'still_full': still_full_meta,
                         'still_thin': still_thin_meta, 'stirred': stirred_meta},
        'soak_geometry_delta': wet_geometry_delta, 'frame_delta_after_stir': frame_delta}, indent=1))
    failed = [n for n, ok in checks if not ok]
    print(f'RESULT {len(checks)-len(failed)} passed, {len(failed)} failed', flush=True)
    sys.exit(1 if failed else 0)
finally:
    for c in clients:
        try: os.killpg(c.pid, signal.SIGTERM)
        except ProcessLookupError: pass
    sock.close()
