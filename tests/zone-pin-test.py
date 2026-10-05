#!/usr/bin/env python3
"""WP1/WP5: a zone memory keeps the window's Shift scale pin (L31) there, with real stipc input.

Shift-drag a window in the periphery to a scale that isn't that spot's natural one, cycle it to
the center and back with Window-mode hints, and check the remembered position and the pinned
scale both come back, drawn without a jump; the same for a window kept at full size (a pin above
WP8's 95% line is still a periphery pin); the same without a pin; a pin made in one zone never
reaching another; the pins surviving a reload; every hint-key path out and back (slow presses
through the widget, a WK15 double tap to the rail); Esc; Shift+arrow pins; and a remembered spot
that zone settings have moved into the center (no longer used, WP8)."""
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


def presses(identifier, name, gaps, samples=None):
    """Real hint presses in one Alt hold: one press, then one more after each gap (s). Slow gaps
    (0.65 s) walk the WK7 loop; a gap under the 300 ms double-tap interval requests the rail
    (WK15). Samples the window's scale for a second after the last press."""
    if not view(name)['widgetized']:
        ipc('window-rules/focus-view', {'id': identifier}); time.sleep(.1)
    hold()
    press_hint(identifier)
    for gap in gaps:
        time.sleep(gap)
        press_hint(identifier)
    end = time.monotonic() + 1.0
    while time.monotonic() < end:
        v = view(name)
        if samples is not None and v: samples.append((v['target_scale'], v['applied_scale']))
        time.sleep(.015)
    release()


def pin_left(name, identifier):
    """Pin the window in the left periphery with a real Shift drop: it keeps the scale it had
    further out, which isn't the zone's scale at the drop."""
    drag(name, width * .12, height * .45)
    drag(name, width * .25, height * .55, shift=True)
    v = view(name)
    return v['applied_scale'], center(v)


def super_drag_esc(name, dx, shift=False):
    """Pick the window up with Super, move it dx, press Esc while still holding the button."""
    time.sleep(2.6)  # a re-grab within 2.5 s of a drop continues that move (WG14): Esc would undo both
    v = view(name)
    f = v['frame']
    cx, cy = f['x'] + f['width'] / 2, f['y'] + f['height'] / 2
    ipc('stipc/move_cursor', {'x': round(cx), 'y': round(cy)})
    if shift: key('LEFTSHIFT', True)
    key('LEFTMETA', True)
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'press'})
    for step in range(1, 11):
        ipc('stipc/move_cursor', {'x': round(cx + dx * step / 10), 'y': round(cy)})
        time.sleep(.025)
    time.sleep(.15)
    tap('ESC'); time.sleep(.2)
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'release'})
    key('LEFTMETA', False)
    if shift: key('LEFTSHIFT', False)
    time.sleep(1.0)


def lands_on_pin(samples, pin):
    """The cycle draws toward the pin from its first frame and never past it by more than the 3%
    cycle spring (WK29), from whichever side it starts (shrinking from 100%, or growing out of a
    widget); the drawn scale ends at the pin."""
    if not samples or not all(abs(target - pin) < .003 for target, _ in samples): return False
    drawn = [applied for _, applied in samples]
    beyond = min(drawn) > pin - .05 if drawn[0] >= pin else max(drawn) < pin + .05
    return beyond and abs(drawn[-1] - pin) < .003


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
    check(lands_on_pin(samples, pin),
          'the cycle draws toward the pin from its first frame, drawn scale included (WP5)', samples[:8])
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

    # --- A full-size pin at the side (Shift's common use: move it aside, keep it at 100%). A pin
    # above WP8's 95% line must not make the spot read as the center: it is a periphery spot.
    f = launch('FullPin', width * .5, height * .5)
    full_start = center(view('FullPin'))
    drag('FullPin', width * .15, height * .5, shift=True)
    v = view('FullPin')
    full_spot, full_pin = center(v), model_window(f).get('pinned_scale')
    check(v['zone'] == 'continuous' and v['scale'] < .9 and v['applied_scale'] > .99 and
          full_pin is not None and full_pin > .99,
          'a Shift drag aside keeps the window at full size (pin above 95%)',
          {'zone': v['zone'], 'zone scale': v['scale'], 'applied': v['applied_scale'], 'pin': full_pin})
    memories = hint(f)['memories']
    check(memories[1]['set'] and near((memories[1]['x'] * width, memories[1]['y'] * height), full_spot) and
          memories[1].get('pin') == full_pin and memories[0]['set'] and
          near((memories[0]['x'] * width, memories[0]['y'] * height), full_start),
          'it is the left periphery memory, with its pin; the center memory is untouched (WP8)', memories)
    presses(f, 'FullPin', [])
    v = view('FullPin')
    check(near(center(v), full_start) and v['zone'] == 'center' and v['applied_scale'] > .999 and
          'pinned_scale' not in model_window(f),
          'its first hint press takes it to its center spot at 100% (not a shrink in place)',
          {'start': full_start, 'now': center(v), 'zone': v['zone'], 'applied': v['applied_scale']})
    samples = []
    presses(f, 'FullPin', [], samples)
    v = view('FullPin')
    check(near(center(v), full_spot) and abs(v['applied_scale'] - full_pin) < .003 and
          model_window(f).get('pinned_scale') == full_pin and lands_on_pin(samples, full_pin),
          'the next press returns it to the side spot at its full-size pin',
          {'spot': full_spot, 'now': center(v), 'applied': v['applied_scale'], 'pin': model_window(f).get('pinned_scale')})
    presses(f, 'FullPin', [.65, .65])   # periphery -> center -> widget -> periphery, one hold
    v = view('FullPin')
    check(not v['widgetized'] and near(center(v), full_spot) and abs(v['applied_scale'] - full_pin) < .003 and
          near((hint(f)['memories'][0]['x'] * width, hint(f)['memories'][0]['y'] * height), full_start),
          'the whole loop through the widget restores the spot and full-size pin; the center memory survives',
          {'now': center(v), 'applied': v['applied_scale'], 'memories': hint(f)['memories']})
    ipc('window-rules/close-view', {'id': f}); time.sleep(.5)

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
    check('pinned_scale' not in model_window(a), 'becoming a widget clears the window pin (WG4)',
          model_window(a).get('pinned_scale'))
    drag('Pinned', width * .3, height * .4)  # past the widget's own rail band
    v = wait_for(lambda: (lambda w: w if w and not w['widgetized'] and not w['hidden'] else None)(view('Pinned')))
    time.sleep(.5); v = view('Pinned')
    check('pinned_scale' not in model_window(a) and abs(v['applied_scale'] - v['scale']) < .003 and
          abs(v['applied_scale'] - pin) > .01,
          'a widget dragged out without Shift follows its zone, not the pin it had before',
          {'applied': v['applied_scale'], 'zone': v['scale'], 'old pin': pin})

    # --- Hint keys (WK6/WK7 slow presses, WK15 double tap): every way out and back restores the pin.
    c = launch('Keys', width * .5, height * .5)
    pin, spot = pin_left('Keys', c)
    check(hint(c)['memories'][1].get('pin') == pin, 'hint-key window: pinned in the left periphery', hint(c)['memories'][1])
    samples = []
    presses(c, 'Keys', [.65, .65], samples)   # periphery -> center -> widget -> periphery, one hold
    v = view('Keys')
    check(not v['widgetized'] and near(center(v), spot) and abs(v['applied_scale'] - pin) < .003 and
          model_window(c).get('pinned_scale') == pin and lands_on_pin(samples, pin),
          'hint keys periphery -> center -> widget -> periphery restore the spot and the pin',
          {'spot': spot, 'now': center(v), 'applied': v['applied_scale'], 'pin': pin, 'samples': samples[:6]})
    presses(c, 'Keys', [])
    check(view('Keys')['zone'] == 'center' and view('Keys')['applied_scale'] > .999, 'hint key periphery -> center: 100%')
    samples = []
    presses(c, 'Keys', [], samples)
    v = view('Keys')
    check(near(center(v), spot) and abs(v['applied_scale'] - pin) < .003 and lands_on_pin(samples, pin),
          'hint keys center -> periphery (new hold) restore the spot and the pin',
          {'now': center(v), 'applied': v['applied_scale'], 'pin': pin})
    presses(c, 'Keys', [.08])   # WK15 double tap: straight to the rail
    wait_for(lambda: view('Keys')['widgetized'])
    time.sleep(.8)
    check(hint(c)['memories'][1].get('pin') == pin and 'pinned_scale' not in model_window(c),
          'double tap to the rail keeps the periphery pin in memory, none on the widget', hint(c)['memories'][1])
    # Widget -> center -> periphery in one hold. If the widget wasn't selected, its first press
    # only selects it (WK7/WK34), so press until the window is out, then once more.
    hold()
    for attempt in range(3):
        press_hint(c); time.sleep(.65)
        if not view('Keys')['widgetized'] and view('Keys')['zone'] == 'center': break
    check(view('Keys')['zone'] == 'center' and view('Keys')['applied_scale'] > .999,
          'hint key widget -> center: the window opens at 100%', view('Keys')['zone'])
    samples = []
    press_hint(c)
    end = time.monotonic() + 1.0
    while time.monotonic() < end:
        samples.append((view('Keys')['target_scale'], view('Keys')['applied_scale'])); time.sleep(.015)
    release()
    v = view('Keys')
    check(not v['widgetized'] and near(center(v), spot) and abs(v['applied_scale'] - pin) < .003 and
          lands_on_pin(samples, pin),
          'hint keys rail -> center -> periphery after a double tap restore the spot and the pin',
          {'now': center(v), 'applied': v['applied_scale'], 'pin': pin, 'widgetized': v['widgetized']})

    # --- Esc (L27): the window goes back in its original form, its pin included.
    super_drag_esc('Keys', 220)
    v = view('Keys')
    check(near(center(v), spot) and model_window(c).get('pinned_scale') == pin and abs(v['applied_scale'] - pin) < .003,
          'Esc on a plain drag of a pinned window restores its spot and pin',
          {'now': center(v), 'applied': v['applied_scale'], 'pin': model_window(c).get('pinned_scale')})
    presses(c, 'Keys', []); presses(c, 'Keys', [])
    v = view('Keys')
    check(near(center(v), spot) and abs(v['applied_scale'] - pin) < .003 and hint(c)['memories'][1].get('pin') == pin,
          'after that Esc, cycling away and back still restores the pin', {'applied': v['applied_scale'], 'pin': pin})
    super_drag_esc('Keys', -int(spot[0]) + 4, shift=True)   # Shift drag over the rail (a preview), Esc
    v = view('Keys')
    check(not v['widgetized'] and near(center(v), spot) and model_window(c).get('pinned_scale') == pin and
          abs(v['applied_scale'] - pin) < .003,
          'Esc on a Shift drag onto the rail returns the window, not a widget, with its pin',
          {'now': center(v), 'widgetized': v['widgetized'], 'applied': v['applied_scale']})
    d = launch('Unpinned', width * .5, height * .3)
    drag('Unpinned', width * .22, height * .3)
    d_spot, d_scale = center(view('Unpinned')), view('Unpinned')['scale']
    super_drag_esc('Unpinned', 260, shift=True)
    v = view('Unpinned')
    check(near(center(v), d_spot) and 'pinned_scale' not in model_window(d) and abs(v['applied_scale'] - d_scale) < .003,
          'Esc on a Shift drag of an unpinned window leaves no pin', {'applied': v['applied_scale'], 'zone': d_scale})

    # --- Shift+arrow (WK19) pins like a Shift drop; the memory keeps it and a cycle restores it.
    ipc('window-rules/focus-view', {'id': d}); time.sleep(.1)
    hold(); key('LEFTSHIFT', True); tap('LEFT'); key('LEFTSHIFT', False); time.sleep(1.5); release()
    v = view('Unpinned')
    arrow_pin, arrow_spot = model_window(d).get('pinned_scale'), center(v)
    check(arrow_pin is not None and abs(arrow_pin - d_scale) < .003 and abs(v['scale'] - arrow_pin) > .01 and
          hint(d)['memories'][1].get('pin') == arrow_pin,
          'a Shift+arrow coast keeps its scale and the zone memory records the pin',
          {'pin': arrow_pin, 'zone': v['scale'], 'memory': hint(d)['memories'][1]})
    presses(d, 'Unpinned', []); presses(d, 'Unpinned', [])
    v = view('Unpinned')
    check(near(center(v), arrow_spot) and arrow_pin is not None and abs(v['applied_scale'] - arrow_pin) < .003,
          'and a cycle away and back restores that Shift+arrow pin', {'now': center(v), 'applied': v['applied_scale']})

    # --- A remembered spot that zone settings have since put inside the center no longer counts as
    # the periphery's (WP8): the cycle places the window in the periphery anew, with no pin.
    presses(c, 'Keys', [])   # periphery -> center; the left memory keeps the pin
    old = ipc('wayfire/get-config-option', {'option': 'scottland/center_width'})['value']
    ipc('wayfire/set-config-options', {'scottland/center_width': 60.0}); time.sleep(.5)
    presses(c, 'Keys', [])
    v = view('Keys')
    check(not near(center(v), spot) and v['zone'] == 'continuous' and 'pinned_scale' not in model_window(c) and
          abs(v['applied_scale'] - v['scale']) < .003,
          'a remembered spot now inside the center zone is not used: placed anew in the periphery, no pin (WP8)',
          {'zone': v['zone'], 'spot': spot, 'now': center(v), 'applied': v['applied_scale'], 'scale': v['scale']})
    ipc('wayfire/set-config-options', {'scottland/center_width': float(old)}); time.sleep(.5)
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
