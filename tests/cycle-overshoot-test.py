#!/usr/bin/env python3
"""WK29: actual Alt/hint cycles, sampled render state, two-output edge safety."""
import json
import math
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time

out = Path(sys.argv[1])
sock = socket.socket(socket.AF_UNIX)
sock.connect(os.environ['WAYFIRE_SOCKET'])
sock.settimeout(5)
passed = failed = 0


def receive(n):
    data = b''
    while len(data) < n:
        part = sock.recv(n - len(data))
        if not part:
            raise RuntimeError('compositor disconnected')
        data += part
    return data


def ipc(method, data=None):
    body = json.dumps({'method': method, 'data': data or {}}).encode()
    sock.sendall(struct.pack('<I', len(body)) + body)
    reply = json.loads(receive(struct.unpack('<I', receive(4))[0]))
    if isinstance(reply, dict) and 'error' in reply:
        raise RuntimeError(reply)
    return reply


def check(ok, name):
    global passed, failed
    passed += bool(ok)
    failed += not ok
    print(('PASS  ' if ok else 'FAIL  ') + name, flush=True)


def wait(fn):
    until = time.monotonic() + 5
    while time.monotonic() < until:
        if value := fn():
            return value
        time.sleep(.02)
    raise RuntimeError('timed out')


def state():
    return next((v for v in ipc('scottland/layout-state')['views'] if v['title'] == 'CycleSpring'), None)


def key(code, down):
    ipc('stipc/feed_key', {'key': 'KEY_' + code, 'state': down})


def tap(code):
    key(code, True)
    key(code, False)


def hold():
    key('LEFTALT', True)
    wait(lambda: ipc('scottland/hints')['active'])
    time.sleep(.08)


def center(v):
    f = v['frame']
    return [f['x'] + f['width'] / 2, f['y'] + f['height'] / 2]


def drag(x, y):
    cx, cy = center(state())
    ipc('stipc/move_cursor', {'x': round(cx), 'y': round(cy)})
    key('LEFTMETA', True)
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'press'})
    for i in range(1, 13):
        ipc('stipc/move_cursor', {'x': round(cx + (x - cx) * i / 12),
                                 'y': round(cy + (y - cy) * i / 12)})
        time.sleep(.02)
    time.sleep(.15)  # stationary drop, never a coast
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'release'})
    key('LEFTMETA', False)
    time.sleep(.5)


def cycle(label, elastic=True, crossing=True, capture=False, scale_crossing=True):
    hold()
    hint = next(h['hint'] for h in ipc('scottland/hints')['hints'] if h['window'] == wid)
    before = state()
    start = time.monotonic()
    for c in hint:
        tap(c.upper())
    samples = []
    captures = []
    deadlines = iter([0, .065, .12, .18, .24, .32] if capture else [])
    deadline = next(deadlines, None)
    while time.monotonic() - start < .5:
        elapsed = time.monotonic() - start
        samples.append({'ms': elapsed * 1000, 'view': state()})
        if deadline is not None and elapsed >= deadline:
            path = out / f'{label}-{len(captures)}.png'
            process = subprocess.Popen(['grim', '-o', output['name'], str(path)])
            captures.append((elapsed, path, process))
            deadline = next(deadlines, None)
        time.sleep(.004)
    key('LEFTALT', False)
    end = state()
    a, b = center(before), center(end)
    delta = [b[i] - a[i] for i in range(2)]
    length2 = sum(d * d for d in delta)
    progress = [sum((center(s['view'])[i] - a[i]) * delta[i] for i in range(2)) / length2 for s in samples]
    peak = max(progress)
    print(f'{label}: peak position {peak:.5f}, duration samples {len(samples)}', flush=True)
    check(length2 > 100, label + ': hint really moves the window')
    if crossing:
        check(1.015 < peak < 1.045 if elastic else peak <= 1.0001,
              label + ': one small position overshoot' if elastic else label + ': zero disables position overshoot')
        first = next((i for i, p in enumerate(progress) if p > 1.0001), None)
        if elastic:
            check(first is not None and all(p >= .99999 for p in progress[first:]), label + ': no second target crossing')
    ds = end['applied_scale'] - before['applied_scale']
    if abs(ds) > .01 and scale_crossing:
        scales = [(s['view']['applied_scale'] - before['applied_scale']) / ds for s in samples]
        check(1.015 < max(scales) < 1.045 if elastic else max(scales) <= 1.001,
              label + ': independent scale spring' if elastic else label + ': zero disables scale overshoot')
    check(all(math.dist(center(s['view']), b) < .0001 and
              abs(s['view']['applied_scale'] - end['target_scale']) < (1e-6 if elastic else .0005)
              for s in samples if s['ms'] >= 340), label + (': settles exactly and stays still' if elastic else ': preserves legacy settlement tolerance'))
    check(len({s['view']['zone'] for s in samples}) == 1 and
          len({s['view']['target_scale'] for s in samples}) == 1,
          label + ': stable destination zone and scale target')
    check(all(s['view']['applied_scale'] >= .05 - 1e-7 for s in samples),
          label + ': scale stays within the supported positive range')
    check(all(s['view']['frame']['x'] >= -.01 and s['view']['frame']['y'] >= -.01 and
              s['view']['frame']['x'] + s['view']['frame']['width'] <= width + .01 and
              s['view']['frame']['y'] + s['view']['frame']['height'] <= height + .01 for s in samples),
          label + ': live scaled footprint stays on its screen')
    check(next(v for v in ipc('window-rules/list-views') if v['id'] == wid)['output-id'] == output['id'],
          label + ': retains output beside the shared seam')
    (out / (label + '.json')).write_text(json.dumps({'before': before, 'samples': samples, 'after': end}, indent=2))
    for _, _, p in captures:
        p.wait(timeout=5)
    if captures:
        # A fixed crop and target-center guide make the small excursion readable.
        left = max(0, math.floor(min(before['frame']['x'], end['frame']['x']) - 45))
        top = max(0, math.floor(min(before['frame']['y'], end['frame']['y']) - 45))
        right = min(width, math.ceil(max(before['frame']['x'] + before['frame']['width'],
                                        end['frame']['x'] + end['frame']['width']) + 45))
        bottom = min(height, math.ceil(max(before['frame']['y'] + before['frame']['height'],
                                          end['frame']['y'] + end['frame']['height']) + 45))
        command = ['magick', 'montage', '-background', '#161b24', '-fill', 'white',
                   '-font', subprocess.check_output(['fc-match', '-f', '%{file}', 'sans'], text=True), '-pointsize', '16']
        for elapsed, path, _ in captures:
            cropped = path.with_name(path.stem + '-detail.png')
            subprocess.run(['magick', str(path), '-stroke', '#52bcca', '-strokewidth', '1',
                            '-draw', f'line {b[0]},{top} {b[0]},{bottom}',
                            '-crop', f'{right-left}x{bottom-top}+{left}+{top}', '+repage', str(cropped)], check=True)
            command += ['-label', f'{elapsed * 1000:.0f} ms', str(cropped)]
        command += ['-geometry', '480x270+4+4', '-tile', '3x2', str(out / 'frame-strip.png')]
        subprocess.run(command, check=True)
    time.sleep(.1)


client = None
try:
    outputs = sorted(ipc('window-rules/list-outputs'), key=lambda o: o['geometry']['x'])
    output = outputs[0]
    width, height = output['geometry']['width'], output['geometry']['height']
    check(len(outputs) == 2, 'two isolated outputs exercise a real shared seam')
    ipc('wayfire/set-config-options', {'scottland/sounds': False, 'scottland/alt_hold_delay': 100})
    values = json.loads(subprocess.check_output(['core/libexec/scottland-ctl', 'get']))
    check(values['cycle_overshoot'] == 3, 'scottland-ctl exposes shipped 3% default')
    client = subprocess.Popen(['python3', 'tests/windowing-key-recorder.py', 'CycleSpring', str(out / 'keys.jsonl')])
    wid = wait(state)['id']
    time.sleep(.5)
    drag(150, height / 2)
    drag(width / 2, height / 2)
    cycle('center-to-periphery', capture=True)
    cycle('periphery-to-center')
    # The second physical press docks during the first spring. The existing
    # morph must capture the visible frame before it cancels that spring.
    hold()
    hint = next(h['hint'] for h in ipc('scottland/hints')['hints'] if h['window'] == wid)
    for c in hint:
        tap(c.upper())
    time.sleep(.07)
    before_dock = state()
    for c in hint:
        tap(c.upper())
    after_dock = state()
    check(math.dist(center(before_dock), center(after_dock)) < 25 and
          abs(before_dock['frame']['width'] - after_dock['frame']['width']) < 20,
          'rapid double-tap hands the drawn spring frame to the unchanged widget morph')
    key('LEFTALT', False)
    time.sleep(.8)
    check(state()['widgetized'], 'rapid double-tap completes docking')
    hold()
    for c in hint:
        tap(c.upper())
    time.sleep(.5)
    check(not state()['widgetized'] and abs(state()['applied_scale'] - 1) < 1e-6,
          'widget hint opens and settles at full scale')
    key('LEFTALT', False)
    subprocess.run(['core/libexec/scottland-ctl', 'set', 'cycle_overshoot', '0'], check=True)
    cycle('disabled-to-periphery', elastic=False)
    cycle('disabled-to-center', elastic=False)
    subprocess.run(['core/libexec/scottland-ctl', 'set', 'cycle_overshoot', '3'], check=True)
    # Both endpoint frames fit, but an unbounded spring would leave the top edge.
    drag(width / 2, 91)
    drag(width - 45, height - 45)
    cycle('edge-to-center', crossing=False)
    cycle('edge-to-periphery', crossing=False)
    drag(45, height - 45)
    cycle('left-edge-to-center', crossing=False)
    cycle('center-to-left-edge', crossing=False)
    drag(width - 45, height - 45)
    subprocess.run(['core/libexec/scottland-ctl', 'set', 'cycle_overshoot', '10'], check=True)
    ipc('wayfire/set-config-options', {'scottland/min_scale': .05, 'scottland/max_scale': .05})
    time.sleep(.4)
    cycle('maximum-at-top-edge', crossing=False, scale_crossing=False)
    cycle('minimum-scale-at-seam', crossing=False, scale_crossing=False)
finally:
    key('LEFTALT', False)
    key('LEFTMETA', False)
    if client:
        client.terminate()
        client.wait(timeout=5)
print(f'{passed} passed, {failed} failed', flush=True)
sys.exit(bool(failed))
