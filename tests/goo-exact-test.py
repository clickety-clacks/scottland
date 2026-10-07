#!/usr/bin/env python3
"""GO21: the cheap paths of a sleeping goo are pixel-exact at any scale, rotation and layout.

Run through tests/goo-exact-test.sh in a private headless session on the test machine:
  goo-exact-test.py SESSION ARTIFACTS [--scale S] [--rotation normal|90|180|270] [--outputs 1|2]

Frames are read from the compositor as it renders them on its own (layout-state's
capture_next_frame), not through a screenshot request, which can force a full repaint and
hide a mistake in a partially damaged frame. Each comparison switches one optimization off
and on in the same settled scene, at held breath values, in keyframe and exact modes:

  reuse     a breath drawn over the cached backdrop      == the scene repainted beneath it
  dry       window content left out of the goo's regions == included
  settled   bands shrunk to the liquid in them           == conservative bands
  cell      one terminal cell changing under a strip (damage wholly inside the strips)
  update    a line of terminal text changing under the film

With --outputs 2 the scene's output sits at a non-zero layout position beside a second
output whose terminal redraws continuously; reuse must stay active and exact.
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
ap.add_argument('--scale', type=float, default=1)
ap.add_argument('--rotation', choices=('normal', '90', '180', '270'), default='normal')
ap.add_argument('--outputs', type=int, choices=(1, 2), default=1)
ap.add_argument('--negative-control', action='store_true',
                help='make the goo ignore other damage: the cell check must then FAIL (proves the test can)')
args = ap.parse_args()
root = Path(__file__).resolve().parents[1]
out = args.artifacts.resolve()
out.mkdir(parents=True, exist_ok=True)
assert os.environ.get('SCOTTLAND_TEST_MODEL') == '1', 'private headless session required'
frame_file = Path(os.environ['SCOTTLAND_TEST_STATE'])/'render-frame.ppm'
sock = socket.socket(socket.AF_UNIX)
sock.connect(os.environ['WAYFIRE_SOCKET'])
OUTPUT = 'HEADLESS-1'
W, H = (1000, 1600) if args.rotation in ('90', '270') else (1600, 1000)
offset = 800 if args.outputs == 2 else 0   # the scene output's layout x

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

def views(): return ipc('scottland/layout-state')['views']
def view(title): return next(v for v in views() if v['title'] == title)
def state(data=None):
    return next(s for s in ipc('scottland/goo-state', data)['screens'] if s['output'] == OUTPUT)
def pointer(x, y): ipc('stipc/move_cursor', {'x': round(x+offset), 'y': round(y)})
def click(x, y):
    pointer(x, y)
    for mode in ('press', 'release'): ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': mode})
def settle(what):
    time.sleep(1)
    for _ in range(600):
        s = state()
        if s['sleeping'] and not s.get('breath_loose'): return
        time.sleep(.1)
    raise AssertionError('goo did not settle: ' + what + ' ' + json.dumps(state()))

checks = []
def check(name, ok, detail=None):
    checks.append({'check': name, 'ok': bool(ok), 'detail': detail})
    print(('PASS ' if ok else 'FAIL ') + name + (' ' + json.dumps(detail) if detail and not ok else ''), flush=True)

def frame(label, reused=None):
    """The next frame the scene output renders by itself. With `reused`, insist every goo
    draw in the captured interval did (True) or did not (False) reuse the backdrop."""
    for _ in range(30):
        before = state()
        count = ipc('scottland/layout-state', {'capture_next_frame': OUTPUT})['captured_frames']
        for _ in range(300):
            if ipc('scottland/layout-state')['captured_frames'] > count: break
            time.sleep(.01)
        else:
            raise AssertionError('no frame rendered to capture: ' + label)
        pixels = frame_file.read_bytes()
        after = state()
        draws, reuses = after['draws']-before['draws'], after['backdrop_reuses']-before['backdrop_reuses']
        if reused is None or (reused and draws and reuses == draws) or (not reused and not reuses):
            return pixels
        time.sleep(.05)
    raise AssertionError(f'never captured a frame with reuse={reused}: {label}: ' +
                         json.dumps({k: state().get(k) for k in ('reuse_blocked', 'draws', 'backdrop_reuses', 'sleeping', 'breath_loose')}))

def differing(a, b, name=None):
    if a == b: return 0
    header = a.index(b'\n255\n')+5
    if name:  # keep only frames that disagree
        (out/(name.replace(' ', '-')+'-a.ppm')).write_bytes(a); (out/(name.replace(' ', '-')+'-b.ppm')).write_bytes(b)
    return sum(a[i:i+3] != b[i:i+3] for i in range(header, min(len(a), len(b)), 3)) or 1
def same(name, a, b):
    n = differing(a, b, name)
    check(name, n == 0, {'differing_pixels': n})

clients = []
def spawn(title, code):
    clients.append(subprocess.Popen(['foot', '-c', '/dev/null', '-o', 'resize-by-cells=no', '-T', title,
                                     'python3', '-u', '-c', code],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
    time.sleep(.5)
    return clients[-1]

try:
    # The mode is the unrotated panel; the transform turns it into the logical W x H.
    changes = {f'output:{OUTPUT}/mode': f'{round(1600*args.scale)}x{round(1000*args.scale)}@60000',
               f'output:{OUTPUT}/scale': args.scale, f'output:{OUTPUT}/transform': args.rotation}
    if args.outputs == 2:
        changes.update({'output:HEADLESS-2/mode': '800x600@60000', 'output:HEADLESS-2/position': '0, 0',
                        f'output:{OUTPUT}/position': f'{offset}, 0'})
    ipc('wayfire/set-config-options', changes)
    time.sleep(1.5)
    outputs = ipc('window-rules/list-outputs')
    (out/'outputs.json').write_text(json.dumps(outputs, indent=2))
    scene_output = next(o for o in outputs if o['name'] == OUTPUT)
    check('scene output has the requested logical size and position',
          (scene_output['geometry']['width'], scene_output['geometry']['height'], scene_output['geometry']['x'])
          == (W, H, offset), scene_output['geometry'])
    with (out/'wallpaper.log').open('w') as f:
        clients.append(subprocess.Popen(['quickshell', '-p', str(root/'tests/GooWallpaper.qml')],
                                        stdout=f, stderr=subprocess.STDOUT, start_new_session=True))
    time.sleep(1)
    click(W*.5, H*.97)   # new windows open on the output last used: make it the scene's
    time.sleep(.3)
    cell = out/'cell'
    # The back terminal: static text; a line that changes on SIGUSR1; one cell that
    # toggles each time the `cell` file changes, at the fraction of the window it names.
    under = spawn('exact-under', f'''
import os, signal, sys, time
print("\\033[?25l" + "\\n".join(f"line {{i:03d}} settled goo over terminal text "*3 for i in range(60)), end="", flush=True)
n = [0]
def line(*_):
    n[0] += 1
    print(f"\\033[9;1Htext update {{n[0]:08d}} "*4, end="", flush=True)
signal.signal(signal.SIGUSR1, line)
seen = None
while True:
    try: fx, fy, mark = open({str(cell)!r}).read().split()
    except (OSError, ValueError): time.sleep(.05); continue
    if (fx, fy, mark) != seen:
        seen = (fx, fy, mark)
        size = os.get_terminal_size()
        print(f"\\033[{{1+int(float(fy)*size.lines)}};{{1+int(float(fx)*size.columns)}}H{{mark}}", end="", flush=True)
    time.sleep(.03)
''')
    spawn('exact-breather', 'import time\ntime.sleep(900)')
    spawn('exact-focus', 'import time\ntime.sleep(900)')
    if args.outputs == 2:
        spawn('exact-other', 'import time\nn=0\nwhile True:\n print(f"other output {n:08d} "*6, flush=True); n+=1; time.sleep(.05)')
    time.sleep(1.5)
    # Fractions of the logical output, so the same scene fits either orientation.
    geometry = {'exact-under': (.08, .12, .62, .70), 'exact-breather': (.34, .22, .50, .56),
                'exact-focus': (.56, .50, .36, .40)}
    for title, (x, y, w, h) in geometry.items():
        ipc('window-rules/configure-view', {'id': view(title)['id'], 'geometry':
            {'x': round(x*W)+.0, 'y': round(y*H), 'width': round(w*W), 'height': round(h*H)}})
        time.sleep(.3)
    if args.outputs == 2:
        other = next(o for o in outputs if o['name'] == 'HEADLESS-2')
        ipc('window-rules/configure-view', {'id': view('exact-other')['id'], 'output_id': other['id'],
            'geometry': {'x': 100, 'y': 80, 'width': 560, 'height': 400}})
        time.sleep(.5)
    # Real clicks: the breather above the back terminal, then the front window focused.
    # (The layout may scale windows away from the center: use the frames it reports.)
    f = view('exact-breather')['frame']
    click(f['x']+20, f['y']+20); time.sleep(.3)
    f = view('exact-focus')['frame']
    click(f['x']+f['width']*.8, f['y']+f['height']*.85)  # below the breather; rests inside its content
    time.sleep(1)
    breather = view('exact-breather')
    check('breathing window is not focused', not breather['frame']['focus'])
    ipc('scottland/attention', {'window': breather['id'], 'attention': True, 'source': 'exact-test'})
    settle('attention')
    s = state()
    check('goo overlaps and sleeps with breathing strips', s['overlapping'] and s['breath_damage'], s['breath_damage'])
    (out/'fixture.json').write_text(json.dumps({'args': {k: str(v) for k, v in vars(args).items()},
                                                'views': views(), 'state': s}, indent=2))
    subprocess.run(['grim', str(out/'scene.png')])   # for looking at; not compared

    # GO24: the watercolor keeps repainting the settled liquid but with the dye held
    # still, so frames taken moments apart can be compared; reuse then covers all of it.
    def keep_watercolor_ticking():
        state({'water_freeze': True, 'water_coast': 3600})
    keep_watercolor_ticking()
    if args.negative_control:
        state({'reuse_deaf': True})
    b, u = breather['frame'], view('exact-under')['frame']
    # A cell of the back terminal just left of the breather's edge: under its film, inside the strips.
    fx, fy = (b['x']-8-u['x'])/u['width'], (b['y']+b['height']*.5-u['y'])/u['height']
    for keys in (True, False):
        ipc('wayfire/set-config-options', {'scottland/goo_breath_keys': keys})
        settle(f'keys {keys}')
        mode = 'keys' if keys else 'exact'
        for hold in (0., .37, 1.):
            tag = f'{mode}-{hold}'
            state({'breath_hold': hold, 'breath_reuse': False, 'dry_content': True, 'breath_tight': True})
            settle(tag)
            keep_watercolor_ticking(); time.sleep(.3)
            fresh = frame(f'{tag}-repainted', reused=False)
            state({'breath_reuse': True}); time.sleep(.2)
            same(f'reuse {tag}', fresh, frame(f'{tag}-reused', reused=True))
            state({'dry_content': False}); time.sleep(.3)
            same(f'dry {tag}', fresh, frame(f'{tag}-not-dry'))
            state({'dry_content': True, 'breath_tight': False}); time.sleep(.5)
            loose = frame(f'{tag}-loose')
            same(f'settled {tag}', fresh, loose)
            state({'breath_tight': True})
            settle(tag + ' tight again')
            keep_watercolor_ticking()
        # Content changing under the strips while breaths reuse the backdrop.
        state({'breath_hold': .37, 'breath_reuse': True})
        for mark in ('#', '.'):
            cell.write_text(f'{fx} {fy} {mark}')
            time.sleep(.25)   # well inside the one-second refresh that would hide a stale backdrop
            cached = frame(f'{mode}-cell-{ord(mark)}-reused')
            state({'breath_reuse': False}); time.sleep(.3)
            same(f'cell under a strip {mode} {ord(mark)}', cached, frame(f'{mode}-cell-{ord(mark)}-repainted', reused=False))
            state({'breath_reuse': True}); time.sleep(.2)
        os.kill(under.pid, signal.SIGUSR1)
        time.sleep(.25)
        cached = frame(f'{mode}-update-reused')
        state({'breath_reuse': False}); time.sleep(.3)
        same(f'text update under the film {mode}', cached, frame(f'{mode}-update-repainted', reused=False))
        state({'breath_reuse': True})
    if args.outputs == 2:
        before = state(); time.sleep(2); after = state()
        draws, reuses = after['draws']-before['draws'], after['backdrop_reuses']-before['backdrop_reuses']
        check('the other output redrawing does not stop backdrop reuse here', reuses >= .8*draws > 0,
              {'draws': draws, 'reuses': reuses})
    s = state()
    keep_watercolor_ticking(); time.sleep(.5)
    s = state()
    if s.get('motion_pixels') and s.get('open_pickup', True):   # a wallpaper on this output and soak on
        before = s; reasons = {}
        for _ in range(40):
            r = state()['reuse_blocked']; reasons[r] = reasons.get(r, 0)+1; time.sleep(.05)
        after = state()
        check('watercolor ticks repaint the settled liquid from the reused backdrop',
              after['water_ticks'] > before['water_ticks'] and
              after['backdrop_reuses']-before['backdrop_reuses'] >= .7*(after['draws']-before['draws']),
              {k: after[k]-before[k] for k in ('water_ticks', 'draws', 'backdrop_reuses')} | {'blocked': reasons})
        state({'water_freeze': False, 'water_coast': 30}); time.sleep(.5)
        # The breather's right edge lies over open wallpaper, above the front window.
        points = [(b['x']+b['width']+6, b['y']+20+k*25) for k in range(6)]
        a = [state({'x': x, 'y': y}) for x, y in points]
        time.sleep(4)
        c = [state({'x': x, 'y': y}) for x, y in points]
        moved = max(abs(x[ch]-y[ch]) for x, y in zip(a, c) for ch in ('red', 'green', 'blue'))
        check('the dye moves while the goo sleeps', moved > .005 and state()['sleeping'] and
              state()['steps'] == a[0]['steps'], {'largest change': moved})
    state({'breath_hold': -1, 'breath_reuse': True, 'dry_content': True, 'breath_tight': True})
    (out/'checks.json').write_text(json.dumps(checks, indent=2))
    failed = [c for c in checks if not c['ok']]
    print(f'RESULT {len(checks)-len(failed)} passed, {len(failed)} failed', flush=True)
    if args.negative_control:
        caught = [c for c in failed if c['check'].startswith('cell under a strip')]
        print('NEGATIVE CONTROL ' + ('caught the stale backdrop' if caught else 'DID NOT catch a stale backdrop'), flush=True)
        sys.exit(0 if caught else 1)
    sys.exit(1 if failed else 0)
finally:
    for client in clients:
        try: os.killpg(client.pid, signal.SIGTERM)
        except ProcessLookupError: pass
    for client in clients:
        try: client.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try: os.killpg(client.pid, signal.SIGKILL)
            except ProcessLookupError: pass
    sock.close()
