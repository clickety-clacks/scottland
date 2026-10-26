#!/usr/bin/env python3
"""E16 scottland-capture: picks, screenshots, recordings, QR codes and text, end to end.

Runs inside a headless session with two virtual outputs (tests/capture-test.sh). Every command is
the installed helper run the way a key binding or a user runs it, driven by real pointer and key
input, and judged by what the command does not report about itself:

- picks: the geometry `region` prints for clicks, drags and keys over a window listing the test
  controls, and a screencopy of the overlay for the hover highlight;
- pixels: an independent screencopy of each output (grim -o) taken before the command, cut with
  the contract's nearest-pixel rule, against the saved file and the clipboard (wl-paste);
- files: the folders and names written, and that nothing else is;
- notices: what a stand-in notification server receives (tests/notification-server.py);
- recordings: the arguments the stand-in recorder receives (tests/capture-recorder.py), and the
  finished file as ffprobe and ffmpeg measure it against the recorder's own unprocessed copy;
- QR and text: zbarimg and tesseract run directly on an independent crop of the same area.

  tests/capture-test.py ARTIFACTS
"""
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import time

out = Path(sys.argv[1]).resolve(); out.mkdir(parents=True, exist_ok=True)
repo = Path(__file__).resolve().parent.parent
hooks = Path(os.environ['SCOTTLAND_HOOKS'])
tool = str(hooks / 'libexec/scottland-capture')
switch = hooks / 'switch'
raw = hooks / 'raw'
scratch = out / 'scratch'
shutil.rmtree(scratch, ignore_errors=True)
pictures, videos = scratch / 'Pictures', scratch / 'Videos'
shots = pictures / 'Screenshots'
videos.mkdir(parents=True)
config_home = Path(os.environ['XDG_CONFIG_HOME'])
config_home.mkdir(parents=True, exist_ok=True)
(config_home / 'user-dirs.dirs').write_text(
    f'XDG_PICTURES_DIR="{pictures}"\nXDG_VIDEOS_DIR="{videos}"\n')
runtime = Path(os.environ['XDG_RUNTIME_DIR'])
private = runtime / 'scottland'
state_home = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state')
fallback_private = state_home / 'scottland/capture'
notice_log = out / 'notices.jsonl'
GRIM, FFMPEG, FFPROBE = '/usr/bin/grim', '/usr/bin/ffmpeg', '/usr/bin/ffprobe'
SECRET = 'JBSWY3DPEHPK3PXP'
OTP_URL = f'otpauth://totp/Example:alice@example.com?secret={SECRET}&issuer=Example'
SENTENCE = 'The quick brown fox jumps over the lazy dog'

sock = socket.socket(socket.AF_UNIX); sock.connect(os.environ['WAYFIRE_SOCKET']); sock.settimeout(10)


def read(n):
    b = b''
    while len(b) < n:
        c = sock.recv(n - len(b))
        if not c: raise RuntimeError('compositor disconnected')
        b += c
    return b


def ipc(method, data=None):
    b = json.dumps({'method': method, 'data': data or {}}).encode(); sock.sendall(struct.pack('<I', len(b)) + b)
    return json.loads(read(struct.unpack('<I', read(4))[0]))


results = []


def check(name, ok, detail=''):
    results.append((name, bool(ok)))
    line = f'{"PASS" if ok else "FAIL"} {name}'
    if not ok and detail != '': line += f'\n     {detail}'
    print(line, flush=True)
    return bool(ok)


def wait(condition, timeout=10, step=0.05):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition(): return True
        time.sleep(step)
    return condition()


def outputs():
    """Each output's layout rectangle (x, y, width, height) in logical pixels."""
    return {o['name']: tuple(round(o['geometry'][k]) for k in ('x', 'y', 'width', 'height'))
            for o in ipc('window-rules/list-outputs')}


def focused_output():
    return ipc('window-rules/get-focused-output').get('info', {}).get('name')


def views():
    reply = ipc('window-rules/list-views')
    return reply if isinstance(reply, list) else []


def overlays():
    return sum(1 for v in views() if v.get('app-id') == 'scottland-capture' and v.get('mapped'))


# Input: the compositor's own pointer and keyboard, as a user's hands would give it.
def pointer(x, y): ipc('stipc/move_cursor', {'x': round(x), 'y': round(y)})
def button(mode): ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': mode})
def key(name, down): ipc('stipc/feed_key', {'key': name, 'state': down})


def hover(x, y):
    pointer(x + 3, y + 3); time.sleep(0.05); pointer(x, y); time.sleep(0.3)


def click(x, y):
    hover(x, y)
    button('press'); time.sleep(0.08); button('release'); time.sleep(0.3)


def drag(x0, y0, x1, y1):
    hover(x0, y0)
    button('press'); time.sleep(0.08)
    for i in range(1, 6):
        pointer(x0 + (x1 - x0) * i / 5, y0 + (y1 - y0) * i / 5); time.sleep(0.05)
    time.sleep(0.1); button('release'); time.sleep(0.3)


def keys(*combos):
    """Each combo is KEY names held together, e.g. ('KEY_LEFTCTRL', 'KEY_TAB')."""
    for combo in combos:
        if isinstance(combo, str): combo = (combo,)
        for k in combo: key(k, True); time.sleep(0.03)
        for k in reversed(combo): key(k, False); time.sleep(0.03)
        time.sleep(0.25)


ESC, TAB, RET = 'KEY_ESC', 'KEY_TAB', 'KEY_ENTER'
CTRL_TAB, CTRL_RET = ('KEY_LEFTCTRL', 'KEY_TAB'), ('KEY_LEFTCTRL', 'KEY_ENTER')


def ensure_focus(name):
    x, y, w, h = outputs()[name]
    pointer(x + w - 5, y + h - 5)
    if not wait(lambda: focused_output() == name, 2):
        click(x + w - 5, y + h - 5)
        wait(lambda: focused_output() == name, 3)


def notices():
    try:
        return [json.loads(line) for line in notice_log.read_text().splitlines() if line.strip()]
    except FileNotFoundError:
        return []


def config_files():
    return sorted(str(p.relative_to(config_home)) for p in config_home.rglob('*'))


class Run:
    def __init__(self, rc, stdout, stderr, notices, seconds):
        self.rc, self.stdout, self.stderr, self.notices, self.seconds = rc, stdout, stderr, notices, seconds

    def __repr__(self):
        return f'rc={self.rc} out={self.stdout!r} err={self.stderr!r} notices={self.notices}'


config_changes = []
home_output = [None]


def capture(*args, actions=None, env=None, timeout=60):
    """Run scottland-capture from the first output; with `actions`, perform them once the overlay
    is up on every output."""
    if home_output[0]: ensure_focus(home_output[0])
    seen = len(notices())
    configured = config_files()
    environment = dict(os.environ)
    for name, value in (env or {}).items():
        if value is None: environment.pop(name, None)
        else: environment[name] = str(value)
    started = time.monotonic()
    process = subprocess.Popen([tool, *args], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, env=environment)
    overlay_up = None
    if actions is not None:
        count = len(outputs())
        overlay_up = wait(lambda: overlays() >= count or process.poll() is not None, 20)
        if process.poll() is None:
            time.sleep(0.5)
            actions()
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        rc = process.returncode
    except subprocess.TimeoutExpired:
        process.kill(); stdout, stderr = process.communicate(); rc = 'timeout'
    seconds = time.monotonic() - started
    wait(lambda: overlays() == 0, 5)
    time.sleep(0.3)
    result = Run(rc, stdout.strip(), stderr.strip(), notices()[seen:], seconds)
    if config_files() != configured:
        config_changes.append((args, sorted(set(config_files()) ^ set(configured))))
    with (out / 'commands.log').open('a') as log:
        log.write(f'$ scottland-capture {" ".join(args)} env={env or {}} overlay={overlay_up}\n'
                  f'-> {result.rc} in {seconds:.1f}s\nstdout: {result.stdout}\nstderr: {result.stderr}\n'
                  f'notices: {json.dumps(result.notices)}\n\n')
    return result


def summaries(run):
    return [n['summary'] for n in run.notices]


# A window listing the test controls: what `scottland-ctl windows --json` would say.
listing_file = hooks / 'windows.json'


def listing(*windows):
    entries = []
    for i, w in enumerate(windows):
        x, y, width, height = w['rect']
        entries.append({'id': i + 1, 'app_id': 'fixture', 'title': f'window {i + 1}',
                        'hidden': w.get('hidden', False), 'output': w.get('output', H1),
                        'rect': {'x': ox + x, 'y': oy + y, 'width': width, 'height': height}})
    listing_file.write_text(json.dumps(entries))


def no_listing():
    listing_file.unlink(missing_ok=True)


def win(x, y, w, h, **extra):
    return {'rect': (x, y, w, h), **extra}


def geo(x, y, w, h):
    return f'{ox + x},{oy + y} {w}x{h}'


# Images: grim's PPM, PNG through ffmpeg, both as (width, height, rgb bytes).
def ppm(data):
    m = re.match(rb'P6\s+(\d+)\s+(\d+)\s+(\d+)\s', data)
    return int(m[1]), int(m[2]), data[m.end():]


def grab(name):
    return ppm(subprocess.run([GRIM, '-t', 'ppm', '-o', name, '-'], capture_output=True, check=True, timeout=10).stdout)


def png(data):
    if isinstance(data, Path): data = data.read_bytes()
    if data[:8] != b'\x89PNG\r\n\x1a\n': return None
    width, height = struct.unpack('>II', data[16:24])
    pixels = subprocess.run([FFMPEG, '-v', 'error', '-f', 'png_pipe', '-i', '-', '-f', 'rawvideo',
                             '-pix_fmt', 'rgb24', '-'], input=data, capture_output=True, check=True).stdout
    return width, height, pixels


def write_png(path, width, height, pixels):
    subprocess.run([FFMPEG, '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{width}x{height}',
                    '-i', '-', str(path)], input=pixels, check=True)


def crop(image, x, y, w, h):
    width, _, pixels = image
    return w, h, b''.join(pixels[((y + r) * width + x) * 3:((y + r) * width + x + w) * 3] for r in range(h))


def cut(rect, stills):
    """The contract's cut: rect at the largest scale it covers, each pixel's centre sampled from
    the still of the output under it (its own pixel ratio), black where no output is."""
    x, y, w, h = rect
    layout = outputs()
    ratios = {name: stills[name][0] / layout[name][2] for name in stills}
    covered = [n for n, (lx, ly, lw, lh) in layout.items() if lx < x + w and x < lx + lw and ly < y + h and y < ly + lh]
    scale = max([1.0] + [ratios[n] for n in covered])
    width, height = round(w * scale), round(h * scale)
    rows = []
    for r in range(height):
        py = y + (r + 0.5) / scale
        row = bytearray(width * 3)
        for c in range(width):
            px = x + (c + 0.5) / scale
            for name in covered:
                lx, ly, lw, lh = layout[name]
                if lx <= px < lx + lw and ly <= py < ly + lh:
                    sw, sh, pixels = stills[name]
                    col = min(sw - 1, math.floor((px - lx) * sw / lw))
                    line = min(sh - 1, math.floor((py - ly) * sh / lh))
                    i = (line * sw + col) * 3
                    row[c * 3:c * 3 + 3] = pixels[i:i + 3]
                    break
        rows.append(bytes(row))
    return width, height, b''.join(rows)


def differs(a, b):
    """How two equal-sized images differ: (pixels that differ, largest channel difference)."""
    if a is None or b is None or a[:2] != b[:2]: return None
    count, largest = 0, 0
    pa, pb = a[2], b[2]
    for i in range(0, len(pa), 3):
        d = max(abs(pa[i] - pb[i]), abs(pa[i + 1] - pb[i + 1]), abs(pa[i + 2] - pb[i + 2]))
        if d: count += 1; largest = max(largest, d)
    return count, largest


def differs_outside_ring(a, b, rect, margin):
    """differs(), skipping pixels within `margin` of the edge of rect (x, y, w, h in a's pixels):
    the band where a window's animated halo moves between two screencopies."""
    if a is None or b is None or a[:2] != b[:2]: return None
    x, y, w, h = rect
    width = a[0]
    count, largest = 0, 0
    pa, pb = a[2], b[2]
    for i in range(0, len(pa), 3):
        px, py = (i // 3) % width, (i // 3) // width
        outer = x - margin <= px < x + w + margin and y - margin <= py < y + h + margin
        inner = x + margin <= px < x + w - margin and y + margin <= py < y + h - margin
        if outer and not inner: continue
        d = max(abs(pa[i] - pb[i]), abs(pa[i + 1] - pb[i + 1]), abs(pa[i + 2] - pb[i + 2]))
        if d: count += 1; largest = max(largest, d)
    return count, largest


def colours(image):
    return len({image[2][i:i + 3] for i in range(0, len(image[2]), 3)})


def mean_difference(a, b):
    return sum(abs(p - q) for p, q in zip(a[2], b[2])) / max(1, len(a[2]))


# Clipboard, read through the compositor like any other client.
def clipboard(kind='text/plain'):
    result = subprocess.run(['wl-paste', '--no-newline', '--type', kind], capture_output=True, timeout=10)
    return result.stdout if result.returncode == 0 else None


def clipboard_types():
    result = subprocess.run(['wl-paste', '--list-types'], capture_output=True, text=True, timeout=10)
    return result.stdout.split()


sentinels = [0]


def set_sentinel():
    sentinels[0] += 1
    text = f'sentinel {sentinels[0]}'
    subprocess.run(['wl-copy', '--type', 'text/plain', text], check=True, timeout=10)
    wait(lambda: clipboard() == text.encode(), 5)
    return text.encode()


# Fixture windows with known content.
fixtures = {}


def show_fixture(name, kind, content):
    """Shows a fixture window in the middle of the first output and returns its layout rectangle.
    Scottland scales a window by where its centre is, so only there is it drawn at its own size,
    pixel for pixel."""
    app_id = 'org.scottland.CaptureFixture.' + name
    fixtures[name] = subprocess.Popen(['python3', str(repo / 'tests/capture-fixture-app.py'), name, kind, content],
                                      stdout=subprocess.DEVNULL, stderr=open(out / f'fixture-{name}.log', 'w'))

    def view():
        return next((v for v in views() if v.get('app-id') == app_id and v.get('mapped')), None)

    def drawn_scale():
        state = next((v for v in ipc('scottland/layout-state')['views'] if v['id'] == view()['id']), {})
        return state.get('applied_scale')
    if not wait(lambda: view() is not None, 20):
        raise RuntimeError(f'fixture {name} never mapped')
    v = view()
    g = v['geometry']
    left, top, width, height = outputs()[H1]
    x, y = (width - round(g['width'])) // 2, (height - round(g['height'])) // 2
    # A view's geometry is relative to its output, so it goes on the first output at (x, y) there
    # and its layout rectangle adds that output's position.
    home = next(o['id'] for o in ipc('window-rules/list-outputs') if o['name'] == H1)
    ipc('window-rules/configure-view', {'id': v['id'], 'output_id': home,
                                        'geometry': {'x': x, 'y': y, 'width': g['width'], 'height': g['height']}})
    wait(lambda: view().get('output-id') == home and (view()['geometry']['x'], view()['geometry']['y']) == (x, y), 3)
    wait(lambda: drawn_scale() is not None and abs(drawn_scale() - 1) < 1e-6, 5)
    time.sleep(0.8)
    v = view()
    if v.get('output-id') != home or abs((drawn_scale() or 0) - 1) > 1e-6:
        raise RuntimeError(f'fixture {name} is not on {H1} at its own size: {v} drawn at {drawn_scale()}')
    g = v['geometry']
    return (left + round(g['x']), top + round(g['y']), round(g['width']), round(g['height']))


def hide_fixture(name):
    process = fixtures.pop(name, None)
    if process:
        process.terminate()
        try: process.wait(5)
        except subprocess.TimeoutExpired: process.kill(); process.wait()
    time.sleep(0.5)


# The notification server: a stand-in that logs every notice and shows nothing.
server = [None]


def start_server(click_notices=False):
    stop_server()
    args = ['python3', str(repo / 'tests/notification-server.py'), str(notice_log)] + (['--click'] if click_notices else [])
    server[0] = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=open(out / 'notification-server.log', 'a'), text=True)
    line = server[0].stdout.readline().strip()
    if line != 'ready':
        raise RuntimeError(f'notification server did not start: {line!r}')


def stop_server():
    if server[0]:
        server[0].terminate()
        try: server[0].wait(5)
        except subprocess.TimeoutExpired: server[0].kill(); server[0].wait()
        server[0] = None
        time.sleep(0.3)


def pids_matching(predicate):
    found = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid(): continue
        try:
            cmdline = (entry / 'cmdline').read_bytes().split(b'\0')
            environ = (entry / 'environ').read_bytes()
        except OSError:
            continue
        if predicate([part.decode(errors='replace') for part in cmdline if part], environ):
            found.append(int(entry.name))
    return found


def recorder_pids():
    return pids_matching(lambda cmd, env: any('capture-recorder.py' in part for part in cmd))


def capture_pids():
    def ours(cmd, env):
        if not cmd: return False
        program = os.path.basename(cmd[0])
        if program.startswith('wl-copy'): return False
        return (b'SCOTTLAND_CAPTURE_PICK=' in env or program == 'scottland-capture'
                or any('capture-recorder.py' in part for part in cmd)
                or (program in ('notify-send', 'ffmpeg', 'ffprobe', 'zbarimg', 'tesseract', 'grim')
                    and str(hooks) not in ' '.join(cmd)))
    return pids_matching(ours)


def files(folder):
    return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []


def new_files(folder, before):
    return [name for name in files(folder) if name not in before]


SHOT_NAME = re.compile(r'screenshot-\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(-\d+)?\.png$')
REC_NAME = re.compile(r'screenrecording-\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(-\d+)?\.mp4$')


def probe(path, *options):
    result = subprocess.run([FFPROBE, '-v', 'error', *options, '-of', 'json', str(path)], capture_output=True, text=True)
    return json.loads(result.stdout or '{}')


def duration(path):
    return float(probe(path, '-show_entries', 'format=duration').get('format', {}).get('duration', 'nan'))


def frames(path):
    """Every frame a player shows, in order, small and grey."""
    data = subprocess.run([FFMPEG, '-v', 'error', '-i', str(path), '-an', '-fps_mode', 'passthrough',
                           '-vf', 'scale=160:90', '-f', 'rawvideo', '-pix_fmt', 'gray', '-'],
                          capture_output=True, timeout=120).stdout
    size = 160 * 90
    return [data[i:i + size] for i in range(0, len(data) - size + 1, size)]


def nearest(frame, candidates):
    """The index of the candidate frame most like `frame`."""
    distance = [sum(abs(a - b) for a, b in zip(frame, c)) for c in candidates]
    return distance.index(min(distance)) if distance else None


def recorder_args():
    path = hooks / 'recorder-args.jsonl'
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def value(args, flag):
    return args[args.index(flag) + 1] if flag in args else None


def versions():
    lines = []
    for command in (['wayfire', '--version'], [GRIM, '-h'], ['wl-copy', '--version'], [FFMPEG, '-version'],
                    ['zbarimg', '--version'], ['tesseract', '--version'], ['notify-send', '--version'],
                    ['qs', '--version'], [tool, '--version']):
        try:
            r = subprocess.run(command, capture_output=True, text=True, timeout=10)
            text = (r.stdout + r.stderr).strip().splitlines()
            lines.append(f'{command[0]}: {text[0] if text else "?"}')
        except Exception as error:
            lines.append(f'{command[0]}: {error}')
    (out / 'versions.txt').write_text('\n'.join(lines) + '\n')
    print('\n'.join(lines), flush=True)


versions()
start_server()
layout = outputs()
H1, H2 = sorted(layout)[:2]
home_output[0] = H1
ensure_focus(H1)
ox, oy, OW, OH = layout[H1]
check('setup: two outputs, the first focused', len(layout) == 2 and focused_output() == H1, (layout, focused_output()))
FULL = geo(0, 0, OW, OH)
h2x, h2y = layout[H2][:2]

# ---------------------------------------------------------------- CP1: what a click or drag picks
A, B = win(100, 100, 600, 400), win(200, 150, 200, 150)
listing(A, B)
ensure_focus(H1)
for label, (x, y), expected in [
        ('a click inside a window inside another picks the inner one', (250, 200), geo(200, 150, 200, 150)),
        ('a click in the outer window only picks it', (150, 120), geo(100, 100, 600, 400)),
        ('a click on no window picks the output', (900, 600), FULL),
        ('left and top edges are inside', (200, 150), geo(200, 150, 200, 150)),
        ('the right and bottom edges are outside', (400, 300), geo(100, 100, 600, 400)),
        ('the outer window\'s right and bottom edges are outside too', (700, 500), FULL)]:
    r = capture('region', 'windows', actions=lambda x=x, y=y: click(ox + x, oy + y))
    check(f'CP1 windows: {label}', r.rc == 0 and r.stdout == expected, r)

listing(win(100, 100, 600, 400, hidden=True), B)
r = capture('region', 'windows', actions=lambda: click(ox + 150, oy + 120))
check('CP1 windows: a hidden window is not a candidate', r.rc == 0 and r.stdout == FULL, r)

listing(win(850, 350, 100, 200), win(800, 400, 200, 100))
r = capture('region', 'windows', actions=lambda: click(ox + 875, oy + 425))
check('CP1 windows: equal areas go to the first in geometry-text order, not listing order',
      r.rc == 0 and r.stdout == geo(800, 400, 200, 100), r)

listing(win(500, 500, 100, 100, output=H2))
r = capture('region', 'windows', actions=lambda: click(ox + 550, oy + 550))
check('CP1 windows: another output\'s window is not a candidate even where it overlaps', r.rc == 0 and r.stdout == FULL, r)

listing(A, B)
up_after = []
def off_output_then_escape():
    click(h2x + 300, h2y + 300)
    time.sleep(0.6)
    up_after.append(overlays())
    keys(ESC)
r = capture('region', 'windows', actions=off_output_then_escape)
check('CP1 windows: a click off the focused output picks nothing; Esc then cancels',
      up_after == [len(layout)] and r.rc == 1 and 'cancelled' in r.stderr and r.stdout == '', (up_after, r))

r = capture('region', 'smart', actions=lambda: click(h2x + 300, h2y + 300))
check('CP1 smart: a click off the focused output picks the dragged (empty) rectangle',
      r.rc == 0 and r.stdout == f'{h2x + 300},{h2y + 300} 0x0', r)
r = capture('region', 'smart', actions=lambda: drag(h2x + 300, h2y + 300, h2x + 303, h2y + 303))
check('CP1 smart: a small drag off the focused output picks the dragged rectangle',
      r.rc == 0 and r.stdout == f'{h2x + 300},{h2y + 300} 3x3', r)
r = capture('region', 'smart', actions=lambda: drag(ox + 250, oy + 200, ox + 254, oy + 204))
check('CP1 smart: a drag under 20 square pixels is a click', r.rc == 0 and r.stdout == geo(200, 150, 200, 150), r)
r = capture('region', 'smart', actions=lambda: drag(ox + 250, oy + 200, ox + 255, oy + 204))
check('CP1 smart: a drag of 20 square pixels picks the drag', r.rc == 0 and r.stdout == geo(250, 200, 5, 4), r)
r = capture('region', actions=lambda: drag(ox + 100, oy + 120, ox + 300, oy + 350))
check('CP1 smart is the default: a drag picks the dragged rectangle', r.rc == 0 and r.stdout == geo(100, 120, 200, 230), r)
r = capture('region', 'region', actions=lambda: click(ox + 250, oy + 200) or keys(CTRL_RET, RET, TAB) or drag(ox + 10, oy + 20, ox + 50, oy + 60))
check('CP1 region: clicks and keys pick nothing; a drag picks the drag', r.rc == 0 and r.stdout == geo(10, 20, 40, 40), r)
r = capture('region', 'fullscreen')
check('CP1 fullscreen: the focused output, with no interaction', r.rc == 0 and r.stdout == FULL and r.seconds < 10, r)
r = capture('region', actions=lambda: keys(ESC))
check('CP1: Esc cancels', r.rc == 1 and 'cancelled' in r.stderr and r.stdout == '', r)

# ---------------------------------------------------------------- CP1 hover highlight
pattern_w, pattern_h = 400, 300
pattern = bytes(v for y in range(pattern_h) for x in range(pattern_w)
                for v in (x * 7 % 256, y * 13 % 256, x * y % 256))
pattern_png = scratch / 'pattern.png'
write_png(pattern_png, pattern_w, pattern_h, pattern)
fx, fy, fw, fh = show_fixture('pattern', '--image', str(pattern_png))
check('fixture: the pattern window is on the first output at its own size',
      (fw, fh) == (pattern_w, pattern_h) and fx - ox + fw <= OW and fy - oy + fh <= OH, (fx, fy, fw, fh))
listing(A, B)
shot = []
def hover_and_grab():
    hover(ox + 300, oy + 200)
    time.sleep(0.6)
    shot.append(grab(H1))
    keys(ESC)
ensure_focus(H1)
reference = grab(H1)
r = capture('region', 'windows', actions=hover_and_grab)
if shot:
    inside_b = mean_difference(crop(shot[0], 210, 160, 180, 130), crop(reference, 210, 160, 180, 130))
    # Part of A outside B, clear of the pattern window in the middle and its animated halo.
    in_a = differs(crop(shot[0], 110, 320, 80, 150), crop(reference, 110, 320, 80, 150))
    check('CP1 hover: the window a click would pick is highlighted, and only it',
          inside_b > 5 and in_a is not None and in_a[1] <= 3, (inside_b, in_a))
else:
    check('CP1 hover: the window a click would pick is highlighted, and only it', False, r)

# ---------------------------------------------------------------- CP2: keys
listing(A, B, win(800, 400, 200, 100), A)
for label, combos, expected in [
        ('Tab picks the first window in reading order', [TAB], geo(100, 100, 600, 400)),
        ('Tab again moves on; a repeated rectangle counts once', [TAB, TAB], geo(200, 150, 200, 150)),
        ('Tab three times reaches the last', [TAB, TAB, TAB], geo(800, 400, 200, 100)),
        ('Tab wraps around', [TAB, TAB, TAB, TAB], geo(100, 100, 600, 400)),
        ('Ctrl+Tab goes backwards from the end', [CTRL_TAB], geo(800, 400, 200, 100)),
        ('Ctrl+Tab twice', [CTRL_TAB, CTRL_TAB], geo(200, 150, 200, 150))]:
    r = capture('region', 'windows', actions=lambda combos=combos: hover(ox + 1100, oy + 650) or keys(*combos, RET))
    check(f'CP2: {label}', r.rc == 0 and r.stdout == expected, r)

listing(win(50, 300, 300, 200), win(500, 300, 300, 200), win(950, 300, 300, 200))
r = capture('region', 'windows', actions=lambda: hover(ox + 650, oy + 400) or keys('KEY_RIGHT', RET))
check('CP2: Right moves to the window on the right', r.rc == 0 and r.stdout == geo(950, 300, 300, 200), r)
r = capture('region', 'windows', actions=lambda: hover(ox + 650, oy + 400) or keys('KEY_LEFT', RET))
check('CP2: Left moves to the window on the left', r.rc == 0 and r.stdout == geo(50, 300, 300, 200), r)
listing(win(100, 100, 100, 100), win(500, 100, 100, 100), win(350, 180, 100, 100))
r = capture('region', 'windows', actions=lambda: hover(ox + 1100, oy + 650) or keys(TAB, 'KEY_RIGHT', RET))
check('CP2: an arrow weighs distance across twice distance along',
      r.rc == 0 and r.stdout == geo(500, 100, 100, 100), r)
r = capture('region', 'windows', actions=lambda: hover(ox + 1100, oy + 650) or keys(TAB, 'KEY_DOWN', RET))
check('CP2: Down picks the window below', r.rc == 0 and r.stdout == geo(350, 180, 100, 100), r)
listing(A, B)
r = capture('region', 'windows', actions=lambda: hover(ox + 1100, oy + 650) or keys(RET))
check('CP2: Return picks the highlighted output', r.rc == 0 and r.stdout == FULL, r)
r = capture('region', 'windows', actions=lambda: hover(ox + 250, oy + 200) or keys(CTRL_RET))
check('CP2: Ctrl+Return picks the whole output even over a window', r.rc == 0 and r.stdout == FULL, r)
r = capture('region', 'windows', actions=lambda: hover(ox + 250, oy + 200) or keys(ESC))
check('CP2: Esc cancels', r.rc == 1 and 'cancelled' in r.stderr, r)

# ---------------------------------------------------------------- no window listing that names outputs
no_listing()
r = capture('region', 'windows', actions=lambda: drag(ox + 100, oy + 100, ox + 300, oy + 250))
check('degraded: windows mode warns and still takes a drag',
      r.rc == 0 and r.stdout == geo(100, 100, 200, 150) and 'window picking is unavailable' in r.stderr
      and 'drag a region instead' in r.stderr, r)
r = capture('region', 'smart', actions=lambda: keys(CTRL_RET))
check('degraded: smart mode warns and Ctrl+Return still picks the output',
      r.rc == 0 and r.stdout == FULL and 'dragging a region and the whole screen still work' in r.stderr, r)
up_after = []
def click_then_escape():
    click(ox + 300, oy + 300)
    keys(TAB)
    time.sleep(0.6)
    up_after.append(overlays())
    keys(ESC)
r = capture('region', 'smart', actions=click_then_escape)
check('degraded: a click or a key does not guess a window; the pick stays open',
      up_after == [len(layout)] and r.rc == 1 and 'cancelled' in r.stderr, (up_after, r))

# ---------------------------------------------------------------- exact pixels, clipboard, notices
listing(A, B)
ensure_focus(H1)
stills = {name: grab(name) for name in layout}
rect = (fx + 20, fy + 20, 300, 200)
before = files(shots)
r = capture('screenshot', 'region', actions=lambda: drag(rect[0], rect[1], rect[0] + rect[2], rect[1] + rect[3]))
made = new_files(shots, before)
saved = shots / made[0] if len(made) == 1 else None
image = png(saved) if saved else None
expected = cut(rect, stills)
shown = differs(expected, crop((pattern_w, pattern_h, pattern), 20, 20, rect[2], rect[3]))
check('fixture: the screencopy under the region is the pattern window, pixel for pixel',
      shown is not None and shown[1] <= 2, shown)
check('CP4: a region screenshot is saved under the pictures folder with the contract name',
      r.rc == 0 and saved and SHOT_NAME.match(saved.name) and r.stdout == str(saved), (r, made))
check('CP4: its pixels are the independent screencopy\'s, exactly', differs(image, expected) == (0, 0),
      (image and image[:2], expected[:2], differs(image, expected)))
check('CP4: the clipboard holds the same PNG', saved and clipboard('image/png') == saved.read_bytes())
check('CP4: notice "Screenshot saved and copied to clipboard" names the file',
      [(n['summary'], n['body']) for n in r.notices] == [('Screenshot saved and copied to clipboard', saved.name if saved else '?')], r.notices)
if image: write_png(out / 'region-shot-expected.png', *expected)

sentinel = set_sentinel()
before = files(shots)
r = capture('screenshot', 'region', 'copy', actions=lambda: drag(rect[0], rect[1], rect[0] + rect[2], rect[1] + rect[3]))
copied = png(clipboard('image/png') or b'')
check('CP4 copy: nothing saved, the clipboard holds the exact pixels, one notice',
      r.rc == 0 and new_files(shots, before) == [] and r.stdout == '' and differs(copied, expected) == (0, 0)
      and summaries(r) == ['Screenshot copied to clipboard'], (r, new_files(shots, before)))
sentinel = set_sentinel()
before = files(shots)
r = capture('screenshot', 'region', 'save', actions=lambda: drag(rect[0], rect[1], rect[0] + rect[2], rect[1] + rect[3]))
made = new_files(shots, before)
check('CP4 save: a file with the exact pixels, the clipboard untouched, one notice',
      r.rc == 0 and len(made) == 1 and differs(png(shots / made[0]), expected) == (0, 0)
      and clipboard() == sentinel and summaries(r) == ['Screenshot saved'], (r, made))

before = files(shots)
stills = {name: grab(name) for name in layout}
r = capture('screenshot', 'fullscreen')
made = new_files(shots, before)
image = png(shots / made[0]) if len(made) == 1 else None
# The pattern window's halo is animated, so it differs between any two screencopies.
whole = differs_outside_ring(image, stills[H1], (fx - ox, fy - oy, fw, fh), 40)
check('CP4 fullscreen: the focused output, saved and copied, with no interaction',
      r.rc == 0 and len(made) == 1 and image and image[:2] == stills[H1][:2]
      and whole is not None and whole[0] < 0.001 * OW * OH, (r, made, whole))

# ---------------------------------------------------------------- CP5 and CP8: folders and names
nested = scratch / 'chosen/nested/deeper'
r = capture('screenshot', 'fullscreen', 'save', env={'SCOTTLAND_SCREENSHOT_DIR': nested})
check('CP5: SCOTTLAND_SCREENSHOT_DIR wins and is created when missing',
      r.rc == 0 and len(files(nested)) == 1 and SHOT_NAME.match(files(nested)[0]) and r.stdout == str(nested / files(nested)[0]), r)
home = scratch / 'home'
home.mkdir()
r = capture('screenshot', 'fullscreen', 'save', env={'HOME': home, 'XDG_CONFIG_HOME': None})
check('CP5: with no pictures folder configured, ~/Pictures/Screenshots',
      r.rc == 0 and len(files(home / 'Pictures/Screenshots')) == 1 and sorted(os.listdir(home)) == ['Pictures'], (r, os.listdir(home)))

same = scratch / 'same-second'
same.mkdir()
now = time.time()
for offset in range(-2, 20):
    stamp = time.strftime('%Y-%m-%d_%H-%M-%S', time.localtime(now + offset))
    for suffix in ('', '-2'):
        (same / f'screenshot-{stamp}{suffix}.png').write_bytes(b'sentinel')
taken = set(files(same))
r = capture('screenshot', 'fullscreen', 'save', env={'SCOTTLAND_SCREENSHOT_DIR': same})
made = new_files(same, taken)
check('CP5: a taken name gets -2, then -3; existing files are never replaced',
      r.rc == 0 and len(made) == 1 and made[0].endswith('-3.png')
      and all((same / name).read_bytes() == b'sentinel' for name in taken), (r, made))

blocker = scratch / 'not-a-folder'
blocker.write_text('a file')
sentinel = set_sentinel()
r = capture('screenshot', 'fullscreen', env={'SCOTTLAND_SCREENSHOT_DIR': blocker / 'Screenshots'})
copied = png(clipboard('image/png') or b'')
check('CP4: a save failure is reported and the image is still copied',
      r.rc == 1 and r.stderr.startswith('scottland-capture: ') and copied is not None and copied[:2] == stills[H1][:2]
      and summaries(r) == ['Screenshot copied to clipboard'] and blocker.read_text() == 'a file', r)

missing = scratch / 'no-such-videos'
r = capture('record', '--fullscreen', env={'SCOTTLAND_RECORDING_DIR': missing})
check('CP8: a missing recordings folder: a notice naming it, an error, nothing recorded',
      r.rc == 1 and str(missing) in r.stderr and not missing.exists()
      and [(n['summary'], n['body']) for n in r.notices] == [('Screen recording folder does not exist', str(missing))]
      and recorder_pids() == [], r)

# ---------------------------------------------------------------- CP10 and CP11: QR codes and text
hide_fixture('pattern')
qr_png = scratch / 'qr.png'
subprocess.run(['qrencode', '-o', str(qr_png), '-s', '6', '-m', '4', OTP_URL], check=True)
qx, qy, qw, qh = show_fixture('qr', '--image', str(qr_png))
sentinel = set_sentinel()
r = capture('qr', actions=lambda: drag(qx - 10, qy - 10, qx + qw + 10, qy + qh + 10))
everything_said = r.stdout + r.stderr + json.dumps(r.notices)
check('CP10: the QR code\'s text is copied', r.rc == 0 and clipboard() == OTP_URL.encode(), (r, clipboard()))
check('CP10: copied as sensitive, so clipboard managers skip it', 'x-kde-passwordManagerHint' in clipboard_types(), clipboard_types())
check('CP10: the secret is not echoed anywhere', SECRET not in everything_said, everything_said)
check('CP10: notice "QR code copied to clipboard"', summaries(r) == ['QR code copied to clipboard'], r.notices)
hide_fixture('qr')


def ean13(digits):
    """An EAN-13 barcode as a 4-pixel-module RGB image: a 1D code, not a QR code."""
    L = ['0001101', '0011001', '0010011', '0111101', '0100011', '0110001', '0101111', '0111011', '0110111', '0001011']
    G = ['0100111', '0110011', '0011011', '0100001', '0011101', '0111001', '0000101', '0010001', '0001001', '0010111']
    R = ['1110010', '1100110', '1101100', '1000010', '1011100', '1001110', '1010000', '1000100', '1001000', '1110100']
    parity = ['LLLLLL', 'LLGLGG', 'LLGGLG', 'LLGGGL', 'LGLLGG', 'LGGLLG', 'LGGGLL', 'LGLGLG', 'LGLGGL', 'LGGLGL'][int(digits[0])]
    bits = '0' * 11 + '101'
    bits += ''.join((L if p == 'L' else G)[int(d)] for p, d in zip(parity, digits[1:7]))
    bits += '01010' + ''.join(R[int(d)] for d in digits[7:13]) + '101' + '0' * 11
    module, height = 4, 200
    row = b''.join((b'\0\0\0' if bit == '1' else b'\xff\xff\xff') * module for bit in bits)
    return len(bits) * module, height, row * height


barcode_png = scratch / 'ean13.png'
write_png(barcode_png, *ean13('4006381333931'))
plain = subprocess.run(['zbarimg', '-q', '--raw', str(barcode_png)], capture_output=True, text=True)
check('fixture: the barcode is readable as a barcode', plain.stdout.strip() == '4006381333931', plain)
bx, by, bw, bh = show_fixture('barcode', '--image', str(barcode_png))
sentinel = set_sentinel()
r = capture('qr', actions=lambda: drag(bx - 10, by - 10, bx + bw + 10, by + bh + 10))
check('CP10: a barcode that is not a QR code: critical "No QR code found", nothing copied',
      r.rc == 1 and clipboard() == sentinel
      and [(n['summary'], n['body'], n['urgency']) for n in r.notices] == [('No QR code found', 'Select a region containing a QR code', 2)], r)
hide_fixture('barcode')

tx, ty, tw, th = show_fixture('text', '--text', SENTENCE)
ensure_focus(H1)
stills = {name: grab(name) for name in layout}
area = (tx + 2, ty + 2, tw - 4, th - 4)
r = capture('text', actions=lambda: drag(area[0], area[1], area[0] + area[2], area[1] + area[3]))
independent = scratch / 'text-crop.png'
write_png(independent, *cut(area, stills))
langs = os.environ.get('SCOTTLAND_OCR_LANGS', 'eng')
direct = subprocess.run(['tesseract', str(independent), 'stdout', '--oem', '1', '--psm', '6', '-l', langs, '--dpi', '300',
                         '-c', 'preserve_interword_spaces=1'], capture_output=True, text=True).stdout.strip()
copied = (clipboard() or b'').decode(errors='replace')
check('CP11: the text tesseract reads in the picked area is copied, trimmed',
      r.rc == 0 and copied == direct and SENTENCE in copied, (r, copied, direct))
check('CP11: notice "Copied text from selection to clipboard"', summaries(r) == ['Copied text from selection to clipboard'], r.notices)
hide_fixture('text')

# ---------------------------------------------------------------- cancelling every command
for command in (['screenshot'], ['qr'], ['text'], ['record']):
    sentinel = set_sentinel()
    before_shots, before_videos = files(shots), files(videos)
    r = capture(*command, actions=lambda: keys(ESC))
    check(f'cancel: {command[0]} cancelled leaves nothing behind',
          r.rc == 1 and 'cancelled' in r.stderr and r.notices == [] and clipboard() == sentinel
          and new_files(shots, before_shots) == [] and new_files(videos, before_videos) == [] and recorder_pids() == [], r)

# ---------------------------------------------------------------- CP6, CP7, CP9: recording
state = private / 'capture-recording'


def recorded(run):
    path = Path(run.stdout) if run.stdout else None
    return path if path and path.is_file() else None


def stop_recording(**kw):
    return capture('record', '--stop', **kw)


before = len(recorder_args())
r = capture('record', '--fullscreen', '--desktop-audio', '--microphone')
args = recorder_args()[before:]
running = recorder_pids()
check('CP6: --fullscreen starts at once and exits 0 while the recording goes on',
      r.rc == 0 and r.seconds < 12 and len(running) >= 1 and state.is_file(), (r, running))
check('CP6: whole output at its own size, 60 fps constant frame rate, desktop and microphone in one track',
      len(args) == 1 and value(args[0], '-w') == H1 and '-s' not in args[0] and value(args[0], '-f') == '60'
      and value(args[0], '-fm') == 'cfr' and value(args[0], '-a') == 'default_output|default_input'
      and value(args[0], '-ac') == 'aac', args)
check('CP9: the running recording is recorded in the runtime folder', state.is_file() and len(state.read_text().splitlines()) == 2,
      state.read_text() if state.exists() else 'missing')
time.sleep(4)
r = stop_recording()
video = recorded(r)
raw_copy = raw / video.name if video else None
check('CP7: --stop prints the file and exits 0; the recorder is gone',
      r.rc == 0 and video and REC_NAME.match(video.name) and video.parent == videos and recorder_pids() == [] and not state.exists(), r)
if video and raw_copy and raw_copy.exists():
    # 0.1 seconds at 60 frames a second is six frames: the first one shown is the raw seventh.
    before_frames, after_frames = frames(raw_copy), frames(video)
    first = nearest(after_frames[0], before_frames[:16]) if after_frames else None
    check('CP7: the first 0.1 seconds are dropped',
          first == 6 and len(before_frames) - len(after_frames) == 6, (first, len(before_frames), len(after_frames)))
    streams = probe(video, '-show_entries', 'stream=codec_type,r_frame_rate,width,height').get('streams', [])
    vstreams = [s for s in streams if s['codec_type'] == 'video']
    astreams = [s for s in streams if s['codec_type'] == 'audio']
    check('CP6: 60 frames a second, one audio track', len(vstreams) == 1 and vstreams[0]['r_frame_rate'] == '60/1'
          and len(astreams) == 1, streams)
    head = subprocess.run([FFMPEG, '-hide_banner', '-t', '0.35', '-i', str(video), '-vn', '-af', 'volumedetect', '-f', 'null', '-'],
                          capture_output=True, text=True).stderr
    peak = re.search(r'max_volume: (-?[\d.]+|-inf) dB', head)
    check('CP7: the first 0.4 seconds are silent', peak and (peak[1] == '-inf' or float(peak[1]) <= -60), peak and peak[0])
    loud = subprocess.run([FFMPEG, '-hide_banner', '-i', str(video), '-vn', '-af', 'ebur128=peak=true', '-f', 'null', '-'],
                          capture_output=True, text=True).stderr
    integrated = re.findall(r'I:\s+(-?[\d.]+) LUFS', loud)
    true_peak = re.findall(r'Peak:\s+(-?[\d.]+) dBFS', loud)
    check('CP7: loudness normalized to -14 LUFS, true peak at most -1.5 dBFS',
          integrated and abs(float(integrated[-1]) + 14) <= 1 and true_peak and float(true_peak[-1]) <= -1.0,
          (integrated[-1:], true_peak[-1:]))
else:
    check('CP7: the processed recording can be measured', False, (r, raw_copy))
saved_notices = [n for n in r.notices if n['summary'] == 'Screen recording saved']
check('CP7: notice "Screen recording saved" names the file and shows its first frame',
      len(saved_notices) == 1 and video and saved_notices[0]['body'] == video.name
      and saved_notices[0].get('icon_exists') and saved_notices[0].get('icon_png')
      and 'default' in saved_notices[0]['actions'], r.notices)

before = len(recorder_args())
r = capture('record', actions=lambda: drag(ox + 100, oy + 100, ox + 740, oy + 460))
args = recorder_args()[before:]
check('CP6: a dragged region is recorded as that region at its own size, with no audio',
      r.rc == 0 and len(args) == 1 and value(args[0], '-w') == f'640x360+{ox + 100}+{oy + 100}'
      and '-s' not in args[0] and '-a' not in args[0], (r, args))
time.sleep(1.5)
before = len(recorder_args())
r = capture('record', '--resolution', '1280x720')
video = recorded(r)
size = probe(video, '-show_entries', 'stream=width,height').get('streams', [{}])[0] if video else {}
check('CP7: record while recording stops the recording instead of starting another',
      r.rc == 0 and video and len(recorder_args()) == before and recorder_pids() == []
      and (size.get('width'), size.get('height')) == (640, 360), (r, size))

before = len(recorder_args())
r = capture('record', '--resolution', '1280x720', '--microphone', actions=lambda: keys(CTRL_RET))
args = recorder_args()[before:]
check('CP6: Ctrl+Return records the whole output; --resolution sets the size; microphone only',
      r.rc == 0 and len(args) == 1 and value(args[0], '-w') == H1 and value(args[0], '-s') == '1280x720'
      and value(args[0], '-a') == 'default_input', (r, args))
time.sleep(0.6)
start_server(click_notices=True)
opened = hooks / 'xdg-open.log'
opened.unlink(missing_ok=True)
r = stop_recording()
video = recorded(r)
frames = probe(video, '-count_frames', '-select_streams', 'v:0', '-show_entries', 'stream=nb_read_frames') if video else {}
read_frames = int(frames.get('streams', [{}])[0].get('nb_read_frames', 0) or 0)
check('CP7: a short recording still plays', r.rc == 0 and video and read_frames > 0, (r, frames))
check('CP7: clicking the saved notice opens the recording in the player',
      wait(lambda: opened.exists(), 5) and video and opened.read_text().strip() == str(video),
      opened.read_text() if opened.exists() else 'not opened')
start_server()

r = stop_recording()
check('CP7: --stop with nothing recording is an error', r.rc == 1 and 'nothing is recording' in r.stderr and r.stdout == '', r)

(switch / 'recorder-stubborn').touch()
r = capture('record', '--fullscreen')
stubborn = recorder_pids()
r = stop_recording()
(switch / 'recorder-stubborn').unlink()
check('CP7: a recorder that will not stop is forced after 5 seconds, with a critical notice',
      r.rc == 1 and 4.5 <= r.seconds < 12 and 'forced to stop' in r.stderr and recorder_pids() == [] and not state.exists()
      and [(n['summary'], n['body'], n['urgency']) for n in r.notices]
      == [('Screen recording error', 'The recording had to be forced to stop. The video may be damaged.', 2)],
      (r, stubborn))

(switch / 'recorder-refuse').touch()
before_videos = files(videos)
r = capture('record', '--fullscreen')
(switch / 'recorder-refuse').unlink()
check('CP6: an encoder that refuses: an error saying why, no file, no recording',
      r.rc == 1 and 'did not start' in r.stderr and 'encoder refused' in r.stderr and new_files(videos, before_videos) == []
      and not state.exists() and recorder_pids() == [], r)

(switch / 'ffmpeg-fail-processing').touch()
r = capture('record', '--fullscreen', '--desktop-audio')
time.sleep(1.5)
r = stop_recording()
(switch / 'ffmpeg-fail-processing').unlink()
video = recorded(r)
check('CP7: processing fails: the recording is kept as recorded and the error says so',
      r.rc == 0 and video and 'unprocessed' in r.stderr and str(video) in r.stderr
      and (raw / video.name).read_bytes() == video.read_bytes()
      and 'Screen recording saved' in summaries(r), r)

(switch / 'grim-refuse').touch()
before_shots, before_videos = files(shots), files(videos)
r1 = capture('screenshot', 'fullscreen')
r2 = capture('record')
(switch / 'grim-refuse').unlink()
check('failures: a refused screen capture fails screenshots and picks, leaving no file',
      r1.rc == 1 and 'screen capture refused' in r1.stderr and r2.rc == 1 and 'screen capture refused' in r2.stderr
      and new_files(shots, before_shots) == [] and new_files(videos, before_videos) == [] and recorder_pids() == [], (r1, r2))

# CP9 without a runtime folder: a private folder under the state folder, never /tmp.
tmpdir = Path(os.environ.get('TMPDIR') or '/tmp')
tmp_before = {p: set(os.listdir(p)) for p in {tmpdir, Path('/tmp')}}
fallback_state = fallback_private / 'capture-recording'
r = capture('record', '--fullscreen', env={'XDG_RUNTIME_DIR': None})
check('CP9: with no XDG_RUNTIME_DIR the recording is recorded in a 0700 folder under the state folder',
      r.rc == 0 and fallback_state.is_file() and (fallback_private.stat().st_mode & 0o777) == 0o700 and not state.exists(), r)
time.sleep(0.8)
r = stop_recording(env={'XDG_RUNTIME_DIR': None})
check('CP9: and is stopped from there', r.rc == 0 and recorded(r) and not fallback_state.exists() and recorder_pids() == [], r)
tmp_new = {str(p): sorted(set(os.listdir(p)) - names) for p, names in tmp_before.items()}
check('CP9: nothing new in /tmp or TMPDIR', all(not names for names in tmp_new.values()), tmp_new)

r = capture('record', '--fullscreen')
pid = int(state.read_text().split()[0]) if state.exists() else None
if pid:
    os.kill(pid, signal.SIGKILL)
    wait(lambda: recorder_pids() == [], 5)
r = stop_recording()
check('CP9: a recorder killed by hand is not "recording"', pid and r.rc == 1 and 'nothing is recording' in r.stderr
      and not state.exists(), (pid, r))

# ---------------------------------------------------------------- output scale, rotation, two outputs
setter = str(hooks / 'libexec/scottland-output')


def set_output(*args):
    result = subprocess.run([setter, *args], capture_output=True, text=True, timeout=60)
    with (out / 'commands.log').open('a') as log:
        log.write(f'$ scottland-output {" ".join(args)}\n-> {result.returncode}\n{result.stdout}{result.stderr}\n\n')
    return result.returncode


set_output('set', H1, '--scale', '2')
wait(lambda: outputs()[H1][2:] == (OW // 2, OH // 2), 10)
ensure_focus(H1)
layout = outputs()
r = capture('region', 'fullscreen')
check('CP1 fullscreen on a scale-2 output: its logical size',
      r.rc == 0 and r.stdout == f'{layout[H1][0]},{layout[H1][1]} {OW // 2}x{OH // 2}', (r, layout))
fx, fy, fw, fh = show_fixture('pattern', '--image', str(pattern_png))
ensure_focus(H1)
stills = {name: grab(name) for name in layout}
rect = (fx + 10, fy + 10, 200, 150)
before = files(shots)
r = capture('screenshot', 'region', 'save', actions=lambda: drag(rect[0], rect[1], rect[0] + rect[2], rect[1] + rect[3]))
made = new_files(shots, before)
image = png(shots / made[0]) if len(made) == 1 else None
expected = cut(rect, stills)
check('fixture: at scale 2 the screencopy under the region shows the pattern', colours(expected) > 1000, colours(expected))
check('CP4 at scale 2: twice the logical size, the exact screencopy pixels',
      r.rc == 0 and image and image[:2] == (400, 300) and differs(image, expected) == (0, 0),
      (r, image and image[:2], differs(image, expected)))
hide_fixture('pattern')
layout = outputs()
x1, y1, w1, h1 = layout[H1]
x2, y2, w2, h2 = layout[H2]
start = (x1 + w1 - 100, y1 + 50)
end = (x2 + 100, y2 + 250)
stills = {name: grab(name) for name in layout}
before = files(shots)
r = capture('screenshot', 'region', 'save', actions=lambda: drag(*start, *end))
made = new_files(shots, before)
image = png(shots / made[0]) if len(made) == 1 else None
rect = (min(start[0], end[0]), min(start[1], end[1]), abs(end[0] - start[0]), abs(end[1] - start[1]))
expected = cut(rect, stills)
check('CP4 across outputs: one image at the largest scale, each part from its own output',
      r.rc == 0 and image and image[:2] == (rect[2] * 2, rect[3] * 2) and differs(image, expected) == (0, 0),
      (r, rect, image and image[:2], differs(image, expected), layout))
if image: write_png(out / 'cross-output-expected.png', *expected)
set_output('set', H1, '--scale', '1')
set_output('set', H1, '--transform', '90')
wait(lambda: outputs()[H1][2:] == (OH, OW), 10)
ensure_focus(H1)
layout = outputs()
r = capture('region', 'fullscreen')
check('CP1 fullscreen on a portrait output', r.rc == 0 and r.stdout == f'{layout[H1][0]},{layout[H1][1]} {OH}x{OW}', (r, layout))
before = files(shots)
r = capture('screenshot', 'fullscreen', 'save')
made = new_files(shots, before)
image = png(shots / made[0]) if len(made) == 1 else None
check('CP4 fullscreen on a portrait output: upright, at its size', r.rc == 0 and image and image[:2] == (OH, OW), (r, image and image[:2]))
set_output('reset')
wait(lambda: outputs()[H1][2:] == (OW, OH), 10)
ensure_focus(H1)

# ---------------------------------------------------------------- CP12: no notification server
stop_server()
before = files(shots)
r = capture('screenshot', 'fullscreen')
check('CP12: with no notification server a screenshot is saved and copied as usual',
      r.rc == 0 and len(new_files(shots, before)) == 1 and r.stdout and png(clipboard('image/png') or b'') is not None and r.seconds < 10, r)
r = capture('record', '--fullscreen')
time.sleep(0.8)
r2 = stop_recording()
check('CP12: and a recording starts and stops as usual', r.rc == 0 and r2.rc == 0 and recorded(r2) and r2.seconds < 20, (r, r2))
start_server()

# ---------------------------------------------------------------- CP13: the Print key bindings
ensure_focus(H1)
before = files(shots)
keys('KEY_PRINT')
check('CP13: Print saves the focused output to the pictures folder',
      wait(lambda: len(new_files(shots, before)) == 1, 15), new_files(shots, before))
before = files(shots)
keys(('KEY_LEFTSHIFT', 'KEY_PRINT'))
up = wait(lambda: overlays() >= len(outputs()), 15)
time.sleep(0.5)
keys(ESC)
gone = wait(lambda: overlays() == 0, 10)
time.sleep(1)
check('CP13: Shift+Print opens the region pick; Esc leaves nothing', up and gone and new_files(shots, before) == [], (up, gone))

# ---------------------------------------------------------------- CP14: nothing left behind
time.sleep(1)
left = {str(d): [p.name for p in d.glob('capture-*') if p.name != 'capture-recording'] for d in (private, fallback_private) if d.is_dir()}
check('CP14: no private working folders are left', all(not names for names in left.values()), left)
check('CP14: no state is left without a recording', not state.exists() and not fallback_state.exists())
lingering = capture_pids()
check('CP14: no processes are left (the clipboard owner aside)', lingering == [],
      [(pid, Path(f'/proc/{pid}/cmdline').read_bytes()) for pid in lingering if Path(f'/proc/{pid}').exists()])
check('CP14: no configuration is written', config_changes == [], config_changes)

stop_server()
for name in list(fixtures): hide_fixture(name)
passed = sum(ok for _, ok in results)
print(f'\n{passed}/{len(results)} checks passed', flush=True)
sys.exit(0 if passed == len(results) else 1)
