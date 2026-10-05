#!/usr/bin/env python3
"""Main-loop latency under real input (docs/main-loop.md, ML1-ML2). Run inside a headless session
through tests/mainloop-latency-test.sh, which starts and stops it.

  mainloop-latency-test.py OUTDIR [--windows N] [--mike] [--gate PHASE] [scenario ...]

Probes: pings at 1 kHz and pointer events at 500 Hz, each pipelined on its own socket (sent on a
clock, replies read by another thread), so a stalled loop shows as lateness instead of slowing the
offered rate. IPC round trip is a proxy for input dispatch delay, not for what reaches the screen.
Per scenario: ping and pointer-reply lateness percentiles, offered and completed rates, the ML2
maximum and per-scope maxima from scottland/loop-stats (absent on builds without it), host load.

Every number is labelled: "measured" (an observation), "ceiling" (a target from the exception table,
tests/mainloop-exceptions.json) or "open exception" (a scope over 2 ms that the table lists). With
--gate, the run fails on a scope over its ceiling, ML2 over 4 ms outside listed exceptions, or ping
p99 over 10 ms; a late ping with no scope over its ceiling and high host load is reported as noise.
"""
import collections, json, math, os, signal, socket, struct, subprocess, sys, threading, time
from pathlib import Path

args = sys.argv[1:]
out = Path(args.pop(0)); out.mkdir(parents=True, exist_ok=True)
def option(name, default=None, flag=False):
    if name not in args: return default
    i = args.index(name); args.pop(i)
    return True if flag else args.pop(i)
nwin = int(option('--windows', 10))
mike = option('--mike', False, True)
gate = option('--gate')
only = list(args)
root = Path(__file__).resolve().parents[1]
exceptions = json.loads((root / 'tests/mainloop-exceptions.json').read_text())


class Channel:
    """One IPC connection. call() waits for its reply; send() pipelines and a reader thread records
    each reply's latency (replies come back in order)."""
    def __init__(self, deadline=10):
        self.sock = socket.socket(socket.AF_UNIX); self.sock.connect(os.environ['WAYFIRE_SOCKET'])
        self.sock.settimeout(deadline)
        self.pending = collections.deque(); self.done = []; self.lock = threading.Lock()
        self.reader = None; self.closed = False; self.sent = 0
    def _read(self, n):
        b = b''
        while len(b) < n:
            more = self.sock.recv(n - len(b))
            if not more: raise EOFError('compositor closed the IPC connection')
            b += more
        return b
    def _frame(self, method, data):
        b = json.dumps({'method': method, 'data': data or {}}).encode()
        return struct.pack('<I', len(b)) + b
    def call(self, method, data=None):
        self.sock.sendall(self._frame(method, data))
        reply = json.loads(self._read(struct.unpack('<I', self._read(4))[0]))
        if isinstance(reply, dict) and 'error' in reply: raise RuntimeError((method, reply))
        return reply
    def start_reader(self):
        def run():
            try:
                while not self.closed:
                    raw = self._read(struct.unpack('<I', self._read(4))[0])
                    now = time.monotonic()
                    with self.lock:
                        sent = self.pending.popleft() if self.pending else now
                        self.done.append((sent, (now - sent) * 1000))
            except (EOFError, OSError, socket.timeout):
                pass
        self.reader = threading.Thread(target=run, daemon=True); self.reader.start()
    def send(self, method, data=None):
        frame = self._frame(method, data)
        with self.lock: self.pending.append(time.monotonic())
        self.sock.sendall(frame); self.sent += 1
    def take(self, t0, t1):
        with self.lock: return [ms for t, ms in self.done if t0 <= t <= t1]
    def close(self):
        self.closed = True
        try: self.sock.shutdown(socket.SHUT_RDWR)
        except OSError: pass
        self.sock.close()


control = Channel()
pings = Channel(); pings.sock.settimeout(30); pings.start_reader()
inputs = Channel(); inputs.sock.settimeout(30); inputs.start_reader()
stop_pinging = threading.Event()
def pinger():
    period, nxt = .001, time.monotonic()
    while not stop_pinging.is_set():
        try: pings.send('stipc/ping')
        except OSError: return
        nxt += period
        delay = nxt - time.monotonic()
        if delay > 0: time.sleep(delay)
        else: nxt = time.monotonic()   # behind: offer at most one per period, never a burst
threading.Thread(target=pinger, daemon=True).start()

pace = {'next': time.monotonic()}
def paced(method, data=None, period=.002):
    """Input at 500 Hz on the input channel, pipelined: never waits for the compositor."""
    delay = pace['next'] - time.monotonic()
    if delay > 0: time.sleep(delay)
    pace['next'] = max(pace['next'] + period, time.monotonic() - .02)
    inputs.send(method, data)
def pointer(x, y): paced('stipc/move_cursor', {'x': round(x), 'y': round(y)})
def key(name, down): paced('stipc/feed_key', {'key': 'KEY_' + name, 'state': down})
def tap(name): key(name, True); key(name, False)
def button(mode): paced('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': mode})
def drain(limit=10):
    end = time.monotonic() + limit
    while time.monotonic() < end:
        with inputs.lock:
            if not inputs.pending: return True
        time.sleep(.01)
    return False
def views(): return control.call('scottland/layout-state')['views']
def view(title): return next(v for v in views() if v['title'] == title)
def goo():
    s = control.call('scottland/goo-state')['screens']
    return s[0] if s else {'sleeping': True, 'steps': 0}
def wait_sleep(limit=45):
    end = time.monotonic() + limit
    while time.monotonic() < end:
        if goo()['sleeping']: return True
        time.sleep(.1)
    return False
def has_stats():
    try: control.call('scottland/loop-stats', {'reset': True}); return True
    except RuntimeError: return False
stats_available = has_stats()
def loadavg(): return float(Path('/proc/loadavg').read_text().split()[0])

results = []
def scenario(name):
    def wrap(fn):
        if only and name not in only: return fn
        time.sleep(.5); drain()
        if stats_available: control.call('scottland/loop-stats', {'reset': True})
        pings_before, inputs_before = pings.sent, inputs.sent
        load0, t0 = loadavg(), time.monotonic()
        try: note = fn()
        except Exception as e: note = 'ERROR ' + repr(e)
        drain(); t1 = time.monotonic(); time.sleep(.05)
        stats = control.call('scottland/loop-stats') if stats_available else None
        results.append({'name': name, 't0': t0, 't1': t1, 'note': note, 'load': max(load0, loadavg()),
            'pings': sorted(pings.take(t0, t1)), 'inputs': sorted(inputs.take(t0, t1)),
            'offered': {'pings': pings.sent - pings_before, 'inputs': inputs.sent - inputs_before}, 'stats': stats})
        print('done', name, round(t1 - t0, 2), note if note is not None else '', flush=True)
        return fn
    return wrap

# Fixture: Fable's overlapping layout at 2560x1600 (design section 1).
outputs = [o['name'] for o in control.call('window-rules/list-outputs')]
control.call('wayfire/set-config-options', {f'output:{outputs[0]}/mode': '2560x1600@60000'})
time.sleep(1)
clients = []
def spawn(title, *extra, seconds=3600):
    p = subprocess.Popen(['foot', '-c', '/dev/null', '-T', title, *extra, 'sleep', str(seconds)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    clients.append(p); return p
for i in range(nwin):
    spawn(f'perf-{i}'); time.sleep(.25)
time.sleep(2)
geo = [(200,150,900,600),(1200,150,1100,700),(250,850,700,500),(1050,950,600,450),(1750,950,600,450),
       (1000,420,400,260),(700,400,500,360),(1100,700,520,380),(1700,500,500,380),(400,650,500,340)]
for i in range(nwin):
    g = geo[i % len(geo)]; k = i // len(geo)
    v = view(f'perf-{i}')
    control.call('window-rules/configure-view', {'id': v['id'],
        'geometry': dict(zip(('x','y','width','height'), (g[0]+37*k, g[1]+29*k, g[2], g[3])))})
    time.sleep(.15)
if mike:  # Mike's live goo values (design 1.3), not shipped defaults
    control.call('wayfire/set-config-options', {'scottland/goo_reach': 33.0, 'scottland/goo_thickness': 22.0,
        'scottland/goo_overlap_film': 10.0, 'scottland/goo_soak': .9, 'scottland/goo_lump': 315.0,
        'scottland/goo_noise': .38, 'scottland/goo_swell': .68})
pointer(1280, 1500); drain()
print('settled', wait_sleep(), flush=True)

@scenario('idle')
def _(): time.sleep(3)

@scenario('window-mode-entry')     # the first Alt hold of the session: cold hint raster (acceptance target)
def _():
    key('LEFTALT', True); drain(); time.sleep(1.2)
    h = control.call('scottland/hints'); n = (h.get('active'), len(h.get('hints', [])))
    key('LEFTALT', False); drain(); time.sleep(1)
    return n

@scenario('ipc-queries')
def _():
    for m in ('scottland/layout-state', 'scottland/desktop-model', 'scottland/widgets', 'scottland/goo-state',
              'scottland/hints', 'window-rules/list-views', 'stipc/ping'):
        for _ in range(30): control.call(m)

@scenario('pointer-sweep')
def _():
    for i in range(3000):
        a = i / 3000 * 2 * math.pi * 3
        pointer(1280 + 1100 * math.cos(a), 800 + 650 * math.sin(a * 1.3))

@scenario('pointer-halo')
def _():
    f = view('perf-4')['frame']
    for k in range(3):
        for i in range(600):
            pointer(f['x'] + f['width'] * i / 600, f['y'] + f['height'] + 4 + (i % 7))
    drain(); return wait_sleep()

@scenario('drag')
def _():
    f = view('perf-5')['frame']
    cx, cy = f['x'] + f['width'] / 2, f['y'] + f['height'] / 2
    pointer(cx, cy); key('LEFTMETA', True); button('press')
    for i in range(1200):
        a = i / 1200 * 4 * math.pi
        pointer(1280 + 900 * math.cos(a), 800 + 550 * math.sin(a))
    pointer(cx, cy); button('release'); key('LEFTMETA', False)

@scenario('after-drag-settle')
def _(): return wait_sleep()

@scenario('window-mode')
def _():
    key('LEFTALT', True); drain(); time.sleep(2.5)
    h = control.call('scottland/hints'); n = (h.get('active'), len(h.get('hints', [])))
    key('LEFTALT', False); drain(); time.sleep(1)
    return n

@scenario('window-mode-arrows')
def _():
    key('LEFTALT', True); drain(); time.sleep(.6)
    for name in ('RIGHT', 'DOWN', 'LEFT', 'UP') * 2:
        key(name, True); drain(); time.sleep(.35); key(name, False); drain(); time.sleep(.1)
    key('LEFTALT', False); drain(); time.sleep(1)

@scenario('always-avoid-drag')
def _():
    control.call('wayfire/set-config-options', {'scottland/window_avoidance_always': True}); time.sleep(.5)
    f = view('perf-6')['frame']
    cx, cy = f['x'] + f['width'] / 2, f['y'] + f['height'] / 2
    pointer(cx, cy); key('LEFTMETA', True); button('press')
    for i in range(1000):
        a = i / 1000 * 4 * math.pi
        pointer(1280 + 800 * math.cos(a), 800 + 500 * math.sin(a))
    pointer(cx, cy); button('release'); key('LEFTMETA', False); drain(); time.sleep(1.5)
    control.call('wayfire/set-config-options', {'scottland/window_avoidance_always': False}); time.sleep(.5)

@scenario('map-unmap')
def _():
    for i in range(4):
        spawn(f'extra-{i}', seconds=2); time.sleep(.6)
    time.sleep(3)

@scenario('long-title')            # a 4,096-character title in the switcher and hints (design 2.4)
def _():
    spawn('long-' + 'x' * 4091); time.sleep(1.5)
    key('LEFTALT', True); drain(); time.sleep(1.5); key('LEFTALT', False); drain(); time.sleep(.5)

@scenario('attention-breath-sleep')
def _():
    v = view('perf-2')
    control.call('scottland/attention', {'window': v['id'], 'attention': True, 'source': 'mainloop'})
    slept = wait_sleep(); time.sleep(6)
    control.call('scottland/attention', {'window': v['id'], 'attention': False, 'source': 'mainloop'})
    time.sleep(1)
    return slept

@scenario('slow-subscriber')       # a model subscriber that never reads (Wayfire drops it, never waits)
def _():
    lazy = socket.socket(socket.AF_UNIX); lazy.connect(os.environ['WAYFIRE_SOCKET'])
    b = json.dumps({'method': 'scottland/subscribe', 'data': {}}).encode()
    lazy.sendall(struct.pack('<I', len(b)) + b)
    f = view('perf-7')['frame']
    pointer(f['x'] + 40, f['y'] + 40); key('LEFTMETA', True); button('press')
    for i in range(600): pointer(f['x'] + 40 + 300 * math.sin(i / 50), f['y'] + 40 + 200 * math.cos(i / 50))
    button('release'); key('LEFTMETA', False); drain(); lazy.close()

@scenario('widgetize')
def _():
    if not os.environ.get('MAINLOOP_WIDGETS'): return 'skipped: no widget service'
    for title, x in (('perf-0', 15), ('perf-1', 2545)):
        f = view(title)['frame']
        cx, cy = f['x'] + f['width'] / 2, f['y'] + f['height'] / 2
        pointer(cx, cy); key('LEFTMETA', True); button('press')
        for i in range(1, 61): pointer(cx + (x - cx) * i / 60, cy + (350 - cy) * i / 60)
        button('release'); key('LEFTMETA', False); drain(); time.sleep(2)
    return len(control.call('scottland/widgets')['widgets'])

@scenario('widgets-8')             # eight widgets on the rails
def _():
    if not os.environ.get('MAINLOOP_WIDGETS'): return 'skipped: no widget service'
    for i in range(6):
        spawn(f'wid-{i}'); time.sleep(.4)
        f = view(f'wid-{i}')['frame']
        cx, cy = f['x'] + f['width'] / 2, f['y'] + f['height'] / 2
        x = 15 if i % 2 else 2545
        pointer(cx, cy); key('LEFTMETA', True); button('press')
        for j in range(1, 41): pointer(cx + (x - cx) * j / 40, cy + (200 + 150 * i - cy) * j / 40)
        button('release'); key('LEFTMETA', False); drain(); time.sleep(1.5)
    return len(control.call('scottland/widgets')['widgets'])

@scenario('widget-attention-sleep')
def _():
    w = control.call('scottland/widgets')['widgets']
    if not w: return 'no widgets'
    control.call('scottland/attention', {'window': w[0]['window'], 'attention': True, 'source': 'mainloop'})
    pointer(1280, 1500); drain(); slept = wait_sleep(); time.sleep(6)
    control.call('scottland/attention', {'window': w[0]['window'], 'attention': False, 'source': 'mainloop'})
    time.sleep(1)
    return slept

@scenario('settings-slider')
def _():
    for i in range(60):
        control.call('wayfire/set-config-options', {'scottland/center_width': 30 + (i % 10), 'scottland/rail_width': 1.9,
            'scottland/blend_width': 40.0})
        control.call('wayfire/set-config-options', {'scottland/goo_reach': 30 + (i % 7)})
        time.sleep(.03)
    time.sleep(1)

@scenario('scale-change')           # output scale change mid-run, with the pointer moving
def _():
    for scale in (1.5, 1.0):
        control.call('wayfire/set-config-options', {f'output:{outputs[0]}/scale': scale})
        for i in range(250): pointer(1280 + 600 * math.cos(i / 40), 800 + 400 * math.sin(i / 40))
        drain(); time.sleep(1)

@scenario('two-outputs')
def _():
    if len(outputs) < 2: return 'skipped: one output'
    for i in range(1000):
        pointer((i * 7) % 5000, 800 + 400 * math.sin(i / 30))

stop_pinging.set(); time.sleep(.2); drain()
for c in clients:
    try: os.killpg(c.pid, signal.SIGTERM)
    except ProcessLookupError: pass

# Report.
def pct(a, p): return a[min(len(a) - 1, int(len(a) * p))] if a else float('nan')
ceilings = {name: e['ceiling_ms'] for name, e in exceptions['scopes'].items()}
budget = exceptions['budget_ms']; ml2_budget = exceptions['ml2_ms']
failures, noise = [], []
lines = [f"{'scenario':24} {'pings':>6} {'p50':>6} {'p99':>7} {'max':>8} {'>8ms':>5} | {'input p50/p99/max':>20} {'offered/s':>9} "
         f"| {'ML2':>6} worst scopes (measured ms; * open exception, ! over ceiling)"]
for r in results:
    d, inp = r['pings'], r['inputs']
    span = max(r['t1'] - r['t0'], 1e-6)
    over = sum(1 for v in d if v > 8)
    row = f"{r['name']:24} {len(d):6} {pct(d,.5):6.2f} {pct(d,.99):7.2f} {d[-1] if d else 0:8.2f} {over:5} | " \
          f"{pct(inp,.5):6.2f}/{pct(inp,.99):6.2f}/{inp[-1] if inp else 0:6.2f} {r['offered']['inputs'] / span:9.0f} | "
    scope_over = []
    if r['stats']:
        s = r['stats']
        row += f"{s['ml2_max_ms']:6.2f} "
        worst = sorted(s['scopes'].items(), key=lambda kv: -kv[1]['max_ms'])[:6]
        marks = []
        for name, v in worst:
            ceiling = ceilings.get(name, budget)
            flag = '!' if v['max_ms'] > ceiling else ('*' if name in ceilings and v['max_ms'] > budget else '')
            marks.append(f"{name} {v['max_ms']:.2f}{flag}")
        row += ', '.join(marks)
        for name, v in s['scopes'].items():
            if v['max_ms'] > ceilings.get(name, budget): scope_over.append((name, v['max_ms'], ceilings.get(name, budget)))
        ml2_listed = any(v['max_ms'] > budget and name in ceilings for name, v in s['scopes'].items())
        if s['ml2_max_ms'] > ml2_budget and not ml2_listed:
            failures.append(f"{r['name']}: ML2 {s['ml2_max_ms']:.2f} ms over {ml2_budget} ms with no listed exception in the scenario")
    else:
        row += '   n/a (no loop-stats in this build)'
    if r['note'] is not None: row += f"  [{r['note']}]"
    lines.append(row)
    for name, value, ceiling in scope_over:
        failures.append(f"{r['name']}: {name} {value:.2f} ms over its ceiling {ceiling} ms")
    if d and pct(d, .99) > 10:
        if not scope_over and r['load'] > exceptions.get('noise_load', 3.0):
            noise.append(f"{r['name']}: ping p99 {pct(d,.99):.1f} ms with no scope over its ceiling, host load {r['load']:.1f}")
        elif not scope_over and not r['stats']:
            failures.append(f"{r['name']}: ping p99 {pct(d,.99):.1f} ms over 10 ms")
        elif not scope_over:
            failures.append(f"{r['name']}: ping p99 {pct(d,.99):.1f} ms over 10 ms, no Scottland scope over its ceiling (not Scottland's callbacks)")
    if isinstance(r['note'], str) and r['note'].startswith('ERROR'):
        failures.append(f"{r['name']}: {r['note']}")

lines.append('')
lines.append('Numbers are measured observations. Ceilings come from tests/mainloop-exceptions.json; '
             '* marks an open exception (over 2 ms, listed with an owner), ! a scope over its ceiling.')
for n in noise: lines.append('noise: ' + n)
for f in failures: lines.append(('FAIL: ' if gate else 'over: ') + f)
report = '\n'.join(lines)
print('\n' + report, flush=True)
(out / 'report.txt').write_text(report + '\n')
(out / 'results.json').write_text(json.dumps([{k: v for k, v in r.items()} for r in results]))
pings.close(); inputs.close(); control.close()
sys.exit(1 if gate and failures else 0)
