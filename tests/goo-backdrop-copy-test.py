#!/usr/bin/env python3
"""The goo shows the real screen beneath it on any GLES driver, without GL errors.

Run through tests/goo-backdrop-copy-test.sh in a private headless session on the test machine:
  goo-backdrop-copy-test.py SESSION ARTIFACTS

Goo copies the rendered screen into a backdrop texture and draws the liquid as a lens over it.
wlroots' screen buffer usually has no alpha channel; copying it into a texture that has one is
an error that Mesa lets through and strict drivers (NVIDIA) refuse, so there the texture stays
black, the halo is a flat black frame and every frame logs GL errors.

With dye off the liquid is a clear lens, so where it lies over the wallpaper its pixels follow
the wallpaper. The same window is captured over a light and a dark wallpaper, and so are the
two wallpapers alone. Pixels the goo changed (outside the window) must brighten with the
wallpaper by a large share of the wallpaper's own change; a black backdrop leaves them dark
over both. The compositor log must hold no GL errors.
"""
import json
import math
import os
from pathlib import Path
import re
import socket
import struct
import subprocess
import sys
import time

import gi
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf

session, out = Path(sys.argv[1]), Path(sys.argv[2]).resolve()
out.mkdir(parents=True, exist_ok=True)
assert os.environ.get('SCOTTLAND_TEST_MODEL') == '1', 'private headless session required'
root = Path(__file__).resolve().parents[1]
sock = socket.socket(socket.AF_UNIX)
sock.connect(os.environ['WAYFIRE_SOCKET'])
OUTPUT = 'HEADLESS-1'
LIGHT, DARK = '#efe9dc', '#101827'
WINDOW = {'x': 550, 'y': 300, 'width': 500, 'height': 340}
CELL = 40   # foot sizes itself to whole character cells, so it can come out up to a cell smaller
PROBE = (200, 850)   # bare wallpaper, far from the window and its goo


def ipc(method, data=None):
    body = json.dumps({'method': method, 'data': data or {}}).encode()
    sock.sendall(struct.pack('<I', len(body)) + body)
    def read(n):
        b = b''
        while len(b) < n:
            chunk = sock.recv(n - len(b))
            if not chunk: raise RuntimeError('compositor disconnected')
            b += chunk
        return b
    result = json.loads(read(struct.unpack('<I', read(4))[0]))
    if 'error' in result: raise RuntimeError(result)
    return result


def views(): return ipc('scottland/layout-state')['views']
def screen(): return next(s for s in ipc('scottland/goo-state')['screens'] if s['output'] == OUTPUT)

checks = []
def check(name, ok, detail=None):
    checks.append({'check': name, 'ok': bool(ok), 'detail': detail})
    print(('PASS ' if ok else 'FAIL ') + name + ' ' + json.dumps(detail), flush=True)


def shot(name):
    path = out/(name + '.png')
    subprocess.run(['grim', '-o', OUTPUT, str(path)], check=True)
    image = GdkPixbuf.Pixbuf.new_from_file(str(path))
    return image.get_width(), image.get_height(), image.get_rowstride(), image.get_n_channels(), image.get_pixels()


def rgb(image, x, y):
    w, h, stride, n, pixels = image
    i = y*stride + x*n
    return pixels[i], pixels[i+1], pixels[i+2]


def luma(c): return .299*c[0] + .587*c[1] + .114*c[2]
def hex_rgb(color): return tuple(int(color[i:i+2], 16) for i in (1, 3, 5))


def settle(what, sources):
    """The goo has its expected sources and has gone to sleep."""
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        s = screen()
        if s['sources'] == sources and s['sleeping'] and not s.get('breath_loose'): return s
        time.sleep(.1)
    raise AssertionError('goo did not settle: ' + what + ' ' + json.dumps(screen()))


clients = {}
def wallpaper(color):
    """Replace the wallpaper and wait until the screen shows it."""
    old = clients.pop('wallpaper', None)
    if old:  # a second background layer could stack beneath the first
        old.terminate(); old.wait(5)
    with (out/'wallpaper.log').open('a') as log:
        clients['wallpaper'] = subprocess.Popen(['quickshell', '-p', str(root/'tests/GooWallpaper.qml')],
                                                env={**os.environ, 'GOO_WALLPAPER_COLOR': color},
                                                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    want = hex_rgb(color)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        seen = rgb(shot('probe'), *PROBE)
        if max(abs(a - b) for a, b in zip(seen, want)) <= 2: break
        time.sleep(.1)
    else:
        raise AssertionError(('wallpaper did not appear', color, seen))


def wait_view(title, present=True):
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        match = [v for v in views() if v['title'] == title]
        if bool(match) == present: return match[0] if match else None
        time.sleep(.1)
    raise AssertionError(('window did not ' + ('map' if present else 'close'), title))


def gl_errors():
    pattern = re.compile(r'GL_INVALID|up-convert')
    return [line for line in (session/'wayfire.log').read_text(errors='replace').splitlines() if pattern.search(line)]


try:
    ipc('wayfire/set-config-options', {f'output:{OUTPUT}/mode': '1600x1000@60000'})
    # No dye and no shine: the liquid is a clear lens over what lies beneath it.
    ipc('wayfire/set-config-options', {'scottland/' + k: v for k, v in {
        'goo': True, 'goo_dye_density': 0, 'goo_shine': 0, 'goo_noise': 0, 'goo_drift': 0,
        'goo_wave_height': 0, 'goo_swirl': 0, 'goo_spread': 0, 'goo_swell': 0}.items()})
    ipc('stipc/move_cursor', {'x': 1590, 'y': 990})
    renderer = [l for l in (session/'wayfire.log').read_text(errors='replace').splitlines()
                if re.search(r'GL (vendor|renderer)|Using OpenGL ES', l)]
    print(json.dumps({'gl': renderer}), flush=True)

    wallpaper(LIGHT)
    settle('bare light wallpaper', 0)
    bare_light = shot('bare-light')
    clients['window'] = subprocess.Popen(['foot', '-c', '/dev/null', '-T', 'backdrop-copy', 'sleep', '600'],
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    window = wait_view('backdrop-copy')
    ipc('window-rules/configure-view', {'id': window['id'], 'geometry': WINDOW})
    placed = lambda f: (abs(f['x'] - WINDOW['x']) < 1 and abs(f['y'] - WINDOW['y']) < 1 and
                        all(0 <= WINDOW[k] - f[k] < CELL for k in ('width', 'height')))
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not placed(wait_view('backdrop-copy')['frame']): time.sleep(.05)
    frame = wait_view('backdrop-copy')['frame']
    check('the window took its geometry', placed(frame), frame)
    settle('window over light wallpaper', 1)
    goo_light = shot('goo-light')
    wallpaper(DARK)
    settle('window over dark wallpaper', 1)
    goo_dark = shot('goo-dark')
    clients.pop('window').terminate()
    wait_view('backdrop-copy', present=False)
    settle('bare dark wallpaper', 0)
    bare_dark = shot('bare-dark')

    # Goo pixels: outside the window, where the goo changed the light wallpaper. Frames are
    # fractional; the scan covers every pixel the window touches and 120 px around it.
    w, h = bare_light[0], bare_light[1]
    fx, fy, fw, fh = frame['x'], frame['y'], frame['width'], frame['height']
    gains, wall = [], luma(rgb(bare_light, *PROBE)) - luma(rgb(bare_dark, *PROBE))
    for y in range(max(math.floor(fy) - 120, 0), min(math.ceil(fy + fh) + 120, h)):
        for x in range(max(math.floor(fx) - 120, 0), min(math.ceil(fx + fw) + 120, w)):
            if fx <= x < fx + fw and fy <= y < fy + fh: continue
            a, b = rgb(goo_light, x, y), rgb(bare_light, x, y)
            if max(abs(p - q) for p, q in zip(a, b)) <= 8: continue
            gains.append(luma(a) - luma(rgb(goo_dark, x, y)))
    gains.sort()
    median = gains[len(gains)//2] if gains else 0
    check('the goo is drawn around the window', len(gains) >= 500, {'goo_pixels': len(gains)})
    check('the goo lens follows the wallpaper beneath it', median >= .4*wall,
          {'median_goo_gain': round(median, 1), 'wallpaper_gain': round(wall, 1),
           'darkest_quartile_gain': round(gains[len(gains)//4], 1) if gains else None})
    errors = gl_errors()
    (out/'gl-errors.txt').write_text('\n'.join(errors) + '\n' if errors else '')
    check('the compositor logged no GL errors', not errors, {'lines': len(errors), 'first': errors[:3]})
finally:
    for client in clients.values():
        client.terminate()
    (out/'checks.json').write_text(json.dumps(checks, indent=2))

passed = sum(c['ok'] for c in checks)
print(f'{passed}/{len(checks)} checks passed', flush=True)
sys.exit(0 if checks and passed == len(checks) else 1)
