#!/usr/bin/env python3
"""Main-loop Phase 3: no GPU wait for the goo's energy or the pointer hit test. Real headless
sessions on a test host. Checks:
- pointer motion over halos runs no GPU read (no goo_sample_at scope); the grab edge is the
  resting outline (Mike, 2026-10-03);
- the goo still falls asleep on its energy, now read asynchronously (readings applied, none
  waited for), and the issue and collection costs are measured against the 0.5 ms target;
- a reading issued before a change never applies; collection continues without frames;
- the timed fallback (GO10, Mike 2026-10-03): with the test switch the goo sleeps 6 s after the
  last change and wakes on a change;
- a reload while readings are in flight."""
import json, math, os, socket, struct, subprocess, sys, time
from pathlib import Path

repo = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo / 'tests'))
from session_reload import reload_session
work = repo / 'build/goo-readback'; work.mkdir(parents=True, exist_ok=True)
runtime = Path(os.environ.get('XDG_RUNTIME_DIR') or f'/run/user/{os.getuid()}') / 'scottland'
windows = int(sys.argv[1]) if len(sys.argv) > 1 else 12
fails = 0
def check(what, ok, detail=''):
    global fails
    print(f"{'PASS' if ok else 'FAIL'}  {what}" + ('' if ok else f'  {detail}'), flush=True)
    fails += not ok

env = dict(os.environ, SCOTTLAND_HEADLESS_DIR=str(work / 'hl'), TMPDIR=str(work))
os.environ['SCOTTLAND_HEADLESS_DIR'] = env['SCOTTLAND_HEADLESS_DIR']
subprocess.run([str(repo / 'tests/headless.sh'), 'stop'], env=env, capture_output=True)
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
def goo(): return ipc('scottland/goo-state')['screens'][0]
def wait(pred, limit=20):
    end = time.monotonic() + limit
    while time.monotonic() < end:
        g = goo()
        if pred(g): return g
        time.sleep(.05)
    return goo()
apps = []
try:
    for i in range(windows):
        apps.append(subprocess.Popen([str(repo / 'tests/headless.sh'), 'run', 'foot', '-T', f'rb-{i}', 'sh', '-c', 'exec sleep 600'],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
        time.sleep(.3)
    time.sleep(1)
    views = ipc('scottland/layout-state')['views']
    for i, v in enumerate(views):
        ipc('window-rules/configure-view', {'id': v['id'], 'geometry': {'x': 80 + (i % 6) * 180, 'y': 60 + (i // 6) * 150,
                                                                        'width': 260, 'height': 170}})
    g = wait(lambda g: g['sleeping'], 30)
    check(f"the goo reads its energy without waiting ({g.get('readback')})", g.get('readback') == 'async', g.get('readback'))

    # Wake it, then let it settle: asleep through applied asynchronous readings.
    ipc('scottland/loop-stats', {'reset': True})
    f = ipc('scottland/layout-state')['views'][0]['frame']
    ipc('window-rules/configure-view', {'id': ipc('scottland/layout-state')['views'][0]['id'],
        'geometry': {'x': f['x'] + 40, 'y': f['y'] + 20, 'width': 260, 'height': 170}})
    t0 = time.monotonic()
    wait(lambda g: not g['sleeping'], 3)
    before = goo()
    # Pointer motion over the halos while it settles: no GPU read in the hit test.
    for i in range(400):
        ipc('stipc/move_cursor', {'x': round(f['x'] - 6 + 300 * math.sin(i / 40)), 'y': round(f['y'] + 80 + 60 * math.cos(i / 25))})
    g = wait(lambda g: g['sleeping'], 20)
    slept_after = time.monotonic() - t0
    st = ipc('scottland/loop-stats')
    check('the goo falls asleep again on its energy', g['sleeping'] and g['readings_applied'] > before['readings_applied'],
          (g['sleeping'], before['readings_applied'], g['readings_applied']))
    check(f'... after {slept_after:.1f} s (3 s response plus a settled reading; measured)', 3 <= slept_after < 12, slept_after)
    check('pointer motion over halos ran no GPU read (no goo_sample_at)', 'goo_sample_at' not in st['scopes'],
          st['scopes'].get('goo_sample_at'))
    issue, collect = st['scopes'].get('goo_energy_issue', {}), st['scopes'].get('goo_energy_collect', {})
    check(f"measured: issue max {issue.get('max_ms', 0):.3f} ms ({issue.get('calls', 0)} calls), collect max "
          f"{collect.get('max_ms', 0):.3f} ms ({collect.get('calls', 0)} calls); target 0.5 ms each",
          True)
    check('readings never waited: no goo_energy_readback scope', 'goo_energy_readback' not in st['scopes'])

    # Collection does not depend on further frames: nothing stays in flight while asleep.
    time.sleep(1.2)
    check('no reading stays in flight once asleep (the timer collected or retired it)', not goo()['readback_pending'])

    # A change between issue and collect: the older reading does not apply.
    ipc('scottland/loop-stats', {'reset': True})
    stale0 = goo()['readings_stale']
    v = ipc('scottland/layout-state')['views'][1]
    for k in range(6):
        ipc('window-rules/configure-view', {'id': v['id'], 'geometry': {'x': v['frame']['x'] + 10 * k, 'y': v['frame']['y'],
                                                                         'width': 260, 'height': 170}})
        time.sleep(.45)
    g = wait(lambda g: g['sleeping'], 20)
    check(f"readings issued before a change were discarded ({g['readings_stale'] - stale0} stale), and it still settled",
          g['sleeping'], g)

    # The timed fallback: 6 s after the last change, and a change wakes it.
    ipc('scottland/goo-state', {'timed_sleep': True})
    check('test switch: timed fallback', goo()['readback'].startswith('timed'), goo()['readback'])
    ipc('window-rules/configure-view', {'id': v['id'], 'geometry': {'x': v['frame']['x'] + 30, 'y': v['frame']['y'] + 10,
                                                                     'width': 260, 'height': 170}})
    t0 = time.monotonic()
    wait(lambda g: not g['sleeping'], 3)
    g = wait(lambda g: g['sleeping'], 15)
    took = time.monotonic() - t0
    check(f'timed fallback sleeps about 6 s after the last change ({took:.1f} s)', g['sleeping'] and 5.5 <= took <= 8, took)
    ipc('window-rules/configure-view', {'id': v['id'], 'geometry': {'x': v['frame']['x'], 'y': v['frame']['y'],
                                                                     'width': 260, 'height': 170}})
    g = wait(lambda g: not g['sleeping'], 3)
    check('and a change wakes it', not g['sleeping'])
    ipc('scottland/goo-state', {'timed_sleep': False})

    # Reload while readings are in flight.
    ipc('window-rules/configure-view', {'id': v['id'], 'geometry': {'x': v['frame']['x'] + 50, 'y': v['frame']['y'],
                                                                     'width': 260, 'height': 170}})
    wait(lambda g: g['readback_pending'], 3)
    out = reload_session()
    g = wait(lambda g: g['sleeping'], 30)
    check('a reload with readings in flight succeeds and the new copy settles asynchronously',
          'reloaded' in out and g['sleeping'] and g['readback'] == 'async', (out, g.get('readback')))
finally:
    for p in apps:
        try: os.killpg(p.pid, 15)
        except ProcessLookupError: pass
    subprocess.run([str(repo / 'tests/headless.sh'), 'stop'], env=env, capture_output=True)
print('all readback checks passed' if not fails else f'{fails} check(s) failed')
sys.exit(1 if fails else 0)
