#!/usr/bin/env python3
"""Main-loop latency (P8) during pairing and a zone cycle, in an isolated headless session.

A second IPC connection pings the compositor (stipc/ping) about every millisecond; a round trip
can only finish when the main loop is free, so its time bounds the longest main-loop stall.
Reports median, 99th percentile and worst per phase. Usage: pairing-latency.py ARTIFACTS
Run on a quiet machine: other load shows up as latency.
"""
import json, os, socket, statistics, struct, threading, time
from pathlib import Path

src = open(Path(__file__).with_name('pairing-test.py')).read()
exec(src[:src.index('\ntry:\n')])  # its helpers: ipc, setup, press_hint, alt, layout, ...

pings = []
stop = False
def pinger():
    s = socket.socket(socket.AF_UNIX); s.connect(os.environ['WAYFIRE_SOCKET'])
    body = json.dumps({'method': 'stipc/ping', 'data': {}}).encode(); message = struct.pack('<I', len(body)) + body
    def read(n):
        out = b''
        while len(out) < n: out += s.recv(n - len(out))
        return out
    while not stop:
        t = time.monotonic(); s.sendall(message); read(struct.unpack('<I', read(4))[0])
        pings.append((t, (time.monotonic() - t) * 1000)); time.sleep(.001)

results = {}
def phase(name, action, settle_s=1.0):
    t0 = time.monotonic()
    try: action()
    except RuntimeError as error: # e.g. a baseline build without pairing or the test touchpad
        print(name, 'skipped:', error, flush=True); return
    time.sleep(settle_s); t1 = time.monotonic()
    d = sorted(ms for t, ms in pings if t0 <= t <= t1)
    results[name] = {'samples': len(d), 'median_ms': round(statistics.median(d), 2),
                     'p99_ms': round(d[int(len(d) * .99) - 1], 2), 'worst_ms': round(d[-1], 2)}
    print(name, results[name], flush=True)

try:
    ipc('wayfire/set-config-options', {'scottland/sounds': False, 'scottland/alt_hold_delay': 300,
        'scottland/window_avoidance_always': False, 'output:HEADLESS-1/mode': '1600x1000@60000'})
    try: ipc('wayfire/set-config-options', {'scottland/window_hold_delay': 500})
    except RuntimeError: print('baseline build: no hint holds', flush=True)
    time.sleep(1)
    A, B, C = launch('LatA'), launch('LatB'), launch('LatC')
    threading.Thread(target=pinger, daemon=True).start()
    phase('idle', lambda: None, 2.0)
    setup([(C, 400, 400, 300, 200), (B, 900, 300, 900, 520), (A, 100, 150, 1000, 600)], A)
    phase('Window mode entry (Alt hold, hints appear)', lambda: alt(True), 1.0)
    phase('hint hold pairs, scaled 0.84, covered window peeks', lambda: press_hint(B, hold=.7), 1.5)
    alt(False)
    setup([(C, 400, 400, 300, 200), (B, 1060, 520, 420, 300), (A, 200, 140, 520, 360)], A)
    alt(True); time.sleep(1)
    phase('zone cycle (focused tap, glide to periphery)', lambda: press_hint(A, hold=.08), 1.5)
    alt(False)
    setup([(C, 400, 400, 300, 200), (B, 1060, 520, 420, 300), (A, 200, 140, 520, 360)], A)
    def pad_pair():
        x1, y1, x2, y2 = footprint(B); pointer((x1 + x2) / 2, (y1 + y2) / 2); time.sleep(.1)
        ipc('scottland/test-touchpad', {'event': 'hold_begin', 'fingers': 3}); time.sleep(.7)
        ipc('scottland/test-touchpad', {'event': 'hold_end', 'cancelled': False})
    phase('three-finger hold pairs', pad_pair, 1.5)
    (art / 'latency.json').write_text(json.dumps(results, indent=2))
finally:
    stop = True
    try: key('LEFTALT', False)
    except Exception: pass
    for client in clients:
        if client.poll() is None: client.terminate()
