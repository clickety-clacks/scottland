#!/usr/bin/env python3
"""WP4/WP8: Window-mode cycles reach the periphery, with real stipc input and Mike's zone settings.

Mike's report (2026-10-04, on 2560x1600 with a 30% center zone and a steep scale curve):
1. A periphery window dragged back to the center, dropped just past the center zone's edge where it
   still shows at 100%, overwrote its periphery memory with that spot: cycling then went between
   two full-scale spots and a widget.
2. A window never in the periphery, cycled there, landed 44 pt past the center zone's edge at 94%,
   most of it over the center zone: it read as another center spot or an unscaled periphery window.
   Its drawn scale also stayed off the zone's scale by its center's pixel rounding.
Both are checked here, plus a periphery memory that zone settings have since moved into the
center's softness band (a memory like Mike's live window 52 kept)."""
import json
import math
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
# Mike's layout.ini on osanwe (2026-10-04).
MIKE = {'scottland/center_width': 30.0, 'scottland/rail_width': 1.9, 'scottland/blend_width': 40.0,
        'scottland/scale_curve': '0.000:0.997 0.041:0.779 1.000:0.255',
        'scottland/min_scale': 0.255, 'scottland/max_scale': 0.997}
PAD = 32 / 3 + 5   # WP7: halo plus 5 pt


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


def memory(identifier, zone):
    """Remembered center x (pt) for zone 0 center, 1/2 left/right periphery, or None."""
    spot = hint(identifier)['memories'][zone]
    return spot['x'] * width if spot['set'] else None


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


def launch(name, x, y, w, h):
    ipc('wayfire/set-config-options', {'place/mode': 'pointer'})
    ipc('stipc/move_cursor', {'x': round(x), 'y': round(y)})
    clients.append(subprocess.Popen(['tests/headless.sh', 'run', 'python3',
        str(Path('tests/sized-window-app.py').resolve()), name, str(w), str(h)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    v = wait_for(lambda: view(name))
    time.sleep(.5)
    return v['id']


def drag(name, x, y):
    """Super+drag the window by its middle so its center ends at (x, y); a deliberate stop, no coast."""
    v = view(name)
    f = v['frame']
    cx, cy = f['x'] + f['width'] / 2, f['y'] + f['height'] / 2
    ipc('stipc/move_cursor', {'x': round(cx), 'y': round(cy)})
    key('LEFTMETA', True)
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'press'})
    for step in range(1, 11):
        ipc('stipc/move_cursor', {'x': round(cx + (x - cx) * step / 10), 'y': round(cy + (y - cy) * step / 10)})
        time.sleep(.025)
    time.sleep(.12)
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'release'})
    key('LEFTMETA', False)
    time.sleep(.8)


def stops(identifier, name, count):
    """One Alt hold, `count` slow hint presses (the WK7 loop): where the window is after each."""
    ipc('window-rules/focus-view', {'id': identifier}); time.sleep(.1)
    hold()
    result = []
    for _ in range(count):
        press_hint(identifier)
        time.sleep(1.0)
        result.append(view(name))
    release()
    return result


def settled(name, duration=.6):
    values = []
    end = time.monotonic() + duration
    while time.monotonic() < end:
        values.append(view(name)['applied_scale'])
        time.sleep(.03)
    return max(values) - min(values)


def summary(v):
    return {'x': round(center(v)[0], 1), 'zone': v['zone'], 'scale': round(v['scale'], 3),
            'applied': round(v['applied_scale'], 3), 'widget': v['widgetized']}


def on_screen(v):
    x, y = center(v)
    s, g = v['applied_scale'], v['geometry']
    return (x - g['width'] * s / 2 >= PAD - 1 and x + g['width'] * s / 2 <= width - PAD + 1 and
            y - g['height'] * s / 2 >= PAD - 1 and y + g['height'] * s / 2 <= height - PAD + 1)


def clear_of_center(v):
    """The scaled footprint stays out of the center zone (WP4)."""
    x, s, w = center(v)[0], v['applied_scale'], v['geometry']['width']
    return x - w * s / 2 >= right_edge - 1 if x > width / 2 else x + w * s / 2 <= left_edge + 1


def lands_in_periphery(v):
    """Visibly scaled (WP8: 5% under full, for this curve), drawn at the zone's scale (WP5)."""
    return (v['zone'] == 'continuous' and not v['widgetized'] and v['scale'] <= .95 and
            abs(v['applied_scale'] - v['scale']) < .003)


def close_all():
    for v in views():
        if not v['widget']:
            ipc('window-rules/close-view', {'id': v['id']})
    wait_for(lambda: not [v for v in views() if not v['widget']])
    time.sleep(.3)


try:
    ipc('wayfire/set-config-options', {'output:HEADLESS-1/mode': '2560x1600@60000'})
    time.sleep(.5)
    ipc('wayfire/set-config-options', dict(MIKE, **{'scottland/sounds': False, 'scottland/alt_hold_delay': 300}))
    output = ipc('window-rules/list-outputs')[0]['geometry']
    width, height = output['width'], output['height']
    check((width, height) == (2560, 1600), "Mike's screen size (2560x1600 pt)", (width, height))
    left_edge, right_edge = width * .35, width * .65
    # Mike's terminals are about 1180x1050 here; an odd width puts each center on a half pixel.
    ww, wh = 1179, 1051

    # --- 1. Periphery, then dragged to the center, dropped just past its edge (still 100%).
    a = launch('Edge', width * .5, height * .5, ww, wh)
    drag('Edge', width * .84, height * .5)
    deep = view('Edge')
    deep_x = center(deep)[0]
    check(lands_in_periphery(deep) and abs(memory(a, 2) - deep_x) < 2,
          'a drop deep in the right periphery is its periphery memory', summary(deep))
    drag('Edge', right_edge + 3, height * .5)
    edge = view('Edge')
    edge_x = center(edge)[0]
    check(right_edge < edge_x < right_edge + 20 and edge['applied_scale'] > .99,
          'dropped a few pt past the center zone, it still shows at 100%', summary(edge))
    check(abs(memory(a, 2) - deep_x) < 2, 'that drop keeps the periphery memory (WP8)',
          {'right periphery memory': memory(a, 2), 'deep': deep_x})
    check(memory(a, 0) is not None and abs(memory(a, 0) - edge_x) < 2,
          'it is remembered as the center instead (WP8)', {'center memory': memory(a, 0), 'edge': edge_x})
    subprocess.run(['tests/headless.sh', 'run', 'grim', str(artifacts / 'edge-drop.png')], check=True)
    loop = stops(a, 'Edge', 3)
    check(abs(center(loop[0])[0] - deep_x) < 2 and lands_in_periphery(loop[0]),
          'cycling from there goes to the remembered periphery spot, scaled', summary(loop[0]))
    check(loop[1]['widgetized'], 'then to a widget', summary(loop[1]))
    check(abs(center(loop[2])[0] - edge_x) < 2 and loop[2]['applied_scale'] > .99,
          'then back to the spot it was dropped at, full scale', summary(loop[2]))
    for attempt in range(2):
        out, back = stops(a, 'Edge', 1)[0], stops(a, 'Edge', 1)[0]
        check(abs(center(out)[0] - deep_x) < 2 and lands_in_periphery(out) and
              abs(center(back)[0] - edge_x) < 2 and back['applied_scale'] > .99,
              f'single presses in fresh holds alternate periphery and center ({attempt + 1})',
              {'out': summary(out), 'back': summary(back)})

    # The same on the left, dropped well inside the center.
    drag('Edge', width * .16, height * .5)
    left_deep = center(view('Edge'))[0]
    drag('Edge', width * .45, height * .5)
    out = stops(a, 'Edge', 1)[0]
    check(abs(memory(a, 1) - left_deep) < 2 and abs(center(out)[0] - left_deep) < 2 and lands_in_periphery(out),
          'left: a drop well inside the center keeps the periphery memory too', summary(out))
    drag('Edge', left_edge - 3, height * .5)
    check(abs(memory(a, 1) - left_deep) < 2 and view('Edge')['applied_scale'] > .99,
          'left: a full-scale drop just past the center zone keeps it', {'memory': memory(a, 1), 'deep': left_deep})
    out = stops(a, 'Edge', 1)[0]
    check(abs(center(out)[0] - left_deep) < 2 and lands_in_periphery(out),
          'left: cycling from there reaches the remembered periphery spot', summary(out))
    close_all()

    # --- 2. Never in the periphery, cycled there: visibly scaled, out of the center zone.
    for name, w, h in (('Small', 301, 181), ('Mike', ww, wh), ('Wide', 1601, 1121)):
        i = launch(name, width * .5, height * .45, w, h)
        start = center(view(name))
        out = stops(i, name, 1)[0]
        spread = settled(name)
        footprint_ok = clear_of_center(out) if name != 'Wide' else out['scale'] < .7
        check(lands_in_periphery(out) and footprint_ok and on_screen(out) and spread < .003,
              f'{name} ({w}x{h}): an unremembered cycle lands in the periphery, scaled, '
              + ('clear of the center zone' if name != 'Wide' else 'as far out as fits') + ', drawn at its zone scale',
              dict(summary(out), spread=spread, clear=clear_of_center(out), on_screen=on_screen(out)))
        subprocess.run(['tests/headless.sh', 'run', 'grim', str(artifacts / f'fresh-{name}.png')], check=True)
        side = 1 if center(out)[0] < width / 2 else 2
        check(abs(memory(i, side) - center(out)[0]) < 2, f'{name}: that spot is its periphery memory')
        back = stops(i, name, 1)[0]
        check(math.dist(center(back), start) < 2 and back['applied_scale'] > .999,
              f'{name}: cycling back returns to its center spot at 100%', summary(back))
        close_all()

    # With other windows about, like Mike's desktop: two in the left periphery, one in the center.
    launch('Left1', width * .2, height * .3, 1050, 960); drag('Left1', width * .17, height * .3)
    launch('Left2', width * .2, height * .6, 1021, 960); drag('Left2', width * .18, height * .7)
    launch('Middle', width * .5, height * .5, ww, wh)
    i = launch('Crowded', width * .5, height * .4, ww, wh)
    out = stops(i, 'Crowded', 1)[0]
    check(center(out)[0] > width / 2 and lands_in_periphery(out) and clear_of_center(out) and on_screen(out),
          'among other windows it takes the open side, scaled and clear of the center zone', summary(out))
    subprocess.run(['tests/headless.sh', 'run', 'grim', str(artifacts / 'fresh-crowded.png')], check=True)
    close_all()

    # --- A periphery memory that zone settings have since put in the center's softness band.
    i = launch('Moved', width * .5, height * .5, ww, wh)
    drag('Moved', right_edge + 70, height * .5)
    spot = center(view('Moved'))[0]
    check(lands_in_periphery(view('Moved')) and abs(memory(i, 2) - spot) < 2,
          'a drop 70 pt past the center zone is a periphery memory', summary(view('Moved')))
    stops(i, 'Moved', 1)   # to the center
    ipc('wayfire/set-config-options', {'scottland/center_width': 34.0}); time.sleep(.5)
    left_edge, right_edge = width * .33, width * .67   # the old spot is now 19 pt past the edge
    out = stops(i, 'Moved', 1)[0]
    check(abs(center(out)[0] - spot) > 20 and lands_in_periphery(out) and clear_of_center(out),
          'once it reads as full scale, that memory is not used: the cycle places it in the periphery',
          dict(summary(out), old_spot=spot))
    ipc('wayfire/set-config-options', {'scottland/center_width': MIKE['scottland/center_width']}); time.sleep(.5)
    left_edge, right_edge = width * .35, width * .65
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
