#!/usr/bin/env python3
"""Main-loop Phase 4: the goo's breathing shrink (GO19/GO20) runs on the shrink worker. Real
headless sessions on a test host; each case starts and stops its own.

Checks: the goo settles to the tight region with no shrink work on the main loop and short
snapshot and install callbacks, drawing the same pixels as the loose bands; with a job held
running (test switch) and a newer one observed pending, the old one is cancelled and never
installed; a woken goo drops to its bands; an output removed under a running job leaves the rest
working and a recreated output starts its own lane; a reload with a job running leaves one worker
thread and no extra descriptors; a worker that can't start leaves the loose strips."""
import json, os, socket, struct, subprocess, sys, time
from pathlib import Path

repo = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo / 'tests'))
from session_reload import reload_session
work = repo / 'build/shrink-worker'; work.mkdir(parents=True, exist_ok=True)
runtime = Path(os.environ.get('XDG_RUNTIME_DIR') or f'/run/user/{os.getuid()}') / 'scottland'
fails = 0
def check(what, ok, detail=''):
    global fails
    print(f"{'PASS' if ok else 'FAIL'}  {what}" + ('' if ok else f'  {detail}'), flush=True)
    fails += not ok

class Session:
    def __init__(self, name, faults=''):
        self.env = dict(os.environ, SCOTTLAND_HEADLESS_DIR=str(work / name), TMPDIR=str(work))
        if faults: self.env['SCOTTLAND_TEST_LOOP_FAULTS'] = faults
        os.environ['SCOTTLAND_HEADLESS_DIR'] = str(work / name)
        subprocess.run([str(repo / 'tests/headless.sh'), 'stop'], env=self.env, capture_output=True)
        self.apps = []
        try:
            subprocess.run([str(repo / 'tests/headless.sh'), 'start'], env=self.env, check=True, capture_output=True)
            self.display = (work / name / 'display').read_text().strip()
            entries = (runtime / f'{self.display}.env').read_bytes().split(b'\0')
            self.path = next(e.split(b'=', 1)[1] for e in entries if e.startswith(b'WAYFIRE_SOCKET=')).decode()
        except BaseException:
            self.stop()  # a failed or interrupted start leaves nothing running
            raise
    def ipc(self, method, data=None):
        s = socket.socket(socket.AF_UNIX); s.connect(self.path)
        b = json.dumps({'method': method, 'data': data or {}}).encode()
        s.sendall(struct.pack('<I', len(b)) + b)
        def read(n):
            out = b''
            while len(out) < n:
                more = s.recv(n - len(out))
                if not more: raise EOFError
                out += more
            return out
        reply = json.loads(read(struct.unpack('<I', read(4))[0])); s.close()
        return reply
    def compositor(self): return int((work / Path(self.env['SCOTTLAND_HEADLESS_DIR']).name / 'compositor.pid').read_text())
    def spawn(self, title):
        self.apps.append(subprocess.Popen([str(repo / 'tests/headless.sh'), 'run', 'foot', '-T', title, 'sh', '-c', 'exec sleep 600'],
            env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
        for _ in range(50):
            v = [v for v in self.ipc('scottland/layout-state')['views'] if v['title'] == title]
            if v: return v[0]
            time.sleep(.1)
        raise AssertionError(title)
    def goo(self): return self.ipc('scottland/goo-state')['screens'][0]
    def lane(self):
        w = self.ipc('scottland/loop-stats').get('workers', {}).get('shrink', {})
        return (w.get('lanes') or [{}])[0], w
    def wait(self, predicate, limit=40):
        end = time.monotonic() + limit
        while time.monotonic() < end:
            s = self.goo()
            if predicate(s): return s
            time.sleep(.1)
        return self.goo()
    def stop(self):
        for p in self.apps:
            try: os.killpg(p.pid, 15)
            except ProcessLookupError: pass
        subprocess.run([str(repo / 'tests/headless.sh'), 'stop'], env=self.env, capture_output=True)

def fixture(s, n=12):
    ids = []
    for i in range(n):
        v = s.spawn(f'shrink-{i}')
        s.ipc('window-rules/configure-view', {'id': v['id'], 'geometry': {'x': 60 + 90 * (i % 10), 'y': 60 + 50 * i % 500,
                                                                          'width': 320, 'height': 200}})
        ids.append(v['id'])
    time.sleep(1)
    s.ipc('scottland/attention', {'window': ids[0], 'attention': True, 'source': 'shrink-test'})
    return ids

def settled(g): return g['sleeping'] and g.get('settled_pixels', 0) > 0 and not g.get('breath_loose')

def capture(s, name):
    """The whole output in pixels (grim, PPM), without its header."""
    path = work / f'{name}.ppm'
    subprocess.run([str(repo / 'tests/headless.sh'), 'run', 'grim', '-t', 'ppm', str(path)], env=s.env, check=True,
                   capture_output=True)
    data = path.read_bytes()
    fields, at = [], 0
    while len(fields) < 4:
        while data[at:at + 1].isspace(): at += 1
        end = at
        while not data[end:end + 1].isspace(): end += 1
        fields.append(data[at:end]); at = end
    return int(fields[1]), data[at + 1:]

def lanes(s):
    return {l['name']: l for l in s.ipc('scottland/loop-stats').get('workers', {}).get('shrink', {}).get('lanes', [])}

def wait_lane(s, predicate, limit=10):
    end = time.monotonic() + limit
    while time.monotonic() < end:
        l = s.lane()[0]
        if predicate(l): return l
        time.sleep(.05)
    return s.lane()[0]

s = Session('main')
try:
    ids = fixture(s)
    s.ipc('scottland/loop-stats', {'reset': True})
    g = s.wait(settled)
    st = s.ipc('scottland/loop-stats')
    lane, worker = s.lane()
    check('the goo falls asleep and settles to the tight region through the worker', settled(g) and lane.get('delivered', 0) >= 1,
          (g.get('sleeping'), g.get('settled_pixels'), g.get('breath_loose'), lane))
    check('no shrink step runs on the main loop', 'goo_settle_tick' not in st['scopes'] and 'tighten_breathing' not in st['scopes'],
          list(st['scopes']))
    install = st['scopes'].get('shrink_install', {})
    check(f"installing the result is short ({install.get('max_ms', 0):.2f} ms, under 2 ms)", install.get('max_ms', 99) < 2, install)
    print(f"measured: snapshot to delivery {lane.get('snapshot_to_deliver_ms', {})} ms; the job ran "
          f"{lane.get('start_to_finish_ms', {})} ms off the loop", flush=True)
    check(f"the settled region ({g.get('settled_pixels')} px) is smaller than the loose bands ({g.get('band_pixels')} px)",
          0 < g.get('settled_pixels', 0) < g.get('band_pixels', 0), g)

    # Pixels: with the breath held still, the tight region draws exactly what the loose bands do
    # (conservative: shrinking never drops goo from the screen).
    s.ipc('scottland/goo-state', {'breath_hold': 0.6}); time.sleep(.5)
    g = s.wait(settled)
    width, tight = capture(s, 'tight')
    s.ipc('scottland/goo-state', {'breath_tight': False}); time.sleep(.6)
    _, loose = capture(s, 'loose')
    differ = sum(1 for i in range(0, min(len(tight), len(loose)), 3) if tight[i:i + 3] != loose[i:i + 3])
    check(f'the tight region renders the same pixels as the loose bands ({differ} of {len(tight) // 3} pixels differ)',
          len(tight) == len(loose) and differ == 0, differ)
    s.ipc('scottland/goo-state', {'breath_tight': True})
    g = s.wait(settled)
    check('tightening on again settles through the worker', settled(g))
    s.ipc('scottland/goo-state', {'breath_hold': -1})

    # A job held running; the sources change (the goo wakes, settles again and submits a newer job,
    # which waits as pending). Released: the old job is cancelled, never installed; the new settles.
    s.ipc('scottland/goo-state', {'shrink_hold': True})
    s.ipc('window-rules/configure-view', {'id': ids[3], 'geometry': {'x': 400, 'y': 300, 'width': 330, 'height': 210}})
    s.wait(lambda g: not g['sleeping'], 3)
    s.wait(lambda g: g['sleeping'], 30)
    held = wait_lane(s, lambda l: l.get('running'))
    check('test switch: a shrink job is observed running (held)', held.get('running') is True, held)
    s.ipc('window-rules/configure-view', {'id': ids[3], 'geometry': {'x': 440, 'y': 320, 'width': 330, 'height': 210}})
    s.wait(lambda g: not g['sleeping'], 3)
    s.wait(lambda g: g['sleeping'], 30)
    both = wait_lane(s, lambda l: l.get('running') and l.get('pending'))
    check('... and a newer job for the changed sources is observed pending behind it', both.get('running') and both.get('pending'), both)
    before = both
    s.ipc('scottland/goo-state', {'shrink_hold': False})
    g = s.wait(settled)
    after = s.lane()[0]
    check(f"released: the old job was cancelled ({before.get('cancelled')} -> {after.get('cancelled')}), only the new one delivered, "
          "and the goo settled", settled(g) and after.get('cancelled', 0) > before.get('cancelled', 0) and
          after.get('delivered', 0) == before.get('delivered', 0) + 1 and after.get('stale', 0) == before.get('stale', 0), (before, after))

    # Woken while a job is held: the goo uses its bands; nothing old is installed.
    s.ipc('scottland/goo-state', {'shrink_hold': True})
    s.ipc('scottland/goo-state', {'breath_max_rects': 6})  # a new settle job while asleep
    wait_lane(s, lambda l: l.get('running'))
    s.ipc('scottland/goo-state', {'breath_tight': False}); time.sleep(.2)
    g = s.goo()
    check('switching tightening off with a job running drops to the loose strips at once', not g.get('settled_pixels'), g.get('settled_pixels'))
    s.ipc('scottland/goo-state', {'shrink_hold': False, 'breath_tight': True})
    g = s.wait(settled)
    check('and back on, it settles through the worker again', settled(g))

    # Output removal and recreation (a scale change is not that): a job held on a second output,
    # the output removed under it; the first output keeps working; a new output settles anew.
    second = s.ipc('wayfire/create-headless-output', {'width': 1024, 'height': 768})['output']
    time.sleep(1)
    def place2(output, x):
        s.ipc('window-rules/configure-view', {'id': ids[8], 'output_id': output['id'],
                                              'geometry': {'x': x, 'y': 200, 'width': 320, 'height': 200}})
    place2(second, 200)
    end = time.monotonic() + 30
    while time.monotonic() < end and not (len(s.ipc('scottland/goo-state')['screens']) == 2 and
                                          settled(s.ipc('scottland/goo-state')['screens'][1])): time.sleep(.1)
    name2 = f"shrink:{second['name']}"
    check('a second output settles through its own lane', len(s.ipc('scottland/goo-state')['screens']) == 2 and
          lanes(s).get(name2, {}).get('delivered', 0) >= 1, lanes(s).keys())
    s.ipc('scottland/goo-state', {'shrink_hold': True})
    place2(second, 260)
    end = time.monotonic() + 20
    while time.monotonic() < end and not lanes(s).get(name2, {}).get('running'): time.sleep(.05)
    check('... a job is held running on it', lanes(s).get(name2, {}).get('running') is True, lanes(s).get(name2))
    s.ipc('wayfire/destroy-headless-output', {'output': second['name'], 'id': second['id']})
    time.sleep(.5)
    s.ipc('scottland/goo-state', {'shrink_hold': False})
    check('removing that output closes its lane', name2 not in lanes(s), list(lanes(s)))
    s.ipc('window-rules/configure-view', {'id': ids[2], 'geometry': {'x': 300, 'y': 260, 'width': 320, 'height': 200}})
    g = s.wait(settled)
    check('the first output still settles through the worker', settled(g))
    third = s.ipc('wayfire/create-headless-output', {'width': 1024, 'height': 768})['output']
    time.sleep(1)
    place2(third, 220)
    end = time.monotonic() + 30
    while time.monotonic() < end and not (len(s.ipc('scottland/goo-state')['screens']) == 2 and
                                          settled(s.ipc('scottland/goo-state')['screens'][1])): time.sleep(.1)
    name3 = f"shrink:{third['name']}"
    check('a recreated output settles through a new lane of its own (nothing inherited)',
          lanes(s).get(name3, {}).get('delivered', 0) >= 1 and lanes(s).get(name3, {}).get('stale', 0) == 0, lanes(s).get(name3))
    s.ipc('wayfire/destroy-headless-output', {'output': third['name'], 'id': third['id']})
    time.sleep(.5)

    # Reload with a shrink job held running.
    s.wait(settled)
    pid = s.compositor()
    # The first reload's reload.d hooks start session helpers a bare test session lacks (each
    # a new client): the baseline is taken after one reload.
    reload_session(); s.wait(settled)
    def own_fds():
        # Not client sockets or wlroots buffers (helpers reconnect, clients come and go): what a
        # leaked eventfd, pidfd, ring or inotify descriptor would show up in.
        out = []
        for fd in os.listdir(f'/proc/{pid}/fd'):
            try: target = os.readlink(f'/proc/{pid}/fd/{fd}')
            except OSError: continue
            if not target.startswith('socket:') and 'wlroots-' not in target: out.append(target)
        return sorted(out)
    base_fds = own_fds()
    s.ipc('scottland/goo-state', {'shrink_hold': True})
    s.ipc('window-rules/configure-view', {'id': ids[5], 'geometry': {'x': 200, 'y': 420, 'width': 300, 'height': 200}})
    s.wait(lambda g: not g['sleeping'], 3)
    s.wait(lambda g: g['sleeping'], 30)
    held = wait_lane(s, lambda l: l.get('running'))
    check('a job is held running before the reload', held.get('running') is True, held)
    out = reload_session()
    time.sleep(.5)
    threads = [Path(f'/proc/{pid}/task/{t}/comm').read_text().strip() for t in os.listdir(f'/proc/{pid}/task')]
    check('reload with a shrink job running succeeds', 'reloaded' in out, out)
    check('exactly one shrink worker and one watchdog thread afterwards',
          threads.count('scottland-shrin') == 1 and threads.count('scottland-wd') == 1, threads)
    g = s.wait(settled)
    check('the new copy settles the goo through its own worker', settled(g))
    fds = own_fds()
    check(f'descriptors other than client sockets back to baseline ({len(base_fds)} -> {len(fds)})', fds == base_fds,
          (set(fds) ^ set(base_fds)))
finally:
    s.stop()

for faults in ('worker-thread', 'worker-eventfd'):
    s = Session('fault-' + faults, faults)
    try:
        fixture(s, 6)
        g = s.wait(lambda g: g['sleeping'], 30)
        time.sleep(1)
        g = s.goo()
        lane, worker = s.lane()
        check(f'{faults} failure: the goo sleeps with the loose strips, Scottland works',
              g['sleeping'] and not g.get('settled_pixels') and not worker.get('available', True) and
              'views' in s.ipc('scottland/layout-state'), (g.get('settled_pixels'), worker))
    finally:
        s.stop()
print('all shrink worker checks passed' if not fails else f'{fails} check(s) failed')
sys.exit(1 if fails else 0)
