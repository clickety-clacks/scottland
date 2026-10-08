#!/usr/bin/env python3
"""WK41: one foreground offset that never settles must not hide the hints behind it.

Run in a caller-owned headless session. Alt and the touch hold are real stipc input. Window
placement and focus are declared IPC fixture setup. A focused cover window leaves a narrow
exposed strip on the tested front window, too small for a minimum hint. In the held case, the
real touch starts in that strip; the cover is already in its final position before touch-down.
The frozen case injects an offset that never steps toward its target through the test-session-only
`freeze_offset` field of scottland/hints. That bypasses no input layer; it stands in for an ease
that cannot finish (a target that keeps changing). Each case passes when both rear windows' hint
letters are found in captured pixels while the front offset is still away from its target, and
absent from the same circles before Alt. The held case freezes only the test-session lift timer;
touch delivery, focus, avoidance and pixels remain real, without a 1.5-second timing race.
"""
import json
import math
import os
from pathlib import Path
import signal
import socket
import struct
import subprocess
import sys
import time

art = Path(sys.argv[1]).resolve(); art.mkdir(parents=True, exist_ok=True)
sock = socket.socket(socket.AF_UNIX); sock.settimeout(8); sock.connect(os.environ['WAYFIRE_SOCKET'])
clients = []
held = set()
touching = False
passes = failures = 0
signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))


def ipc(method, data=None):
    body = json.dumps(dict(method=method, data=data or {})).encode()
    sock.sendall(struct.pack('<I', len(body)) + body)
    def read(n):
        out = b''
        while len(out) < n:
            chunk = sock.recv(n - len(out))
            if not chunk: raise RuntimeError('compositor disconnected')
            out += chunk
        return out
    result = json.loads(read(struct.unpack('<I', read(4))[0]))
    if isinstance(result, dict) and 'error' in result: raise RuntimeError(result)
    return result


def check(name, okay, details=''):
    global passes, failures
    print(('PASS ' if okay else 'FAIL ') + name + (f': {details}' if details and not okay else ''), flush=True)
    passes += bool(okay); failures += not okay


def wait(fn, what, limit=10, timeout_detail=None):
    # A hang guard only: every pass criterion is a state, never how long it took.
    end = time.monotonic() + limit
    last = None
    while time.monotonic() < end:
        last = fn()
        if last: return last
        time.sleep(.02)
    detail = timeout_detail() if timeout_detail else last
    raise RuntimeError(f'{what}: never happened, last {detail}')


def key(name, down):
    ipc('stipc/feed_key', dict(key='KEY_' + name, state=down))
    (held.add if down else held.discard)(name)


def rows(): return {h['window']: h for h in ipc('scottland/hints')['hints']}
def views(): return ipc('window-rules/list-views')
def test_input(data=None): return ipc('scottland/test-input', data)


def launch(title, color, width=420, height=320):
    palette = art / (title + '.json'); palette.write_text(json.dumps(dict(background=color)))
    clients.append(subprocess.Popen([sys.executable, str(Path(__file__).with_name('hint-style-app.py')),
                                    title, str(width), str(height), str(palette)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    return wait(lambda: next((v['id'] for v in views() if v.get('title') == title), None), title + ' mapped')


def capture(name):
    data = subprocess.check_output(['grim', '-t', 'ppm', '-'], timeout=8)
    (art / (name + '.ppm')).write_bytes(data)
    fields, pos = [], 0
    while len(fields) < 4:
        while data[pos:pos + 1].isspace(): pos += 1
        end = pos
        while not data[end:end + 1].isspace(): end += 1
        fields.append(data[pos:end]); pos = end
    return int(fields[1]), int(fields[2]), data[pos + 1:]


def letter_pixels(shot, row):
    width, height, pixels = shot
    b = row['badge']; rgb = tuple(round(c * 255) for c in row['color'])
    cx, cy, radius = b['x'] + b['size'] / 2, b['y'] + b['size'] / 2, b['size'] / 2
    return sum(max(abs(pixels[(y * width + x) * 3 + c] - rgb[c]) for c in range(3)) <= 6
               for y in range(max(0, int(cy - radius)), min(height, int(cy + radius) + 1))
               for x in range(max(0, int(cx - radius)), min(width, int(cx + radius) + 1))
               if (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2)


def away(row):
    return math.hypot(row['target_dx'] - row['dx'], row['target_dy'] - row['dy'])


def at_rest():
    return all(away(r) < .1 and abs(r['dx']) < .1 and abs(r['dy']) < .1 for r in rows().values())


def touch_point(window, blockers):
    state = rows()
    frame = state[window]['drawn']
    blocked = [state[w]['drawn'] for w in blockers]
    # Avoid rounded window corners and leave a margin from a foreground surface so the
    # real touch hit test must land on the intended, exposed window.
    # Prefer the center of the narrow exposed strip, away from the frame edge and cover.
    for fx, fy in ((.035, .5), (.02, .5), (.15, .5), (.85, .5), (.5, .15), (.5, .85),
                   (.25, .25), (.75, .25), (.25, .75), (.75, .75)):
        x = frame['x'] + frame['width'] * fx
        y = frame['y'] + frame['height'] * fy
        if all(not (b['x'] - 8 <= x <= b['x'] + b['width'] + 8 and
                    b['y'] - 8 <= y <= b['y'] + b['height'] + 8) for b in blocked):
            return round(x), round(y)
    raise RuntimeError(f'no exposed touch point on window {window}: frame={frame}, blockers={blocked}')


def run_case(name, front, rears, exposed_touch=None, keep_focused=None, hold_lift_timer=False):
    if hold_lift_timer:
        state = test_input(dict(hold_lift_timer=True))
        if state.get('lift_timer_held') is not True:
            raise RuntimeError(f'{name}: test-model lift timer hold was not enabled: {state}')
    before = capture(name + '-before-alt')
    try:
        if exposed_touch:
            x, y = exposed_touch
            ipc('stipc/touch', dict(finger=0, x=x, y=y))
            global touching
            touching = True
            state = wait(lambda: (lambda s: s if s.get('hold_armed') and not s.get('lifted') and
                                  not s.get('dragging') else None)(test_input()),
                         name + ': real touch hold armed')
            if state.get('hold_window') != front:
                raise RuntimeError(f'{name}: touch held window {state.get("hold_window")}, expected front {front}')
            if keep_focused is not None:
                # A touch can change focus. Restore the cover as the solver anchor and confirm
                # that the real hold is still on Front before the configured lift timer expires.
                ipc('window-rules/focus-view', dict(id=keep_focused))
                wait(lambda: (lambda s: s if s.get('hold_armed') and not s.get('lifted') and
                              not s.get('dragging') and s.get('hold_window') == front and
                              s.get('avoidance_anchor_window') == keep_focused else None)(test_input()),
                     name + ': Anchor focus restored while Front remains held', limit=1,
                     timeout_detail=lambda: json.dumps(test_input()))
        key('LEFTALT', True)
        last_front = {}
        def target_away():
            row = rows()[front]
            last_front.update({key: row.get(key) for key in ('dx', 'dy', 'target_dx', 'target_dy', 'rule', 'rung')})
            last_front['away'] = away(row)
            input_state = test_input()
            last_front['input'] = {key: input_state.get(key) for key in
                                   ('hold_armed', 'hold_window', 'avoidance_anchor_window', 'lifted', 'dragging')}
            return row if last_front['away'] > 20 else None
        wait(target_away, name + ': front window given a target away from where it is drawn',
             timeout_detail=lambda: json.dumps(last_front))
        shown = wait(lambda: (lambda r: r if all(r[w].get('visible') and r[w].get('pop', 0) >= .999 and
                                                 r[w].get('badge') for w in rears) else None)(rows()),
                     name + ': rear hints shown')
    except RuntimeError as error:
        check(name + ': front offset away from its target and rear hints shown', False, str(error))
        key('LEFTALT', False)
        return
    shot = capture(name + '-hints')
    after = rows()
    record = dict(front_away=away(after[front]), front_offset=(after[front]['dx'], after[front]['dy']),
                  front_target=(after[front]['target_dx'], after[front]['target_dy']), rears={})
    check(name + ': front offset still away from its target while rear hints show',
          away(shown[front]) > 20 and away(after[front]) > 20, json.dumps(record))
    if exposed_touch:
        input_state = test_input()
        record['touch_input'] = input_state
        check(name + ': touch remains armed without lifting or dragging while rear hints show',
              input_state.get('hold_armed') and not input_state.get('lifted') and
              not input_state.get('dragging'), json.dumps(input_state))
    for w in rears:
        found, baseline = letter_pixels(shot, shown[w]), letter_pixels(before, shown[w])
        record['rears'][w] = dict(letter_pixels=found, before_alt=baseline, badge=shown[w]['badge'])
        check(f'{name}: rear window {w} hint letter drawn (pixels)', found >= 8 and baseline < 4,
              f'{found} letter pixels, {baseline} before Alt')
    (art / (name + '.json')).write_text(json.dumps(record, indent=2))
    key('LEFTALT', False)


try:
    ipc('wayfire/set-config-options', {'scottland/sounds': False, 'scottland/window_mode_tint': 0,
        'scottland/lift_delay': 1500,
        'scottland/window_avoidance_always': False, 'scottland/hint_avoidance_always': False})
    output = ipc('window-rules/list-outputs')[0]['geometry']
    ids = [launch(title, color) for title, color in
           (('RearA', '#1f232c'), ('RearB', '#24302a'), ('Front', '#2c2420'))]
    ids.append(launch('Anchor', '#242a34'))
    rect = dict(x=output['x'] + output['width'] // 2 - 210, y=output['y'] + output['height'] // 2 - 160,
                width=420, height=320)
    # Leave a 28px strip at Front's left edge for a touch, narrower than the 52.88px
    # minimum-hint room. This produces a >20px target while avoiding any cover move under touch.
    touch_anchor_rect = dict(x=rect['x'] + 28, y=rect['y'],
                             width=rect['width'], height=rect['height'])
    for identifier in ids[:-1]:
        ipc('window-rules/configure-view', dict(id=identifier, geometry=rect))
    ipc('window-rules/configure-view', dict(id=ids[-1], geometry=rect))
    # Keep the equal-sized cover focused and above Front. The held case moves it sideways,
    # avoiding a client resize whose drawn bounds can lag or remain at the original size.
    ipc('window-rules/focus-view', dict(id=ids[-1]))
    front, rears, anchor = ids[2], ids[:2], ids[3]
    def place_anchor(geometry, reason):
        ipc('window-rules/configure-view', dict(id=anchor, geometry=geometry))
        observed = {}
        def placed():
            view = next((v for v in views() if v['id'] == anchor), None)
            hint = next((r for r in rows().values() if r['window'] == anchor), None)
            if not view or not hint: return None
            actual, drawn = view['geometry'], hint['drawn']
            observed.update(view=actual, drawn=drawn)
            keys = ('x', 'y', 'width', 'height')
            return view if all(abs(float(actual[key]) - float(geometry[key])) < .5 and
                               abs(float(drawn[key]) - float(geometry[key])) < .5
                               for key in keys) else None
        wait(placed, 'cover geometry and drawn bounds: ' + reason,
             timeout_detail=lambda: json.dumps(observed))
    def centers():
        return [(g['x'] + g['width'] / 2, g['y'] + g['height'] / 2)
                for g in (v['geometry'] for v in views() if v['id'] in ids)]
    wait(lambda: len(centers()) == 4 and max(math.dist(a, b) for a in centers() for b in centers()) < 30,
         'fixture stacked')

    # A smaller requested cover did not produce smaller drawn bounds on the client in the
    # prior runner result. Keep the cover the same size as Front; full overlap forces the
    # avoidance target away from home.
    place_anchor(rect, 'fully cover Front for the frozen-offset case')
    # Case 1: an offset that cannot ease to its target (fault injection).
    ipc('scottland/hints', dict(freeze_offset=front))
    run_case('frozen', front, rears)
    ipc('scottland/hints', dict(freeze_offset=0))
    wait(at_rest, 'offsets home after the frozen case')

    # Case 2: keep the full-size cover over all but a narrow touch strip before touch-down.
    # The strip cannot fit the minimum hint, so the solver moves Front while the real hold is
    # armed. After verifying touch owns Front, restore Anchor focus without moving geometry.
    place_anchor(touch_anchor_rect, 'move cover aside to expose Front for the real held touch')
    run_case('held', front, rears, touch_point(front, [anchor]), keep_focused=anchor,
             hold_lift_timer=True)
    ipc('stipc/touch_release', dict(finger=0)); touching = False
    ipc('scottland/test-input', dict(hold_lift_timer=False))
    print(f'hint stuck offset: {passes} passed, {failures} failed', flush=True)
    sys.exit(bool(failures))
finally:
    for name in list(held):
        try: key(name, False)
        except Exception: pass
    if touching:
        try: ipc('stipc/touch_release', dict(finger=0))
        except Exception: pass
    try: ipc('scottland/hints', dict(freeze_offset=0))
    except Exception: pass
    try: ipc('scottland/test-input', dict(hold_lift_timer=False))
    except Exception: pass
    for p in clients:
        if p.poll() is None: p.terminate()
    for p in clients:
        try: p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            p.kill(); p.wait()
    sock.close()
