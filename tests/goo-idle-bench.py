#!/usr/bin/env python3
"""Merged GO10/GO17/GO18 idle fixture, for an isolated --widgets session.

Run with tests/headless.sh start --widgets, then:
  tests/headless.sh run python3 tests/goo-idle-bench.py SESSION ARTIFACTS [OPTIONS]

Both keyframe and exact strip modes run by default; each GPU sample is five seconds.
The Astra controls (--settings/--stream-hz/--full-redraw/--refresh-hz/--scale/
--deterministic) and Fable controls (--options/--visual) are retained.

Mike's live desktop (2560x1600, scale 1) with one center window breathing, as read from
its layout-state on 2026-10-02: nothing redraws, a 941x940 center window asks for
attention while partly under a larger focused center window (so its goo has overlap
film and the ordered shader path), scaled windows sit in the periphery and two rail
widgets on the right. Unlike goo-draw-bench.py no client draws, so the breathing is
the compositor's only work and the GPU is otherwise idle.

Cases, goo on then off in the same scene: nothing breathing, the center window
breathing, one rail widget breathing. --visual also holds the breath at fixed values
and compares the keyframe-interpolated surface with the exact per-tick one.
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
ap.add_argument('--breath-keys', choices=('both', 'on', 'off'), default='both')
ap.add_argument('--settings', choices=('shipped', 'wide'), default='wide')
ap.add_argument('--options', type=Path, help='JSON overrides, with or without scottland/ prefixes')
ap.add_argument('--stream-hz', type=float, default=0)
ap.add_argument('--full-redraw', action='store_true')
ap.add_argument('--refresh-hz', type=int, default=60)
ap.add_argument('--scale', type=float, default=1)
ap.add_argument('--verify', action='store_true', help='GO17 and goo-off fallback pixel checks')
ap.add_argument('--visual', action='store_true', help='compare keyframes to the exact surface')
ap.add_argument('--deterministic', action='store_true')
ap.add_argument('--title-hz', type=float, default=0,
                help='a widgetized terminal changes its title this often (an agent session does)')
ap.add_argument('--no-reuse', action='store_true', help='repaint the scene under every breath (the old path)')
ap.add_argument('--awake', action='store_true',
                help='also sample the awake simulation after a settings wake, and time its settling')
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
    started = time.monotonic(); first = state()
    time.sleep(1)  # let the change reach prepare() before trusting the flag
    latest = None
    for _ in range(600):
        s = state()
        if s and s['sleeping'] and not s.get('breath_loose'):  # asleep, strips tightened
            # How long a change keeps the simulation (the expensive part) running.
            record = {'settle': what, 'seconds': round(time.monotonic()-started, 1),
                      'steps': s['steps']-first['steps'] if first else None}
            print(json.dumps(record), flush=True)
            with (out/'measurements.jsonl').open('a') as f: f.write(json.dumps(record)+'\n')
            return
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
    global seconds
    before = state()
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
    load_after = load_snapshot()
    def delta(name):
        return after[name]-before[name] if before and after and name in after and name in before else None
    result = {'case': label, 'seconds': seconds, 'gpu': gpu_output,
              'compositor_gpu': float(gpu_output.split('compositor GPU')[1].split('%')[0]),
              'whole_gpu': float(gpu_output.split('whole GPU')[1].split('%')[0]),
              'load_before': load_before, 'load_after': load_after,
              'steps': delta('steps'), 'sleeping': after['sleeping'] if after else None,
              'draw_gpu_ms_median': sorted(draws)[len(draws)//2] if draws and delta('breath_ticks') else None,
              'breath_ticks': delta('breath_ticks'),
              # Cached layer refreshes; exact direct draws are counted in surface_pixels instead.
              'breath_refreshes': delta('breath_refreshes'),
              'breath_keys': after.get('breath_keys') if after else None,
              'breath_keyframes_active': after.get('breath_keyframes_active') if after else None,
              'draws': delta('draws'), 'surface_pixels': delta('surface_pixels'),
              # Breath-only frames drawn over the cached backdrop (nothing beneath repainted).
              'backdrop_reuses': delta('backdrop_reuses'),
              'capture_pixels': delta('capture_pixels'), 'composite_pixels': delta('composite_pixels'),
              'breath_area': sum(r['width']*r['height'] for r in after['breath_damage']) if after else None,
              # Wakes of the sleeping simulation during the sample, by cause.
              'wakes': {k: v-((before.get('wakes') or {}).get(k, 0)) for k, v in (after.get('wakes') or {}).items()
                        if v-((before.get('wakes') or {}).get(k, 0))} if before and after else None,
              'overlapping': after.get('overlapping') if after else None,
              'sources': after['sources'] if after else None}
    print(json.dumps(result), flush=True)
    with (out/'measurements.jsonl').open('a') as f: f.write(json.dumps(result)+'\n')
    subprocess.run(['grim', str(out/(label+'.png'))], check=True)
    return result

def attention(window, on):
    ipc('scottland/attention', {'window': window, 'attention': on, 'source': 'idle-bench'})

def visual(window):
    """Interpolated keyframes against the exact surface at held breath values."""
    import gi
    gi.require_version('GdkPixbuf', '2.0')
    from gi.repository import GdkPixbuf
    attention(window, True)
    sleep_goo('visual attention')
    if 'breath_keys' not in state():
        print(json.dumps({'visual': 'skipped: build has no breath keyframes'}), flush=True)
        attention(window, False)
        return
    rows = []
    def shot(name, hold, exact):
        state({'breath_hold': hold, 'breath_exact': exact})
        time.sleep(.5)
        path = out/f'{name}.png'
        subprocess.run(['grim', str(path)], check=True)
        return GdkPixbuf.Pixbuf.new_from_file(str(path))
    # Key values and the midpoints between them, where interpolation is furthest from a key.
    keys = state()['breath_key_values']
    holds = sorted(set([0., 1.] + [round((a+b)/2, 4) for a, b in zip(keys, keys[1:])]))
    for hold in holds:
        a = shot(f'visual-exact-{hold:.4f}', hold, True)
        b = shot(f'visual-keys-{hold:.4f}', hold, False)
        da, db = a.get_pixels(), b.get_pixels()
        stride, channels = a.get_rowstride(), a.get_n_channels()
        changed = worst = clear = strong = 0
        for y in range(a.get_height()):
            ra, rb = da[y*stride:y*stride+a.get_width()*channels], db[y*stride:y*stride+a.get_width()*channels]
            if ra == rb: continue
            for x in range(0, len(ra), channels):
                d = max(abs(ra[x+c]-rb[x+c]) for c in range(3))
                if d: changed += 1; worst = max(worst, d)
                if d >= 8: clear += 1
                if d >= 16: strong += 1
        rows.append({'breath': hold, 'changed_pixels': changed, 'changed_8_levels': clear,
                     'changed_16_levels': strong, 'max_channel_difference': worst})
        print(json.dumps({'visual': rows[-1]}), flush=True)
    # The tightened strips must repaint everything the breath changes: trough to peak
    # through them matches the same step through the full support strips.
    def step(tight):
        state({'breath_tight': tight, 'breath_hold': 0, 'breath_exact': False})
        time.sleep(.5)
        while tight and state().get('breath_loose'): time.sleep(.2)
        area = sum(r['width']*r['height'] for r in state()['breath_damage'])
        return area, shot(f'visual-{"tight" if tight else "loose"}-peak', 1, False).get_pixels()
    loose_area, loose = step(False)
    tight_area, tight = step(True)
    damage = {'loose_area': loose_area, 'tight_area': tight_area, 'identical': loose == tight}
    print(json.dumps({'visual_damage': damage}), flush=True)
    # Breath-only frames drawn over the cached backdrop must equal frames whose scene
    # beneath was repainted. Several shots: one frame a second takes the normal path.
    reuse = {'identical': True, 'differing_shots': 0, 'reuses': 0}
    if 'backdrop_reuses' in state():
        for hold in (.37, 1.):
            state({'breath_reuse': False}); time.sleep(.3)
            normal = shot(f'visual-repaint-{hold}', hold, False).get_pixels()
            before = state({'breath_reuse': True})['backdrop_reuses']
            for n in range(3):
                time.sleep(.3)
                if shot(f'visual-reuse-{hold}-{n}', hold, False).get_pixels() != normal:
                    reuse['identical'] = False; reuse['differing_shots'] += 1
            reuse['reuses'] += state()['backdrop_reuses']-before
        print(json.dumps({'visual_backdrop_reuse': reuse}), flush=True)
    state({'breath_hold': -1, 'breath_exact': False})
    (out/'visual.json').write_text(json.dumps({'keys': keys, 'comparisons': rows, 'damage': damage,
                                               'backdrop_reuse': reuse}, indent=2))
    attention(window, False)
    # Edge coverage may cross-fade over the sub-pixel step between keys; nothing else may move.
    # Measured on the RX 580: under 1,600 of four million pixels differ by 8 levels or more
    # (the shore, cross-fading over less than half a pixel) and under 30 by 16 or more
    # (single highlight pixels at window corners).
    # Packed GLES quantizes each cached surface to RGBA8. The measured strong
    # midpoint difference is still under 0.01% of the 4.1M-pixel output; keep a
    # slightly wider bound for that path while retaining the same broad-diff cap.
    strong_limit = 300 if state()['packed'] else 200
    assert all(r['changed_16_levels'] < strong_limit and r['changed_8_levels'] < 5000 for r in rows), rows
    assert damage['identical'] and tight_area < loose_area, damage
    assert reuse['identical'] and (reuse['reuses'] or 'backdrop_reuses' not in state()), reuse

try:
    changes = {'output:HEADLESS-1/mode': f'{round(2560*args.scale)}x{round(1600*args.scale)}@{args.refresh_hz*1000}',
               'output:HEADLESS-1/scale': args.scale}
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
    # Front to back at the end: focus, breather, two periphery windows, two rail widgets.
    geometry = {'idle-widget-a': (1500, 300, 700, 500), 'idle-widget-b': (1500, 900, 700, 500),
                'idle-left-a': (60, 120, 962, 1159), 'idle-left-b': (-360, 720, 1180, 780),
                'idle-breather': (1193, 73, 941, 940), 'idle-focus': (819, 312, 992, 1146)}
    for title in geometry:
        if title == 'idle-breather' and args.stream_hz:
            if args.full_redraw:
                code = (f'import time\nn=0\nwhile True:\n print(f"\\033]11;#{{32+(n%2)*6:02x}}2020\\007",end="",flush=True);'
                        f'n+=1;time.sleep({1/args.stream_hz!r})')
            else:
                code = (f'import time\nn=0\nwhile True:\n print(f"frame {{n:08d}} "*8,flush=True);'
                        f'n+=1;time.sleep({1/args.stream_hz!r})')
            command = ['python3', '-u', '-c', code]
        elif title == 'idle-widget-b' and args.title_hz:
            # Starts once the scene is built (lookups use the original title until then).
            code = (f'import os,time\nn=0\nwhile not os.path.exists({str(out/"retitle")!r}): time.sleep(.2)\n'
                    f'while True:\n print(f"\\033]0;idle-widget-b {{n}}\\007",end="",flush=True);'
                    f'n+=1;time.sleep({1/args.title_hz!r})' if args.title_hz else '')
            command = ['python3', '-u', '-c', code]
        else:
            command = ['sleep', '900']
        clients.append(subprocess.Popen(['foot', '-c', '/dev/null', '-o', 'resize-by-cells=no',
                                         '-T', title, *command],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                        start_new_session=True))
        time.sleep(.3)
    time.sleep(2)
    def place(title):
        x, y, w, h = geometry[title]
        ipc('window-rules/configure-view', {'id': view(title)['id'],
            'geometry': {'x': x, 'y': y, 'width': w, 'height': h}})
        time.sleep(.2)
    for title in ('idle-widget-a', 'idle-widget-b'):
        place(title)
    move('idle-widget-a', 2545, 780)
    move('idle-widget-b', 2545, 1000)
    for title in ('idle-left-a', 'idle-left-b', 'idle-breather', 'idle-focus'):
        place(title)
    # Real click: the larger center window takes focus and stacks over the breather.
    pointer(900, 1380); button('press'); button('release')
    pointer(1300, 1150)  # rest inside its content, away from any goo control
    time.sleep(1)
    widgets = ipc('scottland/widgets')['widgets']
    assert len(widgets) == 2, widgets
    breather = view('idle-breather')['id']
    assert not view('idle-breather')['frame']['focus'], 'the breathing window must not be focused'
    sleep_goo('initial scene')
    (out/'retitle').touch()
    if args.no_reuse:
        state({'breath_reuse': False})
    (out/'fixture.json').write_text(json.dumps({'settings': args.settings, 'args': vars(args) |
        {'session': str(session), 'artifacts': str(out), 'options': str(args.options) if args.options else None},
        'views': views(), 'widgets': widgets, 'state': state(), 'load': load_snapshot(),
        'outputs': ipc('window-rules/list-outputs')}, indent=2))
    results = {}
    modes = ('on', 'off') if args.breath_keys == 'both' else (args.breath_keys,)
    for mode in modes:
        ipc('wayfire/set-config-options', {'scottland/goo_breath_keys': mode == 'on'})
        time.sleep(.5)
        current = state()
        assert current and current['breath_keys_enabled'] == (mode == 'on'), (mode, current)
        results[f'none-keys-{mode}'] = measure(f'none-keys-{mode}')
        attention(breather, True)
        sleep_goo(f'window attention with breath keys {mode}')
        assert view('idle-breather')['frame']['attention'], 'attention was answered'
        results[f'window-keys-{mode}'] = measure(f'window-keys-{mode}')
        attention(breather, False)
        attention(widgets[0]['window'], True)
        sleep_goo(f'widget attention with breath keys {mode}')
        results[f'widget-keys-{mode}'] = measure(f'widget-keys-{mode}')
        attention(widgets[0]['window'], False)
        sleep_goo(f'answered with breath keys {mode}')
        if args.verify and mode == 'on':
            from goo_idle_checks import verify
            attention(breather, True)
            sleep_goo('GO17 large-window verifier')
            verify(ipc, out, args.scale)
            attention(breather, False)
            sleep_goo('GO17 verifier')

    if args.visual:
        # The visual comparison specifically needs both neighbouring cached keys.
        ipc('wayfire/set-config-options', {'scottland/goo_breath_keys': True})
        time.sleep(.5)
        visual(breather)

    if args.awake:
        # Switching goo back on (as a live on/off comparison does) wakes the simulation.
        # It runs at least three seconds; sample those, then time the rest of the settling.
        attention(breather, True)
        sleep_goo('attention before the awake sample')
        ipc('wayfire/set-config-options', {'scottland/goo': False})
        time.sleep(1)
        ipc('wayfire/set-config-options', {'scottland/goo': True})
        time.sleep(.3)
        saved, seconds = seconds, 2.5
        results['awake-window'] = measure('awake-window')
        seconds = saved
        sleep_goo('goo switched on with the window breathing')
        attention(breather, False)
        sleep_goo('answered after the awake sample')

    ipc('wayfire/set-config-options', {'scottland/goo': False})
    time.sleep(1)
    attention(breather, True)
    time.sleep(1)
    results['goo-off-window'] = measure('goo-off-window')
    if args.verify:
        from goo_idle_checks import verify_fallback
        verify_fallback(lambda: view('idle-breather'), out)
    attention(breather, False)
    time.sleep(1)
    ipc('wayfire/set-config-options', {'scottland/goo': True})
    sleep_goo('re-enable')
    ipc('wayfire/set-config-options', {'scottland/goo_breath_keys': True})
    attention(breather, False)
    sleep_goo('final reset')
    summary = {'keyframes_on': {k: results[f'{k}-keys-on']['compositor_gpu']
                                for k in ('window', 'widget') if f'{k}-keys-on' in results},
               'keyframes_off_exact': {k: results[f'{k}-keys-off']['compositor_gpu']
                                       for k in ('window', 'widget') if f'{k}-keys-off' in results}}
    record = {'keyframe_gpu_summary': summary, 'samples_seconds': seconds,
              'load': load_snapshot()}
    print(json.dumps(record), flush=True)
    with (out/'measurements.jsonl').open('a') as f:
        f.write(json.dumps(record)+'\n')
finally:
    for client in clients:
        try:
            os.killpg(client.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    for client in clients:
        try: client.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try: os.killpg(client.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            client.wait()
    sock.close()
