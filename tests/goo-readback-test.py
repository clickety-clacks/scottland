#!/usr/bin/env python3
"""Main-loop Phase 3: no GPU wait for the goo's energy or the pointer hit test. Real headless
sessions on a test host. Test switches (goo-state, SCOTTLAND_TEST_MODEL) hold readings in flight
or force a failure; the checks are on what the goo then applies and does:
- pointer motion over halos runs no GPU read (no goo_sample_at scope); the goo still falls asleep
  on asynchronous readings;
- readings held in flight across a change, a resize A->B->A or a reload never apply; a full ring
  is collected oldest first, at most two slots per dispatch across outputs;
- a failed read (the incoming pack state Astra's probe used, over a known prior buffer value), a
  failed wait, map or unmap applies nothing and enters the timed fallback: the goo keeps
  simulating until 6 s after the last change (observed in the compositor's CPU time, not only in
  the plugin's report), and a change wakes it;
- legal incoming pack state is normalized: readings still apply."""
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
    def wait(pred, limit=20, i=0):
        end = time.monotonic() + limit
        while time.monotonic() < end:
            g = goo(i)
            if pred(g): return g
            time.sleep(.05)
        return goo(i)
    def fault(name): ipc('scottland/goo-state', {'readback_fault': name})
    def compositor_cpu():
        fields = Path(f"/proc/{(work / 'hl' / 'compositor.pid').read_text().strip()}/stat").read_text().rsplit(')', 1)[1].split()
        return (int(fields[11]) + int(fields[12])) / os.sysconf('SC_CLK_TCK')
    def cpu_rate(seconds):
        a = compositor_cpu(); time.sleep(seconds); return (compositor_cpu() - a) / seconds
    views = lambda: ipc('scottland/layout-state')['views']
    def nudge(k=0, dx=40):
        v = views()[k]; f = v['frame']
        ipc('window-rules/configure-view', {'id': v['id'], 'geometry': {'x': f['x'] + dx, 'y': f['y'] + 10, 'width': 260, 'height': 170}})

    for i in range(windows):
        apps.append(subprocess.Popen([str(repo / 'tests/headless.sh'), 'run', 'foot', '-T', f'rb-{i}', 'sh', '-c', 'exec sleep 600'],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
        time.sleep(.3)
    end = time.monotonic() + 15
    while len(views()) < windows and time.monotonic() < end: time.sleep(.2)
    for i, v in enumerate(views()):
        ipc('window-rules/configure-view', {'id': v['id'], 'geometry': {'x': 80 + (i % 6) * 180, 'y': 60 + (i // 6) * 150,
                                                                        'width': 260, 'height': 170}})
    g = wait(lambda g: g['sleeping'], 30)
    check(f"the goo reads its energy without waiting ({g.get('readback')})", g.get('readback') == 'async', g.get('readback'))

    # 1. Wake it, then let it settle on applied asynchronous readings, with pointer motion over
    # the halos meanwhile: no GPU read in the hit test.
    ipc('scottland/loop-stats', {'reset': True})
    f = views()[0]['frame']
    nudge(0)
    t0 = time.monotonic()
    wait(lambda g: not g['sleeping'], 3)
    before = goo()
    for i in range(400):
        ipc('stipc/move_cursor', {'x': round(f['x'] - 6 + 300 * math.sin(i / 40)), 'y': round(f['y'] + 80 + 60 * math.cos(i / 25))})
    g = wait(lambda g: g['sleeping'], 20)
    slept_after = time.monotonic() - t0
    st = ipc('scottland/loop-stats')
    check('the goo falls asleep again on its energy, read asynchronously',
          g['sleeping'] and g['readings_applied'] > before['readings_applied'] and g['readback'] == 'async',
          (g['sleeping'], before['readings_applied'], g['readings_applied']))
    check(f'... after {slept_after:.1f} s (3 s response plus a settled reading)', 3 <= slept_after < 12, slept_after)
    check('pointer motion over halos ran no GPU read (no goo_sample_at)', 'goo_sample_at' not in st['scopes'],
          st['scopes'].get('goo_sample_at'))
    check('readings never waited: no goo_energy_readback scope', 'goo_energy_readback' not in st['scopes'])
    issue, collect = st['scopes'].get('goo_energy_issue', {}), st['scopes'].get('goo_energy_collect', {})
    measured(f"issue max {issue.get('max_ms', 0):.3f} ms ({issue.get('calls', 0)} calls), collect max "
             f"{collect.get('max_ms', 0):.3f} ms ({collect.get('calls', 0)} calls); target 0.5 ms each (open)")
    time.sleep(1.2)
    check('no reading stays in flight once asleep (the timer collected or retired it)', not goo()['readback_pending'])

    # 2. Held in flight across a change: none of them applies.
    fault('hold'); nudge(1)
    g = wait(lambda g: g['readings_in_flight'] >= 2, 6)
    held, stale0, applied0, last0 = g['readings_in_flight'], g['readings_stale'], g['readings_applied'], g['last_applied_step']
    check(f'test switch: {held} readings held in flight', held >= 2, g)
    nudge(1, -40)  # the change they were issued before
    time.sleep(.1)
    changed_at = goo()['steps']
    fault('')
    g = wait(lambda g: g['readings_stale'] - stale0 >= held, 5)
    check(f"released after a change: all {held} discarded as stale; any reading applied since was issued after the change",
          g['readings_stale'] - stale0 >= held and (g['last_applied_step'] == last0 or g['last_applied_step'] > changed_at),
          (held, stale0, g['readings_stale'], last0, changed_at, g['last_applied_step']))
    g = wait(lambda g: g['sleeping'], 20)
    check('... and the goo still settles on later readings', g['sleeping'] and g['readings_applied'] > applied0, g)

    # 3. A full ring (four slots), then released: collected oldest first (an out-of-order
    # collection would make the older ones stale), at most two slots per dispatch.
    fault('hold'); nudge(2)
    g = wait(lambda g: g['readings_in_flight'] == 4 and g['readings_skipped'] > 0, 8)
    stale0, applied0 = g['readings_stale'], g['readings_applied']
    check('test switch: the ring is full and further readings are skipped, never waited for',
          g['readings_in_flight'] == 4, g)
    ipc('scottland/goo-state', {'reset_collect': True})
    fault('')
    g = wait(lambda g: g['readings_in_flight'] == 0 or g['readings_applied'] - applied0 >= 4, 3)
    state = ipc('scottland/goo-state')
    check(f"released ring: {g['readings_applied'] - applied0} applied in issue order, {g['readings_stale'] - stale0} stale",
          g['readings_applied'] - applied0 >= 4 and g['readings_stale'] == stale0, (applied0, stale0, g))
    check(f"at most two slots examined per dispatch ({state['collect_max_per_dispatch']})",
          0 < state['collect_max_per_dispatch'] <= 2, state['collect_max_per_dispatch'])
    wait(lambda g: g['sleeping'], 20)

    # 4. Resize A -> B -> A with readings in flight: a new generation each time; none applies
    # even though the size is the same again.
    output = ipc('window-rules/list-outputs')[0]
    size_a = f"{int(output['geometry']['width'])}x{int(output['geometry']['height'])}"
    fault('hold'); nudge(3)
    g = wait(lambda g: g['readings_in_flight'] >= 1, 6)
    gen0, stale0, applied0 = g['readback_generation'], g['readings_stale'], g['readings_applied']
    ipc('wayfire/set-config-options', {f"output:{output['name']}/mode": '1600x900'}); time.sleep(.6)
    ipc('wayfire/set-config-options', {f"output:{output['name']}/mode": size_a}); time.sleep(.6)
    nudge(3, -40)
    g = wait(lambda g: g['readback_generation'] >= gen0 + 2, 4)
    check(f"resize {size_a} -> 1600x900 -> {size_a}: two new generations ({gen0} -> {g['readback_generation']}), "
          f"no reading in flight across them", g['readback_generation'] >= gen0 + 2 and g['readings_in_flight'] <= 1, g)
    fault('')
    g = wait(lambda g: g['sleeping'], 25)
    check('... no reading from before the resizes applied, and the goo settles on new ones',
          g['sleeping'] and g['readings_applied'] > applied0, (applied0, g))

    # 5. Forced failures, each over a known prior buffer value where the read is concerned:
    # nothing applies, the timed fallback keeps simulating until 6 s after the last change.
    idle_rate = cpu_rate(1.5)
    for name in ('prior-value', 'wait-failed', 'map-failed', 'unmap-failed'):
        fault(name)
        applied0 = goo()['readings_applied']
        nudge(4, 40 if name != 'wait-failed' else -40)
        t0 = time.monotonic()
        wait(lambda g: not g['sleeping'], 3)
        g = wait(lambda g: g['readback'].startswith('timed'), 4)
        check(f"{name}: the failure enters the timed fallback ({g['readback']}) and applies no value",
              g['readback'] == 'timed (readback failed)' and g['readings_applied'] == applied0, (applied0, g))
        # The goo keeps simulating until about 6 s after the last change, then sleeps.
        awake_rate = cpu_rate(2.0)
        awake_at = time.monotonic() - t0
        g = wait(lambda g: g['sleeping'], 12)
        took = time.monotonic() - t0
        asleep_rate = cpu_rate(1.5)
        check(f"{name}: still awake {awake_at:.1f} s after the change (compositor CPU {awake_rate:.3f} s/s); asleep after "
              f"{took:.1f} s (6 s fallback; CPU {asleep_rate:.3f} s/s, idle {idle_rate:.3f})",
              g['sleeping'] and 5.5 <= took <= 9 and awake_rate > 2 * max(asleep_rate, .005), (awake_rate, asleep_rate, took))
        nudge(4, -40 if name != 'wait-failed' else 40)
        g = wait(lambda g: not g['sleeping'], 3)
        check(f'{name}: a change wakes it', not g['sleeping'])
        fault('')  # back to asynchronous readings
        wait(lambda g: g['sleeping'], 25)

    # 6. Legal incoming pack state (Astra's probe: PACK_SKIP_PIXELS=1 and more): normalized.
    fault('incoming-pack-state')
    applied0 = goo()['readings_applied']
    nudge(5)
    wait(lambda g: not g['sleeping'], 3)
    g = wait(lambda g: g['sleeping'], 25)
    check(f"incoming pack state is normalized: readings apply ({g['readings_applied'] - applied0}) and it settles asynchronously",
          g['sleeping'] and g['readings_applied'] > applied0 and g['readback'] == 'async', g)
    fault('')

    # 7. A second output: one allowance per dispatch across both, both settle, and removing an
    # output with readings in flight leaves the other working.
    second = ipc('wayfire/create-headless-output', {'width': 1024, 'height': 768})['output']
    time.sleep(1)
    moved = next(v for v in ipc('window-rules/list-views') if v.get('title') == 'rb-6')
    def place2(x):  # output-local geometry on the second output
        ipc('window-rules/configure-view', {'id': moved['id'], 'output_id': second['id'],
                                            'geometry': {'x': x, 'y': 200, 'width': 260, 'height': 170}})
    place2(200)
    time.sleep(.8)
    on_second = next(v for v in ipc('window-rules/list-views') if v['id'] == moved['id']).get('output-id') == second['id']
    check('the second output has its own goo, with a window on it', len(screens()) == 2 and on_second, (len(screens()), on_second))
    if len(screens()) == 2 and on_second:
        wait(lambda g: g['sleeping'], 25, 1)
        ipc('scottland/goo-state', {'reset_collect': True})
        a0, b0 = goo(0)['readings_applied'], goo(1)['readings_applied']
        nudge(0); place2(260)
        wait(lambda g: not g['sleeping'], 3, 0); wait(lambda g: not g['sleeping'], 3, 1)
        g0, g1 = wait(lambda g: g['sleeping'], 25, 0), wait(lambda g: g['sleeping'], 25, 1)
        state = ipc('scottland/goo-state')
        check(f"two outputs awake together: at most two slots examined per dispatch across both ({state['collect_max_per_dispatch']})",
              0 < state['collect_max_per_dispatch'] <= 2, state['collect_max_per_dispatch'])
        check('both outputs settle on applied readings', g0['sleeping'] and g1['sleeping'] and
              g0['readings_applied'] > a0 and g1['readings_applied'] > b0, (a0, b0, g0['readings_applied'], g1['readings_applied']))
        fault('hold'); place2(200)
        wait(lambda g: g['readings_in_flight'] >= 1, 6, 1)
        ipc('wayfire/destroy-headless-output', {'output': second['name'], 'id': second['id']})
        time.sleep(1)
        fault('')
        nudge(0, -40)
        wait(lambda g: not g['sleeping'], 3, 0)
        g = wait(lambda g: g['sleeping'], 25, 0)
        check('removing an output with readings in flight: the other output still settles', len(screens()) == 1 and g['sleeping'],
              (len(screens()), g))

    # 8. A reload while readings are held in flight: the new copy starts fresh and settles.
    fault('hold'); nudge(1)
    g = wait(lambda g: g['readings_in_flight'] >= 1, 6)
    check('readings in flight before the reload', g['readings_in_flight'] >= 1, g)
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
