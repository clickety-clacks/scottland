#!/usr/bin/env python3
"""Grabbing a peeking window (peek-strip decision 9, WK27): run inside an isolated headless session
with goo and always-on window avoidance. A back window completely covered by a front one peeks;
each drag path grabs it on its visible strip. Throughout the drag its goo island, its drawn pixels
and the grabbed point agree, and on release it stays where it was drawn, in its zone at its scale.
   grab-peek-test.py ARTIFACT_DIR [SCENARIO ...] [--mode MODE ...]"""
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time

args = sys.argv[1:]
out = Path(args.pop(0)); out.mkdir(parents=True, exist_ok=True)
modes = ['super', 'halo', 'pointer-shift', 'touch', 'halo-touch', 'swipe', 'client']
if '--mode' in args:
    at = args.index('--mode'); modes = args[at + 1:]; args = args[:at]
scenarios = args or ['periphery', 'center', 'periphery-side', 'center-side', 'periphery-control']
sock = socket.socket(socket.AF_UNIX); sock.connect(os.environ['WAYFIRE_SOCKET']); sock.settimeout(8)


def read(n):
    b = b''
    while len(b) < n:
        chunk = sock.recv(n - len(b))
        if not chunk: raise RuntimeError('compositor disconnected')
        b += chunk
    return b


def ipc(method, data=None):
    b = json.dumps({'method': method, 'data': data or {}}).encode()
    sock.sendall(struct.pack('<I', len(b)) + b)
    result = json.loads(read(struct.unpack('<I', read(4))[0]))
    if isinstance(result, dict) and 'error' in result: raise RuntimeError(result)
    return result


passes = failures = 0
report = []


def check(name, ok, detail=None):
    global passes, failures
    passes += bool(ok); failures += not ok
    print(('PASS ' if ok else 'FAIL ') + name + ('' if ok or detail is None else ': ' + json.dumps(detail)), flush=True)


def wait_for(fn, timeout=6):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = fn()
        if value: return value
        time.sleep(.03)
    raise AssertionError('timed out waiting for ' + getattr(fn, '__name__', repr(fn)))


def pointer(x, y): ipc('stipc/move_cursor', {'x': round(x), 'y': round(y)})
def key(name, down): ipc('stipc/feed_key', {'key': 'KEY_' + name, 'state': down})
def button(down): ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'press' if down else 'release'})


def view(title): return next((v for v in ipc('scottland/layout-state')['views'] if v['title'] == title), None)
def hint(window): return next((h for h in ipc('scottland/hints')['hints'] if h['window'] == window), None)
def geometry(window): return next(v['geometry'] for v in ipc('window-rules/list-views') if v['id'] == window)


def rect(r): return [r['x'], r['y'], r['x'] + r['width'], r['y'] + r['height']]
def center(r): return ((r[0] + r[2]) / 2, (r[1] + r[3]) / 2)
def apart(a, b): return max(abs(p - q) for p, q in zip(a, b))


def goo_rect(window):
    for screen in ipc('scottland/goo-state').get('screens', []):
        for source in screen.get('source_rects', []):
            if source['id'] == window and not source['hint_circle']: return rect(source)
    return None


def pixels(name, color):
    """Bounding box of the pixels in this exact window color, from a real screenshot."""
    path = out / f'{name}.png'
    subprocess.run(['grim', str(path)], check=True)
    box = subprocess.check_output(['magick', str(path), '-alpha', 'off', '-fuzz', '4%', '-fill', 'black',
                                   '+opaque', '#' + color, '-fuzz', '0', '-format', '%@', 'info:'], text=True)
    size, x, y = box.split('+'); w, h = size.split('x')
    return None if int(w) <= 1 and int(h) <= 1 else [int(x), int(y), int(x) + int(w), int(y) + int(h)]


def cli(*command):
    ctl = Path(__file__).resolve().parents[1] / 'core/libexec/scottland-ctl'
    return subprocess.check_output([str(ctl), *command], text=True, timeout=8)


children = []
def launch(title, color, w, h):
    children.append(subprocess.Popen([sys.executable, str(Path(__file__).with_name('grab-peek-app.py')),
                                      title, color, str(w), str(h)]))
    return wait_for(lambda: view(title), 10)['id']


BACK, FRONT = '2050f0', 'e03020'
try:
    cli('set', 'window_avoidance_always', 'true')
    output = ipc('window-rules/list-outputs')[0]['geometry']
    W, H = output['width'], output['height']
    back = launch('grab-peek-back', BACK, 520, 360)
    front = launch('grab-peek-front', FRONT, 760, 540)
    time.sleep(.6)

    def stage(cx, control=False, side=False):
        # Fixture geometry only (configure-view is setup, not the input under test): the same center
        # puts both in one zone at one scale, so the larger front window covers the back one.
        # The back one sits a little higher, so it peeks upward (decision 8) through its top edge;
        # a side case sits it a little left instead, so it peeks leftward. A control puts the front
        # window on the other side: nothing covers the back one.
        for window, (w, h, up) in ((back, (520, 360, 0 if side else 30)), (front, (760, 540, 0))):
            at = W * .85 if control and window == front else cx - (40 if side and window == back else 0)
            ipc('window-rules/configure-view', {'id': window, 'geometry': {
                'x': round(output['x'] + at - w / 2), 'y': round(output['y'] + H / 2 - h / 2 - up),
                'width': w, 'height': h}})
        ipc('window-rules/focus-view', {'id': front})
        if control:
            time.sleep(1)
            return hint(back)
        def peeking():
            row = hint(back)
            return row and abs(row['dx']) + abs(row['dy']) > 8 and \
                abs(row['dx'] - row['target_dx']) + abs(row['dy'] - row['target_dy']) < .2
        try:
            wait_for(peeking, 8)
        except AssertionError:
            return None
        time.sleep(.3)
        return hint(back)

    def strip(shown, cover):
        """The largest strip of `shown` outside `cover`: (side, grab point)."""
        cands = []
        if shown[1] < cover[1]: cands.append((cover[1] - shown[1], 'top', ((shown[0] + shown[2]) / 2, (shown[1] + cover[1]) / 2)))
        if shown[3] > cover[3]: cands.append((shown[3] - cover[3], 'bottom', ((shown[0] + shown[2]) / 2, (cover[3] + shown[3]) / 2)))
        if shown[0] < cover[0]: cands.append((cover[0] - shown[0], 'left', ((shown[0] + cover[0]) / 2, (shown[1] + shown[3]) / 2)))
        if shown[2] > cover[2]: cands.append((shown[2] - cover[2], 'right', ((cover[2] + shown[2]) / 2, (shown[1] + shown[3]) / 2)))
        cands.sort(reverse=True)
        return cands[0][1:] if cands else (None, None)

    for scenario in scenarios:
        control, side = scenario.endswith('-control'), scenario.endswith('-side')
        # Sideways in the right periphery: the leftward peek heads toward the center zone.
        cx = W * ((.8 if side else .2) if scenario.startswith('periphery') else .5)
        for mode in modes:
            label = f'{scenario}-{mode}'
            time.sleep(2.7)  # no re-grab chain with the previous case
            peek = stage(cx, control, side)
            if not peek:
                check(f'{label}: the covered window peeks', False, hint(back)); continue
            before_view, front_view = view('grab-peek-back'), view('grab-peek-front')
            shown = rect(before_view['scene_frame']); cover = rect(front_view['scene_frame'])
            true_before = rect(before_view['frame'])
            case_side, (gx, gy) = ('top', ((shown[0] + shown[2]) / 2, shown[1] + 25)) if control else strip(shown, cover)
            if case_side is None:
                check(f'{label}: the covered window has a visible strip', False, {'shown': shown, 'cover': cover}); continue
            if mode in ('halo', 'halo-touch'):
                gx, gy = {'top': (gx, shown[1] - 5), 'bottom': (gx, shown[3] + 5),
                          'left': (shown[0] - 5, gy), 'right': (shown[2] + 5, gy)}[case_side]
            if mode == 'client':
                if case_side != 'top':
                    print(f'SKIP {label}: strip is on the {case_side}, the client handle is its top 35 px', flush=True); continue
                gy = shown[1] + 6
            case = {'case': label, 'side': case_side, 'grab': [gx, gy], 'peek': [peek['dx'], peek['dy']],
                    'shown_before': shown, 'true_frame_before': true_before, 'cover': cover,
                    'zone_before': before_view['zone'], 'scale_before': before_view['applied_scale'], 'samples': []}
            pointer(gx, gy)
            if mode in ('touch', 'halo-touch'):
                ipc('stipc/touch', {'finger': 0, 'x': round(gx), 'y': round(gy)}); time.sleep(.6)
            elif mode == 'swipe':
                ipc('scottland/test-input', {'swipe': 'begin', 'fingers': 3})
            else:
                if mode == 'super': key('LEFTMETA', True)
                if mode == 'pointer-shift': key('LEFTMETA', True); key('LEFTSHIFT', True)
                button(True)
            time.sleep(.15)
            grabbed = 'scottland/test-input'
            dragging = ipc(grabbed).get('dragging')
            check(f'{label}: the grab starts a drag', bool(dragging), ipc(grabbed))
            # Toward the screen center, never toward a rail (no widgetizing).
            # Periphery drags rescale on the way. The right-periphery side case goes outward, away
            # from the center zone's edge (the user may cross it; this checks that the grab doesn't).
            step = ((8 if side else 14) if scenario.startswith('periphery') else 10, 9)
            px, py = gx, gy
            for i in range(7):
                if i:
                    px, py = px + step[0], py + step[1]
                    if mode in ('touch', 'halo-touch'): ipc('stipc/touch', {'finger': 0, 'x': round(px), 'y': round(py)})
                    elif mode == 'swipe': ipc('scottland/test-input', {'swipe': 'update', 'dx': step[0], 'dy': step[1]})
                    else: pointer(px, py)
                time.sleep(.2)
                drawn = pixels(f'{label}-{i}', BACK)
                v = view('grab-peek-back'); h = hint(back)
                case['samples'].append({'pointer': [px, py], 'pixels': drawn, 'goo': goo_rect(back),
                                        'scene_frame': rect(v['scene_frame']), 'frame': rect(v['frame']),
                                        'geometry': geometry(back), 'offset': [h['dx'], h['dy']],
                                        'zone': v['zone'], 'applied_scale': v['applied_scale']})
            if mode in ('touch', 'halo-touch'): ipc('stipc/touch_release', {'finger': 0})
            elif mode == 'swipe': ipc('scottland/test-input', {'swipe': 'end'})
            else:
                button(False)
                if mode == 'pointer-shift': key('LEFTSHIFT', False)
                if mode in ('super', 'pointer-shift'): key('LEFTMETA', False)
            time.sleep(1.2)
            v = view('grab-peek-back'); h = hint(back)
            case['after'] = {'pixels': pixels(f'{label}-after', BACK), 'goo': goo_rect(back),
                             'scene_frame': rect(v['scene_frame']), 'frame': rect(v['frame']),
                             'offset': [h['dx'], h['dy']], 'zone': v['zone'], 'applied_scale': v['applied_scale']}
            report.append(case)
            s = case['samples']
            first = s[0]
            # At the grab: drawn where it peeked (scene rectangle before grab), raised so fully visible.
            check(f'{label}: at the grab it stays exactly where it was drawn',
                  first['pixels'] and apart(center(first['pixels']), center(shown)) <= 2.5,
                  {'pixels': first['pixels'], 'shown_before': shown})
            check(f'{label}: grabbing keeps its zone and scale (P13)',
                  first['zone'] == case['zone_before'] and abs(first['applied_scale'] - case['scale_before']) < .01,
                  {'zone': [case['zone_before'], first['zone']], 'scale': [case['scale_before'], first['applied_scale']]})
            worst_goo = max((apart(center(x['goo']), center(x['pixels'])) if x['goo'] and x['pixels'] else 1e9) for x in s)
            check(f'{label}: goo stays on the window throughout the drag', worst_goo <= 2.5,
                  [{'goo': x['goo'], 'pixels': x['pixels']} for x in s])
            size = lambda r: (r[2] - r[0], r[3] - r[1])
            worst_scene = max((max(apart(center(x['scene_frame']), center(x['pixels'])),
                                   apart(size(x['scene_frame']), size(x['pixels'])) / 2) if x['pixels'] else 1e9) for x in s)
            check(f'{label}: its reported scene rectangle is where it is drawn', worst_scene <= 2.5,
                  [{'scene': x['scene_frame'], 'pixels': x['pixels']} for x in s])
            worst_offset = max(abs(x['offset'][0]) + abs(x['offset'][1]) for x in s)
            check(f'{label}: the avoidance offset became its real position at the grab', worst_offset < .5,
                  [x['offset'] for x in s])
            if s[0]['pixels']:
                # As a fraction of the drawn window: it rescales as it moves through the periphery.
                rel = lambda x: ((x['pointer'][0] - x['pixels'][0]) / (x['pixels'][2] - x['pixels'][0]),
                                 (x['pointer'][1] - x['pixels'][1]) / (x['pixels'][3] - x['pixels'][1]))
                drift = max(apart(rel(x), rel(s[0])) for x in s if x['pixels'])
                # In the periphery the fraction is kept on the box with its fixed halo margin while the
                # window rescales, so it creeps a little: the uncovered control shows the same ~.016.
                check(f'{label}: the grabbed point stays under the input',
                      drift <= (.03 if scenario.startswith('periphery') else .012),
                      [rel(x) for x in s if x['pixels']])
            a = case['after']
            check(f'{label}: on release it stays where it was drawn, goo on it',
                  a['pixels'] and s[-1]['pixels'] and apart(center(a['pixels']), center(s[-1]['pixels'])) <= 2.5 and
                  a['goo'] and apart(center(a['goo']), center(a['pixels'])) <= 2.5 and
                  abs(a['offset'][0]) + abs(a['offset'][1]) < .5,
                  {'last': s[-1]['pixels'], 'after': a['pixels'], 'goo': a['goo'], 'offset': a['offset']})
            check(f'{label}: it ends in the zone it was in', a['zone'] == case['zone_before'],
                  [case['zone_before'], a['zone']])
    # The exception (decision 9): selecting a peeking window by tapping its hint in Window mode
    # brings it to its true position as it is raised and focused.
    for scenario in [s for s in scenarios if s in ('periphery', 'center')]:
        cx = W * (.2 if scenario == 'periphery' else .5)
        label = f'{scenario}-hint-tap'
        time.sleep(1)
        peek = stage(cx)
        if not peek:
            check(f'{label}: the covered window peeks', False, hint(back)); continue
        true_before = rect(view('grab-peek-back')['frame'])
        key('LEFTALT', True)
        wait_for(lambda: ipc('scottland/hints')['active'])
        for letter in hint(back)['hint']:
            key(letter.upper(), True); key(letter.upper(), False)
        key('LEFTALT', False)
        wait_for(lambda: not ipc('scottland/hints')['active'])
        try:
            wait_for(lambda: abs(hint(back)['dx']) + abs(hint(back)['dy']) < .2, 4)
        except AssertionError:
            pass
        time.sleep(.3)
        v = view('grab-peek-back'); h = hint(back); drawn = pixels(label, BACK)
        focused = ipc('window-rules/get-focused-view')['info']['id']
        check(f'{label}: tapping its hint brings it to its true position, raised and focused',
              focused == back and abs(h['dx']) + abs(h['dy']) < .5 and apart(rect(v['frame']), true_before) <= 1 and
              drawn and apart(center(drawn), center(true_before)) <= 2.5,
              {'focused': focused, 'offset': [h['dx'], h['dy']], 'frame': rect(v['frame']),
               'true_before': true_before, 'pixels': drawn})
finally:
    (out / 'results.json').write_text(json.dumps(report, indent=2))
    for child in children:
        child.terminate()
        try: child.wait(timeout=5)
        except subprocess.TimeoutExpired: child.kill()
print(f'{passes} passed, {failures} failed', flush=True)
sys.exit(1 if failures else 0)
