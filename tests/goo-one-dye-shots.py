#!/usr/bin/env python3
"""GO28 pictures: the same scene at the shipped goo settings and at Mike's, at rest.

  tests/headless.sh run python3 tests/goo-one-dye-shots.py ARTIFACTS

1600x1000, a wallpaper of strong color patches. A focused window in front of a magenta one
(its film lies over the magenta), a window asking for attention, and an unfocused one.
For each preset: a full screenshot and a crop, once the goo is at rest. Run it on main and on
this branch to compare. Pictures only; no assertions.
"""
import json, os, signal, socket, struct, subprocess, sys, time
from pathlib import Path

root = Path(__file__).resolve().parents[1]
out = Path(sys.argv[1]).resolve(); out.mkdir(parents=True, exist_ok=True)
assert os.environ.get('SCOTTLAND_TEST_MODEL') == '1', 'private headless session required'
sock = socket.socket(socket.AF_UNIX); sock.connect(os.environ['WAYFIRE_SOCKET'])
def ipc(method, data=None):
    b = json.dumps({'method': method, 'data': data or {}}).encode(); sock.sendall(struct.pack('<I', len(b)) + b)
    def read(n):
        r = b''
        while len(r) < n:
            c = sock.recv(n - len(r))
            if not c: raise RuntimeError('compositor disconnected')
            r += c
        return r
    return json.loads(read(struct.unpack('<I', read(4))[0]))
def views(): return ipc('scottland/layout-state')['views']
def view(t): return next(v for v in views() if v['title'] == t)
def state(): return ipc('scottland/goo-state')['screens'][0]
def click(x, y):
    ipc('stipc/move_cursor', {'x': round(x), 'y': round(y)})
    for m in ('press', 'release'): ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': m})
    ipc('stipc/move_cursor', {'x': 5, 'y': 5})
def rest():
    time.sleep(1)
    for _ in range(1200):
        s = state()
        if s['sleeping'] and not s.get('breath_loose') and not s.get('water_running') and not s.get('pickup_pending'): return
        time.sleep(.1)
def options(values):
    for k, v in values.items(): ipc('wayfire/set-config-options', {'scottland/' + k: v})   # one by one: older builds lack some

SHIPPED = {'goo_thickness': 13., 'goo_thinning': .45, 'goo_noise': .32, 'goo_lump': 190., 'goo_drift': .12,
           'goo_wave_speed': .28, 'goo_wave_damp': .985, 'goo_wave_height': .55, 'goo_spread': .45, 'goo_swirl': .9,
           'goo_release': .06, 'goo_shine': .75, 'goo_relief': 5., 'goo_depth': 6., 'goo_profile': .65, 'goo_soak': .12,
           'goo_overlap_film': 4., 'goo_hover_cloudiness': .65, 'goo_hover_emissivity': .35,
           'goo_dye_density': 1., 'goo_dye_strength': 1.}
MIKE = dict(SHIPPED, goo_thickness=22., goo_thinning=.27, goo_noise=.38, goo_lump=315., goo_drift=.39, goo_wave_speed=.16,
            goo_wave_damp=.958, goo_wave_height=.76, goo_spread=.83, goo_swirl=3., goo_release=.3, goo_shine=.89,
            goo_relief=5.6, goo_depth=4., goo_profile=.45, goo_soak=1., goo_overlap_film=10., goo_hover_cloudiness=.2,
            goo_hover_emissivity=.31, goo_dye_density=1.5, goo_dye_strength=1.5)
clients = []
try:
    ipc('wayfire/set-config-options', {'output:HEADLESS-1/mode': '1600x1000@60000'})
    geometry = {'shot-magenta': (760, 430, 520, 380, 'd02090'), 'shot-front': (420, 230, 560, 400, None),
                'shot-attention': (1120, 90, 380, 260, None), 'shot-apart': (120, 560, 360, 260, None)}
    for title, (x, y, w, h, bg) in geometry.items():
        cmd = ['foot', '-c', '/dev/null', '-o', 'resize-by-cells=no', '-T', title]
        cmd += (['python3', '-u', '-c', "import time; print('\\033]11;#' + %r + '\\007', end='', flush=True); time.sleep(3600)" % bg]
                if bg else ['sleep', '3600'])
        clients.append(subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
        for _ in range(100):
            if any(v['title'] == title for v in views()): break
            time.sleep(.05)
        ipc('window-rules/configure-view', {'id': view(title)['id'], 'geometry': {'x': x, 'y': y, 'width': w, 'height': h}})
        time.sleep(.3)
    clients.append(subprocess.Popen(['quickshell', '-p', str(root / 'tests/GooWallpaper.qml')], env=dict(os.environ, GOO_WALLPAPER_PATCHES='dark'),
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
    time.sleep(2)
    ipc('scottland/attention', {'window': view('shot-attention')['id'], 'attention': True, 'source': 'one-dye-shots'})
    for name, preset in (('shipped', SHIPPED), ('mike', MIKE)):
        options(preset)
        for focus in ('shot-front', 'shot-apart'):
            f = view(focus)['frame']; click(f['x'] + 40, f['y'] + 40)
            rest()
            ipc('scottland/goo-state', {'breath_hold': 0.})
            time.sleep(.5)
            tag = f'{name}-focus-{"front" if focus == "shot-front" else "apart"}'
            subprocess.run(['grim', str(out / f'{tag}.png')], check=True)
            subprocess.run(['grim', '-g', '360,170 960x700', str(out / f'{tag}-crop.png')], check=True)
            ipc('scottland/goo-state', {'breath_hold': -1.})
            print(tag, flush=True)
finally:
    for c in clients:
        try: os.killpg(c.pid, signal.SIGTERM)
        except ProcessLookupError: pass
    sock.close()
