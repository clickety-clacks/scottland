#!/usr/bin/env python3
"""WK41: a hint on a quiet output appears during a glide on another output.

Run in a caller-owned two-output headless session. Alt is real stipc input. IPC placement and
present are declared fixture setup: present starts the unrelated glide and changes focus, which
removes the quiet window's existing hint. Captured client-colored pixels prove the glide is moving;
captured letter-color pixels prove that the quiet hint is recreated before that motion finishes.
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

art = Path(sys.argv[1]).resolve(); art.mkdir(parents=True, exist_ok=True)
sock = socket.socket(socket.AF_UNIX); sock.settimeout(8); sock.connect(os.environ['WAYFIRE_SOCKET'])
clients = []
held = set()
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


def wait(fn, what):
    end = time.monotonic() + 10
    last = None
    while time.monotonic() < end:
        last = fn()
        if last: return last
        time.sleep(.02)
    raise RuntimeError(f'{what}: timeout, last {last}')


def key(name, down):
    ipc('stipc/feed_key', dict(key='KEY_' + name, state=down))
    (held.add if down else held.discard)(name)


def hints(): return ipc('scottland/hints')['hints']
def views(): return ipc('window-rules/list-views')


def launch(title, color):
    palette = art / (title + '.json'); palette.write_text(json.dumps(dict(background=color)))
    clients.append(subprocess.Popen([sys.executable, str(Path(__file__).with_name('hint-style-app.py')),
                                    title, '280', '240', str(palette)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    return wait(lambda: next((v['id'] for v in views() if v.get('title') == title), None), title + ' mapped')


def capture():
    data = subprocess.check_output(['grim', '-t', 'ppm', '-'], timeout=8)
    fields, pos = [], 0
    while len(fields) < 4:
        while data[pos:pos + 1].isspace(): pos += 1
        end = pos
        while not data[end:end + 1].isspace(): end += 1
        fields.append(data[pos:end]); pos = end
    return int(fields[1]), int(fields[2]), data[pos + 1:]


try:
    outputs = sorted(ipc('window-rules/list-outputs'), key=lambda o: o['geometry']['x'])
    assert len(outputs) == 2, 'two private outputs required'
    ipc('wayfire/set-config-options', {'scottland/sounds': False, 'scottland/window_mode_tint': 0,
        'scottland/window_avoidance_always': False, 'scottland/hint_avoidance_always': False})
    quiet = launch('Quiet', '#1f232c'); moving = launch('Moving', '#d05090')
    for identifier, output, x in ((quiet, outputs[0], 500), (moving, outputs[1], 60)):
        ipc('window-rules/configure-view', dict(id=identifier, output_id=output['id'],
            geometry=dict(x=x, y=240, width=280, height=240)))
    wait(lambda: next(v for v in views() if v['id'] == moving)['output-id'] == outputs[1]['id'], 'moving output')
    ipc('window-rules/focus-view', dict(id=quiet))
    key('LEFTALT', True)
    h = wait(lambda: next((h for h in hints() if h['window'] == quiet and h.get('visible') and
                          h.get('pop', 0) >= .999 and h.get('badge')), None), 'quiet hint initially settled')
    b = h['badge']; rgb = tuple(round(c * 255) for c in h['color'])
    cx, cy, radius = b['x'] + b['size'] / 2, b['y'] + b['size'] / 2, b['size'] / 2
    split = outputs[1]['geometry']['x']
    moving_color = bytes((0xd0, 0x50, 0x90))
    rows = []
    ipc('scottland/present', dict(window=moving))  # unrelated-glide fixture, bypassing its launcher input
    end = time.monotonic() + 5
    while time.monotonic() < end:
        width, height, pixels = capture()
        points = [m.start() // 3 for m in re.finditer(re.escape(moving_color), pixels)
                  if m.start() % 3 == 0 and m.start() // 3 % width >= split]
        center = ((min(i % width for i in points) + max(i % width for i in points)) / 2) if points else None
        letter = sum(max(abs(pixels[(y * width + x) * 3 + c] - rgb[c]) for c in range(3)) <= 6
                     for y in range(max(0, int(cy - radius)), min(height, int(cy + radius) + 1))
                     for x in range(max(0, int(cx - radius)), min(width, int(cx + radius) + 1))
                     if (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2)
        rows.append(dict(moving_pixel_center=center, quiet_letter_pixels=letter))
        # A stopped motion plus a fully restored circle ends observation; no timing pass criterion.
        if len(rows) >= 4 and center is not None and all(
                r['moving_pixel_center'] is not None and abs(r['moving_pixel_center'] - center) < 1
                for r in rows[-3:]) and letter >= 8: break
    (art / 'unrelated-motion-pixels.json').write_text(json.dumps(rows, indent=2))
    centers = [r['moving_pixel_center'] for r in rows if r['moving_pixel_center'] is not None]
    moved = bool(centers) and max(centers) - min(centers) > 20
    concurrent = any(a['moving_pixel_center'] is not None and b['moving_pixel_center'] is not None and
                     abs(b['moving_pixel_center'] - a['moving_pixel_center']) > 3 and
                     b['quiet_letter_pixels'] >= 4 for a, b in zip(rows, rows[1:]))
    print(('PASS' if moved else 'FAIL') + ' unrelated glide really moves (client pixels)', flush=True)
    print(('PASS' if concurrent else 'FAIL') + ' quiet hint is drawn while unrelated output still moves', flush=True)
    print(json.dumps(rows), flush=True)
    sys.exit(0 if moved and concurrent else 1)
finally:
    for name in list(held):
        try: key(name, False)
        except Exception: pass
    for p in clients:
        if p.poll() is None: p.terminate()
    for p in clients:
        try: p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            p.kill(); p.wait()
    sock.close()
