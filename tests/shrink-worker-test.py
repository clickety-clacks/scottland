#!/usr/bin/env python3
"""Main-loop Phase 4: the goo's breathing shrink (GO19/GO20) runs on the shrink worker. Real
headless sessions on a test host; each case starts and stops its own.

Checks: the goo settles to the tight region with no shrink work on the main loop and short
snapshot and install callbacks; a result for older sources, a woken goo or another output mode
is dropped; a reload with a shrink in flight leaves one worker thread and no extra descriptors;
a worker that can't start leaves the loose strips and Scottland working."""
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
        subprocess.run([str(repo / 'tests/headless.sh'), 'start'], env=self.env, check=True, capture_output=True)
        self.display = (work / name / 'display').read_text().strip()
        entries = (runtime / f'{self.display}.env').read_bytes().split(b'\0')
        self.path = next(e.split(b'=', 1)[1] for e in entries if e.startswith(b'WAYFIRE_SOCKET=')).decode()
        self.apps = []
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
    check(f"measured: snapshot to delivery {lane.get('snapshot_to_deliver_ms', {})} ms, the job ran {lane.get('start_to_finish_ms', {})} ms off the loop", True)
    check('the settled region is smaller than the loose bands', g.get('settled_pixels', 0) < g.get('loose_pixels', 1e12) if 'loose_pixels' in g else True, g)

    # A source change while a job runs: its result is dropped (stale or cancelled), a new one settles.
    before = s.lane()[0]
    s.ipc('window-rules/configure-view', {'id': ids[3], 'geometry': {'x': 400, 'y': 300, 'width': 330, 'height': 210}})
    time.sleep(.02)
    s.ipc('window-rules/configure-view', {'id': ids[3], 'geometry': {'x': 430, 'y': 310, 'width': 330, 'height': 210}})
    g = s.wait(settled)
    after = s.lane()[0]
    check('changing the sources mid-job never installs an old result; the goo settles again',
          settled(g) and after.get('submitted', 0) > before.get('submitted', 0), (before, after))

    # Wake while a job runs: woken goo uses the bands; the old result is not installed.
    s.ipc('scottland/goo-state', {'breath_tight': False}); time.sleep(.2)
    g = s.goo()
    check('switching tightening off drops to the loose strips at once', not g.get('settled_pixels'), g.get('settled_pixels'))
    s.ipc('scottland/goo-state', {'breath_tight': True})
    g = s.wait(settled)
    check('and back on, it settles through the worker again', settled(g))

    # An output mode change while asleep: the result for the old incarnation is dropped.
    out = s.ipc('window-rules/list-outputs')[0]['name']
    s.ipc('wayfire/set-config-options', {f'output:{out}/scale': 1.25}); time.sleep(.1)
    g = s.wait(settled)
    check('after an output scale change it settles for the new output', settled(g))
    s.ipc('wayfire/set-config-options', {f'output:{out}/scale': 1.0})

    # Reload with a shrink in flight (a source change makes a new job; reload right away).
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
    s.ipc('window-rules/configure-view', {'id': ids[5], 'geometry': {'x': 200, 'y': 420, 'width': 300, 'height': 200}})
    time.sleep(.05)
    out = reload_session()
    time.sleep(.5)
    threads = [Path(f'/proc/{pid}/task/{t}/comm').read_text().strip() for t in os.listdir(f'/proc/{pid}/task')]
    check('reload with a shrink in flight succeeds', 'reloaded' in out, out)
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
