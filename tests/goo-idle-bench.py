#!/usr/bin/env python3
"""Large breathing window regression for GO10/GO17. Private headless session only.

Run through headless.sh run: goo-idle-bench.py SESSION ARTIFACTS [--seconds 5]
[--settings shipped|wide] [--stream-hz 0|1|30] [--refresh-hz 60|120]
[--scale 1] [--verify].
'wide' is an explicit anonymous numeric snapshot, never a personal config import.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import struct
import subprocess
import sys
import time

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument('session', type=Path)
ap.add_argument('artifacts', type=Path)
ap.add_argument('--seconds', type=float, default=5)
ap.add_argument('--settings', choices=['shipped', 'wide'], default='wide')
ap.add_argument('--stream-hz', type=float, default=0)
ap.add_argument('--full-redraw', action='store_true', help='change the entire terminal background each frame')
ap.add_argument('--scale', type=float, default=1)
ap.add_argument('--refresh-hz', type=int, default=60)
ap.add_argument('--verify', action='store_true')
ap.add_argument('--deterministic', action='store_true')
args = ap.parse_args()
if args.full_redraw and args.stream_hz <= 0:
    ap.error('--full-redraw requires --stream-hz > 0')
root = Path(__file__).resolve().parents[1]
out = args.artifacts.resolve()
out.mkdir(parents=True, exist_ok=True)
# Tests must be launched by the private harness, never pointed at a live socket.
assert os.environ.get('SCOTTLAND_TEST_MODEL') == '1', 'private test session required'
sock = socket.socket(socket.AF_UNIX)
sock.connect(os.environ['WAYFIRE_SOCKET'])

def ipc(method, data=None):
    b = json.dumps({'method': method, 'data': data or {}}).encode()
    sock.sendall(struct.pack('<I', len(b)) + b)
    def read(n):
        b = b''
        while len(b) < n:
            chunk = sock.recv(n-len(b))
            if not chunk:
                raise RuntimeError('compositor disconnected')
            b += chunk
        return b
    result = json.loads(read(struct.unpack('<I', read(4))[0]))
    if isinstance(result, dict) and 'error' in result:
        raise RuntimeError(result)
    return result

def views(): return ipc('scottland/layout-state')['views']
def state():
    screens = ipc('scottland/goo-state')['screens']
    return screens[0] if screens else {}
def pointer(x, y): ipc('stipc/move_cursor', {'x': round(x), 'y': round(y)})
def button(mode): ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': mode})
def click(x, y):
    pointer(x, y)
    button('press')
    button('release')
def view(i): return next(v for v in views() if v['title'] == f'idle-{i}')
def place(i, x, y, w, h):
    ipc('window-rules/configure-view', {'id': view(i)['id'],
        'geometry': dict(zip(('x', 'y', 'width', 'height'), (x, y, w, h)))})
def move(i, x, y):
    ipc('window-rules/focus-view', {'id': view(i)['id']})
    time.sleep(.7)
    f = view(i)['frame']
    cx, cy = f['x']+f['width']/2, f['y']+f['height']/2
    pointer(cx, cy)
    ipc('stipc/feed_key', {'key': 'KEY_LEFTMETA', 'state': True})
    button('press')
    for step in range(1, 31):
        pointer(cx+(x-cx)*step/30, cy+(y-cy)*step/30)
        time.sleep(.02)
    button('release')
    ipc('stipc/feed_key', {'key': 'KEY_LEFTMETA', 'state': False})
    time.sleep(.7)
def settle():
    time.sleep(.5)  # allow prepare() to see changed options/sources
    end = time.monotonic()+60
    while time.monotonic() < end:
        if state().get('sleeping'):
            return
        time.sleep(.1)
    raise AssertionError(f'goo did not sleep: {state()}')

pid = int((args.session/'pid').read_text())
for child in Path(f'/proc/{pid}/task/{pid}/children').read_text().split():
    if Path(f'/proc/{child}/comm').read_text().strip() == 'wayfire':
        pid = int(child)
        break
clients = []

def measure(label):
    before = state()
    start = time.monotonic()
    sample = subprocess.Popen([sys.executable, str(root/'tests/gpu-sample.py'), str(pid), str(args.seconds)],
                              stdout=subprocess.PIPE, text=True)
    draws = []
    swells = []
    while time.monotonic()-start < args.seconds:
        current = state()
        if current and current['draw_gpu_ms'] > 0:
            draws.append(current['draw_gpu_ms'])
        if not current:
            swells.append(view(1)['frame']['swell'])
        time.sleep(.1)
    gpu = sample.communicate()[0].strip()
    assert sample.returncode == 0
    after = state()
    result = {'case': label, 'seconds': time.monotonic()-start, 'gpu': gpu,
              'sleeping': after.get('sleeping'), 'draw_gpu_ms_median':
              sorted(draws)[len(draws)//2] if draws else None,
              'sources': after.get('sources'), 'breath_damage': after.get('breath_damage')}
    for key in ('steps', 'breath_ticks', 'draws', 'surface_pixels', 'capture_pixels', 'composite_pixels'):
        result[key] = after.get(key, 0)-before.get(key, 0) if after else None
    if after and result['draws'] == 0:
        result['draw_gpu_ms_median'] = None  # the last query is stale at rest
    if swells:
        result['fallback_swell_range'] = [min(swells), max(swells)]
        # Only a full five-second cycle is guaranteed to span most of the curve.
        assert max(swells)-min(swells) > (.08 if args.seconds >= 5 else .005), result
    print(json.dumps(result), flush=True)
    with (out/'measurements.jsonl').open('a') as f:
        f.write(json.dumps(result)+'\n')
    subprocess.run(['grim', str(out/(label+'.png'))], check=True)
    if after:
        assert after['sleeping'] and result['steps'] == 0, result
    if label == 'answered':
        assert all(result[k] == 0 for k in ('draws', 'breath_ticks', 'surface_pixels',
                                           'capture_pixels', 'composite_pixels')), result

try:
    ipc('wayfire/set-config-options', {
        'output:HEADLESS-1/mode': f'{round(2560*args.scale)}x{round(1600*args.scale)}@{args.refresh_hz*1000}',
        'output:HEADLESS-1/scale': args.scale})
    time.sleep(1)
    settings = {}
    if args.settings == 'wide':
        settings = {'goo_thickness': 22., 'goo_reach': 33., 'goo_thinning': .27,
            'goo_swell': .68, 'goo_noise': .38, 'goo_lump': 315., 'goo_wave_speed': .16,
            'goo_wave_damp': .958, 'goo_wave_height': .76, 'goo_spread': .66,
            'goo_swirl': 3., 'goo_release': .195, 'goo_shine': .83, 'goo_relief': 5.6,
            'goo_depth': 4., 'goo_soak': .9, 'goo_overlap_film': 10.,
            'goo_hover_cloudiness': .05, 'goo_hover_emissivity': .31,
            'center_opacity_unfocused': .91,
            'goo_falloff': '0.000:1.000 0.063:0.779 0.125:0.607 0.188:0.472 0.250:0.368 '
            '0.313:0.287 0.375:0.223 0.395:0.223 0.500:0.135 0.563:0.105 0.625:0.082 '
            '0.688:0.064 0.750:0.050 0.813:0.039 0.875:0.030 0.938:0.024 1.000:0.018'}
        ipc('wayfire/set-config-options', {'scottland/'+k: v for k, v in settings.items()})
    if args.deterministic:
        ipc('wayfire/set-config-options', {'scottland/'+k: v for k, v in
            {'goo_noise': 0., 'goo_drift': 0., 'goo_wave_height': 0., 'goo_swirl': 0., 'goo_release': .3}.items()})
    # Wayland display numbers are reused on the shared host. Initialize this
    # private session's palette before cards can read a prior session's file.
    subprocess.run([str(root/'core/libexec/scottland-color-scheme'), 'once'],
                   check=True, stdout=subprocess.DEVNULL)
    # A stationary backdrop and six clients: live-sized overlapping pair, two
    # peripheral windows, two actual card widgets. Only the attention client streams.
    with (out/'wallpaper.log').open('w') as log:
        clients.append(subprocess.Popen(['quickshell', '-p', str(root/'tests/GooWallpaper.qml')],
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True))
    for i in range(6):
        stream = (f'n=0\nwhile True:\n print(f"frame {{n:08d}} "*8,flush=True);n+=1;time.sleep({1/args.stream_hz!r})'
                  if args.stream_hz else 'time.sleep(600)')
        if args.full_redraw:
            # OSC 11 invalidates the terminal's whole background, unlike foot's
            # small line damage. Models a GPU client's full-surface commits.
            stream = (f'n=0\nwhile True:\n print(f"\\033]11;#{{32+(n%2)*6:02x}}2020\\007",end="",flush=True);'
                      f'n+=1;time.sleep({1/args.stream_hz!r})')
        code = ('import time\nprint("\\033[?25l" + "sample content "*400,flush=True)\n'
                + (stream if i == 1 else 'time.sleep(600)'))
        clients.append(subprocess.Popen(['foot', '-c', '/dev/null', '-o', 'resize-by-cells=no', '-T', f'idle-{i}',
            'python3', '-u', '-c', code], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True))
        for _ in range(100):
            if any(v['title'] == f'idle-{i}' for v in views()):
                break
            time.sleep(.05)
    time.sleep(1)
    place(4, 1700, 500, 700, 500)
    move(4, 2540, 1040)
    place(5, 1700, 500, 700, 500)
    move(5, 2540, 820)
    # Center windows have these exact presented sizes; peripheral dimensions
    # intentionally stay transformed by the real shipped layout model.
    place(2, -350, 590, 1180, 780)
    place(3, 50, -120, 962, 1160)
    place(1, 1193, 73, 941, 940)
    place(0, 819, 312, 992, 1146)
    time.sleep(1)
    move(1, 1663.5, 543.)
    move(0, 1315., 885.)
    click(1000, 500)  # actual input puts the large focus window in front
    pointer(2500, 1500)
    assert len(ipc('scottland/widgets')['widgets']) == 2, views()
    ipc('scottland/attention', {'window': view(1)['id'], 'attention': True, 'source': 'idle-bench'})
    settle()
    metadata = {'settings': settings, 'args': vars(args) | {'session': str(args.session), 'artifacts': str(out)},
        'views': views(), 'widgets': ipc('scottland/widgets'), 'state': state(),
        'outputs': ipc('window-rules/list-outputs'),
        'processes': subprocess.check_output(['ps', '-C', 'wayfire', '-o', 'pid,etimes,args'], text=True)}
    (out/'fixture.json').write_text(json.dumps(metadata, indent=2))
    measure('on-1')
    if args.verify:
        from goo_idle_checks import verify
        verify(ipc, out, args.scale)
    ipc('wayfire/set-config-options', {'scottland/goo': False})
    # Some quiescent client transforms do not re-render on the config switch.
    # Re-arm the same source so the off comparator really breathes too.
    for enabled in (False, True):
        ipc('scottland/attention', {'window': view(1)['id'], 'attention': enabled, 'source': 'idle-bench'})
        time.sleep(.5)
    time.sleep(3)
    measure('off')
    if args.verify:
        from goo_idle_checks import verify_fallback
        verify_fallback(lambda: view(1), out)
    ipc('wayfire/set-config-options', {'scottland/goo': True})
    settle()
    measure('on-2')
    if args.verify:
        ipc('scottland/attention', {'window': view(1)['id'], 'attention': False, 'source': 'idle-bench'})
        settle()
        measure('answered')
finally:
    for client in clients:
        try:
            os.killpg(client.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    for client in clients:
        try:
            client.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(client.pid, signal.SIGKILL)
            client.wait()
