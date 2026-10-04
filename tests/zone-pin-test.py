#!/usr/bin/env python3
"""WP1/WP5: a zone memory keeps the window's Shift scale pin (L31) there, with real stipc input.

Shift-drag a window in the periphery to a scale that isn't that spot's natural one, cycle it to
the center and back with Window-mode hints, and check the remembered position and the pinned
scale both come back, drawn without a jump; the same without a pin; a pin made in one zone never
reaching another; and the pins surviving a reload."""
import json
import math
import os
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

artifacts = Path(sys.argv[1])
passed = failed = 0
clients = []
request_path = None


def ipc(method, data=None):
    global request_path
    if request_path is None:
        # The headless session's own recorded socket, never the caller's desktop.
        request_path = subprocess.check_output(['tests/headless.sh', 'run', 'python3', '-c',
            "import os; print(os.environ['WAYFIRE_SOCKET'])"], text=True).strip()
    with socket.socket(socket.AF_UNIX) as request:
        request.settimeout(10)
        request.connect(request_path)

        def receive(n):
            data = b''
            while len(data) < n:
                chunk = request.recv(n - len(data))
                if not chunk: raise ConnectionError('compositor disconnected')
                data += chunk
            return data
        body = json.dumps({'method': method, 'data': data or {}}).encode()
        request.sendall(struct.pack('<I', len(body)) + body)
        return json.loads(receive(struct.unpack('<I', receive(4))[0]))


def check(ok, name, detail=None):
    global passed, failed
    print(('PASS  ' if ok else 'FAIL  ') + name + ('' if ok or detail is None else f'  ({detail})'), flush=True)
    passed += bool(ok)
    failed += not ok


def wait_for(predicate, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        result = predicate()
        if result: return result
        time.sleep(.05)
    raise RuntimeError('timed out waiting for compositor/client state')


def views():
    state = ipc('scottland/layout-state')['views']
    geometries = {v['id']: v['geometry'] for v in ipc('window-rules/list-views')}
    for v in state:
        v['geometry'] = geometries[v['id']]
    return state


def view(name):
    return next((v for v in views() if v['title'] == name and not v['widget']), None)


def hint(identifier):
    return next(h for h in ipc('scottland/hints')['hints'] if h['window'] == identifier)


def model_window(identifier):
    return next(w for w in ipc('scottland/desktop-model')['windows'] if w['id'] == identifier)


def center(v):
    g = v['geometry']
    return (g['x'] + g['width'] / 2, g['y'] + g['height'] / 2)


def key(code, down):
    return ipc('stipc/feed_key', {'key': 'KEY_' + code, 'state': down})


def tap(code):
    key(code, True)
    key(code, False)


def hold():
    key('LEFTALT', True)
    wait_for(lambda: ipc('scottland/hints')['active'])
    time.sleep(.4)


def release():
    key('LEFTALT', False)
    time.sleep(.5)


def press_hint(identifier):
    for letter in hint(identifier)['hint']:
        tap(letter.upper())


def launch(name, x, y):
    ipc('wayfire/set-config-options', {'place/mode': 'pointer'})
    ipc('stipc/move_cursor', {'x': round(x), 'y': round(y)})
    log = artifacts / (name + '.keys')
    log.write_text('')
    clients.append(subprocess.Popen(['tests/headless.sh', 'run', 'python3',
        str(Path('tests/windowing-key-recorder.py').resolve()), name, str(log)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    v = wait_for(lambda: view(name))
    time.sleep(.4)
    return v['id']


def drag(name, x, y, shift=False):
    """Super+drag the window (or its widget) to (x, y), optionally with Shift held throughout."""
    v = view(name)
    if v['widgetized']:
        link = next(w for w in ipc('scottland/widgets')['widgets'] if int(w['id']) == v['id'])
        v = next(item for item in views() if item['id'] == link['widget_view'])
    f = v['frame']
    cx, cy = f['x'] + f['width'] / 2, f['y'] + f['height'] / 2
    ipc('stipc/move_cursor', {'x': round(cx), 'y': round(cy)})
    if shift: key('LEFTSHIFT', True)
    key('LEFTMETA', True)
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'press'})
    for step in range(1, 11):
        ipc('stipc/move_cursor', {'x': round(cx + (x - cx) * step / 10), 'y': round(cy + (y - cy) * step / 10)})
        time.sleep(.025)
    time.sleep(.12)  # a deliberate stop, not a flick (no coast, L32)
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'release'})
    key('LEFTMETA', False)
    if shift: key('LEFTSHIFT', False)
    time.sleep(.6)


def cycle(identifier, name, samples=None):
    """One zone step: focus the window, hold Alt, press its hint once, sampling its scale while it
    travels. A fresh hold each time: from the center it goes to the periphery, from the
    periphery to the center (a second press in the same hold would continue to the rail)."""
    ipc('window-rules/focus-view', {'id': identifier}); time.sleep(.1)
    hold()
    press_hint(identifier)
    end = time.monotonic() + 1.0
    while time.monotonic() < end:
        v = view(name)
        if samples is not None and v: samples.append((v['target_scale'], v['applied_scale']))
        time.sleep(.015)
    release()


def settled_scale(name, duration=.6):
    """Samples over `duration`: a late retarget after the glide would show up as a spread."""
    values = []
    end = time.monotonic() + duration
    while time.monotonic() < end:
        values.append(view(name)['applied_scale'])
        time.sleep(.03)
    return values


def near(a, b, eps=2):
    return math.dist(a, b) <= eps


def close_all():
    for v in views():
        if not v['widget']:
            ipc('window-rules/close-view', {'id': v['id']})
    wait_for(lambda: not [v for v in views() if not v['widget']])
    time.sleep(.3)


try:
    ipc('wayfire/set-config-options', {'scottland/sounds': False, 'scottland/alt_hold_delay': 300})
    output = ipc('window-rules/list-outputs')[0]['geometry']
    width, height = output['width'], output['height']

    # --- With a pin: Shift-drag in the left periphery keeps the scale it had further out.
    a = launch('Pinned', width * .5, height * .5)
    drag('Pinned', width * .12, height * .45)
    outer = view('Pinned')['applied_scale']
    drag('Pinned', width * .25, height * .55, shift=True)
    v = view('Pinned')
    pin, spot = v['applied_scale'], center(v)
    check(v['zone'] == 'continuous' and abs(pin - outer) < .003 and model_window(a).get('pinned_scale') == pin,
          'Shift drop in the periphery pins the scale it had further out',
          {'zone': v['zone'], 'pin': pin, 'outer': outer, 'model': model_window(a).get('pinned_scale')})
    memories = hint(a)['memories']
    check(memories[1]['set'] and memories[1].get('pin') == pin, 'the left periphery memory keeps the pin (WP1)',
          memories[1])
    (artifacts / 'pinned-before.json').write_text(json.dumps({'view': v, 'hint': hint(a)}, indent=2))
    subprocess.run(['tests/headless.sh', 'run', 'grim', str(artifacts / 'pinned-before.png')], check=True)

    cycle(a, 'Pinned')
    v = view('Pinned')
    check(v['zone'] == 'center' and v['applied_scale'] > .999 and 'pinned_scale' not in model_window(a),
          'cycling to the center is full scale with no pin (tenet 4, WP5)',
          {'zone': v['zone'], 'applied': v['applied_scale'], 'pin': model_window(a).get('pinned_scale')})
    check(not hint(a)['memories'][0].get('pin'), 'the center memory has no pin')
    samples = []
    cycle(a, 'Pinned', samples)
    v = view('Pinned')
    settled = settled_scale('Pinned')
    check(near(center(v), spot), 'cycling back restores the remembered periphery spot (WP2)',
          {'spot': spot, 'now': center(v)})
    check(abs(v['applied_scale'] - pin) < .003 and model_window(a).get('pinned_scale') == pin,
          'cycling back restores the pinned scale, not the zone scale there (WP1)',
          {'applied': v['applied_scale'], 'pin': pin, 'model': model_window(a).get('pinned_scale')})
    check(samples and all(abs(target - pin) < .003 for target, _ in samples),
          'the cycle draws toward the pin from its first frame (WP5)', samples[:5])
    check(max(settled) - min(settled) < .003 and abs(settled[-1] - pin) < .003,
          'no scale jump after the cycle lands', (min(settled), max(settled)))
    (artifacts / 'pinned-cycle.json').write_text(json.dumps({'samples': samples, 'settled': settled}, indent=2))
    subprocess.run(['tests/headless.sh', 'run', 'grim', str(artifacts / 'pinned-after.png')], check=True)

    # --- No leak: a plain drag to the right periphery has no pin there; the left pin stays put.
    drag('Pinned', width * .78, height * .5)
    v = view('Pinned')
    right_spot, right_natural = center(v), v['scale']
    check('pinned_scale' not in model_window(a) and abs(v['applied_scale'] - right_natural) < .003,
          'a plain drag clears the current pin (L31)', {'applied': v['applied_scale'], 'natural': right_natural})
    memories = hint(a)['memories']
    check(not memories[2].get('pin') and memories[1].get('pin') == pin,
          'the right memory has no pin; the left one keeps its own', memories)
    cycle(a, 'Pinned'); cycle(a, 'Pinned')
    v = view('Pinned')
    check(near(center(v), right_spot) and abs(v['applied_scale'] - right_natural) < .003 and
          'pinned_scale' not in model_window(a),
          'cycling back to the right periphery uses its zone scale: the left pin does not leak',
          {'spot': right_spot, 'now': center(v), 'applied': v['applied_scale'], 'natural': right_natural})

    # --- Without a pin: the same round trip restores the spot at the zone's normal scale.
    b = launch('Plain', width * .5, height * .3)
    drag('Plain', width * .22, height * .3)
    v = view('Plain')
    plain_spot, plain_scale = center(v), v['scale']
    check(v['zone'] == 'continuous' and plain_scale < .97 and not hint(b)['memories'][1].get('pin'),
          'a plain drop in the periphery remembers no pin', hint(b)['memories'][1])
    cycle(b, 'Plain')
    check(view('Plain')['zone'] == 'center' and view('Plain')['applied_scale'] > .999, 'unpinned window: center at 100%')
    samples = []
    cycle(b, 'Plain', samples)
    v = view('Plain')
    settled = settled_scale('Plain')
    check(near(center(v), plain_spot) and abs(v['applied_scale'] - plain_scale) < .003 and
          'pinned_scale' not in model_window(b) and max(settled) - min(settled) < .003,
          'unpinned window: back at its spot at the zone scale, no jump',
          {'spot': plain_spot, 'now': center(v), 'applied': v['applied_scale'], 'zone': plain_scale})

    # --- Persisted across a reload, then still restored.
    drag('Pinned', width * .25, height * .55, shift=True)  # a fresh left pin, at the pin's own scale
    pin = view('Pinned')['applied_scale']; spot = center(view('Pinned'))
    cycle(a, 'Pinned')   # now in the center, the pin only in the left memory
    before = [(h['window'], h['memories']) for h in ipc('scottland/hints')['hints']]
    runtime = Path(os.environ['XDG_RUNTIME_DIR'])
    display = (Path(os.environ['SCOTTLAND_HEADLESS_DIR']) / 'display').read_text().strip()
    mark = runtime / 'scottland' / (display + '.reloading')
    mark.touch()
    fresh = Path(os.environ['SCOTTLAND_HEADLESS_DIR']) / 'libscottland-zone-pin-reload.so'
    subprocess.run(['cp', 'build/libscottland.so', str(fresh)], check=True)
    plugins = ipc('wayfire/get-config-option', {'option': 'core/plugins'})['value']
    ipc('wayfire/set-config-options', {'core/plugins': ' '.join(
        str(fresh) if p == 'scottland' else p for p in plugins.split())})
    time.sleep(1)
    mark.unlink()
    after = [(h['window'], h['memories']) for h in ipc('scottland/hints')['hints']]
    check(before == after and dict(after)[a][1].get('pin') == pin, 'a reload keeps the zone pins', after)
    cycle(a, 'Pinned')
    v = view('Pinned')
    check(near(center(v), spot) and abs(v['applied_scale'] - pin) < .003,
          'after the reload, cycling back still restores the spot and the pin',
          {'spot': spot, 'now': center(v), 'applied': v['applied_scale'], 'pin': pin})

    # --- A widget never carries a pin out: dragged off the rail without Shift, zone scale applies.
    # Shift-dragged onto the rail, it carries a pin into the widget; widgets never use it (WG4).
    drag('Pinned', 4, height * .4, shift=True)
    wait_for(lambda: view('Pinned')['widgetized'])
    time.sleep(.8)
    drag('Pinned', width * .3, height * .4)  # past the widget's own rail band
    v = wait_for(lambda: (lambda w: w if w and not w['widgetized'] and not w['hidden'] else None)(view('Pinned')))
    time.sleep(.5); v = view('Pinned')
    check('pinned_scale' not in model_window(a) and abs(v['applied_scale'] - v['scale']) < .003 and
          abs(v['applied_scale'] - pin) > .01,
          'a widget dragged out without Shift follows its zone, not the pin it had before',
          {'applied': v['applied_scale'], 'zone': v['scale'], 'old pin': pin})
    close_all()
except Exception as error:
    check(False, 'suite exception: ' + repr(error))
    import traceback
    traceback.print_exc()
finally:
    print(f'{passed} passed, {failed} failed', flush=True)
    for process in clients:
        try:
            process.wait(timeout=.2)
        except subprocess.TimeoutExpired:
            pass  # the session owner stops its clients
sys.exit(bool(failed))
