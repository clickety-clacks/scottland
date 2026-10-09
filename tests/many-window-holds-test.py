#!/usr/bin/env python3
"""Exercise hint solo and pair with 20 or 32 real clients in an isolated headless session.

Fixture placement and focus use IPC; the actions use stipc keyboard input, and
the results are judged from compositor view geometry. Run once per fresh session
so the hint width resets between the two window counts.
"""
import json
import os
from pathlib import Path
import signal
import socket
import struct
import subprocess
import sys
import time

assert os.environ.get('SCOTTLAND_TEST_MODEL') == '1'
count = int(sys.argv[1])
assert count in (20, 32)
evidence = Path(sys.argv[2]).resolve()
evidence.mkdir(parents=True, exist_ok=True)
sock = socket.socket(socket.AF_UNIX)
sock.settimeout(15)
sock.connect(os.environ['WAYFIRE_SOCKET'])
clients = []


def ipc(method, data=None):
    body = json.dumps({'method': method, 'data': data or {}}).encode()
    sock.sendall(struct.pack('<I', len(body)) + body)

    def read(size):
        result = b''
        while len(result) < size:
            chunk = sock.recv(size - len(result))
            if not chunk:
                raise RuntimeError('compositor disconnected')
            result += chunk
        return result

    reply = json.loads(read(struct.unpack('<I', read(4))[0]))
    if isinstance(reply, dict) and 'error' in reply:
        raise RuntimeError(reply)
    return reply


def wait(predicate, label, timeout=15):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(.03)
    raise AssertionError(f'{label} timed out; last observation: {last!r}')


def views():
    return ipc('window-rules/list-views')


def geometry(view_id):
    return next(v['geometry'] for v in views() if v['id'] == view_id)


def hints():
    return ipc('scottland/hints')


def hint(view_id):
    return next(h['hint'] for h in hints()['hints'] if h['window'] == view_id)


def key(name, down):
    ipc('stipc/feed_key', {'key': 'KEY_' + name, 'state': down})


def hold_hint(view_id):
    text = hint(view_id)
    for letter in text[:-1]:
        key(letter.upper(), True)
        key(letter.upper(), False)
    last = text[-1].upper()
    key(last, True)
    try:
        time.sleep(.8)  # The deliberate WK39 hold, beyond its 500 ms default.
    finally:
        key(last, False)


def place(view_id, x, y, width, height):
    target = {'x': x, 'y': y, 'width': width, 'height': height}
    for _ in range(8):
        ipc('window-rules/configure-view', {'id': view_id, 'geometry': target})
        try:
            wait(lambda: (lambda g: (g['x'], g['y']) == (x, y) and
                         abs(g['width'] - width) <= 24 and abs(g['height'] - height) <= 24)
                 (geometry(view_id)), 'fixture placement', 1.5)
            return
        except AssertionError:
            pass
    raise AssertionError(f'fixture placement failed: {geometry(view_id)}')


def launch(index):
    title = f'scottland-many-window-{index}'
    clients.append(subprocess.Popen(
        ['foot', '--app-id=scottland-many-window', '--title=' + title,
         'sh', '-c', 'sleep 900'], start_new_session=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    return wait(lambda: next((v['id'] for v in views() if v.get('title') == title), None),
                f'client {index} mapped')


def center_x(g):
    return g['x'] + g['width'] / 2


try:
    ids = [launch(index) for index in range(count)]
    output = ipc('window-rules/list-outputs')[0]['geometry']
    left, right = ids[:2]
    place(left, output['x'] + 25, output['y'] + 130, 300, 220)
    place(right, output['x'] + output['width'] - 325, output['y'] + 210, 300, 220)
    ipc('window-rules/focus-view', {'id': left})
    width = 1 if count == 20 else 2
    key('LEFTALT', True)
    try:
        wait(lambda: hints()['active'], 'Window mode entered')
        labels = {view_id: hint(view_id) for view_id in (left, right)}
        assert all(len(label) == width for label in labels.values()), labels
        before_solo = geometry(left)
        hold_hint(left)
        middle = output['x'] + output['width'] / 2
        solo = wait(lambda: (lambda g: g if abs(center_x(g) - middle) < output['width'] / 5
                            and abs(center_x(g) - center_x(before_solo)) > 100 else None)
                    (geometry(left)), 'focused hint solo')
        place(right, output['x'] + output['width'] - 325, output['y'] + 210, 300, 220)
        ipc('window-rules/focus-view', {'id': left})
        before_pair = {view_id: geometry(view_id) for view_id in (left, right)}
        hold_hint(right)
        pair = wait(lambda: (lambda a, b: {left: a, right: b}
                            if center_x(a) < center_x(b) and
                            abs((a['y'] + a['height'] / 2) -
                                (b['y'] + b['height'] / 2)) <= 3 and
                            (a['x'], b['x']) != (before_pair[left]['x'], before_pair[right]['x'])
                            else None)(geometry(left), geometry(right)),
                    'unfocused hint pair')
        result = {'count': count, 'hint_width': width, 'labels': labels,
                  'before_solo': before_solo, 'solo': solo,
                  'before_pair': before_pair, 'pair': pair}
        (evidence / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        print(f'PASS {count} windows: {width}-letter focused solo and unfocused pair', flush=True)
    finally:
        key('LEFTALT', False)
        wait(lambda: not hints()['active'], 'Window mode exited')
finally:
    for client in clients:
        if client.poll() is None:
            os.killpg(client.pid, signal.SIGTERM)
    for client in clients:
        try:
            client.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(client.pid, signal.SIGKILL)
            client.wait(timeout=3)
    sock.close()
