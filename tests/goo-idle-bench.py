#!/usr/bin/env python3
"""Idle-desktop breathing fixture: run via headless.sh run with a private --widgets session.

Args: headless directory, artifact directory, seconds per case (default 5), optional --visual
and --options FILE (a JSON object of option values to apply in the test session first,
e.g. someone's own goo settings: wider goo means more pixels in the breathing strips).

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
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time

root = Path(__file__).resolve().parents[1]
session = Path(sys.argv[1])
out = Path(sys.argv[2])
seconds = float(sys.argv[3]) if len(sys.argv) > 3 and not sys.argv[3].startswith('--') else 5
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
    for _ in range(600):
        s = state()
        if s and s['sleeping']: return
        time.sleep(.1)
    raise AssertionError('goo failed to sleep: ' + what)

pid = int((session/'pid').read_text())
for child in Path(f'/proc/{pid}/task/{pid}/children').read_text().split():
    if Path(f'/proc/{child}/comm').read_text().strip() == 'wayfire': pid = int(child); break

clients = []
def measure(label):
    before = state()
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
    def delta(name):
        return after[name]-before[name] if before and after and name in after and name in before else None
    result = {'case': label, 'gpu': gpu_output,
              'compositor_gpu': float(gpu_output.split('compositor GPU')[1].split('%')[0]),
              'steps': delta('steps'), 'sleeping': after['sleeping'] if after else None,
              'draw_gpu_ms_median': sorted(draws)[len(draws)//2] if draws and delta('breath_ticks') else None,
              'breath_ticks': delta('breath_ticks'),
              # Full-shader refreshes of the breathing strips (absent before keyframes: one per tick).
              'breath_refreshes': delta('breath_refreshes'),
              'breath_keys': after.get('breath_keys') if after else None,
              'breath_area': sum(r['width']*r['height'] for r in after['breath_damage']) if after else None,
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
        area = sum(r['width']*r['height'] for r in state()['breath_damage'])
        return area, shot(f'visual-{"tight" if tight else "loose"}-peak', 1, False).get_pixels()
    loose_area, loose = step(False)
    tight_area, tight = step(True)
    damage = {'loose_area': loose_area, 'tight_area': tight_area, 'identical': loose == tight}
    print(json.dumps({'visual_damage': damage}), flush=True)
    state({'breath_hold': -1, 'breath_exact': False})
    (out/'visual.json').write_text(json.dumps({'keys': keys, 'comparisons': rows, 'damage': damage}, indent=2))
    attention(window, False)
    # Edge coverage may cross-fade over the sub-pixel step between keys; nothing else may move.
    # Measured on the RX 580: under 1,600 of four million pixels differ by 8 levels or more
    # (the shore, cross-fading over less than half a pixel) and under 30 by 16 or more
    # (single highlight pixels at window corners).
    assert all(r['changed_16_levels'] < 200 and r['changed_8_levels'] < 5000 for r in rows), rows
    assert damage['identical'] and tight_area < loose_area, damage

try:
    ipc('wayfire/set-config-options', {'output:HEADLESS-1/mode': '2560x1600@60000'})
    time.sleep(1)
    if '--options' in sys.argv:
        ipc('wayfire/set-config-options', json.loads(Path(sys.argv[sys.argv.index('--options')+1]).read_text()))
    with (out/'wallpaper.log').open('w') as f:
        clients.append(subprocess.Popen(['quickshell', '-p', str(root/'tests/GooWallpaper.qml')],
                                        stdout=f, stderr=subprocess.STDOUT))
    # Front to back at the end: focus, breather, two periphery windows, two rail widgets.
    geometry = {'idle-widget-a': (1500, 300, 700, 500), 'idle-widget-b': (1500, 900, 700, 500),
                'idle-left-a': (60, 120, 962, 1159), 'idle-left-b': (-360, 720, 1180, 780),
                'idle-breather': (1193, 73, 941, 940), 'idle-focus': (819, 312, 992, 1146)}
    for title in geometry:
        clients.append(subprocess.Popen(['foot', '-c', '/dev/null', '-T', title, 'sleep', '900'],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
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
    (out/'fixture.json').write_text(json.dumps({'views': views(), 'widgets': widgets,
                                                'state': state()}, indent=2))
    results = {}
    def cases(goo):
        results[f'none-{goo}'] = measure(f'none-{goo}')
        attention(breather, True)
        if goo == 'on': sleep_goo('window attention')
        else: time.sleep(2)
        assert view('idle-breather')['frame']['attention'], 'attention was answered'
        results[f'window-{goo}'] = measure(f'window-{goo}')
        attention(breather, False)
        attention(widgets[0]['window'], True)
        if goo == 'on': sleep_goo('widget attention')
        else: time.sleep(2)
        results[f'widget-{goo}'] = measure(f'widget-{goo}')
        attention(widgets[0]['window'], False)
        if goo == 'on': sleep_goo('answered')
        else: time.sleep(2)
    cases('on')
    ipc('wayfire/set-config-options', {'scottland/goo': False})
    time.sleep(3)
    cases('off')
    ipc('wayfire/set-config-options', {'scottland/goo': True})
    sleep_goo('re-enable')
    summary = {k: round(results[f'{k}-on']['compositor_gpu']-results[f'{k}-off']['compositor_gpu'], 2)
               for k in ('none', 'window', 'widget')}
    print(json.dumps({'goo_increment_points': summary}), flush=True)
    with (out/'measurements.jsonl').open('a') as f:
        f.write(json.dumps({'goo_increment_points': summary})+'\n')
    if '--visual' in sys.argv:
        visual(breather)
finally:
    for client in clients:
        client.terminate()
    for client in clients:
        try: client.wait(timeout=3)
        except subprocess.TimeoutExpired: client.kill()
