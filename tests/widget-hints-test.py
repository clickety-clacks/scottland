#!/usr/bin/env python3
"""WK26: actual Alt holds and drags, card/custom widgets, both rails and text sizes."""
import json
import math
import subprocess
import sys
import time
from pathlib import Path
from headless_palette import replace_session_file, write_session_file

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


def write_session_palette(value):
    # The test driver runs outside the canonical wrapper's private runtime
    # namespace. Write the palette from a wrapper-owned process so the
    # compositor sees the same session file that the test configures.
    run('python3', '-c', '''
import os, sys
from pathlib import Path
palette = Path(os.environ['SCOTTLAND_SESSION_DIR']) / (os.environ['WAYLAND_DISPLAY'] + '.palette.json')
tmp = palette.with_suffix('.wk26.tmp')
tmp.write_text(sys.argv[1])
tmp.replace(palette)
''', json.dumps(value))


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
        check(b['size'] == round(48*scale),
              label + ' retains the consistent WK30 circle size and desktop text scaling')
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


def wait_hint_settled(window_id):
    # Hint circles ease to a new attachment over 160 ms. Sample only after both
    # the widget frame and badge have stopped moving, so geometry assertions do
    # not depend on which animation frame the IPC/screenshot happened to catch.
    previous = None
    stable_samples = 0

    def settled():
        nonlocal previous, stable_samples
        view = next(v for v in views() if v['id'] == widget(window_id))
        hint = next(h for h in hints() if h['window'] == window_id)
        frame, badge = view['frame'], hint['badge']
        sample = tuple(float(value) for value in (
            frame['x'], frame['y'], frame['width'], frame['height'],
            badge['x'], badge['y'], badge['size'], hint['dx'], hint['dy']))
        if previous is not None and max(abs(a-b) for a, b in zip(sample, previous)) < .05:
            stable_samples += 1
        else:
            stable_samples = 0
        previous = sample
        return stable_samples >= 3

    wait(settled)


try:
    # Ask the headless session for its palette path and seed it through that session namespace.
    palette = Path(run('python3', '-c', "import os; print(os.path.join(os.environ['SCOTTLAND_SESSION_DIR'], os.environ['WAYLAND_DISPLAY']+'.palette.json'))"))
    # Session socket names can be reused; seed this session's full palette explicitly.
    tmp = palette.with_suffix('.wk26.tmp')
    write_session_file(tmp, json.dumps({'scheme':'light', 'background':'#f4f5f7',
        'foreground':'#232a35', 'accent':'#3b6ea8'}))
    replace_session_file(tmp, palette)
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
    # Window mode shows collapsed widgets expanded (WG16), and their clients resize then: lay
    # out the stacked fixture expanded, where Alt changes no widget's size or placement.
    ipc('scottland/widget-mode', {'mode': 'expanded'})
    ipc('window-rules/focus-view', {'id': widget(custom)})
    tap('SPACE')
    time.sleep(.8)
    # WG26 commits space made during the real rail drop, so this sequence no longer
    # leaves stacked cards. Recreate a pre-existing overlap with fixture-only
    # geometry setup to keep exercising hint decluttering for overlapping widgets.
    left_card, right_card = widget(left), widget(right)
    left_frame = next(v for v in views() if v['id'] == left_card)['frame']
    right_frame = next(v for v in views() if v['id'] == right_card)['frame']
    ipc('window-rules/configure-view', {'id': left_card, 'geometry': {
        'x': right_frame['x'] if right_frame['x'] < 640 else  # rail-side edges aligned
             right_frame['x'] + right_frame['width'] - left_frame['width'], 'y': right_frame['y'],
        'width': left_frame['width'], 'height': left_frame['height']}})
    wait(lambda: abs(next(v for v in views() if v['id'] == left_card)['frame']['y'] -
                     right_frame['y']) < .1)
    # This assertion covers temporary Window-mode offsets. The shipped default
    # keeps avoidance active outside the mode, so opt out for this scenario.
    ipc('wayfire/set-config-options', {'scottland/window_avoidance_always': False,
        'scottland/hint_avoidance_always': False})
    hold()
    before = capture('stacked-expanded')
    check(any(abs(h['dy']) > 5 for h in before if h['window'] in (left,right)),
          'stacked widgets declutter vertically')
    geometry = {v['id']: tuple(v['frame'][k] for k in ('x', 'y', 'width', 'height'))
        for v in views() if v['widget']}
    memories = {h['window']: h['memories'] for h in before}
    release()
    after_geometry = {v['id']: tuple(v['frame'][k] for k in ('x', 'y', 'width', 'height'))
        for v in views() if v['widget']}
    after_memories = {h['window']: h['memories'] for h in hints()}
    check(geometry == after_geometry, 'declutter/release never changes widget geometry')
    check(memories == after_memories, 'declutter/release never changes remembered placement')
    check(not any(h['visible'] or abs(h['dx']) > .01 or abs(h['dy']) > .01 for h in hints()),
          'Alt release removes hints and visual displacement')
    # Live text scale while Alt stays down: no restart, and corners remain clear.
    hold()
    for scale in (1.5, 3):
        tmp = palette.with_suffix('.wk26.tmp')
        write_session_file(tmp, json.dumps({'scheme':'dark', 'text_scale':scale,
            'background':'#1f232c', 'foreground':'#d8deea', 'accent':'#81a1c1'}))
        replace_session_file(tmp, palette)
        time.sleep(1.5)
        capture('stacked-text-'+str(scale), scale)
    release()
    # Restore the shipped defaults before the remaining scenarios.
    ipc('wayfire/set-config-options', {'scottland/window_avoidance_always': True,
        'scottland/hint_avoidance_always': False})
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

    # WK34: isolate the timed hint selection after the existing placement invariants.
    key('LEFTMETA', True); tap('M'); key('LEFTMETA', False)
    wait(lambda: all(next(v for v in views() if v['id'] == widget(i))['frame']['width'] <= 97
        for i in (left, right)))
    hold()
    # WG16 already expands the cards while Alt is held. Preserve the badge
    # assertions here, before selection starts the five-second peek clock.
    wait_hint_settled(left)
    capture('wk34-hint-peek-expanded', 3)
    left_card, right_card = widget(left), widget(right)
    left_label = next(h['hint'] for h in hints() if h['window'] == left)
    for letter in left_label[:-1]: tap(letter.upper())
    # The final hint character selects the widget and starts its five-second
    # peek. Bracket selection so IPC delay is not counted as product duration.
    selection_before = time.monotonic()
    tap(left_label[-1].upper())
    wait(lambda: ipc('scottland/hints')['selected'] == left and
        next(v for v in views() if v['id'] == left_card)['frame']['width'] > 100 and
        next(w for w in links() if int(w['id']) == left)['peek'])
    peek_started = time.monotonic()
    # Sample immediately after release, before the five-second peek can expire.
    key('LEFTALT', False)
    expanded = next(v for v in views() if v['id'] == left_card)['frame']['width'] > 100
    peeking = next(w for w in links() if int(w['id']) == left)['peek']
    check(expanded and peeking and time.monotonic() - peek_started < 5,
        'WK34 selected collapsed widget stays expanded after Alt release')
    # Alt-up starts the other card's collapse morph. Poll its width rather than
    # depending on IPC latency to outlast the animation; leave time to observe
    # the selected card's expiry independently. A missing/unsettled frame fails.
    collapse_deadline = min(time.monotonic() + 2, peek_started + 4.5)
    right_collapsed = False
    while time.monotonic() < collapse_deadline:
        right_view = next((v for v in views() if v['id'] == right_card), None)
        width = (right_view or {}).get('frame', {}).get('width')
        if width is not None and width <= 97:
            right_collapsed = time.monotonic() < collapse_deadline
            break
        time.sleep(min(.05, max(0, collapse_deadline - time.monotonic())))
    check(right_collapsed,
        'WG16 the other collapsed widget collapsed again when Alt was released')
    # A positive peek observation proves expiry has not happened yet; a narrow
    # card with no peek proves the visible collapse is complete. Timestamp each
    # request before and after it rather than charging its response delay to the
    # product. Use the saved card ID to avoid an extra lookup on every poll.
    collapse_before = selection_before
    collapse_deadline = time.monotonic() + 10
    while True:
        width = next(v for v in views() if v['id'] == left_card)['frame']['width']
        observed_before = time.monotonic()
        peeking = next(w for w in links() if int(w['id']) == left)['peek']
        observed_after = time.monotonic()
        if peeking:
            collapse_before = observed_before
        if width <= 97 and not peeking:
            break
        if observed_after >= collapse_deadline:
            raise RuntimeError('timed out waiting for widget peek collapse')
        time.sleep(.05)
    # Selection lies between the final tap request and its confirmation, and
    # collapse between the last positive peek read and the final response.
    # Reject only durations proven outside the unchanged permitted interval.
    peek_min = max(0, collapse_before - peek_started)
    peek_max = observed_after - selection_before
    print(f'WK34 selection-to-collapse duration bracket: [{peek_min:.3f}, {peek_max:.3f}]s', flush=True)
    if peek_min < 4.5 and peek_max > 5.9:
        print('WARN  WK34 timing observation is inconclusive: IPC bracket spans both duration limits; '
              'continuing without a duration verdict', flush=True)
    else:
        check(peek_max >= 4.5 and peek_min <= 5.9,
            f'WK34 timed hint peek collapses on its own after about five seconds '
            f'(duration bracket [{peek_min:.2f}, {peek_max:.2f}]s)')
    hold()

    ipc('wayfire/set-config-options', {'scottland/window_double_tap_delay': 3000})
    right_label = next(h['hint'] for h in hints() if h['window'] == right)
    for letter in right_label: tap(letter.upper())
    wait(lambda: ipc('scottland/hints')['selected'] == right and
        next(v for v in views() if v['id'] == widget(right))['frame']['width'] > 100)
    wait_hint_settled(right)
    capture('wk34-before-center-cycle', 3)
    time.sleep(.45)  # inside both the double-tap interval and the five-second peek
    for letter in right_label: tap(letter.upper())
    wait(lambda: not any(int(w['id']) == right for w in links()))
    check(not any(int(w['id']) == right for w in links()) and
        not next(v for v in views() if v['id'] == right)['widgetized'],
        'WK34 second hint press within five seconds cycles the widget to center')
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
