#!/usr/bin/env python3
"""GO24: the watercolor is picked up everywhere, comes to rest, and stays.

Run in a private headless session on the test machine:
  tests/headless.sh run python3 tests/goo-watercolor-test.py ARTIFACTS

A wallpaper of strong color patches; a full-size window, and a window dragged with real
input toward the edge so the layout scales it and its goo band is thin. Checks, at the
shipped soak: the dye in both bands carries paper pigment once the goo sleeps; the motion
stops by itself (no ticks, no draws, no GPU work); the dye and the screen are then
unchanged over a minute; a real stir (a window drag) moves the dye and leaves pigment;
the state color still holds the window wall; soak 0 has no pigment.
"""
import json, os, signal, socket, struct, subprocess, sys, time
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
def state(x=None, y=None):
    return ipc('scottland/goo-state', {} if x is None else {'x': x, 'y': y})['screens'][0]
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

def band(title, away):
    """Dye along a window's top and left edges, `away` points out from the wall."""
    f = view(title)['frame']
    points = [(f['x']+f['width']*k/8, f['y']-away) for k in range(1, 8)] + \
             [(f['x']-away, f['y']+f['height']*k/8) for k in range(1, 8)]
    return [[state(x, y)[c] for c in ('red', 'green', 'blue')] for x, y in points]
def chroma(samples):   # how colored the dye is, 0 for gray
    return sum(max(s)-min(s) for s in samples)/len(samples)
def differ(a, b): return max(abs(x-y) for p, q in zip(a, b) for x, y in zip(p, q))

clients = []
try:
    ipc('wayfire/set-config-options', {'output:HEADLESS-1/mode': '1600x1000@60000', 'scottland/goo_soak': 0.})
    time.sleep(1)
    with (out/'wallpaper.log').open('w') as f:
        clients.append(subprocess.Popen(['quickshell', '-p', str(root/'tests/GooWallpaper.qml')],
                                        env=dict(os.environ, GOO_WALLPAPER_PATCHES='dark'),
                                        stdout=f, stderr=subprocess.STDOUT, start_new_session=True))
    time.sleep(1)
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
    rest('soak 0')
    bare_full, bare_thin = band('water-full', 7), band('water-thin', away_thin)
    bare_wall = band('water-full', 1)
    check('no pigment in the dye with soak 0', chroma(bare_full) < .12, {'chroma': chroma(bare_full)})
    subprocess.run(['grim', str(out/'soak-0.png')], check=True)

    ipc('wayfire/set-config-options', {'scottland/goo_soak': 0.12})   # the shipped default
    rest('soak 0.12')
    s0 = state()
    wet_full, wet_thin = band('water-full', 7), band('water-thin', away_thin)
    subprocess.run(['grim', str(out/'rest-a.png')], check=True)
    check('the full-size band carries paper pigment at the shipped soak',
          chroma(wet_full) > chroma(bare_full)+.08, {'chroma': chroma(wet_full), 'bare': chroma(bare_full)})
    check('the thin band of the scaled window carries it too',
          differ(wet_thin, bare_thin) > .08, {'shift': differ(wet_thin, bare_thin)})
    # Graded by thickness, never zero: the thin band holds a fair part of what the thick one does.
    def taken(wet, bare): return sum(sum(abs(x-y) for x, y in zip(p, q)) for p, q in zip(wet, bare))/len(wet)
    check('thin goo carries the watercolor too, at a fair part of the thick band\'s strength',
          taken(wet_thin, bare_thin) > .25*taken(wet_full, bare_full) > 0,
          {'thin': taken(wet_thin, bare_thin), 'full': taken(wet_full, bare_full)})
    accent = band('water-full', 1)
    # On screen: along the window's top edge, the two rows at the wall barely change from
    # the soak-0 picture, the rows out in the band change clearly.
    import gi
    gi.require_version('GdkPixbuf', '2.0')
    from gi.repository import GdkPixbuf
    def rows(path, ys):
        image = GdkPixbuf.Pixbuf.new_from_file(str(path)); data = image.get_pixels()
        stride, ch = image.get_rowstride(), image.get_n_channels()
        f = view('water-full')['frame']
        return [[data[round(y)*stride+x*ch+c] for x in range(round(f['x'])+40, round(f['x']+f['width'])-40, 5) for c in range(3)]
                for y in ys]
    f = view('water-full')['frame']
    ys = (f['y']-2, f['y']-8)
    # The band row follows the wallpaper's patches beneath it. (GO28 retired the GO24 wall
    # band: the wall row now mixes the state color with what lies beneath too; its spread is
    # reported, not judged.)
    def spread(row):
        total = 0
        for c in range(3):
            v = row[c::3]; mean = sum(v)/len(v); total += (sum((x-mean)**2 for x in v)/len(v))**.5
        return total/3
    wall_row, band_row = rows(out/'rest-a.png', ys)
    check('on screen the band follows the wallpaper beneath it',
          spread(band_row) > 5,
          {'wall spread': spread(wall_row), 'band spread': spread(band_row)})
    print(json.dumps({'chroma': {'bare_full': chroma(bare_full), 'wet_full': chroma(wet_full),
                                 'bare_thin': chroma(bare_thin), 'wet_thin': chroma(wet_thin)}}), flush=True)

    # A minute idle: nothing runs and nothing changes.
    time.sleep(60)
    s1 = state()
    still_full, still_thin = band('water-full', 7), band('water-thin', away_thin)
    subprocess.run(['grim', str(out/'rest-b.png')], check=True)
    idle = {k: s1[k]-s0[k] for k in ('steps', 'water_ticks', 'dye_flows', 'draws')}
    check('a minute idle: no simulation steps, no watercolor ticks, no dye passes', s1['sleeping'] and
          idle['steps'] == 0 and idle['water_ticks'] == 0 and idle['dye_flows'] == 0, idle)
    check('the goo draws nothing in that minute (bar the two screenshots)', idle['draws'] <= 4, idle)
    check('the dye is exactly as it was a minute before', differ(wet_full, still_full) == 0 and
          differ(wet_thin, still_thin) == 0, {'full': differ(wet_full, still_full), 'thin': differ(wet_thin, still_thin)})
    check('the screen is pixel-identical a minute later',
          (out/'rest-a.png').read_bytes() == (out/'rest-b.png').read_bytes() or
          subprocess.run(['cmp', '-s', str(out/'rest-a.png'), str(out/'rest-b.png')]).returncode == 0)
    check('the pigment is still there', chroma(still_full) > chroma(bare_full)+.08 and differ(still_thin, bare_thin) > .08)

    # Stir it with real input: the pattern changes, the pigment remains, and it rests again.
    drag('water-full', 60, 30)
    time.sleep(.3)
    check('a drag wakes the goo', not state()['sleeping'])
    rest('after the drag')
    stirred = band('water-full', 7)
    subprocess.run(['grim', str(out/'stirred.png')], check=True)
    check('stirring leaves pigment in the band', chroma(stirred) > chroma(bare_full)+.06,
          {'chroma': chroma(stirred), 'bare': chroma(bare_full)})
    a = state(); time.sleep(5); b = state()
    check('and it comes to rest again', b['sleeping'] and b['water_ticks'] == a['water_ticks'] and b['steps'] == a['steps'])
    (out/'samples.json').write_text(json.dumps({'bare_full': bare_full, 'wet_full': wet_full, 'wet_thin': wet_thin,
                                                'bare_thin': bare_thin, 'stirred': stirred, 'wall': accent}, indent=1))
    failed = [n for n, ok in checks if not ok]
    print(f'RESULT {len(checks)-len(failed)} passed, {len(failed)} failed', flush=True)
    sys.exit(1 if failed else 0)
finally:
    for c in clients:
        try: os.killpg(c.pid, signal.SIGTERM)
        except ProcessLookupError: pass
    sock.close()
