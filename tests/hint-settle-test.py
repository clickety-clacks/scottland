#!/usr/bin/env python3
"""Hints appear only once settled (ruling 2026-10-05, WK41): judged frame by frame from pixels.

Run inside a private --widgets headless session via tests/hint-settle-test.sh. Real input: the
layout is made with Super-drags, the widgets collapse with Super+M, Front is focused by a click,
and Window mode is entered by holding Alt (stipc). Each entry is recorded losslessly with
wf-recorder from before the Alt press until every hint has settled, and every recorded frame is
judged by its pixels: a hint's letter must first appear at the spot where it settles and never
leave it again, and must never be drawn anywhere else. The scottland/hints IPC only says when
the state has settled (so the recording can stop) and where the settled hints are; it is never
the verdict.

Layout: three collapsed widgets on the left rail, three side windows beside them, one large
focused front window over all of them. Window mode expands the widgets, which re-solves window
avoidance for several ticks; before the fix the front window's hint was drawn at entry and
removed when a widget moved. Each layout is entered with window avoidance always on (side
windows already peeking) and off, and each once more with a test-only smaller avoidance slice
(fault injection standing in for a loaded machine, where a pass spans several ticks; before the
fix, hints were drawn from the previous targets and removed when the pass reached them).

Limits: wf-recorder copies frames as the compositor presents them and can skip some under load;
a disappearance shorter than its frame interval would be missed. Each recording reports its frame
count and largest gap.
"""
import json
import math
import os
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

art = Path(sys.argv[1]).resolve()
art.mkdir(parents=True, exist_ok=True)
assert os.environ.get('SCOTTLAND_TEST_MODEL') == '1', 'private headless session required'
recorder_tool = os.environ.get('SCOTTLAND_TEST_RECORDER', 'wf-recorder')
for tool in (recorder_tool, 'ffmpeg', 'ffprobe'):
    assert shutil.which(tool), tool + ' is required to record frames'

sock = socket.socket(socket.AF_UNIX)
sock.settimeout(8)
sock.connect(os.environ['WAYFIRE_SOCKET'])
clients = []
recorder = None
held = set()
button_down = False
passed = failed = 0
W, H = 1280, 720
APP = str(Path(__file__).with_name('hint-style-app.py'))
signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))


def ipc(method, data=None):
    body = json.dumps({'method': method, 'data': data or {}}).encode()
    sock.sendall(struct.pack('<I', len(body)) + body)

    def read(n):
        out = b''
        while len(out) < n:
            part = sock.recv(n - len(out))
            if not part:
                raise RuntimeError('compositor disconnected')
            out += part
        return out
    result = json.loads(read(struct.unpack('<I', read(4))[0]))
    if isinstance(result, dict) and (result.get('result') == 'error' or 'error' in result):
        raise RuntimeError(result)
    return result


def check(ok, name, detail=''):
    global passed, failed
    print(('PASS  ' if ok else 'FAIL  ') + name + (f'  [{detail}]' if detail else ''), flush=True)
    passed += bool(ok)
    failed += not ok


def key(name, down):
    ipc('stipc/feed_key', {'key': 'KEY_' + name, 'state': down})
    (held.add if down else held.discard)(name)


def tap(name):
    key(name, True)
    key(name, False)


def views():
    return {v['id']: v for v in ipc('scottland/layout-state')['views']}


def wait(predicate, what, timeout=30, observe=None):
    """Poll a state predicate; a deadline that expires fails with the last observation."""
    until = time.monotonic() + timeout
    last = None
    while time.monotonic() < until:
        last = predicate()
        if last:
            return last
        time.sleep(.02)
    raise RuntimeError(f'timed out waiting for {what}; last observation: {observe() if observe else last!r}')


def drag(identifier, x, y):
    """A real Super-drag from the window's center to (x, y)."""
    ipc('window-rules/focus-view', {'id': identifier})  # setup: expose the window to grab
    f = views()[identifier]['frame']
    cx, cy = f['x'] + f['width'] / 2, f['y'] + f['height'] / 2
    ipc('stipc/move_cursor', {'x': round(cx), 'y': round(cy)})
    global button_down
    key('LEFTMETA', True)
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'press'})
    button_down = True
    for step in range(1, 11):
        ipc('stipc/move_cursor', {'x': round(cx + (x - cx) * step / 10), 'y': round(cy + (y - cy) * step / 10)})
        time.sleep(.03)  # paces the gesture
    time.sleep(.2)  # an intended hold: drop still, with no release coast (L32)
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'release'})
    button_down = False
    key('LEFTMETA', False)
    ipc('stipc/move_cursor', {'x': 640, 'y': 4})


def widgets():
    return ipc('scottland/widgets')['widgets']


def at_rest():
    """Nothing animating: no widget transition, avoidance solved and every offset home."""
    state = ipc('scottland/hints')
    layout = ipc('scottland/layout-state')
    return (layout['widget_transition_count'] == 0 and not state['avoidance_solve_pending'] and
            state['avoidance_windows_pending'] == 0 and
            all(abs(h['dx'] - h['target_dx']) < .1 and abs(h['dy'] - h['target_dy']) < .1
                for h in state['hints'])) and state


def settled_in_mode(count):
    """Every hint shown at full size, nothing moving, the solve finished."""
    state = at_rest()
    return (state and state['active'] and len(state['hints']) == count and
            all(h.get('visible') and abs(h.get('pop', 0) - 1) < 1e-3 for h in state['hints'])) and state


# ---------------------------------------------------------------------------- frames
def frames(video):
    """Decoded frames, as presented and in order (no duplicates or drops added)."""
    process = subprocess.Popen(['ffmpeg', '-v', 'error', '-i', str(video), '-fps_mode', 'passthrough',
        '-f', 'rawvideo', '-pix_fmt', 'rgb0', '-'], stdout=subprocess.PIPE)
    size = W * H * 4
    try:
        while True:
            frame = process.stdout.read(size)
            if not frame: break
            if len(frame) != size: raise RuntimeError('incomplete decoded frame')
            yield frame
        if process.wait(timeout=8): raise RuntimeError('frame decoder failed')
    finally:
        process.stdout.close()
        if process.poll() is None: process.terminate()
        try: process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait()


def frame_times(video):
    return [float(t) for t in subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'v',
        '-show_entries', 'frame=pts_time', '-of', 'csv=p=0', str(video)], text=True).split()]


def pixel(frame, x, y):
    i = (y * W + x) * 4
    return frame[i], frame[i + 1], frame[i + 2]


def letter_color(frame, hint):
    """The opaque letter color of a settled hint, read from its pixels: the commonest color
    inside its circle that lies near its hint color."""
    b = hint['badge']
    r = b['size'] / 2
    cx, cy = b['x'] + r, b['y'] + r
    target = [c * 255 for c in hint['color']]
    counts = {}
    for y in range(max(0, int(cy - r)), min(H, int(cy + r) + 1)):
        for x in range(max(0, int(cx - r)), min(W, int(cx + r) + 1)):
            if (x - cx) ** 2 + (y - cy) ** 2 > r * r:
                continue
            p = pixel(frame, x, y)
            if max(abs(p[i] - target[i]) for i in range(3)) <= 40:
                counts[p] = counts.get(p, 0) + 1
    return max(counts, key=counts.get) if counts else None


TOLERANCE = 6


def letter_pixels(frame, colors):
    """Per hint, the thick letter-colored pixels (a pixel and its four neighbours all within
    TOLERANCE of that hint's letter color). Two-pixel outlines (WK37) are thinner than that."""
    def span(c):
        return b'[' + re.escape(bytes([max(0, c - TOLERANCE)])) + b'-' + re.escape(bytes([min(255, c + TOLERANCE)])) + b']'
    classes = [span(c[0]) + span(c[1]) + span(c[2]) for c in colors.values()]
    pattern = re.compile(b'(?=(?:' + b'|'.join(classes) + b'))', re.DOTALL)
    found = {name: set() for name in colors}
    for match in pattern.finditer(frame):
        at = match.start()
        if at % 4:
            continue
        p = frame[at:at + 3]
        for name, c in colors.items():
            if all(abs(p[i] - c[i]) <= TOLERANCE for i in range(3)):
                found[name].add(at // 4)
                break
    return {name: [i for i in s if i + 1 in s and i - 1 in s and i + W in s and i - W in s]
            for name, s in found.items()}


def outline_pixels(frame, hint, discs):
    """Known hint-color pixels on straight outline edges, outside every hint circle."""
    f = hint['outline_frame']
    color = tuple(round(c * 255) for c in hint['color'])
    x1, y1, x2, y2 = f['x'], f['y'], f['x'] + f['width'], f['y'] + f['height']
    points = set()
    for t in (.2, .3, .4, .5, .6, .7, .8):
        for x, y in ((x1 + (x2 - x1) * t, y1 + 1), (x1 + (x2 - x1) * t, y2 - 1),
                     (x1 + 1, y1 + (y2 - y1) * t), (x2 - 1, y1 + (y2 - y1) * t)):
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    px, py = round(x + dx), round(y + dy)
                    if 0 <= px < W and 0 <= py < H and all(
                            (px - cx) ** 2 + (py - cy) ** 2 > (r + 4) ** 2 for cx, cy, r in discs.values()):
                        points.add((px, py))
    return sum(max(abs(a - b) for a, b in zip(pixel(frame, x, y), color)) <= TOLERANCE
               for x, y in points)


def judge(label, video, final, baseline):
    """Every recorded frame, by its pixels, against where each hint settled."""
    hints = {h['hint']: h for h in final['hints']}
    times = frame_times(video)
    check(len(times) >= 10, f'{label}: the recording captured the entry',
          f'{len(times)} frames, largest gap {max((b - a for a, b in zip(times, times[1:])), default=0) * 1000:.0f} ms')
    last = None
    for frame in frames(video):
        last = frame
    colors = {}
    for name, h in hints.items():
        color = letter_color(last, h)
        check(color is not None, f'{label}: settled hint {name.upper()} is on screen in the last frame')
        if color:
            colors[name] = color
    discs = {name: (h['badge']['x'] + h['badge']['size'] / 2, h['badge']['y'] + h['badge']['size'] / 2,
                    h['badge']['size'] / 2) for name, h in hints.items()}
    # Calibrate absence before pressing Alt, against the same measured final letter colors.
    absent = letter_pixels(baseline, colors)
    noise = {name: sum((i % W - discs[name][0]) ** 2 + (i // W - discs[name][1]) ** 2 <= discs[name][2] ** 2
                      for i in pixels) for name, pixels in absent.items()}
    outside_noise = {name: len(pixels) - noise[name] for name, pixels in absent.items()}
    inside_series = {name: [] for name in colors}
    outside_series = {name: [] for name in colors}
    outlines = {name: h for name, h in hints.items() if h.get('outline') and h.get('outline_frame')}
    check(bool(outlines), f'{label}: fixture includes an occluded window outline')
    outline_series = {name: [] for name in outlines}
    outline_noise = {name: outline_pixels(baseline, h, discs) for name, h in outlines.items()}
    for frame in frames(video):
        for name, pixels in letter_pixels(frame, colors).items():
            cx, cy, r = discs[name]
            inside = sum((i % W - cx) ** 2 + (i // W - cy) ** 2 <= r * r for i in pixels)
            inside_series[name].append(inside)
            outside_series[name].append(len(pixels) - inside)
        for name, h in outlines.items():
            outline_series[name].append(outline_pixels(frame, h, discs))
    report = {'times': times, 'colors': {k: list(v) for k, v in colors.items()},
              'inside': inside_series, 'outside': outside_series, 'absence': noise,
              'outline': outline_series, 'outline_absence': outline_noise}
    (art / (label + '-pixels.json')).write_text(json.dumps(report))
    ok_all = bool(outlines)
    for name in colors:
        inside, outside = inside_series[name], outside_series[name]
        settled = inside[-1]
        check(settled >= 8, f'{label}: hint {name.upper()} letter is measurable when settled', f'{settled} px')
        threshold = noise[name] + 3  # at least four thick pixels above the calibrated absence
        first = next((i for i, n in enumerate(inside) if n > threshold), None)
        dips = [i for i in range(first or 0, len(inside)) if inside[i] <= threshold] if first is not None else []
        ok = first is not None and not dips
        ok_all &= ok
        check(ok, f'{label}: hint {name.upper()} appears once at its settled place and stays',
              f'first visible frame {first}, absence threshold {threshold}, disappearances: {dips[:8]}')
        stray = [(i, n) for i, n in enumerate(outside) if n > outside_noise[name] + 3]
        ok_all &= not stray
        check(not stray, f'{label}: hint {name.upper()} is never drawn anywhere else',
              f'frames with its letter elsewhere: {stray[:8]}')
    for name, series in outline_series.items():
        threshold = outline_noise[name] + 3
        first = next((i for i, n in enumerate(series) if n > threshold), None)
        dips = [i for i in range(first or 0, len(series)) if series[i] <= threshold] if first is not None else []
        ok = first is not None and series[-1] > threshold and not dips
        ok_all &= ok
        check(ok, f'{label}: outline {name.upper()} appears once on its settled edges and stays',
              f'first visible frame {first}, absence threshold {threshold}, disappearances: {dips[:8]}')
    return ok_all


def record_entry(label, count):
    """Hold Alt with a lossless recording running; stop it once everything has settled."""
    global recorder
    wait(lambda: not any(h.get('visible') for h in ipc('scottland/hints')['hints']), 'previous hints to leave')
    baseline_path = art / (label + '-before.png')
    subprocess.run(['grim', str(baseline_path)], check=True, timeout=8)
    baseline = next(frames(baseline_path))
    video = art / (label + '.mkv')
    log = art / (label + '-recorder.log')
    with log.open('w') as recorder_log:
        recorder = subprocess.Popen([recorder_tool, '-c', 'ffv1', '-x', 'bgr0', '-y', '-f', str(video)],
            stdout=subprocess.DEVNULL, stderr=recorder_log)
    # The recorder opens its output once the first frame has been copied.
    wait(lambda: 'Output #0' in log.read_text(), 'the recorder to start')
    key('LEFTALT', True)
    wait(lambda: ipc('scottland/hints')['active'], 'Window mode')
    final = wait(lambda: settled_in_mode(count), 'every hint to settle')
    final['views'] = list(views().values())
    # Frames presented after the settled state: a paced pointer gesture along the empty top
    # edge. The judge requires the last recorded frame to show every settled hint.
    for x in range(600, 690, 6):
        ipc('stipc/move_cursor', {'x': x, 'y': 4})
        time.sleep(.02)  # paces the gesture
    recorder.send_signal(signal.SIGINT)
    recorder.wait(timeout=60)
    recorder = None
    key('LEFTALT', False)
    wait(lambda: not ipc('scottland/hints')['active'], 'Window mode to end')
    (art / (label + '-final.json')).write_text(json.dumps(final, indent=1))
    return video, final, baseline


try:
    ipc('wayfire/set-config-options', {'scottland/sounds': False, 'place/mode': 'pointer',
        'scottland/window_mode_tint': 0})  # a flat window under the circles: letters stand out
    palette = {'scheme': 'dark', 'background': '#1f232c', 'foreground': '#d8deea',
               'accent': '#81a1c1', 'text_scale': .82, 'reduced_motion': False}
    palette_file = art / 'palette.json'
    palette_file.write_text(json.dumps(palette))
    session_palette = Path(os.environ['XDG_RUNTIME_DIR']) / 'scottland' / (os.environ['WAYLAND_DISPLAY'] + '.palette.json')
    session_palette.write_text(json.dumps(palette))
    wait(lambda: abs(ipc('scottland/hints')['hint_text_scale'] - .82) < 1e-6, 'the palette')
    layout = [('W0', 300, 200, 10, 555), ('W1', 300, 200, 10, 625), ('W2', 300, 200, 10, 680),
              ('PA', 494, 570, 241, 412), ('PH', 335, 310, 92, 237), ('PS', 305, 419, 84, 511),
              ('Front', 1034, 640, 640, 370)]
    ids = {}
    for name, width, height, x, y in layout:
        clients.append(subprocess.Popen(['python3', APP, name, str(width), str(height), str(palette_file)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        v = wait(lambda: next((v for v in views().values() if v['title'] == name and 'frame' in v), None),
                 name + ' to map')
        ids[name] = v['id']
        wait(lambda: at_rest(), name + ' to come to rest')
        drag(v['id'], x, y)
        if name.startswith('W'):
            wait(lambda: any(int(w['id']) == v['id'] for w in widgets()), name + ' to become a widget')
        wait(lambda: at_rest(), name + ' to land')
    key('LEFTMETA', True)
    tap('M')
    key('LEFTMETA', False)
    collapsed = lambda: all(views()[w['widget_view']]['frame']['width'] <= 97 for w in widgets())
    wait(lambda: len(widgets()) == 3 and collapsed(), 'the widgets to collapse',
         observe=lambda: [(w['widget_view'], views()[w['widget_view']]['frame']) for w in widgets()])
    # Focus Front with a real click on a spot only it covers.
    ipc('stipc/move_cursor', {'x': 900, 'y': 300})
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'press'})
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'release'})
    ipc('stipc/move_cursor', {'x': 640, 'y': 4})
    wait(lambda: ipc('window-rules/get-focused-view')['info']['id'] == ids['Front'], 'Front to take focus')
    (art / 'ids.json').write_text(json.dumps(ids))

    scenarios = 0
    for always in (True, False):
        for sliced in (False, True):
            label = ('always' if always else 'mode-only') + ('-sliced' if sliced else '')
            ipc('wayfire/set-config-options', {'scottland/window_avoidance_always': always})
            # Test hook (fault injection): a pass of a few work units per tick, as under load.
            ipc('scottland/hints', {'slice_units': 4 if sliced else 0})
            wait(lambda: at_rest() and collapsed(), 'the desktop to come to rest')
            video, final, baseline = record_entry(label, len(layout))
            if judge(label, video, final, baseline):
                video.unlink()  # kept only when a check failed
            scenarios += 1
    ipc('scottland/hints', {'slice_units': 0})
    print(f'{scenarios} scenarios (always-on avoidance on/off x whole/sliced solve), '
          f'{len(layout)} hints each: {passed} checks passed, {failed} failed', flush=True)
finally:
    # Each cleanup step continues even when the compositor or recorder has already gone.
    if recorder and recorder.poll() is None:
        try: recorder.send_signal(signal.SIGINT)
        except ProcessLookupError: pass
        try: recorder.wait(timeout=5)
        except subprocess.TimeoutExpired:
            recorder.kill(); recorder.wait()
    for name in list(held):
        try: key(name, False)
        except Exception: pass
    if button_down:
        try: ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'release'})
        except Exception: pass
    for client in clients:
        if client.poll() is None: client.terminate()
    for client in clients:
        try:
            client.wait(timeout=3)
        except subprocess.TimeoutExpired:
            client.kill(); client.wait()
    sock.close()
sys.exit(bool(failed))
