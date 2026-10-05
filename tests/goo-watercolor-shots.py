#!/usr/bin/env python3
"""GO24 watercolor: screenshots and short frame sequences, in a private headless session.

  goo-watercolor-shots.py SESSION ARTIFACTS [--scheme dark|light] [--soak 0,0.12,0.5,0.9]

A 1600x1000 output with a wallpaper of strong color patches, two overlapping center
windows (one focused), a third apart, and one asking for attention. For each soak value:
wait for the goo to sleep, let the watercolor run, crop a frame every two seconds
while the watercolor coasts to a stop (sampling compositor GPU), then take a full
screenshot at rest and sample GPU again. Run on the test machine.
"""
import argparse, json, os, signal, socket, struct, subprocess, sys, time
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument('session', type=Path)
ap.add_argument('artifacts', type=Path)
ap.add_argument('--scheme', choices=('dark', 'light'), default='dark')
ap.add_argument('--soak', default='0,0.12,0.5,0.9')
ap.add_argument('--options', type=Path)
args = ap.parse_args()
root = Path(__file__).resolve().parents[1]
out = args.artifacts.resolve(); out.mkdir(parents=True, exist_ok=True)
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
def state(): return ipc('scottland/goo-state')['screens'][0]
def click(x, y):
    ipc('stipc/move_cursor', {'x': round(x), 'y': round(y)})
    for mode in ('press', 'release'): ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': mode})
def settle(moving=False):
    time.sleep(1)
    for _ in range(600):
        s = state()
        if s['sleeping'] and not s.get('breath_loose') and (moving or not s.get('water_running')): return
        time.sleep(.1)
    raise AssertionError('goo did not settle ' + json.dumps({k: state().get(k) for k in ('sleeping', 'energy', 'wave_energy', 'dye_energy', 'last_wake')}))

pid = int((args.session/'pid').read_text())
for child in Path(f'/proc/{pid}/task/{pid}/children').read_text().split():
    if Path(f'/proc/{child}/comm').read_text().strip() == 'wayfire': pid = int(child); break
clients = []
try:
    changes = {'output:HEADLESS-1/mode': '1600x1000@60000', 'scottland/color_scheme': args.scheme}
    if args.options:
        changes.update({k if '/' in k else 'scottland/'+k: v for k, v in json.loads(args.options.read_text()).items()})
    ipc('wayfire/set-config-options', changes)
    time.sleep(1)
    with (out/'wallpaper.log').open('w') as f:
        clients.append(subprocess.Popen(['quickshell', '-p', str(root/'tests/GooWallpaper.qml')],
                                        env=dict(os.environ, GOO_WALLPAPER_PATCHES=args.scheme),
                                        stdout=f, stderr=subprocess.STDOUT, start_new_session=True))
    time.sleep(1)
    geometry = {'water-back': (330, 150, 620, 470), 'water-front': (640, 380, 560, 420), 'water-apart': (1080, 110, 400, 300)}
    for title in geometry:
        clients.append(subprocess.Popen(['foot', '-c', '/dev/null', '-o', 'resize-by-cells=no', '-T', title, 'sleep', '900'],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
        time.sleep(.4)
    time.sleep(1.5)
    for title, (x, y, w, h) in geometry.items():
        ipc('window-rules/configure-view', {'id': view(title)['id'], 'geometry': {'x': x, 'y': y, 'width': w, 'height': h}})
        time.sleep(.3)
    f = view('water-back')['frame']; click(f['x']+30, f['y']+30); time.sleep(.3)
    f = view('water-front')['frame']; click(f['x']+f['width']*.7, f['y']+f['height']*.8)
    time.sleep(.5)
    ipc('scottland/attention', {'window': view('water-apart')['id'], 'attention': True, 'source': 'watercolor-shots'})
    rows = []
    for soak in [float(v) for v in args.soak.split(',')]:
        ipc('wayfire/set-config-options', {'scottland/goo_soak': soak})
        settle(moving=True)   # asleep; the watercolor is coasting to a stop
        tag = f'{args.scheme}-soak-{soak:g}'
        before = state()
        sample = subprocess.Popen([sys.executable, str(root/'tests/gpu-sample.py'), str(pid), '12'], stdout=subprocess.PIPE, text=True)
        fb = view('water-back')['frame']
        trace = []
        for n in range(6):
            subprocess.run(['grim', '-g', '250,60 760x520', str(out/f'{tag}-frame-{n}.png')], check=True)
            # The dye itself along the back window's left band, to tell motion from stills.
            row = []
            for k in range(5):
                v = ipc('scottland/goo-state', {'x': fb['x']-6, 'y': fb['y']+40+k*90})['screens'][0]
                row.append([round(v[c], 3) for c in ('red', 'green', 'blue')])
            trace.append(row)
            time.sleep(2.2)
        print(json.dumps({'dye_trace': [trace[0], trace[-1]]}), flush=True)
        gpu = sample.communicate()[0].strip().split('\n')[0]
        after = state()
        d = lambda k: after.get(k, 0)-before.get(k, 0)
        settle()              # come to rest: this picture is what stays
        subprocess.run(['grim', str(out/f'{tag}.png')], check=True)
        idle = subprocess.run([sys.executable, str(root/'tests/gpu-sample.py'), str(pid), '5'], capture_output=True, text=True).stdout.split('\n')[0]
        row = {'scheme': args.scheme, 'soak': soak, 'gpu': gpu, 'gpu_at_rest': idle, 'sleeping': after['sleeping'], 'steps': d('steps'),
               'water_ticks': d('water_ticks'), 'dye_flows': d('dye_flows'), 'draws': d('draws'),
               'backdrop_reuses': d('backdrop_reuses'), 'breath_ticks': d('breath_ticks'),
               'motion_pixels': after.get('motion_pixels'), 'motion_rects': after.get('motion_rects'),
               'reuse_blocked': after.get('reuse_blocked')}
        rows.append(row); print(json.dumps(row), flush=True)
    (out/f'{args.scheme}-measurements.json').write_text(json.dumps(rows, indent=2))
finally:
    for c in clients:
        try: os.killpg(c.pid, signal.SIGTERM)
        except ProcessLookupError: pass
    sock.close()
