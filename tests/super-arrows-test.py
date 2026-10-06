#!/usr/bin/env python3
"""WK40: Super+arrows focus and raise the neighboring window. Runs inside an isolated headless
session (tests/super-arrows-test.sh starts it). Input is real (stipc keys and pointer); results are
judged by what the clients receive (activation, the next typed key, pointer events) and by pixels.
Window placement, minimizing and full screen are fixture setup through IPC.
   super-arrows-test.py ARTIFACTS [SCENARIO ...] [--reload NEW_SO SESSION_XML NEW_XML]
--reload first reloads this session's plugin in place into NEW_SO, as scottland-reload does, then
runs the scenarios on the reloaded build."""
import json
import os
import signal
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import time

args = sys.argv[1:]
out = Path(args.pop(0)).resolve(); out.mkdir(parents=True, exist_ok=True)
reload_args = None
if '--reload' in args:
    at = args.index('--reload'); reload_args = args[at + 1:at + 4]; args = args[:at] + args[at + 4:]
scenarios = args or ['cross', 'zones', 'hidden', 'concentric', 'peek']
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
signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))


def check(name, ok, detail=None):
    global passes, failures
    passes += bool(ok); failures += not ok
    print(('PASS ' if ok else 'FAIL ') + name + ('' if ok or detail is None else ': ' + json.dumps(detail, default=str)),
          flush=True)


def wait_for(fn, timeout=6, what=None):
    """Poll a predicate; a deadline that expires raises with the last observation."""
    deadline = time.monotonic() + timeout
    value = None
    while time.monotonic() < deadline:
        value = fn()
        if value: return value
        time.sleep(.03)
    raise AssertionError(f'timed out waiting for {what or getattr(fn, "__name__", "state")}; last: {value!r}')


held = set()  # keys this test holds down, released on any exit


def key(name, down):
    ipc('stipc/feed_key', {'key': 'KEY_' + name, 'state': down})
    (held.add if down else held.discard)(name)
def tap(name): key(name, True); key(name, False)
def pointer(x, y): ipc('stipc/move_cursor', {'x': round(x), 'y': round(y)})


def super_arrow(direction):
    """One physical Super+arrow press: no retries."""
    key('LEFTMETA', True); tap(direction); key('LEFTMETA', False)


def views(): return ipc('scottland/layout-state')['views']
def view(title): return next((v for v in views() if v['title'] == title), None)
def by_id(id): return next((v for v in views() if v['id'] == id), None)
def rect(r): return [r['x'], r['y'], r['x'] + r['width'], r['y'] + r['height']]
def center(r): return ((r[0] + r[2]) / 2, (r[1] + r[3]) / 2)
def geometry(id): return next(v['geometry'] for v in ipc('window-rules/list-views') if v['id'] == id)
def minimized(id): return next(v for v in ipc('window-rules/list-views') if v['id'] == id)['minimized']


def has_option(name):
    try: ipc('wayfire/get-config-option', {'option': name}); return True
    except RuntimeError: return False


def set_options(options): ipc('wayfire/set-config-options', options)


clients, apps = [], {}


def launch(title, color, w, h):
    log = out / f'{title}.log'; log.write_text('')
    clients.append(subprocess.Popen([sys.executable, str(Path(__file__).with_name('nav-app.py')),
                                     title, color, str(w), str(h), str(log)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    id = wait_for(lambda: (view(title) or {}).get('id'), 10, 'launch ' + title)
    apps[title] = {'id': id, 'log': log, 'color': color}
    return id


def stop_clients():
    for c in clients: c.terminate()
    for c in clients:
        try: c.wait(5)
        except subprocess.TimeoutExpired:
            c.kill(); c.wait()


def close_all():
    stop_clients()
    titles = list(apps)
    clients.clear(); apps.clear()
    wait_for(lambda: not any(view(t) for t in titles), 8, 'windows to close')


def events(title):
    return [json.loads(line) for line in apps[title]['log'].read_text().splitlines() if line.strip()]


def marks(): return {t: len(events(t)) for t in apps}
def since(title, mark): return events(title)[mark[title]:]


def place(id, cx, cy, w, h, output=None):
    """Fixture: a window of w x h centered at (cx, cy) on its screen (or `output`), placed until the
    client has committed it."""
    x, y = round(cx - w / 2), round(cy - h / 2)
    for _ in range(8):
        data = {'id': id, 'geometry': {'x': x, 'y': y, 'width': w, 'height': h}}
        if output is not None: data['output_id'] = output
        ipc('window-rules/configure-view', data)
        try:
            wait_for(lambda: (lambda g: (g['x'], g['y'], g['width'], g['height']) == (x, y, w, h))(geometry(id)), 1.5)
            return
        except AssertionError:
            pass
    raise RuntimeError(f'fixture placement {w}x{h}+{x}+{y} -> {geometry(id)}')


def focus(title):
    """Fixture: focus and raise through IPC, then wait for the client to see it."""
    ipc('window-rules/focus-view', {'id': apps[title]['id']})
    wait_for(lambda: active_client() == title, 4, 'fixture focus ' + title)


def active_client():
    """The app that is active according to its own last activation event."""
    for t in apps:
        states = [e['active'] for e in events(t) if 'active' in e]
        if states and states[-1]: return t
    return None


def probe(mark):
    """Type one key; the app that receives it holds keyboard focus. Waits for exactly one receiver."""
    tap('A')
    def receivers():
        got = [t for t in apps if any(e.get('key') == 'a' for e in since(t, mark))]
        return got or None
    got = wait_for(receivers, 4, 'the typed key to arrive')
    return got[0] if len(got) == 1 else got


def navigate_case(label, direction, expect, start=None):
    """Press Super+arrow once and check the result from the clients: the expected app becomes
    active (or, with expect=None, focus stays on `start`), gets the next typed key, and no app
    receives the arrow itself."""
    mark = marks()
    super_arrow(direction)
    receiver = probe(mark)
    want = expect or start
    arrows = {t: [e['key'] for e in since(t, mark) if e.get('key') in ('Left', 'Right', 'Up', 'Down')] for t in apps}
    changed = {t: [e['active'] for e in since(t, mark) if 'active' in e] for t in apps}
    ok = receiver == want
    if expect: ok = ok and changed.get(expect, [])[-1:] == [True]
    else: ok = ok and not any(changed.values())
    check(f'{label}: Super+{direction.title()} {"focuses " + expect if expect else "does nothing"}', ok,
          {'key_went_to': receiver, 'activation': changed})
    check(f'{label}: no app receives the arrow key', not any(arrows.values()), arrows)
    return receiver


def pixel(name, x, y):
    path = out / f'{name}.png'
    subprocess.run(['grim', str(path)], check=True)
    return subprocess.check_output(['magick', str(path), '-format', f'%[hex:p{{{round(x)},{round(y)}}}]', 'info:'],
                                   text=True).strip().lower()[:6]


def shows(name, x, y, color, timeout=4):
    """Wait for (x, y) on screen to show exactly this window color; returns the last pixel seen."""
    seen = []
    try:
        wait_for(lambda: seen.append(pixel(name, x, y)) or seen[-1] == color, timeout, f'{color} at {x},{y}')
    except AssertionError:
        pass
    return seen[-1] if seen else None


def drawn(title):
    v = view(title)
    return rect(v['scene_frame'] if 'scene_frame' in v else v['frame'])


def hint_row(id): return next((h for h in ipc('scottland/hints')['hints'] if h['window'] == id), None)


def reload_in_place(new_so, session_xml, new_xml):
    """As scottland-reload does it: the new build's metadata, a fresh plugin copy, the hand-over
    mark, and the config rebuilt from this checkout (here: the [grid] keys it no longer binds)."""
    pre = launch('reload-pre', '808080', 300, 200)
    shutil.copy(new_xml, session_xml)
    ipc('wayfire/reload-config-metadata')
    mark = Path(os.environ['XDG_RUNTIME_DIR']) / 'scottland' / (os.environ['WAYLAND_DISPLAY'] + '.reloading')
    mark.parent.mkdir(parents=True, exist_ok=True); mark.touch()
    fresh = out / f'libscottland-rehearsal-{time.time_ns()}.so'
    shutil.copy(new_so, fresh)
    plugins = ipc('wayfire/get-config-option', {'option': 'core/plugins'})['value']
    ipc('wayfire/set-config-options', {'core/plugins': ' '.join(
        str(fresh) if p == 'scottland' or '/libscottland' in p else p for p in plugins.split())})
    set_options({'grid/slot_l': '<super> KEY_KP4', 'grid/slot_c': '<super> KEY_KP5',
                 'grid/slot_r': '<super> KEY_KP6', 'grid/restore': '<super> KEY_KP0'})
    wait_for(lambda: has_option('scottland/navigate_left'), 10, 'the new build\'s options')
    wait_for(lambda: by_id(pre) is not None, 10, 'the window to survive the reload')
    check('reload: the session survives the reload with its window', by_id(pre) is not None)
    close_all()


try:
    names = [o['name'] for o in ipc('window-rules/list-outputs')]
    set_options({'scottland/sounds': False, **{f'output:{n}/mode': '1920x1080@60000' for n in names}})
    wait_for(lambda: all(o['geometry']['width'] == 1920 for o in ipc('window-rules/list-outputs')), 6, 'output mode')
    if reload_args: reload_in_place(*reload_args)
    W, H = 1920, 1080

    if 'cross' in scenarios:
        # A wide center zone keeps all five at full scale; L overlaps M so a raise is visible.
        set_options({'scottland/center_width': 80.0, 'scottland/window_avoidance_always': False})
        colors = {'M': '2050f0', 'L': 'e03020', 'R': '20a040', 'U': 'd0a000', 'D': '9030c0'}
        spec = {'M': (960, 540, 500, 340), 'L': (560, 560, 420, 300), 'R': (1440, 520, 360, 300),
                'U': (960, 210, 420, 180), 'D': (960, 880, 420, 180)}
        for t, c in colors.items(): launch('nav-' + t, c, *spec[t][2:])
        for t, (cx, cy, w, h) in spec.items(): place(apps['nav-' + t]['id'], cx, cy, w, h)
        for t in ('L', 'R', 'U', 'D', 'M'): focus('nav-' + t)
        overlap = (740, 560)  # inside both M and L, M in front
        check('cross: fixture: M is in front where it overlaps L', shows('cross-before', *overlap, colors['M']) == colors['M'])
        # The pointer rests on R where nothing else is; it must neither move nor freeze.
        rest = (1560, 520)
        mark = marks(); pointer(*rest)
        first = wait_for(lambda: [e for e in since('nav-R', mark) if e.get('pointer') in ('enter', 'motion')], 4,
                         'pointer on R')[-1]
        pointer_mark = marks()

        navigate_case('cross', 'LEFT', 'nav-L', 'nav-M')
        seen = shows('cross-raised-L', *overlap, colors['L'])
        check('cross: the focused window is raised (its color shows where it overlapped M)', seen == colors['L'], seen)
        navigate_case('cross', 'LEFT', None, 'nav-L')          # leftmost: no wrap-around
        navigate_case('cross', 'RIGHT', 'nav-M', 'nav-L')      # the nearest center to the right
        navigate_case('cross', 'RIGHT', 'nav-R')
        navigate_case('cross', 'RIGHT', None, 'nav-R')         # rightmost
        navigate_case('cross', 'LEFT', 'nav-M')
        navigate_case('cross', 'UP', 'nav-U')                  # nearer than R, whose center is a little higher
        navigate_case('cross', 'UP', None, 'nav-U')
        navigate_case('cross', 'DOWN', 'nav-M')                # nearer than D, L and R below U
        navigate_case('cross', 'DOWN', 'nav-D')
        navigate_case('cross', 'DOWN', None, 'nav-D')

        pointer_events = {t: [e for e in since(t, pointer_mark) if 'pointer' in e] for t in apps}
        check('cross: navigating never moved the pointer (no pointer event anywhere)',
              not any(pointer_events.values()), pointer_events)
        mark = marks(); pointer(rest[0] + 7, rest[1] + 3)
        moved = wait_for(lambda: [e for e in since('nav-R', mark) if e.get('pointer') == 'motion'], 4, 'pointer motion')[-1]
        check('cross: the pointer still moves, exactly from where it was',
              abs(moved['x'] - first['x'] - 7) < .01 and abs(moved['y'] - first['y'] - 3) < .01, [first, moved])
        close_all()

    if 'zones' in scenarios:
        # Shipped zones: a narrow center. Q sits in the center zone, P in the right periphery, K on
        # the left rail as a widget.
        set_options({'scottland/center_width': 33.333, 'scottland/window_avoidance_always': False})
        colors = {'O': '2050f0', 'Q': 'e03020', 'P': '20a040', 'K': 'd0a000'}
        spec = {'O': (960, 540, 400, 300), 'Q': (1260, 740, 300, 200), 'P': (1700, 350, 360, 700), 'K': (300, 300, 300, 200)}
        for t, c in colors.items(): launch('zone-' + t, c, *spec[t][2:])
        for t, (cx, cy, w, h) in spec.items(): place(apps['zone-' + t]['id'], cx, cy, w, h)
        # K goes to its rail (a widget) by the Window mode double-tap, real keys.
        K = apps['zone-K']['id']
        key('LEFTALT', True)
        wait_for(lambda: ipc('scottland/hints')['active'], 4, 'Window mode')
        letters = wait_for(lambda: (hint_row(K) or {}).get('hint'), 4, 'K hint')
        for _ in range(2):
            for c in letters: tap(c.upper())
        key('LEFTALT', False)
        wait_for(lambda: by_id(K)['widgetized'], 6, 'K to become a widget')
        for t in ('P', 'Q', 'O'): focus('zone-' + t)
        wait_for(lambda: abs(view('zone-P')['applied_scale'] - view('zone-P')['target_scale']) < .01, 4, 'P scale')
        check('zones: fixture: P shows scaled down in the periphery', view('zone-P')['applied_scale'] < .9,
              view('zone-P')['applied_scale'])
        navigate_case('zones', 'RIGHT', 'zone-Q', 'zone-O')     # nearer than P
        navigate_case('zones', 'RIGHT', 'zone-P', 'zone-Q')     # center to periphery
        # Widgets are targets like windows, judged by where they show on their rail.
        widget = wait_for(lambda: next((v for v in views() if v['widget'] and 'scene_frame' in v), None), 10, 'K\'s widget')
        side = 'LEFT' if center(rect(widget['scene_frame']))[0] < W / 2 else 'RIGHT'
        back = 'RIGHT' if side == 'LEFT' else 'LEFT'
        extreme = 'zone-P' if side == 'RIGHT' else 'zone-O'
        if extreme != 'zone-P': focus(extreme)
        label = f'zones (K is a widget on the {side.lower()} rail)'

        def to_widget(why):
            mark = marks()
            super_arrow(side)
            lost = wait_for(lambda: since(extreme, mark) and [e for e in since(extreme, mark) if e.get('active') is False],
                            4, extreme + ' to lose focus')
            tap('A')
            # Nothing else gains focus and the typed key reaches no window app: the widget has it.
            # The Return press below confirms it: Return on a focused widget opens it (WG25).
            others = {t: [e for e in since(t, mark) if e.get('active') is True or e.get('key') == 'a'] for t in apps}
            check(f'{label}: Super+{side.title()} {why} focuses the widget (no window keeps or gains focus)',
                  bool(lost) and not any(others.values()), others)
            return mark
        to_widget('from the outermost window')
        navigate_case(label + ', from the widget', back, extreme)
        mark = to_widget('again')
        tap('ENTER')
        wait_for(lambda: any(e.get('active') is True for e in since('zone-K', mark)), 6, 'K to open and take focus')
        check(f'{label}: Return then opens the focused widget into its window (WG25), which takes focus',
              not by_id(K)['widgetized'])
        close_all()

    if 'hidden' in scenarios:
        # A minimized window is drawn nowhere, so it is in no direction; a window behind a full-
        # screen one is still where it is, so it is a neighbor like any other.
        set_options({'scottland/center_width': 80.0, 'scottland/window_avoidance_always': False})
        colors = {'A': '2050f0', 'B': 'e03020', 'C': '20a040'}
        spec = {'A': (1100, 540, 360, 260), 'B': (700, 540, 300, 220), 'C': (350, 540, 300, 220)}
        for t, c in colors.items(): launch('hidden-' + t, c, *spec[t][2:])
        for t, (cx, cy, w, h) in spec.items(): place(apps['hidden-' + t]['id'], cx, cy, w, h)
        for t in ('C', 'B', 'A'): focus('hidden-' + t)
        ipc('wm-actions/set-minimized', {'view_id': apps['hidden-B']['id'], 'state': True})
        wait_for(lambda: minimized(apps['hidden-B']['id']), 4, 'B minimized')
        focus('hidden-A')
        seen = shows('hidden-minimized', *spec['B'][:2], 'ffffff', timeout=1)
        check('hidden: fixture: the minimized window is not on screen', seen != colors['B'], seen)
        navigate_case('hidden (B minimized)', 'LEFT', 'hidden-C', 'hidden-A')
        ipc('wm-actions/set-minimized', {'view_id': apps['hidden-B']['id'], 'state': False})
        wait_for(lambda: not minimized(apps['hidden-B']['id']), 4, 'B restored')
        focus('hidden-A')
        navigate_case('hidden (B restored)', 'LEFT', 'hidden-B', 'hidden-A')
        focus('hidden-A')
        ipc('wm-actions/set-fullscreen', {'view_id': apps['hidden-A']['id'], 'state': True})
        wait_for(lambda: geometry(apps['hidden-A']['id'])['width'] == W, 4, 'A full screen')
        navigate_case('hidden (A is full screen, B behind it)', 'LEFT', 'hidden-B', 'hidden-A')
        seen = shows('hidden-fullscreen-raised', *spec['B'][:2], colors['B'])
        check('hidden: B is raised over the full-screen window', seen == colors['B'], seen)
        # Back again: the full-screen window is a destination too, centered on its screen.
        navigate_case('hidden (back to the full-screen window)', 'RIGHT', 'hidden-A', 'hidden-B')
        seen = shows('hidden-fullscreen-back', *spec['B'][:2], colors['A'])
        check('hidden: the full-screen window is raised over B again', seen == colors['A'], seen)
        time.sleep(1.5)  # an intended hold: evidence of the settled scene, not a readiness wait
        subprocess.run(['grim', str(out / 'hidden-fullscreen-settled.png')], check=True)
        a = view('hidden-A')
        print(f'INFO hidden: after navigating away A is full screen: {geometry(apps["hidden-A"]["id"])["width"] == W}, '
              f'drawn {a.get("scene_frame")}, scale {a["applied_scale"]:.2f} (hidden-fullscreen-settled.png)', flush=True)
        close_all()

    if 'screens' in scenarios:
        # Navigation crosses screens: neighbors are judged in the layout's coordinates.
        set_options({'scottland/center_width': 80.0, 'scottland/window_avoidance_always': False})
        outputs = sorted(ipc('window-rules/list-outputs'), key=lambda o: o['geometry']['x'])
        check('screens: fixture: two screens side by side', len(outputs) == 2, outputs)
        launch('screen-1', '2050f0', 400, 300); launch('screen-2', 'e03020', 400, 300)
        place(apps['screen-1']['id'], 1400, 540, 400, 300, outputs[0]['id'])
        place(apps['screen-2']['id'], 500, 540, 400, 300, outputs[1]['id'])
        focus('screen-1')
        navigate_case('screens', 'RIGHT', 'screen-2', 'screen-1')
        navigate_case('screens', 'RIGHT', None, 'screen-2')
        navigate_case('screens', 'LEFT', 'screen-1', 'screen-2')
        close_all()

    if 'concentric' in scenarios:
        # Coincident centers, no avoidance: a larger rear window shows around a smaller front one.
        # Neither center is on any side of the other, so they are ordered by id (opening order).
        set_options({'scottland/center_width': 80.0, 'scottland/window_avoidance_always': False})
        launch('same-front', '2050f0', 600, 400); launch('same-rear', 'e03020', 800, 600)
        front, rear = apps['same-front']['id'], apps['same-rear']['id']
        place(front, 960, 540, 600, 400); place(rear, 960, 540, 800, 600)
        focus('same-rear'); focus('same-front')
        band = (960, 540 - 250)  # inside the rear window, outside the front one
        seen = shows('concentric-before', *band, 'e03020')
        check('concentric: fixture: the rear window shows around the front one', seen == 'e03020', seen)
        later, earlier = ('RIGHT', 'LEFT') if rear > front else ('LEFT', 'RIGHT')
        navigate_case('concentric', earlier, None, 'same-front')
        navigate_case('concentric', later, 'same-rear', 'same-front')
        seen = shows('concentric-raised', 960, 540, 'e03020')
        check('concentric: the rear window is raised over the front one', seen == 'e03020', seen)
        navigate_case('concentric', earlier, 'same-front', 'same-rear')
        close_all()

    if 'peek' in scenarios:
        # Back window centered exactly behind the front one: by true centers it is in no direction.
        # Always-on avoidance makes it peek out; the arrow toward where it shows reaches it.
        set_options({'scottland/center_width': 80.0, 'scottland/window_avoidance_always': True})
        launch('peek-back', 'e03020', 520, 360); launch('peek-front', '2050f0', 760, 540)
        back, front = apps['peek-back']['id'], apps['peek-front']['id']
        place(back, 960, 540, 520, 360); place(front, 960, 540, 760, 540)
        focus('peek-back'); focus('peek-front')

        def peeking():
            row = hint_row(back)
            return row if row and abs(row['dx']) + abs(row['dy']) > 8 and \
                abs(row['dx'] - row['target_dx']) + abs(row['dy'] - row['target_dy']) < .2 else None
        row = wait_for(peeking, 8, 'the back window to peek')
        dx, dy = row['dx'], row['dy']
        toward = ('RIGHT' if dx > 0 else 'LEFT') if abs(dx) > abs(dy) else ('DOWN' if dy > 0 else 'UP')
        away = {'LEFT': 'RIGHT', 'RIGHT': 'LEFT', 'UP': 'DOWN', 'DOWN': 'UP'}[toward]
        shown_before = drawn('peek-back')
        check('peek: fixture: true centers coincide, the drawn one is offset',
              center(rect(geometry(back))) == center(rect(geometry(front))) and
              center(shown_before) != center(drawn('peek-front')), {'peek': [dx, dy]})
        navigate_case(f'peek (peeks {toward.lower()})', away, None, 'peek-front')
        navigate_case(f'peek (peeks {toward.lower()})', toward, 'peek-back', 'peek-front')
        seen = shows('peek-raised', 960, 540, 'e03020')
        check('peek: the peeking window is raised over the one that covered it', seen == 'e03020', seen)
        row = hint_row(back)
        print(f'INFO peek: back window offset after focus {row["dx"]:.1f},{row["dy"]:.1f} (target '
              f'{row["target_dx"]:.1f},{row["target_dy"]:.1f}); before {dx:.1f},{dy:.1f}', flush=True)
        close_all()
except Exception as error:
    check('scenario ran to completion', False, repr(error))
finally:
    # Independent steps: each runs even if the compositor is already gone.
    for name in list(held):
        try: key(name, False)
        except Exception: pass
    stop_clients()
    print(f'{passes} passed, {failures} failed', flush=True)
sys.exit(1 if failures else 0)
