#!/usr/bin/env python3
"""WK31: a window nothing covers has its hint at its exact center, with real input."""
import json
import math
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

artifacts = Path(sys.argv[1]).resolve()
clients = []
passed = failed = 0
socket_path = subprocess.check_output(['tests/headless.sh', 'run', 'python3', '-c',
    "import os;print(os.environ['WAYFIRE_SOCKET'])"], text=True).strip()


def ipc(method, data=None):
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(10)
        s.connect(socket_path)
        def read(n):
            result = b''
            while len(result) < n:
                chunk = s.recv(n-len(result))
                if not chunk:
                    raise ConnectionError('headless compositor disconnected')
                result += chunk
            return result
        body = json.dumps({'method': method, 'data': data or {}}).encode()
        s.sendall(struct.pack('<I', len(body))+body)
        return json.loads(read(struct.unpack('<I', read(4))[0]))


def check(ok, name, detail=''):
    global passed, failed
    print(('PASS  ' if ok else 'FAIL  ')+name+(f'  [{detail}]' if detail else ''), flush=True)
    passed += bool(ok)
    failed += not ok


def key(name, down):
    ipc('stipc/feed_key', {'key': 'KEY_'+name, 'state': down})


def tap(name):
    key(name, True)
    key(name, False)


def views():
    return {v['id']: v for v in ipc('scottland/layout-state')['views']}


def hints():
    return {h['window']: h for h in ipc('scottland/hints')['hints']}


def focused():
    return ipc('window-rules/get-focused-view')['info']['id']


def wait(predicate, timeout=10):
    until = time.monotonic()+timeout
    while time.monotonic() < until:
        result = predicate()
        if result:
            return result
        time.sleep(.05)
    raise RuntimeError('timed out waiting for headless state')


def drag(identifier, x, y):
    ipc('window-rules/focus-view', {'id': identifier})
    f = views()[identifier]['frame']
    cx, cy = f['x']+f['width']/2, f['y']+f['height']/2
    ipc('stipc/move_cursor', {'x': round(cx), 'y': round(cy)})
    key('LEFTMETA', True)
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'press'})
    for i in range(1, 11):
        ipc('stipc/move_cursor', {'x': round(cx+(x-cx)*i/10), 'y': round(cy+(y-cy)*i/10)})
        time.sleep(.03)
    time.sleep(.2)
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'release'})
    key('LEFTMETA', False)
    ipc('stipc/move_cursor', {'x': 640, 'y': 5})
    time.sleep(.8)


def rect(v, h):
    f = v['frame']
    return (f['x']+h['dx'], f['y']+h['dy'], f['width'], f['height'])


def area(a, b):
    return max(0, min(a[0]+a[2], b[0]+b[2])-max(a[0], b[0])) * max(
        0, min(a[1]+a[3], b[1]+b[3])-max(a[1], b[1]))


def settled():
    """Wait until offsets reach their targets and every drawn badge stops moving."""
    def still():
        a = hints()
        time.sleep(.15)
        b = hints()
        steady = all(abs(h['dx']-h['target_dx']) < .1 and abs(h['dy']-h['target_dy']) < .1
                     for h in b.values())
        same = all(a[i].get('badge') == b[i].get('badge') for i in b if i in a)
        return b if steady and same else None
    return wait(still)


def badge_center(h):
    b = h['badge']
    return (b['x']+b['size']/2, b['y']+b['size']/2)


def center_error(identifier, vs, hs):
    r = rect(vs[identifier], hs[identifier])
    c = badge_center(hs[identifier])
    return math.dist(c, (r[0]+r[2]/2, r[1]+r[3]/2))


def stacking():
    """Front-to-back ids of the test windows, from the compositor's stacking order."""
    return [h['window'] for h in sorted(hints().values(), key=lambda h: h['avoidance_order'])
            if h['avoidance_order'] >= 0]


def uncovered(identifier, vs, hs, order):
    mine = rect(vs[identifier], hs[identifier])
    above = order[:order.index(identifier)]
    return all(area(mine, rect(vs[j], hs[j])) == 0 for j in above)


def capture(name):
    vs, hs = views(), hints()
    order = stacking()
    report = {}
    for i in order:
        r = rect(vs[i], hs[i])
        report[vs[i]['title']] = {
            'frame_drawn': r, 'center': (r[0]+r[2]/2, r[1]+r[3]/2),
            'badge_center': badge_center(hs[i]) if 'badge' in hs[i] else None,
            'badge_size': hs[i]['badge']['size'] if 'badge' in hs[i] else None,
            'offset': (hs[i]['dx'], hs[i]['dy']),
            'uncovered': uncovered(i, vs, hs, order),
            'center_error': center_error(i, vs, hs) if 'badge' in hs[i] else None}
    (artifacts/(name+'.json')).write_text(json.dumps({'front_to_back': [vs[i]['title'] for i in order],
        'windows': report, 'hints': hs, 'views': vs}, indent=2))
    subprocess.run(['tests/headless.sh', 'run', 'grim', str(artifacts/(name+'.png'))], check=True)
    for title, w in report.items():
        print(f'      {name}: {title:6} uncovered={w["uncovered"]!s:5} '
              f'center=({w["center"][0]:.1f},{w["center"][1]:.1f}) badge='
              + (f'({w["badge_center"][0]:.1f},{w["badge_center"][1]:.1f}) err={w["center_error"]:.1f}'
                 if w['badge_center'] else 'none')
              + f' offset=({w["offset"][0]:.1f},{w["offset"][1]:.1f})', flush=True)
    return vs, hs, order


def check_uncovered_centered(name, vs, hs, order):
    for i in order:
        if uncovered(i, vs, hs, order):
            check('badge' in hs[i] and center_error(i, vs, hs) <= 1.0,
                  f'{name}: uncovered {vs[i]["title"]} has its hint at its exact center',
                  f'error {center_error(i, vs, hs):.1f}px' if 'badge' in hs[i] else 'no badge')


try:
    ipc('wayfire/set-config-options', {'scottland/sounds': False, 'place/mode': 'pointer'})
    palette = artifacts/'palette.json'
    colors = {'scheme': 'dark', 'background': '#1f232c', 'foreground': '#d8deea',
              'accent': '#81a1c1', 'text_scale': 1, 'reduced_motion': False}
    palette.write_text(json.dumps(colors))
    palette_path = Path(subprocess.check_output(['tests/headless.sh', 'run', 'python3', '-c',
        "import os;print(os.path.join(os.environ['XDG_RUNTIME_DIR'],'scottland',os.environ['WAYLAND_DISPLAY']+'.palette.json'))"],
        text=True).strip())
    palette_path.write_text(json.dumps(colors))
    ids = {}
    # Side sits alone; Back is large and centered; Front covers Back's center.
    for name, w, h, x, y in [('Side', 240, 180, 1180, 600), ('Back', 900, 600, 640, 360),
                             ('Front', 480, 320, 700, 400)]:
        clients.append(subprocess.Popen(['tests/headless.sh', 'run', 'python3',
            str(Path('tests/hint-style-app.py').resolve()), name, str(w), str(h), str(palette)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        v = wait(lambda: next((v for v in views().values() if v['title'] == name and 'frame' in v), None))
        time.sleep(.5)
        ids[name] = v['id']
        drag(v['id'], x, y)
    letters = {name: hints()[i]['hint'] for name, i in ids.items()}

    # 1. Alt hold: the front window and a separate, uncovered rear window are centered.
    key('LEFTALT', True)
    wait(lambda: ipc('scottland/hints')['active'])
    time.sleep(1.5)
    settled()
    vs, hs, order = capture('1-held')
    check(order[0] == ids['Front'], 'Front is the frontmost window')
    check(uncovered(ids['Side'], vs, hs, order), 'Side is uncovered although it is not frontmost')
    check_uncovered_centered('held', vs, hs, order)
    check(not uncovered(ids['Back'], vs, hs, order) and center_error(ids['Back'], vs, hs) > 20,
          'covered Back keeps searching for a visible spot (strict rule)')

    # 2. Select the covered Back by its hint letter: it is raised, focused and now uncovered.
    for letter in letters['Back']:
        tap(letter.upper())
    wait(lambda: focused() == ids['Back'])
    time.sleep(1)
    settled()
    vs, hs, order = capture('2-back-raised')
    check(order[0] == ids['Back'], 'Back is frontmost after selecting its hint')
    check(abs(hs[ids['Back']]['dx'])+abs(hs[ids['Back']]['dy']) < .1, 'focused front Back never moves')
    check_uncovered_centered('back raised', vs, hs, order)

    # 3. Select Front again: it had been covered (its hint off center), now it is frontmost.
    for letter in letters['Front']:
        tap(letter.upper())
    wait(lambda: focused() == ids['Front'])
    time.sleep(1)
    settled()
    vs, hs, order = capture('3-front-raised')
    check(order[0] == ids['Front'], 'Front is frontmost after selecting its hint')
    check(abs(hs[ids['Front']]['dx'])+abs(hs[ids['Front']]['dy']) < .1, 'focused front Front never moves')
    check_uncovered_centered('front raised', vs, hs, order)
    key('LEFTALT', False)
    time.sleep(.8)

    # 4. Always-on avoidance: raise Back with a real click, then enter Window mode.
    ipc('wayfire/set-config-options', {'scottland/window_avoidance_always': True})
    time.sleep(1)
    b = rect(views()[ids['Back']], hints()[ids['Back']])
    ipc('stipc/move_cursor', {'x': round(b[0]+30), 'y': round(b[1]+30)})
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'press'})
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'release'})
    wait(lambda: focused() == ids['Back'])
    ipc('stipc/move_cursor', {'x': 640, 'y': 5})
    time.sleep(1)
    key('LEFTALT', True)
    wait(lambda: ipc('scottland/hints')['active'])
    time.sleep(1.5)
    settled()
    vs, hs, order = capture('4-always-clicked')
    check(order[0] == ids['Back'], 'clicked Back is frontmost')
    check_uncovered_centered('always-on avoidance', vs, hs, order)
    key('LEFTALT', False)
    time.sleep(.5)
finally:
    for client in clients:
        client.terminate()
    print(f'{passed} passed, {failed} failed', flush=True)
sys.exit(1 if failed else 0)
