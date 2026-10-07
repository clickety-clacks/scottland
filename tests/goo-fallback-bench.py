#!/usr/bin/env python3
"""Benchmark: what the goo's timed fallback (GO10, main-loop Phase 3) costs. Not a gate; it
reports and always exits 0 unless the session itself fails.

For each forced readback failure, a change wakes the goo and the fallback keeps it simulating
until it sleeps on time alone. Reported: the compositor's CPU rate (user plus system time from
/proc) while awake on the fallback and once asleep, an idle baseline, and how long after the
change the goo slept, with the machine's CPU model, the GL renderer, the load average and the
sample count. Run on a test host:
  tests/goo-fallback-bench.py [--windows 12] [--rounds 3]"""
import argparse, json, os, socket, struct, subprocess, sys, time
from pathlib import Path

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument('--windows', type=int, default=12)
ap.add_argument('--rounds', type=int, default=3, help='samples per failure')
args = ap.parse_args()

repo = Path(__file__).resolve().parents[1]
work = repo / 'build/goo-fallback-bench'; work.mkdir(parents=True, exist_ok=True)
runtime = Path(os.environ.get('XDG_RUNTIME_DIR') or f'/run/user/{os.getuid()}') / 'scottland'
env = dict(os.environ, SCOTTLAND_HEADLESS_DIR=str(work / 'hl'), TMPDIR=str(work))
subprocess.run([str(repo / 'tests/headless.sh'), 'stop'], env=env, capture_output=True)
apps = []
def cpu_model():
    for line in Path('/proc/cpuinfo').read_text().splitlines():
        if line.startswith('model name'): return line.split(':', 1)[1].strip()
    return 'unknown'
def loadavg(): return ' '.join(Path('/proc/loadavg').read_text().split()[:3])
try:
    subprocess.run([str(repo / 'tests/headless.sh'), 'start'], env=env, check=True, capture_output=True)
    display = (work / 'hl' / 'display').read_text().strip()
    entries = (runtime / f'{display}.env').read_bytes().split(b'\0')
    path = next(e.split(b'=', 1)[1] for e in entries if e.startswith(b'WAYFIRE_SOCKET=')).decode()
    def ipc(method, data=None):
        s = socket.socket(socket.AF_UNIX); s.connect(path)
        b = json.dumps({'method': method, 'data': data or {}}).encode()
        s.sendall(struct.pack('<I', len(b)) + b)
        def read(n):
            out = b''
            while len(out) < n:
                more = s.recv(n - len(out))
                if not more: raise EOFError
                out += more
            return out
        r = json.loads(read(struct.unpack('<I', read(4))[0])); s.close(); return r
    goo = lambda: ipc('scottland/goo-state')['screens'][0]
    views = lambda: ipc('scottland/layout-state')['views']
    def until(pred, limit, get=goo):
        end = time.monotonic() + limit
        while time.monotonic() < end:
            v = get()
            if pred(v): return v
            time.sleep(.05)
        sys.exit(f'the session did not reach the expected state in {limit} s: {get()}')
    def compositor_cpu():
        fields = Path(f"/proc/{(work / 'hl' / 'compositor.pid').read_text().strip()}/stat").read_text().rsplit(')', 1)[1].split()
        return (int(fields[11]) + int(fields[12])) / os.sysconf('SC_CLK_TCK')
    def cpu_rate(seconds):
        a = compositor_cpu(); time.sleep(seconds); return (compositor_cpu() - a) / seconds
    def nudge(dx):
        v = views()[0]; f = v['frame']
        ipc('window-rules/configure-view', {'id': v['id'], 'geometry': {'x': f['x'] + dx, 'y': f['y'], 'width': 260, 'height': 170}})

    for i in range(args.windows):
        apps.append(subprocess.Popen([str(repo / 'tests/headless.sh'), 'run', 'foot', '-T', f'fb-{i}', 'sh', '-c', 'exec sleep 600'],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
        until(lambda v: len(v) > i, 15, views)
    for i, v in enumerate(views()):
        ipc('window-rules/configure-view', {'id': v['id'], 'geometry': {'x': 80 + (i % 6) * 180, 'y': 60 + (i // 6) * 150,
                                                                        'width': 260, 'height': 170}})
    g = until(lambda g: g['sleeping'], 30)
    print(f"machine: {cpu_model()}, {os.cpu_count()} CPUs; GL: {g.get('gl')}; {args.windows} windows; load {loadavg()}", flush=True)
    print(f'idle: compositor CPU {cpu_rate(2.0):.3f} s/s', flush=True)
    dx = 40
    for name in ('prior-value', 'wait-failed', 'map-failed', 'unmap-failed'):
        for r in range(args.rounds):
            ipc('scottland/goo-state', {'readback_fault': name})
            nudge(dx); dx = -dx
            t0 = time.monotonic()
            until(lambda g: not g['sleeping'] and g['readback'].startswith('timed'), 10)
            awake = cpu_rate(2.0)
            until(lambda g: g['sleeping'], 30)
            took = time.monotonic() - t0
            asleep = cpu_rate(1.5)
            print(f'{name} sample {r + 1}/{args.rounds}: awake {awake:.3f} s/s, asleep {asleep:.3f} s/s, '
                  f'slept {took:.2f} s after the change; load {loadavg()}', flush=True)
            ipc('scottland/goo-state', {'readback_fault': ''})
            nudge(dx); dx = -dx
            until(lambda g: g['sleeping'] and g['readback'] == 'async', 30)
finally:
    for p in apps:
        try: os.killpg(p.pid, 15)
        except ProcessLookupError: pass
    subprocess.run([str(repo / 'tests/headless.sh'), 'stop'], env=env, capture_output=True)
