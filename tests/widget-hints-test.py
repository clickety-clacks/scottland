#!/usr/bin/env python3
"""WK26: actual Alt holds and drags, card/custom widgets, both rails and text sizes."""
import json
import math
import subprocess
import sys
import time
from pathlib import Path

artifacts = Path(sys.argv[1])
clients = []
passed = failed = 0


def ipc(method, data=None):
    args = ['tests/headless.sh', 'ipc', method]
    if data is not None:
        args.append(json.dumps(data))
    return json.loads(subprocess.check_output(args, text=True))


def run(*args):
    return subprocess.check_output(['tests/headless.sh', 'run', *args], text=True).strip()


def check(ok, message):
    global passed, failed
    print(('PASS  ' if ok else 'FAIL  ') + message, flush=True)
    passed += bool(ok)
    failed += not ok


def wait(predicate):
    end = time.monotonic() + 10
    while time.monotonic() < end:
        result = predicate()
        if result:
            return result
        time.sleep(.05)
    raise RuntimeError('timed out waiting for compositor state')


def views():
    return ipc('scottland/layout-state')['views']


def links():
    return ipc('scottland/widgets')['widgets']


def hints():
    return ipc('scottland/hints')['hints']


def key(name, state):
    ipc('stipc/feed_key', {'key': 'KEY_' + name, 'state': state})


def tap(name):
    key(name, True)
    key(name, False)


def hold():
    key('LEFTALT', True)
    wait(lambda: ipc('scottland/hints')['active'])
    time.sleep(1)


def release():
    key('LEFTALT', False)
    time.sleep(.7)


def drag(view_id, x, y):
    # Overlapping cards must expose the intended target before the real pointer drag.
    ipc('window-rules/focus-view', {'id': view_id})
    f = next(v for v in views() if v['id'] == view_id)['frame']
    cx, cy = f['x'] + f['width']/2, f['y'] + f['height']/2
    ipc('stipc/move_cursor', {'x': round(cx), 'y': round(cy)})
    key('LEFTMETA', True)
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'press'})
    for step in range(1, 11):
        ipc('stipc/move_cursor', {'x': round(cx+(x-cx)*step/10), 'y': round(cy+(y-cy)*step/10)})
        time.sleep(.03)
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'release'})
    key('LEFTMETA', False)
    ipc('stipc/move_cursor', {'x': 640, 'y': 10})
    time.sleep(.8)


def spawn(name, app_id, x, y):
    ipc('stipc/move_cursor', {'x': 640, 'y': 350})
    clients.append(subprocess.Popen(['tests/headless.sh', 'run', 'foot', '-a', app_id,
        '-T', name, '-w', '400x200', 'sleep', '600'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    v = wait(lambda: next((v for v in views() if v['title'] == name and 'frame' in v), None))
    time.sleep(.5)
    drag(v['id'], x, y)
    wait(lambda: any(int(w['id']) == v['id'] for w in links()))
    return v['id']


def widget(app):
    return next(w['widget_view'] for w in links() if int(w['id']) == app)


def capture(name, scale=1):
    state, drawn, widgets = hints(), views(), links()
    (artifacts/(name+'.json')).write_text(json.dumps({'hints': state, 'views': drawn, 'widgets': widgets}, indent=2))
    run('grim', str(artifacts/(name+'.png')))
    pairs = []
    for w in widgets:
        v = next(v for v in drawn if v['id'] == w['widget_view'])
        h = next(h for h in state if h['window'] == int(w['id']))
        f, b = v['frame'], h['badge']
        label = name + ': ' + v['title']
        radius = b['size']/2
        cx, cy = b['x']+radius, b['y']+radius
        left = f['x'] < 640
        edge = f['x'] + h['dx'] + (f['width'] if left else 0)
        overlap = edge - b['x'] if left else b['x'] + b['size'] - edge
        expected = min(b['size']*.15, radius-math.sqrt(max(0, radius**2-min(radius,f['height']*.30)**2)))
        check(h['visible'] and abs(overlap-expected) < 1 and (cx > edge if left else cx < edge),
              label + ' is outside the inward edge with the specified slight overlap')
        check(abs(cy-(f['y']+f['height']/2+h['dy'])) < 1,
              label + ' stays vertically centered on its drawn widget')
        check(0 <= b['x'] and b['x']+b['size'] <= 1280 and 0 <= b['y'] and b['y']+b['size'] <= 720,
              label + ' is wholly on screen')
        check(abs(h['dx']) < .01, label + ' stays attached horizontally during declutter')
        check(b['size'] == round(2/3 * max(72*scale, min(132*scale, min(f['width'], f['height'])*.34*scale))),
              label + ' uses 2/3 of WK25 window badge sizing')
        # The independent badges-fixedsize change puts a 22px-high rounded count at
        # (0, 0) on the inward side. Its nearest end-circle is centered 11px in/down;
        # wider counts extend away from the hint and retain this same end-circle.
        if v['app_id'] != 'org.scottland.TestGravity':
            count_x = edge-11 if left else edge+11
            count_y = f['y']+h['dy']+11
            check(math.hypot(cx-count_x, cy-count_y) >= radius+11,
                  label + ' leaves the upper inward count-badge corner clear')
        pairs.append((cx, cy, radius))
    check(all(math.dist(a[:2], b[:2]) >= a[2]+b[2]+5 for i,a in enumerate(pairs) for b in pairs[:i]),
          name + ': widget hints do not overlap, with at least 5px rounding-safe clearance')
    return state


try:
    palette = Path(run('python3', '-c', "import os; print(os.path.join(os.environ['XDG_RUNTIME_DIR'], 'scottland', os.environ['WAYLAND_DISPLAY']+'.palette.json'))"))
    # Session socket names can be reused; seed this session's full palette explicitly.
    tmp = palette.with_suffix('.wk26.tmp')
    tmp.write_text(json.dumps({'scheme':'light', 'background':'#f4f5f7',
        'foreground':'#232a35', 'accent':'#3b6ea8'}))
    tmp.replace(palette)
    ipc('wayfire/set-config-options', {'scottland/sounds': False, 'place/mode': 'pointer',
        'scottland/color_scheme': 'light', 'scottland/accent_color':'#3b6ea8ff'})
    left = spawn('Left card', 'foot', 10, 230)
    right = spawn('Right card', 'foot', 1270, 230)
    custom = spawn('Custom widget', 'scottland-test-gravity', 1270, 500)
    run('busctl', '--user', 'emit', '/com/canonical/unity/launcherentry/1',
        'com.canonical.Unity.LauncherEntry', 'Update', 'sa{sv}', 'application://foot.desktop',
        '2', 'count', 'x', '7', 'count-visible', 'b', 'true')
    time.sleep(.5)
    hold()
    capture('expanded-both-rails')
    original = next(v for v in views() if v['id'] == widget(left))['frame']
    label = next(h['hint'] for h in hints() if h['window'] == left)
    for letter in label: tap(letter.upper())
    time.sleep(.5)
    check(any(int(w['id']) == left for w in links()) and
          ipc('scottland/hints')['selected'] == left and
          all(abs(next(v for v in views() if v['id'] == widget(left))['frame'][k] - original[k]) < .05
              for k in ('x', 'y', 'width', 'height')),
          'WK30 first widget hint selects without moving or opening')
    for letter in label: tap(letter.upper())
    time.sleep(.7)
    check(not any(int(w['id']) == left for w in links()),
          'WK30 second slow widget hint opens center')
    release()
    drag(left, 10, 230)
    hold()
    # Tab selection counts as selection: the next slow hint opens directly.
    while ipc('scottland/hints')['selected'] != left: tap('TAB')
    for letter in label: tap(letter.upper())
    time.sleep(.7)
    check(not any(int(w['id']) == left for w in links()), 'WK30 Tab-selected widget hint opens center')
    release()
    drag(left, 10, 230)
    key('LEFTMETA', True); tap('M'); key('LEFTMETA', False)
    wait(lambda: all(next(v for v in views() if v['id'] == widget(i))['frame']['width'] <= 97 for i in (left,right)))
    # Third-party widgets choose how to implement collapse; the fixture uses real Space.
    ipc('window-rules/focus-view', {'id': widget(custom)})
    tap('SPACE')
    time.sleep(.8)
    hold()
    capture('collapsed-both-rails')
    release()
    drag(widget(left), 1270, 230)
    hold()
    capture('stacked-right-collapsed')
    release()
    drag(widget(left), 10, 230)
    drag(widget(custom), 10, 500)
    hold()
    capture('custom-left-rail')
    release()
    drag(widget(right), 10, 230)
    hold()
    before = capture('stacked-collapsed')
    check(any(abs(h['dy']) > 5 for h in before if h['window'] in (left,right)),
          'stacked widgets declutter vertically')
    geometry = {v['id']: v['frame'] for v in views() if v['widget']}
    memories = {h['window']: h['memories'] for h in before}
    release()
    check(geometry == {v['id']: v['frame'] for v in views() if v['widget']} and
          memories == {h['window']: h['memories'] for h in hints()},
          'declutter/release never changes widget geometry or remembered placement')
    check(not any(h['visible'] or abs(h['dx']) > .01 or abs(h['dy']) > .01 for h in hints()),
          'Alt release removes hints and visual displacement')
    key('LEFTMETA', True); tap('M'); key('LEFTMETA', False)
    ipc('window-rules/focus-view', {'id': widget(custom)})
    tap('SPACE')
    time.sleep(.8)
    hold()
    capture('stacked-expanded')
    release()
    # Live text scale while Alt stays down: no restart, and corners remain clear.
    hold()
    for scale in (1.5, 3):
        tmp = palette.with_suffix('.wk26.tmp')
        tmp.write_text(json.dumps({'scheme':'dark', 'text_scale':scale,
            'background':'#1f232c', 'foreground':'#d8deea', 'accent':'#81a1c1'}))
        tmp.replace(palette)
        time.sleep(1.5)
        capture('stacked-text-'+str(scale), scale)
    release()
    drag(widget(left), 10, 10)
    drag(widget(right), 1270, 710)
    top = next(v for v in views() if v['id'] == widget(left))['frame']
    bottom = next(v for v in views() if v['id'] == widget(right))['frame']
    check(top['y'] < 40 and bottom['y']+bottom['height'] > 680,
          'real drags place the intended widgets at the top and bottom screen ends')
    hold()
    capture('screen-ends-text-3', 3)
    tap('ESC')
    time.sleep(.7)
    check(not any(h['visible'] for h in hints()), 'Esc clears all exterior hints')
    release()
finally:
    try:
        key('LEFTALT', False)
    except subprocess.CalledProcessError:
        pass  # still clean up clients if the compositor failed
    for p in clients:
        if p.poll() is None:
            p.terminate()
            p.wait(timeout=5)
    print(f'{passed} passed, {failed} failed', flush=True)
if failed:
    raise SystemExit(1)
