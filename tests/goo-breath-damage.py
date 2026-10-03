#!/usr/bin/env python3
"""Large attention window above a slowly redrawing terminal; plumbus only.

Use goo-breath-damage.sh; 2560x1600, configurable refresh, device-pixel counters.
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
ap.add_argument('--settings', choices=('shipped', 'wide'), default='wide')
ap.add_argument('--options', type=Path, help='JSON overrides, with or without scottland/ prefixes')
ap.add_argument('--stream-hz', type=float, default=1)
ap.add_argument('--rounds', type=int, default=2)
ap.add_argument('--warmup', type=float, default=10)
ap.add_argument('--full-redraw', action='store_true')
ap.add_argument('--refresh-hz', type=int, default=120)
ap.add_argument('--scale', type=float, default=1)
ap.add_argument('--rotation', choices=('normal','90','180','270'), default='normal')
ap.add_argument('--verify', action='store_true', help='natural-frame backdrop equality and visible fallback breathing')
ap.add_argument('--deterministic', action='store_true')
args = ap.parse_args()
if args.full_redraw and args.stream_hz <= 0:
    ap.error('--full-redraw requires --stream-hz > 0')
if args.seconds <= 0 or args.scale <= 0 or args.refresh_hz <= 0:
    ap.error('seconds, scale, and refresh rate must be positive')
root = Path(__file__).resolve().parents[1]
session = args.session
out = args.artifacts.resolve()
seconds = args.seconds
assert os.environ.get('SCOTTLAND_TEST_MODEL') == '1', 'private headless session required'
out.mkdir(parents=True, exist_ok=True)
sock = socket.socket(socket.AF_UNIX)
sock.connect(os.environ['WAYFIRE_SOCKET'])

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
    if 'error' in result: raise RuntimeError(result)
    return result

def views(): return ipc('scottland/layout-state')['views']
def view(title): return next(v for v in views() if v['title'] == title)
def state(data=None):
    screens = ipc('scottland/goo-state', data)['screens']
    return screens[0] if screens else None
def pointer(x, y): ipc('stipc/move_cursor', {'x': round(x), 'y': round(y)})
def key(down): ipc('stipc/feed_key', {'key': 'KEY_LEFTMETA', 'state': down})
def button(mode): ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': mode})
def move(title, x, y):
    frame = view(title)['frame']; cx = frame['x']+frame['width']/2; cy = frame['y']+frame['height']/2
    pointer(cx, cy); key(True); button('press')
    for i in range(1, 31):
        pointer(cx+(x-cx)*i/30, cy+(y-cy)*i/30); time.sleep(.02)
    button('release'); key(False); time.sleep(1)
def sleep_goo(what):
    time.sleep(1)  # let the change reach prepare() before trusting the flag
    latest = None
    for _ in range(250):
        s = state()
        if s and s['sleeping']: return
        latest = s
        time.sleep(.1)
    scene = []
    for v in views():
        if v['title'].startswith('idle-'):
            f = v.get('frame', {})
            scene.append({k: v.get(k) for k in ('id', 'title', 'zone', 'hidden', 'widgetized')} |
                         {k: f.get(k) for k in ('x', 'y', 'width', 'height', 'swell', 'dot', 'attention', 'alpha_shape')})
    fixture = json.loads((out / 'fixture.json').read_text()) if (out / 'fixture.json').exists() else {}
    raise AssertionError('goo failed to sleep: ' + what + ': ' + json.dumps({
        'state': latest, 'scene': scene, 'widgets': ipc('scottland/widgets').get('widgets'),
        'initial_state': fixture.get('state')}))

pid = int((session/'pid').read_text())
for child in Path(f'/proc/{pid}/task/{pid}/children').read_text().split():
    if Path(f'/proc/{child}/comm').read_text().strip() == 'wayfire': pid = int(child); break

clients = []

def load_snapshot():
    process = subprocess.check_output(
        ['ps', '-eo', 'pid,ppid,stat,pcpu,comm,args', '--sort=-pcpu'], text=True).splitlines()
    return {'loadavg': Path('/proc/loadavg').read_text().split()[:3],
            'top_processes': process[:13]}

def measure(label):
    counter_start = time.monotonic()
    before = state()
    layout_before = ipc('scottland/layout-state')
    load_before = load_snapshot()
    sample = subprocess.Popen([sys.executable, str(root/'tests/gpu-sample.py'), str(pid), str(seconds)],
                              stdout=subprocess.PIPE, text=True)
    draws = []
    end = time.monotonic()+seconds
    while time.monotonic() < end:
        current = state()
        if current: draws.append(current['draw_gpu_ms'])
        time.sleep(.1)
    gpu_output = sample.communicate()[0].strip()
    assert sample.returncode == 0
    after = state()
    layout_after = ipc('scottland/layout-state')
    counter_seconds = time.monotonic()-counter_start
    load_after = load_snapshot()
    def delta(name):
        return after[name]-before[name] if before and after and name in after and name in before else None
    result = {'case': label, 'seconds': seconds, 'counter_seconds': counter_seconds, 'gpu': gpu_output,
              'compositor_gpu': float(gpu_output.split('compositor GPU')[1].split('%')[0]),
              'whole_gpu': float(gpu_output.split('whole GPU')[1].split('%')[0]),
              'load_before': load_before, 'load_after': load_after,
              'steps': delta('steps'), 'sleeping': after['sleeping'] if after else None,
              'draw_gpu_ms_median': sorted(draws)[len(draws)//2] if draws and delta('breath_ticks') else None,
              'breath_ticks': delta('breath_ticks'),
              # Cached layer refreshes; exact direct draws are counted in surface_pixels instead.
              'breath_refreshes': delta('breath_refreshes'),
              'backdrop_reuse_pixels': delta('backdrop_reuse_pixels'),
              'breath_keys': after.get('breath_keys') if after else None,
              'breath_keyframes_active': after.get('breath_keyframes_active') if after else None,
              'draws': delta('draws'), 'surface_pixels': delta('surface_pixels'),
              'capture_pixels': delta('capture_pixels'), 'composite_pixels': delta('composite_pixels'),
              'breath_area': sum(r['width']*r['height'] for r in after['breath_damage']) if after else None,
              'overlapping': after.get('overlapping') if after else None,
              'sources': after['sources'] if after else None}
    for k in ('output_frames', 'output_damage_pixels'):
        result[k] = layout_after.get(k, 0)-layout_before.get(k, 0)
    first = {v['title']: v.get('frame', {}) for v in layout_before['views']}
    result['windows'] = {v['title']: {k: v['frame'].get(k, 0)-first.get(v['title'], {}).get(k, 0)
                         for k in ('content_pixels', 'render_calls')}
                         for v in layout_after['views'] if 'frame' in v and v['title'].startswith('damage-')}
    print(json.dumps(result), flush=True)
    with (out/'measurements.jsonl').open('a') as f: f.write(json.dumps(result)+'\n')
    subprocess.run(['grim', str(out/(label+'.png'))], check=True)
    return result

def attention(window, on):
    ipc('scottland/attention', {'window': window, 'attention': on, 'source': 'idle-bench'})

def check_reuse():
    import gi
    gi.require_version('GdkPixbuf', '2.0')
    from gi.repository import GdkPixbuf
    checks = []
    def capture(label):
        count = ipc('scottland/layout-state', {'capture_next_frame': True})['captured_frames']
        for _ in range(200):
            if ipc('scottland/layout-state')['captured_frames'] > count: break
            time.sleep(.02)
        else: raise AssertionError('no natural frame to capture')
        source = Path(os.environ['SCOTTLAND_TEST_STATE'])/'render-frame.ppm'
        image = GdkPixbuf.Pixbuf.new_from_file(str(source))
        image.savev(str(out/(label+'.png')), 'png', [], [])
        return image.get_pixels()
    def compare(label, hold):
        state({'reuse_backdrop': False, 'breath_hold': hold})
        time.sleep(.4)
        exact = capture(label+'-fresh')
        state({'reuse_backdrop': True})
        time.sleep(.4)
        reused = state().get('backdrop_reuse_pixels', 0)
        cached = capture(label+'-reused')
        advanced = state().get('backdrop_reuse_pixels', 0)-reused
        changed = sum(a != b for a,b in zip(exact,cached))
        check = {'case':label, 'changed_channels':changed, 'reused_pixels_during_capture':advanced}
        checks.append(check)
        print(json.dumps({'pixel_check':check}), flush=True)
        assert changed == 0 and advanced > 0, check
    def compare_current(label):
        reused = state()['backdrop_reuse_pixels']
        cached = capture(label+'-reused')
        advanced = state()['backdrop_reuse_pixels']-reused
        state({'reuse_backdrop':False})
        time.sleep(.3)
        fresh = capture(label+'-fresh')
        changed = sum(a != b for a,b in zip(cached,fresh))
        check = {'case':label, 'changed_channels':changed,
                 'reused_pixels_during_capture':advanced}
        checks.append(check)
        print(json.dumps({'pixel_check':check}),flush=True)
        assert changed == 0 and advanced > 0, check
        state({'reuse_backdrop':True})
    try:
        ipc('wayfire/set-config-options', {'scottland/goo':True})
        attention(breather,True)
        sleep_goo('pixel checks')
        for c in clients[1:]: os.killpg(c.pid,signal.SIGSTOP)
        for keys in (True,False):
            ipc('wayfire/set-config-options',{'scottland/goo_breath_keys':keys})
            sleep_goo('pixel mode')
            for hold in (0.,.5,1.): compare(f'pixels-{keys}-{hold}',hold)
            # Resume slow updates in both terminals, then compare the latest
            # settled cached scene with a fresh repaint of exactly that content.
            for c in clients[1:]: os.killpg(c.pid,signal.SIGCONT)
            time.sleep(2.3)
            for c in clients[1:]: os.killpg(c.pid,signal.SIGSTOP)
            time.sleep(.2)
            compare_current(f'content-update-{keys}')
        for c in clients[1:]: os.killpg(c.pid,signal.SIGCONT)
        move('damage-focus', 850, 800)
        pointer(850, 800)
        sleep_goo('real move/stacking invalidation')
        for c in clients[1:]: os.killpg(c.pid,signal.SIGSTOP)
        time.sleep(.4)
        compare_current('real-move-and-stacking')
        ipc('wayfire/set-config-options', {'scottland/goo':False})
        time.sleep(2)
        from goo_idle_checks import verify_fallback
        verify_fallback(lambda: view('damage-breather'), out)
        (out/'pixel-checks.json').write_text(json.dumps(checks,indent=2))
    finally:
        for c in clients[1:]: os.killpg(c.pid,signal.SIGCONT)
        state({'breath_hold':-1,'reuse_backdrop':True})

try:
    width, height = (1600,2560) if args.rotation in ('90','270') else (2560,1600)
    changes = {'output:HEADLESS-1/mode': f'{round(width*args.scale)}x{round(height*args.scale)}@{args.refresh_hz*1000}',
               'output:HEADLESS-1/scale': args.scale, 'output:HEADLESS-1/transform': args.rotation}
    if args.settings == 'wide':
        changes.update({
            'scottland/goo_thickness': 22., 'scottland/goo_reach': 33., 'scottland/goo_thinning': .27,
            'scottland/goo_swell': .68, 'scottland/goo_noise': .38, 'scottland/goo_lump': 315.,
            'scottland/goo_wave_speed': .16, 'scottland/goo_wave_damp': .958,
            'scottland/goo_wave_height': .76, 'scottland/goo_spread': .66,
            'scottland/goo_swirl': 3., 'scottland/goo_release': .195,
            'scottland/goo_shine': .83, 'scottland/goo_relief': 5.6,
            'scottland/goo_depth': 4., 'scottland/goo_soak': .9,
            'scottland/goo_overlap_film': 10., 'scottland/goo_hover_cloudiness': .05,
            'scottland/goo_hover_emissivity': .31, 'scottland/center_opacity_unfocused': .91,
            'scottland/goo_falloff': '0.000:1.000 0.063:0.779 0.125:0.607 0.188:0.472 0.250:0.368 '
                '0.313:0.287 0.375:0.223 0.395:0.223 0.500:0.135 0.563:0.105 0.625:0.082 '
                '0.688:0.064 0.750:0.050 0.813:0.039 0.875:0.030 0.938:0.024 1.000:0.018'})
    if args.options:
        values = json.loads(args.options.read_text())
        changes.update({key if '/' in key else 'scottland/' + key: value
                        for key, value in values.items()})
    if args.deterministic:
        changes.update({'scottland/goo_noise': 0., 'scottland/goo_drift': 0.,
                        'scottland/goo_wave_height': 0., 'scottland/goo_swirl': 0.,
                        'scottland/goo_release': .3})
    ipc('wayfire/set-config-options', changes)
    time.sleep(1)
    with (out/'wallpaper.log').open('w') as f:
        clients.append(subprocess.Popen(['quickshell', '-p', str(root/'tests/GooWallpaper.qml')],
                                        stdout=f, stderr=subprocess.STDOUT, start_new_session=True))
    geometry = {'damage-under': (520, 180, 1520, 1280),
                'damage-breather': (900, 247, 1012, 1106),
                'damage-focus': (220, 450, 420, 400)}
    for title in geometry:
        # A terminal with stable text and a single changing line. The optional
        # full-redraw workload changes the background, as a repainting client may do.
        code = ('import time\nprint("\\033[?25l", end="")\n'
                'print("\\n".join(f"line {i:03d}  Scottland slow terminal redraw "*2 for i in range(45)), flush=True)\n')
        if title != 'damage-focus' and args.stream_hz > 0:
            if args.full_redraw:
                code += (f'n=0\nwhile True:\n print(f"\\033]11;#{{32+(n%2)*6:02x}}2020\\007",end="",flush=True);'
                         f'n+=1;time.sleep({1/args.stream_hz!r})')
            else:
                code += (f'n=0\nwhile True:\n print(f"\\033[18;1Htext update {{n:08d}}",end="",flush=True);'
                         f'n+=1;time.sleep({1/args.stream_hz!r})')
        else: code += 'time.sleep(900)'
        clients.append(subprocess.Popen(['foot', '-c', '/dev/null', '-o', 'resize-by-cells=no',
                                         '-T', title, 'python3', '-u', '-c', code],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                        start_new_session=True))
        time.sleep(.5)
    time.sleep(2)
    for title, (x,y,w,h) in geometry.items():
        ipc('window-rules/configure-view', {'id': view(title)['id'],
            'geometry': {'x': x, 'y': y, 'width': w, 'height': h}})
        time.sleep(.3)
    # Real clicks put the large breathing window above the redrawing background
    # terminal, then focus the separate small window to leave attention pending.
    pointer(1800, 1200); button('press'); button('release'); time.sleep(.3)
    pointer(450, 600); button('press'); button('release')
    pointer(450, 650)
    time.sleep(1)
    breather = view('damage-breather')['id']
    assert not view('damage-breather')['frame']['focus']
    f = view('damage-breather')['frame']
    assert (f['width'], f['height']) == (1012, 1106), f
    sleep_goo('initial scene')
    (out/'fixture.json').write_text(json.dumps({'args': {k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()},
        'views':views(), 'outputs':ipc('window-rules/list-outputs'), 'state':state(), 'load':load_snapshot()}, indent=2))
    for trial in range(args.rounds):
        for mode in ('idle', 'keys', 'exact', 'halo'):
            ipc('wayfire/set-config-options', {'scottland/goo': mode != 'halo',
                                               'scottland/goo_breath_keys': mode != 'exact'})
            attention(breather, mode != 'idle')
            if mode != 'halo': sleep_goo(mode)
            time.sleep(args.warmup)
            measure(f'{trial}-{mode}')
    if args.verify: check_reuse()
finally:
    for client in clients:
        try: os.killpg(client.pid, signal.SIGTERM)
        except ProcessLookupError: pass
    for client in clients:
        try: client.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try: os.killpg(client.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            client.wait()
    sock.close()
