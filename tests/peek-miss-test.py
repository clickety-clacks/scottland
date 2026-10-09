#!/usr/bin/env python3
"""P12 with real input: a window completely covered by another one peeks, whatever happened to either
window before (focus, solo, pairing, a grab of the peeking window, a coasting drop, a click after the
drop) and wherever it is (center, periphery, under a widget card). The focused window does not move
for avoidance (WK13); FOCUSED_PEEKS states the policy under test for a focused window under a card.

Each client is one solid color. Fixture geometry and focus are set over IPC (window-rules
configure/focus: they bypass input); every cover is a real Super+drag and every solo and pairing a
real key hold (stipc). Visibility is judged from captured pixels: the covered
window must show a strip of its own color at least 24 x 100 pt (or the window's own size in a
smaller dimension) while the cover is still held and after the drop. Also: the covering window stays
exactly where it was dropped (P14), the covered window's real geometry does not change (P3) and its
displayed center stays in its zone (P13). Always-on window avoidance, outside Window mode; the goo
without shine, relief and overlap film, so a strip's pixels keep their client's color.
Usage: peek-miss-test.py ARTIFACTS [CASE ...]
"""
import json
import hashlib
import math
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import time
import zlib

here = Path(__file__).resolve().parent
args = sys.argv[1:]
art = Path(args.pop(0)).resolve(); art.mkdir(parents=True, exist_ok=True)
only = set(args)
sys.argv = [sys.argv[0], str(art)]
src = open(here / 'pairing-test.py').read()
exec(src[:src.index('\ntry:\n')])  # its helpers: ipc, wait, check, setup, press_hint, alt, hint, layout, ...
results = []
FOCUSED_PEEKS = False  # WK13: the focused window never moves for avoidance (not yet decided otherwise)
STRIP_D, STRIP_L, EDGE = 24, 100, 2   # EDGE: the frame's own antialiased outline
COLORS = {'Small': '#e02828', 'Big': '#2850c8', 'Other': '#28b450', 'Cover': '#c8a028', 'Probe': '#c828b4'}
NEUTRALS = ['#181818', '#2a2a2a', '#505058', '#6e6e78', '#8c8c96', '#b4b4be', '#e6e6ee']
CENTER_PCT = 33.333


class Unready(Exception):
    pass


def until(predicate, timeout, what):
    """Poll a bounded predicate; expiring is a failure that carries the last observation."""
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        ok, last = predicate()
        if ok: return last
        time.sleep(.05)
    raise Unready(f'{what}: not reached in {timeout} s; last {last}')


def launch_colored(title, w, h):
    palette = art / f'{title}.json'
    palette.write_text(json.dumps({'background': COLORS[title]}))
    clients.append(subprocess.Popen([sys.executable, str(here / 'hint-style-app.py'), title, str(w), str(h), str(palette)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    return wait(lambda: next((v['id'] for v in views() if v.get('title') == title), None), what='launch ' + title)


def scene(id):
    """The compositor's own drawn rectangle for the window's scene node (avoidance offset included)."""
    v = until(lambda: (lambda v: (v is not None and 'frame' in v, v))(
        next((v for v in ipc('scottland/layout-state')['views'] if v['id'] == id), None)), 3, f'window {id} drawn')
    f = v.get('scene_frame') or v['frame']
    return (f['x'], f['y'], f['x'] + f['width'], f['y'] + f['height'])


def capture(name, defer_png=False):
    data = subprocess.check_output(['grim', '-t', 'ppm', '-'])
    (art / (name + '.ppm')).write_bytes(data)
    header = re.match(rb'P6\s+(\d+)\s+(\d+)\s+255\s', data)
    if header is None:
        raise Unready(f'{name}: invalid RGB PPM header')
    w, h = int(header[1]), int(header[2])
    px = data[header.end():]
    if w <= 0 or h <= 0 or len(px) != w * h * 3:
        raise Unready(f'{name}: invalid RGB PPM capture')
    image = w, h, px
    if not defer_png: save_png(name, image)
    return image


def save_png(name, image):
    """The review image and pixel assertions describe the same captured frame."""
    w, h, px = image
    def chunk(kind, body):
        return struct.pack('!I', len(body)) + kind + body + struct.pack('!I', zlib.crc32(kind + body))
    scanlines = b''.join(b'\0' + px[y * w * 3:(y + 1) * w * 3] for y in range(h))
    (art / (name + '.png')).write_bytes(b'\x89PNG\r\n\x1a\n' +
        chunk(b'IHDR', struct.pack('!2I5B', w, h, 8, 2, 0, 0, 0)) +
        chunk(b'IDAT', zlib.compress(scanlines)) + chunk(b'IEND', b''))


def labels_of(image):
    """Each pixel labelled with the nearest known color (window colors, then the neutrals of the
    background and the goo); 0 for a neutral."""
    w, h, px = image
    refs = [tuple(int(c[i:i + 2], 16) for i in (1, 3, 5)) for c in list(dict.fromkeys(COLORS.values())) + NEUTRALS]
    n = len(dict.fromkeys(COLORS.values()))
    cache, rows = {}, []
    for y in range(h):
        row = px[y * w * 3:(y + 1) * w * 3]
        line = bytearray(w)
        for x in range(w):
            p = row[3 * x:3 * x + 3]
            label = cache.get(p)
            if label is None:
                r, g, b = p
                best = min(range(len(refs)), key=lambda k: (refs[k][0] - r) ** 2 + (refs[k][1] - g) ** 2 + (refs[k][2] - b) ** 2)
                label = cache[p] = best + 1 if best < n else 0
            line[x] = label
        rows.append(bytes(line))
    return rows


def visible_bounds(rows, color):
    """Bounds of one connected fixture-color region; absent or competing regions fail closed.
    Coordinates are pixel edges (right/bottom exclusive), with no IPC geometry fallback.
    """
    index = list(dict.fromkeys(COLORS.values())).index(color) + 1
    table = bytes(1 if k == index else 0 for k in range(256))
    mask = [bytearray(row.translate(table)) for row in rows]
    if not mask or not mask[0] or any(len(row) != len(mask[0]) for row in mask):
        raise Unready('invalid pixel labels')
    seed = next(((row.index(1), y) for y, row in enumerate(mask) if 1 in row), None)
    if seed is None:
        raise Unready('cover color missing from capture')
    w, h = len(mask[0]), len(mask)
    bounds, pending = [w, h, 0, 0], [seed]
    while pending:
        x, y = pending.pop()
        if not mask[y][x]: continue
        left, right = x, x + 1
        while left > 0 and mask[y][left - 1]: left -= 1
        while right < w and mask[y][right]: right += 1
        mask[y][left:right] = b'\0' * (right - left)
        bounds = [min(bounds[0], left), min(bounds[1], y), max(bounds[2], right), max(bounds[3], y + 1)]
        for ny in (y - 1, y + 1):
            if 0 <= ny < h:
                for match in re.finditer(b'\x01+', mask[ny][left:right]):
                    pending.append((left + match.start(), ny))
    if any(1 in row for row in mask):
        raise Unready('ambiguous cover color: disconnected regions in capture')
    return bounds


def strip_of(rows, color, size):
    """Does the color show a block STRIP_L x STRIP_D (either way; a dimension larger than the
    window's drawn size shrinks to it)? Returns (found, pixel count)."""
    index = list(dict.fromkeys(COLORS.values())).index(color) + 1
    table = bytes(1 if k == index else 0 for k in range(256))
    mask = [line.translate(table) for line in rows]
    count = sum(line.count(1) for line in mask)
    def fits(bw, bh):
        bw, bh = max(1, math.ceil(bw)), max(1, math.ceil(bh))
        pattern = re.compile(b'\x01{%d,}' % bw)
        spans = [[(m.start() + bw - 1, m.end()) for m in pattern.finditer(line)] if 1 in line else [] for line in mask]
        for y in range(len(mask) - bh + 1):
            current, k = spans[y], 1
            while current and k < bh:
                current = [(max(a, c), min(b, d)) for a, b in current for c, d in spans[y + k] if max(a, c) < min(b, d)]
                k += 1
            if current: return True
        return False
    fw, fh = size
    d, l = STRIP_D - EDGE, STRIP_L - EDGE
    return (fits(min(l, fw - EDGE), min(d, fh - EDGE)) or fits(min(d, fw - EDGE), min(l, fh - EDGE))), count


def zone_of(x):
    half = W * CENTER_PCT / 200
    return 'center' if abs(x - W / 2) <= half else 'left' if x < W / 2 else 'right'


def observe(name, phase, covered, title, quiet=False, cover_title=None, image=None, frame=None, diagnostic=None):
    capture_name = f'{name}-{phase}'
    if image is None: image = capture(capture_name)
    else: save_png(capture_name, image)
    x1, y1, x2, y2 = frame if frame is not None else scene(covered)
    rows = labels_of(image)
    found, count = strip_of(rows, COLORS[title], (x2 - x1, y2 - y1))
    h = diagnostic if diagnostic is not None else hint(covered)
    row = {'case': name, 'phase': phase, 'strip_in_pixels': found, 'pixels': count,
           'scene_frame': [round(v, 1) for v in (x1, y1, x2, y2)],
           'ppm': capture_name + '.ppm',
           'ppm_sha256': hashlib.sha256((art / (capture_name + '.ppm')).read_bytes()).hexdigest(),
           'capture_size': list(image[:2]),
           'png': capture_name + '.png',
           'diagnostic': {k: h.get(k) for k in ('outcome', 'rung', 'rule')}}
    if cover_title is not None:
        row['cover_title'] = cover_title
        try:
            row['cover_bounds'] = visible_bounds(rows, COLORS[cover_title])
        except Unready as e:
            row.update(cover_bounds=None, cover_bounds_error=str(e))
    if not quiet:
        results.append(row)
        print(f'      {name} [{phase}]: {json.dumps(row)}', flush=True)
    return found, row


def settled():
    """Offsets have reached their targets and no avoidance pass is pending (diagnostic readiness)."""
    def ready():
        s = hints()
        return (not s['avoidance_solve_pending'] and all(
            abs(h['dx'] - h['target_dx']) < .2 and abs(h['dy'] - h['target_dy']) < .2 for h in s['hints']), 'offsets moving')
    until(ready, 8, 'avoidance settled')


def pointer_drag(id, to, coast=False):
    """Super+press on the window's drawn center and move to `to`; the button stays down."""
    x1, y1, x2, y2 = scene(id); x, y = (x1 + x2) / 2, (y1 + y2) / 2
    pointer(x, y); time.sleep(.1); key('LEFTMETA', True); button(True); time.sleep(.1)
    steps = 6 if coast else 24
    for i in range(1, steps + 1):  # pacing a gesture
        pointer(x + (to[0] - x) * i / steps, y + (to[1] - y) * i / steps); time.sleep(.008 if coast else .02)
    return (to[0] - x, to[1] - y)


def release():
    button(False); key('LEFTMETA', False)


def strip_shows(name, phase, covered, title, timeout, cover_title=None):
    """Wait for the covered window's strip to show in captured pixels; fail with the last capture."""
    last = {}
    def look():
        found, row = observe(name, phase, covered, title, quiet=True, cover_title=cover_title)
        last.clear(); last.update(row)
        return found, {k: row[k] for k in ('pixels', 'scene_frame', 'diagnostic')}
    try:
        until(look, timeout, f'{name} {phase}: a strip of the covered window on screen')
        ok, detail = True, last
    except Unready as e:
        ok, detail = False, str(e)
    results.append(dict(last))
    print(f'      {name} [{phase}]: {json.dumps(last)}', flush=True)
    return ok, detail


try:
    ipc('wayfire/set-config-options', {'scottland/sounds': False, 'scottland/window_avoidance_always': True,
        'place/mode': 'pointer', 'scottland/goo_shine': 0.0, 'scottland/goo_relief': 0.5,
        'scottland/goo_overlap_film': 0.0})
    out = ipc('window-rules/list-outputs')[0]['geometry']
    W, H = out['width'], out['height']
    S = launch_colored('Small', 400, 300); B = launch_colored('Big', 760, 560); C = launch_colored('Other', 460, 360)
    base = [(C, W - 560, 120, 460, 360), (S, W // 2 - 200, H // 2 - 150, 400, 300), (B, 40, 60, 760, 560)]

    def case(name, before, focus, covered=S, covering=B, coast=False, spec=None, after_drop=None, title='Small'):
        if only and name not in only: return
        try:
            setup(spec or base, focus)
            settled()
            if before: before()
            settled()
            true_before = geometry(covered)
            zone_before = zone_of(true_before['x'] + true_before['width'] / 2)
            # The covering window is in front before it is dragged (as a click on it would leave it).
            ipc('window-rules/focus-view', {'id': covering})
            until(lambda: (focused_id() == covering, focused_id()), 3, 'cover focused')
            cover_title = raw(covering)['title']
            x1, y1, x2, y2 = scene(covered)
            pointer_drag(covering, ((x1 + x2) / 2, (y1 + y2) / 2), coast=coast)
            if coast:
                release()
            else:
                # Under 3 s: a build that still has the drag audition must not start one meanwhile.
                ok, detail = strip_shows(name, 'held', covered, title, 2.5)
                held_at = scene(covering)  # diagnostic only: not synchronized with the rendered frame
                held_frame, held_hint = scene(covered), hint(covered)
                held_image = capture(f'{name}-held-at-release', defer_png=True)
                # No polling, PNG encoding, or pixel analysis between this final frame and release.
                release()
            if after_drop: after_drop()
            settled()
            if not coast:
                final_ok, held = observe(name, 'held-at-release', covered, title, cover_title=cover_title,
                    image=held_image, frame=held_frame, diagnostic=held_hint)
                held['cover_scene_frame'] = list(held_at)
                check(ok and final_ok, f'{name}: while the cover is held, the covered window shows a strip (pixels)',
                      {'readiness': detail, 'at_release': held})
            ok, detail = strip_shows(name, 'dropped', covered, title, 6, cover_title=cover_title if not coast else None)
            check(ok, f'{name}: after the drop, the covered window shows a strip (pixels)', detail)
            if not coast:
                now = scene(covering)
                dropped = results[-1]
                dropped['cover_scene_frame'] = list(now)
                held_bounds, dropped_bounds = held['cover_bounds'], dropped.get('cover_bounds')
                check(held_bounds is not None and dropped_bounds is not None and
                      all(abs(a - b) <= 1 for a, b in zip(dropped_bounds, held_bounds)),
                      f'{name}: the covering window stays exactly where it was dropped (P14)',
                      {'visible_at_release': held, 'visible_dropped': dropped,
                       'ipc_dropped': now, 'ipc_at_release': held_at})
            if not after_drop:
                check(geometry(covered) == true_before, f'{name}: the covered window\'s real geometry is unchanged (P3)',
                      f'{geometry(covered)} vs {true_before}')
            x1, y1, x2, y2 = scene(covered)
            check(zone_of((x1 + x2) / 2) == zone_before, f'{name}: its displayed center stays in its zone (P13)',
                  f'{zone_of((x1 + x2) / 2)} vs {zone_before}')
        except Exception as e:  # an unready precondition or a lookup that failed: a failure, never a skip
            import traceback
            check(False, f'{name}: setup', f'{type(e).__name__}: {e} at {traceback.format_exc().splitlines()[-3].strip()}')
        finally:
            release()
            if hints()['active']: alt(False)

    def solo_small():
        ipc('window-rules/focus-view', {'id': S})
        until(lambda: (focused_id() == S, focused_id()), 3, 'small focused')
        solves = ipc('scottland/spread-state')['solves']
        alt(True); press_hint(S, hold=.75); alt(False)
        until(lambda: (lambda st: (st['solves'] > solves and not st['running'] and st['last']['purpose'] == 'solo', st.get('last')))(
            ipc('scottland/spread-state')), 5, 'Small is soloed')

    def pair_small():
        ipc('window-rules/focus-view', {'id': C})
        until(lambda: (focused_id() == C, focused_id()), 3, 'other focused')
        s_before, c_before = geometry(S), geometry(C)
        alt(True); press_hint(S, hold=.75); alt(False)
        # The pair forms: both windows move, side by side (WK36).
        until(lambda: (lambda s, c: (s != s_before and c != c_before and (s['x'] + s['width'] <= c['x'] + 1 or c['x'] + c['width'] <= s['x'] + 1),
                                     (s, c)))(geometry(S), geometry(C)), 5, 'Small and Other are paired')

    def grab_small_while_peeking():
        # Cover it first, so it peeks; then grab its strip where it is drawn and drop it (WK27 commit).
        x1, y1, x2, y2 = scene(S)
        pointer_drag(B, ((x1 + x2) / 2, (y1 + y2) / 2)); release(); settled()
        found, row = observe('grabbed-while-peeking', 'peeking-before-grab', S, 'Small')
        if not found: raise Unready(f'Small does not peek before the grab: {row}')
        sx1, sy1, sx2, sy2 = scene(S); fx = scene(B)
        gx, gy = (sx1 + 8, (sy1 + sy2) / 2) if sx1 < fx[0] - 10 else (sx2 - 8, (sy1 + sy2) / 2) if sx2 > fx[2] + 10 else \
            ((sx1 + sx2) / 2, sy1 + 8) if sy1 < fx[1] - 10 else ((sx1 + sx2) / 2, sy2 - 8)
        pointer(gx, gy); time.sleep(.1); key('LEFTMETA', True); button(True); time.sleep(.2)
        pointer(gx + 3, gy + 3); time.sleep(.3); release()
        until(lambda: (ipc('scottland/desktop-model').get('drag', {}).get('dragged', 0) in (0, None), 'dragging'), 3, 'grab ended')
        # Clear the cover off it again, back where it started, so the case's own cover starts clear.
        pointer_drag(B, (40 + 760 / 2, 60 + 560 / 2)); release(); settled()

    def solo_big():
        ipc('window-rules/focus-view', {'id': B})
        until(lambda: (focused_id() == B, focused_id()), 3, 'big focused')
        solves = ipc('scottland/spread-state')['solves']
        alt(True); press_hint(B, hold=.75); alt(False)
        until(lambda: (lambda st: (st['solves'] > solves and not st['running'] and st['last']['purpose'] == 'solo', st.get('last')))(
            ipc('scottland/spread-state')), 5, 'Big is soloed')

    def click_small_strip():
        # Right after the drop, a plain click in the middle of the covered window's visible band.
        x1, y1, x2, y2 = scene(S); fx = scene(B)
        gx, gy = ((x1 + fx[0]) / 2, (y1 + y2) / 2) if x1 < fx[0] - 10 else ((fx[2] + x2) / 2, (y1 + y2) / 2) if x2 > fx[2] + 10 else \
            ((x1 + x2) / 2, (y1 + fx[1]) / 2) if y1 < fx[1] - 10 else ((x1 + x2) / 2, (fx[3] + y2) / 2)
        pointer(gx, gy); button(True); time.sleep(.05); button(False)
        until(lambda: (focused_id() == S, focused_id()), 3, 'the click focuses Small')

    case('unfocused', None, C)
    case('previously-focused', None, S)
    case('soloed-small', solo_small, C)
    case('paired-small', pair_small, C)
    case('soloed-cover', solo_big, C)
    case('grabbed-while-peeking', grab_small_while_peeking, C)
    case('coast', None, C, coast=True)
    case('click-after-drop', None, C, after_drop=click_small_strip)
    periphery = [(C, W - 560, 120, 460, 360), (S, 140, H // 2 - 150, 400, 300), (B, W // 2 - 300, 60, 760, 560)]
    case('periphery', None, C, spec=periphery)

    # A window dropped under a widget card: once the dropped window's hold above the widgets ends
    # (L29) the card covers it. Unfocused (another window clicked), it must peek; focused, it follows
    # the focus policy (FOCUSED_PEEKS).
    def under_card(name, unfocus):
        if only and name not in only: return
        try:
            # The other windows stay well away from the rail.
            setup([(C, 40, 400, 460, 280), (S, 40, 60, 400, 300), (B, 520, 60, 300, 200)], C)
            card = card_frame()
            place(P, 600, 450, 180, 100)
            ipc('window-rules/focus-view', {'id': P})
            until(lambda: (focused_id() == P, focused_id()), 3, 'probe focused')
            wt.drag_begin(framed('Probe'), card['x'] + card['width'] / 2, card['y'] + card['height'] / 2)
            held_at = scene(P)
            wt.drag_end()
            last = []
            def still():
                last.append(geometry(P)); time.sleep(.1)
                return len(last) > 3 and last[-1] == last[-2] == last[-3], last[-1]
            dropped_at = until(still, 4, 'the drop stops moving')
            until(lambda: (lambda d: (d['held_above'] == P, d))(ipc('scottland/desktop-model')['drag']),
                  3, 'the dropped window is held above the widgets')
            px1, py1, px2, py2 = scene(P)
            check(card['x'] <= px1 and px2 <= card['x'] + card['width'] and card['y'] <= py1 and py2 <= card['y'] + card['height'],
                  f'{name}: fixture: the dropped window lies wholly within the card', f'{[px1, py1, px2, py2]} in {card}')
            check(all(abs(a - b) <= 1 for a, b in zip(scene(P), held_at)),
                  f'{name}: the dropped window is drawn where it was let go (P14)', f'{scene(P)} vs {held_at}')
            if unfocus:
                # A real click on another window's visible middle: focus moves, the hold above ends.
                x1, y1, x2, y2 = scene(C)
                pointer((x1 + x2) / 2, (y1 + y2) / 2); button(True); time.sleep(.05); button(False)
                until(lambda: (focused_id() == C, focused_id()), 3, 'the click focuses Other')
            until(lambda: (lambda d: (d['held_above'] == -1, d))(ipc('scottland/desktop-model')['drag']),
                  6, 'the hold above the widgets ends')
            if unfocus or FOCUSED_PEEKS:
                ok, detail = strip_shows(name, 'covered-by-card', P, 'Probe', 6)
                check(ok, f'{name}: the window under the card shows a strip (pixels)', detail)
            else:
                check(focused_id() == P, f'{name}: the dropped window is still focused', str(focused_id()))
                settled()
                image = capture(f'{name}-covered-by-card')
                rows = labels_of(image)
                index = list(dict.fromkeys(COLORS.values())).index(COLORS['Probe']) + 1
                outside = sum(1 for y, line in enumerate(rows) for x, v in enumerate(line) if v == index and not (
                    card['x'] <= x < card['x'] + card['width'] and card['y'] <= y < card['y'] + card['height']))
                check(outside == 0, f'{name}: the focused window does not move: none of it is drawn outside the card (WK13)',
                      f'{outside} pixels outside')
            check(geometry(P) == dropped_at, f'{name}: its real geometry stays where it was dropped (P14)',
                  f'{geometry(P)} vs {dropped_at}')
            x1, y1, x2, y2 = scene(P)
            check(zone_of((x1 + x2) / 2) == zone_of(dropped_at['x'] + dropped_at['width'] / 2),
                  f'{name}: its displayed center stays in its zone (P13)')
        except Exception as e:
            import traceback
            check(False, f'{name}: setup', f'{type(e).__name__}: {e} at {traceback.format_exc().splitlines()[-3].strip()}')
        finally:
            release()

    if not only or {'unfocused-under-widget', 'focused-under-widget'} & only:
        # The widget suite's own helpers make the card and drag the window (real stipc input).
        import importlib.util
        spec_w = importlib.util.spec_from_file_location('widget_input', here / 'widget-input-test.py')
        wt = importlib.util.module_from_spec(spec_w); spec_w.loader.exec_module(wt)
        framed = lambda title: until(lambda: (lambda v: (bool(v) and 'frame' in v, v))(wt.app(title)), 5, f'{title} drawn')
        card_title = 'peek-miss-card'
        made = []
        def card_frame():
            if not made:
                clients.append(subprocess.Popen(['foot', '--app-id', 'foot', '-T', card_title, '-W', '40x8', 'sh', '-c',
                                                 'exec sleep 600'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
                wt.drag_begin(framed(card_title), wt.screen['width'] - 6, 320)  # to the right rail: a widget
                wt.drag_end()
                made.append(True)
            return until(lambda: (lambda c: (bool(c) and 'frame' in c and not c.get('preview'), c))(wt.card(card_title)),
                         6, 'widget card')['frame']
        P = launch_colored('Probe', 180, 100)
        under_card('unfocused-under-widget', True)
        under_card('focused-under-widget', False)
finally:
    release()
    for client in clients: client.terminate()
    (art / 'peek-miss.json').write_text(json.dumps(results, indent=2))
    print(f'{passed} passed, {failed} failed', flush=True)
sys.exit(1 if failed else 0)
