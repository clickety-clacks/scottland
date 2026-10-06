#!/usr/bin/env python3
"""E9: allocation lifetime for avoidance, live drag, subsurface snapshot capture and reload,
plus model-version environment writes during a terminal workload.

Invoked by offscreen-leak-test.sh inside its own headless session, whose Wayfire preloads
tests/transform-census.c: an independent census of wlroots color transforms (created, referenced
by render targets, freed), attributed by call stack, plus writes of the model version to the
environment. Wayfire 0.11 leaks one transform per render target it builds on an auxiliary buffer;
Scottland builds its own targets and holds one shared transform per plugin copy, released when the
plugin unloads. glibc keeps every value ever given to setenv; the version is written only at unload.

Each phase drives one path with real input (stipc pointer and keys; clients drawing) and checks:
the census shows that path built render targets (the mechanism ran), Scottland never holds more
than one live transform, and the screen or the compositor's geometry shows the input took effect.
  redraw   terminals redraw and one retitles under window avoidance (hint-offset references
           are required; at least fifty model versions, none written to the environment)
  drag     a live drag of a redrawing terminal
  morph    a window with a subsurface becomes a widget and is dragged back out: the morph
           captures the window every other tick through the snapshot fallback
  reload   the plugin is replaced in place: the old copy's transform is freed, a new one made,
           and the version is written to the environment once, for the new copy

Reference counts include render-target copies; they are not counts of snapshots or renders.
Goo shape/wallpaper capture and morph blend freezing are not required by this test. Captured
pixels establish redraw and the final dropped window, not all intermediate morph pixels.
configure-view is setup only and bypasses input; the drag and widget transitions use real input.
"""
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import struct
import subprocess
import sys
import time

if os.environ.get('SCOTTLAND_TEST_MODEL') != '1':
    sys.exit('run through tests/offscreen-leak-test.sh (an isolated headless session)')

artifacts, plugin, subsurface_app = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
census_path = Path(os.environ['SCOTTLAND_TEST_STATE']) / 'transform-census.json'
DEADLINE = 30   # hang deadlines, not performance budgets
passed = failed = 0
clients = []


def check(ok, message, details=None):
    global passed, failed
    print(('PASS  ' if ok else 'FAIL  ') + message + ('' if ok or details is None else f': {details}'), flush=True)
    passed += bool(ok)
    failed += not ok


sock = socket.socket(socket.AF_UNIX)
sock.settimeout(10)
sock.connect(os.environ['WAYFIRE_SOCKET'])


def ipc(method, data=None):
    body = json.dumps({'method': method, 'data': data or {}}).encode()
    sock.sendall(struct.pack('<I', len(body)) + body)
    def exactly(n):
        b = b''
        while len(b) < n:
            chunk = sock.recv(n - len(b))
            if not chunk:
                raise ConnectionError('compositor disconnected')
            b += chunk
        return b
    reply = json.loads(exactly(struct.unpack('<I', exactly(4))[0]))
    if isinstance(reply, dict) and reply.get('error'):
        raise RuntimeError(f'{method}: {reply}')
    return reply


def wait_until(predicate, what, deadline=DEADLINE, observation=None):
    end = time.monotonic() + deadline
    last = None
    while time.monotonic() < end:
        last = predicate()
        if last:
            return last
        time.sleep(.1)
    raise AssertionError(f'timed out waiting for {what}; last: {observation() if observation else last}')


def census():
    return json.loads(census_path.read_text())


def newer_census(after):
    """A census written after `after` (seconds since the epoch), so it includes everything before."""
    return wait_until(lambda: census_path.stat().st_mtime > after + .15 and census(), 'a newer census')


def views():
    return ipc('scottland/layout-state')['views']


def view(title):
    return next((v for v in views() if v['title'] == title), None)


def widgets():
    return ipc('scottland/widgets')['widgets']


def region(x, y, w, h):
    """Captured pixels of a screen rectangle (PPM bytes after the header)."""
    out = subprocess.run(['grim', '-t', 'ppm', '-g', f'{round(x)},{round(y)} {round(w)}x{round(h)}', '-'],
                         check=True, capture_output=True).stdout
    return out[out.index(b'\n255\n') + 5:]


def count_color(pixels, rgb, tolerance=24):
    return sum(all(abs(pixels[i + c] - rgb[c]) <= tolerance for c in range(3)) for i in range(0, len(pixels), 3))


def key(name, down):
    ipc('stipc/feed_key', {'key': 'KEY_' + name, 'state': down})


def pointer(x, y):
    ipc('stipc/move_cursor', {'x': round(x), 'y': round(y)})


def super_drag(from_xy, to_xy, steps, pause):
    pointer(*from_xy)
    key('LEFTMETA', True)
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'press'})
    try:
        for i in range(1, steps + 1):
            pointer(from_xy[0] + (to_xy[0] - from_xy[0]) * i / steps, from_xy[1] + (to_xy[1] - from_xy[1]) * i / steps)
            time.sleep(pause)   # paces the gesture
    finally:
        ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'release'})
        key('LEFTMETA', False)


def center(frame):
    return frame['x'] + frame['width'] / 2, frame['y'] + frame['height'] / 2


def settled_frame(get_view, what):
    """Wait for positive frame bounds to agree across three consecutive observations."""
    previous = None
    matching = 0
    last = None
    def ready():
        nonlocal previous, matching, last
        last = get_view()
        frame = last and last.get('frame')
        bounds = frame and tuple(frame[k] for k in ('x', 'y', 'width', 'height'))
        if not bounds or bounds[2] <= 0 or bounds[3] <= 0:
            previous, matching = None, 0
            return False
        matching = matching + 1 if bounds == previous else 1
        previous = bounds
        return last if matching >= 3 else False
    return wait_until(ready, what, observation=lambda: last)


def place(client, geometry):
    """One setup request, then observe the mapped geometry and settled frame."""
    ipc('window-rules/configure-view', {'id': client['id'], 'geometry': geometry})
    last = None
    def placed():
        nonlocal last
        last = next((v for v in ipc('window-rules/list-views') if v['id'] == client['id']), None)
        actual = last and last.get('geometry')
        return actual and all(abs(actual[k] - value) <= 1 for k, value in geometry.items())
    wait_until(placed, 'the configured client geometry', observation=lambda: last)
    return settled_frame(lambda: next((v for v in views() if v['id'] == client['id']), None),
                         'the configured frame to settle')


def terminal(title, retitle=False):
    shown = f'{title} {{n}}' if retitle else title
    code = ('import sys,time\nn=0\nwhile True:\n'
            f' sys.stdout.write(f"\\033]0;{shown}\\007{title} line {{n}}\\n"); sys.stdout.flush(); n+=1; time.sleep(.03)\n')
    clients.append(subprocess.Popen(['foot', '-c', '/dev/null', '-T', title, 'python3', '-u', '-c', code],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
    if retitle:
        return wait_until(lambda: next((v for v in views() if v['title'].startswith(title) and 'frame' in v), None),
                          'terminal ' + title)
    return wait_until(lambda: view(title) and 'frame' in view(title) and view(title), 'terminal ' + title)


def phase(name):
    """Census at the start of a phase; returns a function giving its deltas and the latest census."""
    start = newer_census(time.time())
    start_version = ipc('scottland/desktop-model')['version']
    def progress(latest=None):
        latest = census() if latest is None else latest
        delta = {k: latest['ref_tags'][k] - start['ref_tags'][k] for k in latest['ref_tags']}
        delta['refs_scottland'] = latest['refs_scottland'] - start['refs_scottland']
        delta['created_scottland'] = latest['created_scottland'] - start['created_scottland']
        delta['version_setenv'] = latest['version_setenv'] - start['version_setenv']
        delta['versions'] = ipc('scottland/desktop-model')['version'] - start_version
        return delta, latest
    def end():
        delta, latest = progress(newer_census(time.time()))
        (artifacts / f'census-{name}.json').write_text(json.dumps({'start': start, 'end': latest, 'delta': delta}, indent=1))
        return delta, latest
    end.progress = progress
    return end


def wait_for_work(end, tag=None, versions=0):
    """Wait for a work count, without requiring any work rate or leak-free outcome."""
    def ready():
        delta, latest = end.progress()
        return (delta, latest) if delta['versions'] >= versions and (not tag or delta[tag] > 0) else False
    return wait_until(ready, f'phase work (versions >= {versions}, reference tag {tag})', deadline=120,
                      observation=lambda: end.progress()[0])


def held(latest, name):
    check(latest['live_scottland'] <= 1 and not latest['overflow'],
          f'{name}: Scottland holds at most one live color transform', latest)


try:
    ipc('wayfire/set-config-options', {'output:HEADLESS-1/mode': '2560x1600@60000',
                                       'scottland/window_avoidance_always': True})
    wait_until(lambda: census_path.exists(), 'the census (is tests/transform-census.c preloaded?)')

    # --- redraw ---
    end = phase('redraw')
    terms = [terminal(f'leak-term-{i}') for i in range(3)]
    retitler = terminal('leak-retitle', retitle=True)
    place(retitler, {'x': 100, 'y': 1000, 'width': 500, 'height': 400})
    for t, x in zip(terms, (500, 900, 1300)):
        place(t, {'x': x, 'y': 300, 'width': 700, 'height': 600})
    first = view('leak-term-0')['frame']
    shot = lambda: region(first['x'] + 20, first['y'] + 40, 300, 200)
    before = shot()
    wait_until(lambda: shot() != before, 'a terminal redraw to reach the screen')
    wait_for_work(end, tag='9view_2d_t10instance_t', versions=50)
    delta, latest = end()
    check(delta['9view_2d_t10instance_t'] > 0, f'redraw: {delta["9view_2d_t10instance_t"]} hint-offset reference events', delta)
    held(latest, 'redraw')
    check(delta['versions'] >= 50 and delta['version_setenv'] == 0,
          f'redraw: {delta["versions"]} model versions, none written to the environment', delta)

    # --- drag ---
    end = phase('drag')
    t = view('leak-term-1')
    start_xy = center(t['frame'])
    super_drag(start_xy, (start_xy[0] - 400, start_xy[1] + 250), steps=60, pause=.03)
    moved = wait_until(lambda: (f := view('leak-term-1')['frame']) and abs(center(f)[0] - start_xy[0] + 400) < 40 and f,
                       'the dragged terminal to land')
    wait_for_work(end, tag='live_drag_transform_t')
    delta, latest = end()
    check(delta['live_drag_transform_t'] > 0, f'drag: {delta["live_drag_transform_t"]} live-drag reference events', delta)
    check(abs(center(moved)[1] - start_xy[1] - 250) < 40, 'drag: the window followed the pointer', moved)
    held(latest, 'drag')

    # --- morph (snapshot fallback) ---
    end = phase('morph')
    clients.append(subprocess.Popen([subsurface_app, 'leak-subsurface'], stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, start_new_session=True))
    app = wait_until(lambda: view('leak-subsurface') and 'frame' in view('leak-subsurface') and view('leak-subsurface'),
                     'the subsurface client')
    frame = place(app, {'x': 1000, 'y': 500, 'width': 480, 'height': 320})['frame']
    super_drag(center(frame), (2550, 400), steps=30, pause=.03)
    def card_view():
        link = next((w for w in widgets() if int(w['id']) == app['id'] and w.get('widget_view')), None)
        return link and next((v for v in views() if v['id'] == int(link['widget_view']) and 'frame' in v), None)
    card = settled_frame(card_view, 'the window to become a widget with a settled card')
    # Back out, slowly: every other morph tick captures the window, which has a subsurface.
    super_drag(center(card['frame']), (1300, 700), steps=80, pause=.05)
    wait_until(lambda: not any(int(w['id']) == app['id'] for w in widgets()), 'the widget to become a window again')
    last_pixels = None
    def dropped_pixels():
        global last_pixels
        v = view('leak-subsurface')
        back = v and v.get('frame')
        if not back or back['width'] <= 0 or back['height'] <= 0:
            last_pixels = {'frame': back}
            return False
        blue = count_color(region(back['x'], back['y'], back['width'], back['height']), (0x20, 0x50, 0xc0))
        last_pixels = {'frame': back, 'blue': blue}
        return (back, blue) if blue > back['width'] * back['height'] * .3 else False
    back, blue = wait_until(dropped_pixels, 'the dropped window blue pixels', observation=lambda: last_pixels)
    wait_for_work(end, tag='widget_image_t7capture')
    delta, latest = end()
    check(delta['widget_image_t7capture'] > 0,
          f'morph: {delta["widget_image_t7capture"]} capture-fallback reference events for a subsurface window', delta)
    check(blue > back['width'] * back['height'] * .3, f'morph: the window is drawn where it was dropped ({blue} blue pixels)', back)
    held(latest, 'morph')

    # --- reload ---
    end = phase('reload')
    before_model = ipc('scottland/desktop-model')
    fresh = artifacts / f'libscottland-{time.monotonic_ns()}.so'
    shutil.copyfile(plugin, fresh)
    plugins = ipc('wayfire/get-config-option', {'option': 'core/plugins'})['value']
    mark = Path(os.environ['XDG_RUNTIME_DIR']) / 'scottland' / (os.environ['WAYLAND_DISPLAY'] + '.reloading')
    mark.touch()
    try:
        ipc('wayfire/set-config-options', {'core/plugins': ' '.join(
            str(fresh) if p == 'scottland' or '/libscottland' in p else p for p in plugins.split())})
        def reloaded():
            try:
                return ipc('scottland/desktop-model')['version'] > before_model['version']
            except RuntimeError as error:
                if 'No such method' in str(error):
                    return False
                raise
        wait_until(reloaded, 'the reloaded plugin')
    finally:
        mark.unlink(missing_ok=True)
    first = view('leak-term-0')['frame']
    before = shot()
    wait_until(lambda: shot() != before, 'redraws to reach the screen after the reload')
    wait_for_work(end, tag='refs_scottland')
    delta, latest = end()
    fresh.unlink()
    check(delta['created_scottland'] == 1 and delta['refs_scottland'] > 0,
          'reload: the new plugin copy made one transform and rendered through it', delta)
    check(latest['live_scottland'] == 1, 'reload: the old copy released its transform', latest)
    check(delta['version_setenv'] == 1, 'reload: the version was written to the environment once, at unload', delta)
    held(latest, 'reload')
finally:
    for client in clients:
        try:
            os.killpg(client.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    for client in clients:
        client.wait()

print(f'{passed} passed, {failed} failed')
sys.exit(1 if failed else 0)
