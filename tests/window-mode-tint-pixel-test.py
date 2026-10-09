#!/usr/bin/env python3
"""Pixel-check Window-mode tint composition, overlay isolation and Goo/halo retention.

Run only in an owned Scottland headless session with widgets enabled. Setup uses test IPC to
place solid-color clients; Alt enters Window mode through stipc input. Screenshots, observations
and client logs are written under the caller's build/ evidence directory.
"""
import json
import os
from pathlib import Path
import re
import signal
import socket
import struct
import subprocess
import sys
import time

assert os.environ.get('SCOTTLAND_TEST_MODEL') == '1', 'caller-owned headless session required'
repo = Path(__file__).resolve().parents[1]
art = Path(sys.argv[1]).resolve()
art.mkdir(parents=True, exist_ok=True)
client_log = (art / 'clients.log').open('w')
sock = socket.socket(socket.AF_UNIX)
sock.settimeout(8)
sock.connect(os.environ['WAYFIRE_SOCKET'])
clients, held, results = [], set(), []
screen_origin = (0, 0)
screen_scale = (1, 1)
screen_size = None
failures, passes = 0, 0


def ipc(method, data=None):
    body = json.dumps({'method': method, 'data': data or {}}).encode()
    sock.sendall(struct.pack('<I', len(body)) + body)

    def read(count):
        chunks = b''
        while len(chunks) < count:
            part = sock.recv(count - len(chunks))
            if not part:
                raise RuntimeError('compositor disconnected')
            chunks += part
        return chunks

    response = json.loads(read(struct.unpack('<I', read(4))[0]))
    if isinstance(response, dict) and 'error' in response:
        raise RuntimeError(f'{method}: {response}')
    return response


def wait(predicate, description, timeout=15):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(.04)
    raise RuntimeError(f'timed out waiting for {description}; last={last}')


def check(name, condition, detail=None):
    global failures, passes
    results.append({'name': name, 'passed': bool(condition), 'detail': detail})
    print(('PASS ' if condition else 'FAIL ') + name + (f': {detail}' if detail is not None else ''), flush=True)
    if condition:
        passes += 1
    else:
        failures += 1


def key(name, down):
    ipc('stipc/feed_key', {'key': 'KEY_' + name, 'state': down})
    (held.add if down else held.discard)(name)


def pointer(x, y):
    ipc('stipc/move_cursor', {'x': round(x), 'y': round(y)})


def button(down):
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'press' if down else 'release'})
    (held.add if down else held.discard)('BUTTON')


def drag(x, y, dx, dy):
    pointer(x, y)
    button(True)
    for step in range(1, 17):
        pointer(x + dx * step / 16, y + dy * step / 16)
        time.sleep(.025)  # pace one continuous input gesture
    button(False)


def views():
    return {int(view['id']): view for view in ipc('scottland/layout-state')['views']}


def list_views():
    return ipc('window-rules/list-views')


def hints():
    return {int(item['window']): item for item in ipc('scottland/hints')['hints']}


def widgets():
    return ipc('scottland/widgets')['widgets']


def set_options(**options):
    ipc('wayfire/set-config-options', {'scottland/' + key: value for key, value in options.items()})


def option(name):
    return ipc('wayfire/get-config-option', {'option': 'scottland/' + name})['value']


def mapped(title):
    return next((view for view in list_views() if view.get('title') == title), None)


def launch(title, color, geometry):
    palette = art / (title + '.palette.json')
    palette.write_text(json.dumps({'background': color}))
    process = subprocess.Popen(
        [sys.executable, str(repo / 'tests/hint-style-app.py'), title,
         str(geometry[2]), str(geometry[3]), str(palette)],
        stdout=client_log, stderr=client_log)
    clients.append(process)
    view = wait(lambda: mapped(title), title + ' maps')
    identifier = int(view['id'])
    rect = dict(zip(('x', 'y', 'width', 'height'), geometry))
    ipc('window-rules/configure-view', {'id': identifier, 'geometry': rect})
    wait(lambda: next((v for v in list_views() if int(v['id']) == identifier and
                       v.get('geometry') == rect), None), title + ' fixture geometry')
    return identifier


def stable_window_mode():
    previous, repeats = None, 0

    def settled():
        nonlocal previous, repeats
        state, represented = hints(), views()
        if not state or not all(item.get('visible') and item.get('pop', 0) >= .999 and
                                item.get('badge') for item in state.values()):
            repeats = 0
            return False
        signature = tuple(sorted((identifier, round(item.get('dx', 0), 2),
            round(item.get('dy', 0), 2), round(item['badge']['x'], 2),
            round(item['badge']['y'], 2), round(item['badge']['size'], 2))
            for identifier, item in state.items())) + tuple(sorted(
                (identifier, round(view['frame']['x'], 2), round(view['frame']['y'], 2),
                 round(view['frame']['width'], 2), round(view['frame']['height'], 2))
                for identifier, view in represented.items() if view.get('frame')))
        repeats = repeats + 1 if signature == previous else 0
        previous = signature
        return {'hints': state, 'views': represented} if repeats >= 4 else False

    return wait(settled, 'settled visible hints')


def stable_frames(identifiers, widget_links):
    previous, repeats = None, 0

    def settled():
        nonlocal previous, repeats
        state = views()
        signature = []
        for identifier in identifiers:
            represented = widget_links.get(identifier, identifier)
            frame = state.get(represented, {}).get('frame')
            if not frame:
                repeats = 0
                return False
            signature.append((represented, round(frame['x'], 2), round(frame['y'], 2),
                              round(frame['width'], 2), round(frame['height'], 2)))
        signature = tuple(sorted(signature))
        repeats = repeats + 1 if signature == previous else 0
        previous = signature
        return state if repeats >= 4 else False

    return wait(settled, 'fixture frames settle')


class Shot:
    def __init__(self, raw):
        match = re.match(rb'P6\s+(\d+)\s+(\d+)\s+255\s', raw)
        if not match:
            raise RuntimeError('grim did not return a binary PPM image')
        self.width, self.height = int(match.group(1)), int(match.group(2))
        self.pixels = raw[match.end():]
        if len(self.pixels) != self.width * self.height * 3:
            raise RuntimeError('unexpected PPM pixel payload length')

    def pixel(self, x, y):
        px = round((x - screen_origin[0]) * screen_scale[0])
        py = round((y - screen_origin[1]) * screen_scale[1])
        if not (0 <= px < self.width and 0 <= py < self.height):
            raise ValueError(f'point outside capture: logical=({x},{y}) device=({px},{py})')
        offset = (py * self.width + px) * 3
        return tuple(self.pixels[offset:offset + 3])

    def save(self, name):
        (art / (name + '.ppm')).write_bytes(
            f'P6\n{self.width} {self.height}\n255\n'.encode() + self.pixels)


def capture():
    global screen_scale
    image = Shot(subprocess.check_output(['grim', '-t', 'ppm', '-'], timeout=8))
    if screen_size:
        screen_scale = (image.width / screen_size[0], image.height / screen_size[1])
    return image


def max_delta(left, right):
    return max(abs(a - b) for a, b in zip(left, right))


def rgb_bytes(color):
    return tuple(round(float(channel) * 255) for channel in color)


def blend(base, color, strength):
    return tuple(round((1 - strength) * old + strength * new)
                 for old, new in zip(base, rgb_bytes(color)))


def extent(identifier, hint, state, widget_links):
    represented_id = widget_links.get(identifier, identifier)
    view = state['views'].get(represented_id)
    if not view or 'frame' not in view:
        raise RuntimeError(f'no drawn frame for window {identifier} (represented by {represented_id})')
    frame = view['frame']
    return {'x': float(frame['x']) + float(hint.get('dx', 0)),
            'y': float(frame['y']) + float(hint.get('dy', 0)),
            'width': float(frame['width']), 'height': float(frame['height'])}


def center(rect):
    return rect['x'] + rect['width'] / 2, rect['y'] + rect['height'] / 2


def contains(rect, point, inset=0):
    x, y = point
    return rect['x'] + inset <= x < rect['x'] + rect['width'] - inset and \
        rect['y'] + inset <= y < rect['y'] + rect['height'] - inset


def expected_pixel(base, point, ordered_shapes, strength):
    expected = base.pixel(*point)
    for rect, color in ordered_shapes:
        if contains(rect, point, inset=2):
            expected = blend(expected, color, strength)
    return expected


def dyed(image, x, y, color):
    strongest = max(range(3), key=lambda channel: color[channel])
    target = rgb_bytes(color)
    for dy in range(-2, 3):
        pixel = image.pixel(x, y + dy)
        if pixel[strongest] > max(pixel[channel] for channel in range(3) if channel != strongest) + 8 and \
                pixel[strongest] > target[strongest] * .45:
            return True
    return False


def wait_option(name, expected):
    return wait(lambda: abs(float(option(name)) - expected) < .1, f'{name}={expected}')


def set_tint_and_capture(value, name, sample_point=None, previous=None):
    set_options(window_mode_tint=float(value))
    wait_option('window_mode_tint', value)
    wait(stable_window_mode, 'settled hints after tint change')
    if sample_point is None or previous is None:
        image = capture()
    else:
        before = previous.pixel(*sample_point)
        def changed_pixels():
            shot = capture()
            return shot if max_delta(shot.pixel(*sample_point), before) > 2 else False
        image = wait(changed_pixels, f'new pixels after Window tint {value}%')
    image.save(name)
    return image


def run_renderer(goo):
    set_options(goo=goo)
    wait(lambda: str(option('goo')).lower() in (('true', '1') if goo else ('false', '0')),
         f'Goo switch {goo}')
    wait(stable_window_mode, 'stable mode after Goo change')
    mode = 'goo-on' if goo else 'goo-off'
    state = stable_window_mode()
    hints_now = state['hints']
    link_by_window = {}
    for widget in widgets():
        if int(widget.get('widget_view', -1)) > 0:
            link_by_window[int(widget['id'])] = int(widget['widget_view'])

    rear_id, front_id, card_id = fixture_ids
    assert rear_id in hints_now and front_id in hints_now and card_id in hints_now, \
        'all three fixtures must have visible Window-mode hints'
    rects = {identifier: extent(identifier, hints_now[identifier], state, link_by_window)
             for identifier in (rear_id, front_id, card_id)}
    rear, front, card = (rects[i] for i in (rear_id, front_id, card_id))
    x1, y1 = max(rear['x'], front['x']), max(rear['y'], front['y'])
    x2, y2 = min(rear['x'] + rear['width'], front['x'] + front['width']), \
        min(rear['y'] + rear['height'], front['y'] + front['height'])
    assert x2 - x1 > 80 and y2 - y1 > 80, f'fixture windows must overlap: {rear} {front}'
    overlap = ((x1 + x2) / 2, (y1 + y2) / 2)
    rear_only = (rear['x'] + rear['width'] * .18, rear['y'] + rear['height'] * .55)
    front_only = (front['x'] + front['width'] * .82, front['y'] + front['height'] * .55)
    card_only = center(card)
    assert contains(rear, rear_only) and not contains(front, rear_only), \
        f'rear-only sample is not exclusive: {rear_only}, {rear}, {front}'
    assert contains(front, front_only) and not contains(rear, front_only), \
        f'front-only sample is not exclusive: {front_only}, {rear}, {front}'
    assert not contains(card, rear_only) and not contains(card, front_only), \
        f'widget card overlaps a window-only sample: {card}, {rear_only}, {front_only}'
    assert contains(card, card_only) and not contains(rear, card_only) and not contains(front, card_only), \
        f'card sample must be card-only: {card_only}, {card}, {rear}, {front}'

    # Keep the base client colors far from the hint palette so a 7% blend is observable.
    sample_for_update = rear_only
    images = {}
    before_reset = capture() if float(option('window_mode_tint')) > 0 else None
    if goo:
        def dye_ready():
            current = stable_window_mode()
            links = {int(w['id']): int(w['widget_view']) for w in widgets()
                     if int(w.get('widget_view', -1)) > 0}
            image = capture()
            for identifier in (rear_id, front_id, card_id):
                frame = extent(identifier, current['hints'][identifier], current, links)
                x = round(frame['x'] + frame['width'] / 2)
                y = round(frame['y'] + (2 if identifier == card_id else -4))
                if not dyed(image, x, y, current['hints'][identifier]['color']):
                    return False
            return image
        wait(dye_ready, 'visible Goo dye on all drawn fixture extents', timeout=8)
    for value in (0, 7, 30, 100):
        earlier = [old_value for old_value in images if old_value < value]
        previous = images[max(earlier)] if earlier else before_reset
        images[value] = set_tint_and_capture(value, f'{mode}-{value}', sample_for_update, previous)
    base = images[0]

    base_overlap = base.pixel(*overlap)
    fixture_colors = {'TintRear': (18, 44, 82), 'TintFront': (230, 224, 210)}
    rear_client, front_client = fixture_colors['TintRear'], fixture_colors['TintFront']
    client_distances = {rear_id: max_delta(base_overlap, rear_client),
                        front_id: max_delta(base_overlap, front_client)}
    top_id = min(client_distances, key=client_distances.get)
    check(mode + ': overlap base pixel identifies one opaque client',
          client_distances[top_id] <= 12, client_distances)
    if top_id == front_id:
        ordered = [(rear, hints_now[rear_id]['color']), (front, hints_now[front_id]['color'])]
    else:
        ordered = [(front, hints_now[front_id]['color']), (rear, hints_now[rear_id]['color'])]
    check(mode + ': card is outside the two-window overlap',
          not contains(card, overlap), {'card': card, 'overlap': overlap})

    points = [(rear_only, [(rear, hints_now[rear_id]['color'])], 'rear-only window'),
              (front_only, [(front, hints_now[front_id]['color'])], 'front-only window'),
              (overlap, ordered, 'rear-first overlapping windows'),
              (card_only, [(card, hints_now[card_id]['color'])], 'widget card extent')]
    for value, image in images.items():
        strength = value / 100
        for point, shapes, label in points:
            expected = expected_pixel(base, point, shapes, strength)
            actual = image.pixel(*point)
            check(f'{mode} {value}%: {label} follows drawn stacking order',
                  max_delta(actual, expected) <= 5,
                  {'point': point, 'actual': actual, 'expected': expected})

    # A side-edge sample more than one device pixel inside must follow the same area tint;
    # the corresponding sample outside must not acquire a Window-mode rim or stroke.
    edge_inside = (rear['x'] + 3, rear['y'] + rear['height'] * .52)
    edge_outside = (rear['x'] - 5, rear['y'] + rear['height'] * .52)
    check(mode + ': edge probes are on opposite sides of the isolated rear frame',
          contains(rear, edge_inside) and not contains(front, edge_inside) and
          not contains(rear, edge_outside) and not contains(front, edge_outside),
          {'inside': edge_inside, 'outside': edge_outside})
    for value in (7, 100):
        strength = value / 100
        expected = blend(images[0].pixel(*edge_inside), hints_now[rear_id]['color'], strength)
        check(f'{mode} {value}%: no added Window-mode stroke inside frame',
              max_delta(images[value].pixel(*edge_inside), expected) <= 5,
              {'actual': images[value].pixel(*edge_inside), 'expected': expected})
    if not goo:
        check('Goo-off: tint strength adds no outside-frame stroke',
              max_delta(images[0].pixel(*edge_outside), images[100].pixel(*edge_outside)) <= 3,
              {'base': images[0].pixel(*edge_outside), 'tinted': images[100].pixel(*edge_outside)})

    # Hint overlays stay above the tint: opaque glyph pixels and the badge fill are identical
    # at the two ends of the setting. Dye/halo is checked independently at the drawn frame edge.
    for identifier, hint in hints_now.items():
        badge = hint.get('badge')
        if not badge:
            continue
        color = rgb_bytes(hint['color'])
        radius = float(badge['size']) / 2
        cx, cy = float(badge['x']) + radius, float(badge['y']) + radius
        fill_point = (cx + radius * .38, cy)
        glyph = [(x, y) for x in range(round(cx - radius * .55), round(cx + radius * .55) + 1)
                 for y in range(round(cy - radius * .60), round(cy + radius * .60) + 1)
                 if max_delta(images[0].pixel(x, y), color) <= 3]
        check(f'{mode}: hint badge fill unchanged by tint',
              max_delta(images[0].pixel(*fill_point), images[100].pixel(*fill_point)) <= 3,
              {'window': identifier, 'zero': images[0].pixel(*fill_point),
               'full': images[100].pixel(*fill_point)})
        check(f'{mode}: hint glyph unchanged by tint', len(glyph) >= 5 and
              all(max_delta(images[0].pixel(x, y), images[100].pixel(x, y)) <= 3 for x, y in glyph),
              {'window': identifier, 'opaque_glyph_pixels': len(glyph)})
        if identifier in rects:
            frame = rects[identifier]
        elif identifier in link_by_window:
            frame = extent(identifier, hint, state, link_by_window)
        else:
            continue
        x = round(frame['x'] + frame['width'] / 2)
        if goo:
            y = round(frame['y'] + (2 if identifier == card_id else -4))
        else:
            y = round(frame['y'] - 1)
        if goo:
            check(f'{mode}: hint dye remains visible at tint 0 and 100',
                  dyed(images[0], x, y, hint['color']) and dyed(images[100], x, y, hint['color']),
                  {'window': identifier, 'sample': (x, y)})
        else:
            rim0, rim100 = images[0].pixel(x, y), images[100].pixel(x, y)
            check(f'{mode}: fallback halo remains visible at tint 0 and 100',
                  max_delta(rim0, color) <= 22 and max_delta(rim100, color) <= 22,
                  {'window': identifier, 'zero': rim0, 'full': rim100,
                   'hint_color': color})
    return images[0]


def interrupted(signum, _frame):
    raise RuntimeError(f'interrupted by signal {signum}')


signal.signal(signal.SIGTERM, interrupted)
signal.signal(signal.SIGINT, interrupted)

try:
    outputs = ipc('window-rules/list-outputs')
    assert len(outputs) == 1, 'one headless output is required for deterministic pixel coordinates'
    geometry = outputs[0]['geometry']
    width, height = float(geometry['width']), float(geometry['height'])
    screen_size = (width, height)
    assert width >= 1000 and height >= 650, f'headless output is too small: {geometry}'
    screen_origin = (float(geometry['x']), float(geometry['y']))
    set_options(sounds=False, color_scheme='dark', accent_color='#81a1c1ff',
                window_avoidance_always=False, hint_avoidance_always=False,
                window_mode_tint=0)

    rear_width, rear_height = min(520, width * .42), min(360, height * .52)
    front_width, front_height = min(520, width * .42), min(360, height * .52)
    rear_id = launch('TintRear', '#122c52',
        (round(width * .12), round(height * .24), round(rear_width), round(rear_height)))
    front_id = launch('TintFront', '#e6e0d2',
        (round(width * .32), round(height * .35), round(front_width), round(front_height)))
    card_source = launch('TintCard', '#3f7250',
        (round(width * .68), round(height * .22), round(width * .22), round(height * .22)))
    state = views()
    frame = state[card_source]['frame']
    cx, cy = frame['x'] + frame['width'] / 2, frame['y'] + frame['height'] / 2
    key('LEFTMETA', True)
    drag(cx, cy, width - 8 - cx, 0)
    key('LEFTMETA', False)
    card_link = wait(lambda: next((entry for entry in widgets()
        if int(entry.get('id', -1)) == card_source and int(entry.get('widget_view', -1)) > 0), None),
        'TintCard becomes a drawn widget card')
    card_id = card_source
    card_view = int(card_link['widget_view'])
    wait(lambda: card_view in views() and views()[card_view].get('frame'), 'widget card frame')
    pointer(width / 2, 8)
    set_options(goo=True, window_mode_tint=0)
    wait_option('window_mode_tint', 0)
    wait(lambda: str(option('goo')).lower() in ('true', '1'), 'Goo enabled before Window mode')
    ipc('window-rules/focus-view', {'id': front_id})
    key('LEFTALT', True)
    wait(lambda: ipc('scottland/hints')['active'], 'real Alt input enters Window mode')
    state = stable_window_mode()
    fixture_ids = (rear_id, front_id, card_id)
    for goo in (True, False):
        set_options(goo=goo)
        wait(lambda: str(option('goo')).lower() in (('true', '1') if goo else ('false', '0')),
             'Goo on/off selection')
        wait(stable_window_mode, 'settled hints after Goo selection')
        if goo:
            wait(lambda: ipc('scottland/goo-state').get('enabled'), 'Goo field enabled')
        run_renderer(goo)

    key('LEFTALT', False)
    wait(lambda: not ipc('scottland/hints')['active'], 'Alt release leaves Window mode')
    wait(lambda: not any(item.get('visible') for item in ipc('scottland/hints')['hints']),
         'hint overlays clear after Alt release')
    set_options(goo=False)
    wait(lambda: str(option('goo')).lower() in ('false', '0'), 'Goo off for deterministic clear check')
    clear_state = stable_frames((rear_id, front_id, card_id), {card_id: card_view})
    set_options(window_mode_tint=0)
    wait_option('window_mode_tint', 0)
    after_zero = capture()
    set_options(window_mode_tint=100)
    wait_option('window_mode_tint', 100)
    after_full = capture()
    clear_points = [center(extent(identifier, {'dx': 0, 'dy': 0},
        {'views': clear_state}, {card_id: card_view})) for identifier in (rear_id, front_id, card_id)]
    check('inactive Window mode leaves no tint layer at 100%',
          all(max_delta(after_zero.pixel(*point), after_full.pixel(*point)) <= 3
              for point in clear_points),
          {'samples': [{'point': point, 'zero': after_zero.pixel(*point),
                        'full': after_full.pixel(*point)} for point in clear_points]})
    after_full.save('window-mode-tint-cleared-full')
except Exception as error:
    failures += 1
    print(f'FAIL Window tint pixel check: {error}', flush=True)
    results.append({'name': 'setup or pixel driver error', 'passed': False, 'detail': str(error)})
    import traceback
    traceback.print_exc()
finally:
    for held_key in list(held):
        try:
            button(False) if held_key == 'BUTTON' else key(held_key, False)
        except Exception:
            pass
    try:
        key('LEFTALT', False)
        key('LEFTMETA', False)
    except Exception:
        pass
    for process in clients:
        if process.poll() is None:
            process.terminate()
    for process in clients:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    for palette in art.glob('Tint*.palette.json'):
        palette.unlink(missing_ok=True)
    (art / 'observations.json').write_text(json.dumps(results, indent=2))
    client_log.close()
    sock.close()
    print(f'{passes} checks passed, {failures} failed', flush=True)
    raise SystemExit(1 if failures else 0)
