#!/usr/bin/env python3
"""Main-loop Phase 3: no GPU wait for the goo's energy or the pointer hit test. Real headless
sessions on a test host. Test switches (goo-state, SCOTTLAND_TEST_MODEL) hold readings in flight
or force a failure; the checks are on what the goo then applies and does:
- pointer motion over halos runs no GPU read (no goo_sample_at scope); the goo still falls asleep
  on asynchronous readings;
- readings held in flight across a change, a resize A->B->A or a reload never apply; a full ring
  is collected oldest first (the two-slot bound between main-loop waits is goo-allowance-unit's,
  measured there against the event loop's own waits); with the oldest reading held while newer
  ones complete, the newer ones apply and the late oldest never does;
- a failed read (the incoming pack state Astra's probe used, over a known prior buffer value), a
  failed wait, map or unmap applies nothing and enters the timed fallback: the goo keeps
  simulating, then sleeps, and a change wakes it;
- legal incoming pack state is normalized: readings still apply.
The exact sleep boundaries (6 s timed, 3 s and 30 steps asynchronous) and the reading acceptance
rule are unit-tested on supplied times (goo-settle-unit.sh); the fallback's CPU cost is
goo-fallback-bench.py's. Every wait here is on state, with a generous hang deadline; a wait that
runs out is a failure and ends the run."""
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
def measured(what): print(f'measured: {what}', flush=True)
class Abort(Exception): pass

env = dict(os.environ, SCOTTLAND_HEADLESS_DIR=str(work / 'hl'), TMPDIR=str(work))
os.environ['SCOTTLAND_HEADLESS_DIR'] = env['SCOTTLAND_HEADLESS_DIR']
subprocess.run([str(repo / 'tests/headless.sh'), 'stop'], env=env, capture_output=True)
apps = []
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
    def screens(): return ipc('scottland/goo-state')['screens']
    def goo(i=0): return screens()[i]
    def need(what, pred, limit=20, i=0, get=None):
        """Wait for a state (a hang deadline, not a budget); on timeout, FAIL with the last one."""
        end = time.monotonic() + limit
        while True:
            try: v = get() if get else goo(i)
            except (IndexError, StopIteration): v = None
            if v is not None and pred(v): return v
            if time.monotonic() >= end:
                check(what, False, v)
                raise Abort(what)
            time.sleep(.05)
    def fault(name): ipc('scottland/goo-state', {'readback_fault': name})
    views = lambda: ipc('scottland/layout-state')['views']
    def nudge(k=0, dx=40):
        v = views()[k]; f = v['frame']
        ipc('window-rules/configure-view', {'id': v['id'], 'geometry': {'x': f['x'] + dx, 'y': f['y'] + 10, 'width': 260, 'height': 170}})
    def output_size(name):
        o = next(o for o in ipc('window-rules/list-outputs') if o['name'] == name)
        return f"{int(o['geometry']['width'])}x{int(o['geometry']['height'])}"

    for i in range(windows):
        apps.append(subprocess.Popen([str(repo / 'tests/headless.sh'), 'run', 'foot', '-T', f'rb-{i}', 'sh', '-c', 'exec sleep 600'],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
        need(f'window rb-{i} mapped', lambda v: len(v) > i, 15, get=views)
    for i, v in enumerate(views()):
        ipc('window-rules/configure-view', {'id': v['id'], 'geometry': {'x': 80 + (i % 6) * 180, 'y': 60 + (i // 6) * 150,
                                                                        'width': 260, 'height': 170}})
    g = need('the goo settles with the windows placed', lambda g: g['sleeping'], 30)
    check(f"the goo reads its energy without waiting ({g.get('readback')})", g.get('readback') == 'async', g.get('readback'))

    # 1. Wake it, then let it settle on applied asynchronous readings, with pointer motion over
    # the halos meanwhile: no GPU read in the hit test.
    ipc('scottland/loop-stats', {'reset': True})
    f = views()[0]['frame']
    nudge(0)
    t0 = time.monotonic()
    before = need('a change wakes the goo', lambda g: not g['sleeping'], 5)
    for i in range(400):
        ipc('stipc/move_cursor', {'x': round(f['x'] - 6 + 300 * math.sin(i / 40)), 'y': round(f['y'] + 80 + 60 * math.cos(i / 25))})
    g = need('the goo falls asleep again', lambda g: g['sleeping'], 30)
    measured(f'asleep {time.monotonic() - t0:.1f} s after the change (3 s response plus a settled reading)')
    st = ipc('scottland/loop-stats')
    check('the goo falls asleep again on its energy, read asynchronously',
          g['readings_applied'] > before['readings_applied'] and g['readback'] == 'async',
          (before['readings_applied'], g['readings_applied'], g['readback']))
    check('pointer motion over halos ran no GPU read (no goo_sample_at)', 'goo_sample_at' not in st['scopes'],
          st['scopes'].get('goo_sample_at'))
    check('readings never waited: no goo_energy_readback scope', 'goo_energy_readback' not in st['scopes'])
    issue, collect = st['scopes'].get('goo_energy_issue', {}), st['scopes'].get('goo_energy_collect', {})
    measured(f"issue max {issue.get('max_ms', 0):.3f} ms ({issue.get('calls', 0)} calls), collect max "
             f"{collect.get('max_ms', 0):.3f} ms ({collect.get('calls', 0)} calls); target 0.5 ms each (open)")
    need('no reading stays in flight once asleep (the timer collected or retired it)', lambda g: not g['readback_pending'], 5)

    # 2. Held in flight across a change: none of them applies.
    fault('hold'); nudge(1)
    g = need('test switch: at least two readings held in flight', lambda g: g['readings_in_flight'] >= 2, 10)
    held, stale0, applied0, last0 = g['readings_in_flight'], g['readings_stale'], g['readings_applied'], g['last_applied_step']
    changed_at = goo()['steps']  # every held reading was issued at or before this step
    nudge(1, -40)  # the change they were issued before
    fault('')
    g = need(f'released after a change: all {held} held readings discarded as stale',
             lambda g: g['readings_stale'] - stale0 >= held, 10)
    check(f"released after a change: all {held} discarded as stale; any reading applied since was issued after the change",
          g['last_applied_step'] == last0 or g['last_applied_step'] > changed_at,
          (held, stale0, g['readings_stale'], last0, changed_at, g['last_applied_step']))
    g = need('... and the goo still settles', lambda g: g['sleeping'], 30)
    check('... on later readings', g['readings_applied'] > applied0, (applied0, g['readings_applied']))

    # 3. A full ring (four slots), then released: collected oldest first (an out-of-order
    # collection would make the older ones stale).
    fault('hold'); nudge(2)
    g = need('test switch: the ring is full and further readings are skipped, never waited for',
             lambda g: g['readings_in_flight'] == 4 and g['readings_skipped'] > 0, 10)
    stale0, applied0 = g['readings_stale'], g['readings_applied']
    fault('')
    g = need('the released ring is collected', lambda g: g['readings_in_flight'] == 0 or g['readings_applied'] - applied0 >= 4, 5)
    check(f"released ring: {g['readings_applied'] - applied0} applied in issue order, {g['readings_stale'] - stale0} stale",
          g['readings_applied'] - applied0 >= 4 and g['readings_stale'] == stale0, (applied0, stale0, g))
    need('... and the goo settles', lambda g: g['sleeping'], 30)

    # 3b. Completion out of order: the oldest reading is held while newer ones complete. The newer
    # ones apply; released, the oldest is refused and the applied step never goes back.
    fault('hold-oldest'); nudge(2, -40)
    g = need('test switch: a newer reading applies while the oldest is held',
             lambda g: g['oldest_in_flight_step'] > 0 and g['last_applied_step'] > g['oldest_in_flight_step'], 10)
    held_step, stale0, last0 = g['oldest_in_flight_step'], g['readings_stale'], g['last_applied_step']
    check(f'out of order: the reading of step {last0} applied while step {held_step} was still in flight', True)
    fault('')
    lowest = last0
    def released(g):
        global lowest
        lowest = min(lowest, g['last_applied_step'])
        return g['oldest_in_flight_step'] != held_step
    g = need(f'the held reading of step {held_step} is collected once released', released, 5)
    check(f"... and refused as stale ({g['readings_stale'] - stale0} stale); the applied step never went back "
          f"(lowest {lowest} from {last0})", g['readings_stale'] > stale0 and lowest >= last0 and g['last_applied_step'] >= last0,
          (stale0, g['readings_stale'], last0, lowest, g['last_applied_step']))
    need('... and the goo settles', lambda g: g['sleeping'], 30)

    # 4. Resize A -> B -> A with readings in flight: a new generation each time; none applies
    # even though the size is the same again.
    output = ipc('window-rules/list-outputs')[0]
    size_a = output_size(output['name'])
    fault('hold'); nudge(3)
    g = need('test switch: readings held in flight before the resizes', lambda g: g['readings_in_flight'] >= 1, 10)
    gen0, stale0, applied0 = g['readback_generation'], g['readings_stale'], g['readings_applied']
    ipc('wayfire/set-config-options', {f"output:{output['name']}/mode": '1600x900'})
    need('the output is resized to 1600x900', lambda s: s == '1600x900', 10, get=lambda: output_size(output['name']))
    steps_mid = goo()['steps']  # every reading issued before the size went back is at or before this step
    ipc('wayfire/set-config-options', {f"output:{output['name']}/mode": size_a})
    need(f'the output is back at {size_a}', lambda s: s == size_a, 10, get=lambda: output_size(output['name']))
    nudge(3, -40)
    g = need('two new readback generations', lambda g: g['readback_generation'] >= gen0 + 2, 10)
    check(f"resize {size_a} -> 1600x900 -> {size_a}: two new generations ({gen0} -> {g['readback_generation']}), "
          f"no reading in flight across them", g['readings_in_flight'] <= 1, g)
    fault('')
    g = need('... and the goo settles after the resizes', lambda g: g['sleeping'], 30)
    check(f"... on readings issued after the size went back (applied step {g['last_applied_step']} > {steps_mid})",
          g['readings_applied'] > applied0 and g['last_applied_step'] > steps_mid, (applied0, steps_mid, g))

    # 5. Forced failures, each over a known prior buffer value where the read is concerned:
    # nothing applies, the timed fallback keeps simulating, then sleeps; a change wakes it.
    for name in ('prior-value', 'wait-failed', 'map-failed', 'unmap-failed'):
        fault(name)
        applied0 = goo()['readings_applied']
        nudge(4, 40 if name != 'wait-failed' else -40)
        t0 = time.monotonic()
        need(f'{name}: a change wakes the goo', lambda g: not g['sleeping'], 5)
        g = need(f'{name}: the failure enters the timed fallback', lambda g: g['readback'].startswith('timed'), 10)
        steps0 = g['steps']
        check(f"{name}: the failure enters the timed fallback ({g['readback']}) and applies no value",
              g['readback'] == 'timed (readback failed)' and g['readings_applied'] == applied0, (applied0, g))
        g = need(f'{name}: the goo sleeps on the timed fallback', lambda g: g['sleeping'], 30)
        measured(f'{name}: asleep {time.monotonic() - t0:.1f} s after the change (6 s timed fallback)')
        check(f"{name}: it kept simulating meanwhile ({g['steps'] - steps0} steps) and applied nothing",
              g['steps'] > steps0 and g['readings_applied'] == applied0, (steps0, applied0, g))
        nudge(4, -40 if name != 'wait-failed' else 40)
        need(f'{name}: a change wakes it', lambda g: not g['sleeping'], 5)
        check(f'{name}: a change wakes it', True)
        fault('')  # back to asynchronous readings
        need(f'{name}: released, the goo settles', lambda g: g['sleeping'], 30)

    # 6. Legal incoming pack state (Astra's probe: PACK_SKIP_PIXELS=1 and more): normalized.
    fault('incoming-pack-state')
    applied0 = goo()['readings_applied']
    nudge(5)
    need('a change wakes the goo', lambda g: not g['sleeping'], 5)
    g = need('the goo settles with incoming pack state', lambda g: g['sleeping'], 30)
    check(f"incoming pack state is normalized: readings apply ({g['readings_applied'] - applied0}) and it settles asynchronously",
          g['readings_applied'] > applied0 and g['readback'] == 'async', g)
    fault('')

    # 7. A second output: both goos share one collection allowance and both settle, and removing
    # an output with readings in flight leaves the other working.
    second = ipc('wayfire/create-headless-output', {'width': 1024, 'height': 768})['output']
    need('the second output exists', lambda o: any(x['id'] == second['id'] for x in o), 10,
         get=lambda: ipc('window-rules/list-outputs'))
    moved = next(v for v in ipc('window-rules/list-views') if v.get('title') == 'rb-6')
    def place2(x):  # output-local geometry on the second output
        ipc('window-rules/configure-view', {'id': moved['id'], 'output_id': second['id'],
                                            'geometry': {'x': x, 'y': 200, 'width': 260, 'height': 170}})
    def on_second(): return next(v for v in ipc('window-rules/list-views') if v['id'] == moved['id']).get('output-id') == second['id']
    place2(200)
    need('the window moves to the second output', lambda on: on, 10, get=on_second)
    need('the second output has its own goo', lambda s: len(s) == 2, 10, get=screens)
    check('the second output has its own goo, with a window on it', True)
    need('the second output\'s goo settles', lambda g: g['sleeping'], 30, 1)
    a0, b0 = goo(0)['readings_applied'], goo(1)['readings_applied']
    nudge(0); place2(260)
    need('a change wakes the first output\'s goo', lambda g: not g['sleeping'], 5, 0)
    need('a change wakes the second output\'s goo', lambda g: not g['sleeping'], 5, 1)
    g0 = need('the first output settles', lambda g: g['sleeping'], 30, 0)
    g1 = need('the second output settles', lambda g: g['sleeping'], 30, 1)
    check('both outputs settle on applied readings', g0['readings_applied'] > a0 and g1['readings_applied'] > b0,
          (a0, b0, g0['readings_applied'], g1['readings_applied']))
    fault('hold'); place2(200)
    need('readings held in flight on the second output', lambda g: g['readings_in_flight'] >= 1, 10, 1)
    ipc('wayfire/destroy-headless-output', {'output': second['name'], 'id': second['id']})
    need('the second output is gone', lambda s: len(s) == 1, 10, get=screens)
    fault('')
    nudge(0, -40)
    need('a change wakes the remaining goo', lambda g: not g['sleeping'], 5, 0)
    need('removing an output with readings in flight: the other output still settles', lambda g: g['sleeping'], 30, 0)
    check('removing an output with readings in flight: the other output still settles', True)

    # 8. A reload while readings are held in flight: the new copy starts fresh and settles.
    fault('hold'); nudge(1)
    need('readings in flight before the reload', lambda g: g['readings_in_flight'] >= 1, 10)
    out = reload_session()
    check('the reload succeeds with readings in flight', 'reloaded' in out, out)
    g = need('the new copy settles', lambda g: g['sleeping'], 30)
    check('... and the new copy settles asynchronously', g['readback'] == 'async', g.get('readback'))
except Abort:
    pass
finally:
    for p in apps:
        try: os.killpg(p.pid, 15)
        except ProcessLookupError: pass
    subprocess.run([str(repo / 'tests/headless.sh'), 'stop'], env=env, capture_output=True)
print('all readback checks passed' if not fails else f'{fails} check(s) failed')
sys.exit(1 if fails else 0)
