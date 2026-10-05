#!/usr/bin/env python3
"""Reload handover matrix (docs/main-loop.md "Reload", design 3.8 and Astra's R6-1). Run on a test
host, never on a daily desktop; each case starts and stops its own headless session.

  tests/reload-handover-test.py [--from CHECKOUT] [case ...]

--from starts the session from another checkout's build and helpers (the build installed when the
change ships: the upgrade rehearsal); the reload itself always uses this checkout's
scottland-reload and plugin, as dev-install installs them together.

Every case has an unrelated minimized window and a widgetized window that is also minimized (a
second owner's disable on the same root node). Assertions are on Scottland's own lease balance
(the window stays hidden exactly while Scottland holds its lease, and the other owner's state is
unchanged) and on descriptor counts, never on "every root is enabled".
"""
import json, os, signal, shutil, socket, struct, subprocess, sys, time
from pathlib import Path

repo = Path(__file__).resolve().parents[1]
args = sys.argv[1:]
origin = repo
if '--from' in args:
    i = args.index('--from'); origin = Path(args[i + 1]).resolve(); del args[i:i + 2]
older = None  # --older CHECKOUT: a build before this reload protocol, for the rollback case
if '--older' in args:
    i = args.index('--older'); older = Path(args[i + 1]).resolve(); del args[i:i + 2]
only = set(args)
runtime = Path(os.environ.get('XDG_RUNTIME_DIR') or f'/run/user/{os.getuid()}') / 'scottland'
work = repo / 'build/reload-test'; work.mkdir(parents=True, exist_ok=True)
results = []


def log(*a): print(*a, flush=True)


class Session:
    def __init__(self, name, start_from=None):
        self.name = name
        self.checkout = start_from or origin
        self.dir = work / f'hl-{name}'
        self.env = dict(os.environ, SCOTTLAND_HEADLESS_DIR=str(self.dir), TMPDIR=str(work / 'tmp'),
                        SCOTTLAND_WIDGET_PATH=str(self.widgets()))
        (work / 'tmp').mkdir(exist_ok=True)
        self.headless('stop', check=False)
        if (self.dir / 'pid').exists() and not (self.dir / 'display').exists():
            # A start that never finished: its own process group, nothing else.
            try: os.killpg(int((self.dir / 'pid').read_text()), signal.SIGKILL)
            except (OSError, ValueError): pass
            shutil.rmtree(self.dir, ignore_errors=True)
        started = self.headless('start', '--widgets', check=False)
        if started.returncode or not (self.dir / 'display').exists():
            raise RuntimeError(f'headless start failed: {started.stdout[-600:]}')
        if self.checkout != repo:
            # What dev-install does: the session's helpers become the new build's, and so does its
            # settings metadata (new options), which scottland-reload registers before the swap.
            subprocess.run(['make', '--no-print-directory', '-C', str(repo), 'hooks',
                            f'HOOKS_DIR={self.checkout}/build/hooks'], check=True, stdout=subprocess.DEVNULL)
            xml = self.checkout / 'core/plugin/metadata/scottland.xml'
            saved = work / f'scottland.xml.{self.name}.old'
            if not saved.exists(): shutil.copy(xml, saved)
            shutil.copy(repo / 'core/plugin/metadata/scottland.xml', xml)
            self.restore_xml = (saved, xml)
        self.display = (self.dir / 'display').read_text().strip()
        self.state = self.dir / 'state'
        entries = (runtime / f'{self.display}.env').read_bytes().split(b'\0')
        self.socket = next(e.split(b'=', 1)[1] for e in entries if e.startswith(b'WAYFIRE_SOCKET=')).decode()
        self.ipc('wayfire/set-config-options', {'scottland/sounds': False})
        self.apps = []
        self.colors = {}

    def widgets(self):
        path = work / 'widgets'
        if not path.exists():
            (path / 'daemon').mkdir(parents=True)
            (path / 'slow').mkdir()
            (path / 'daemon/widget.toml').write_text('id = "daemon"\napps = ["^scottland-test-daemon$"]\nexec = "./start %t"\n')
            (path / 'daemon/start').write_text('#!/bin/sh\nsetsid -f foot -W 24x4 -T "daemon-card" sh -c "exec sleep 600"\nsleep 2\nexit 0\n')
            (path / 'daemon/start').chmod(0o755)
            (path / 'slow/widget.toml').write_text('id = "slow"\napps = ["^scottland-test-slow$"]\nexec = "./start"\n')
            (path / 'slow/start').write_text('#!/bin/sh\nsleep 6\nexec foot -T slow-card sh -c "exec sleep 600"\n')
            (path / 'slow/start').chmod(0o755)
        return path

    def headless(self, *a, check=True):
        return subprocess.run([str(self.checkout / 'tests/headless.sh'), *a], env=self.env, check=check,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def ipc(self, method, data=None, timeout=10):
        for attempt in range(100):
            s = socket.socket(socket.AF_UNIX); s.settimeout(timeout)
            try:
                s.connect(self.socket); break
            except BlockingIOError:  # the accept backlog is full while the loop is busy: wait
                s.close(); time.sleep(.1)
        else:
            raise TimeoutError('the compositor accepted no IPC connection for 10 s')
        body = json.dumps({'method': method, 'data': data or {}}).encode()
        s.sendall(struct.pack('<I', len(body)) + body)
        def read(n):
            b = b''
            while len(b) < n:
                more = s.recv(n - len(b))
                if not more: raise EOFError
                b += more
            return b
        reply = json.loads(read(struct.unpack('<I', read(4))[0])); s.close()
        return reply

    def run(self, *command):
        p = subprocess.Popen([str(self.checkout / 'tests/headless.sh'), 'run', *command], env=self.env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        self.apps.append(p)
        return p

    def compositor(self): return int((self.dir / 'compositor.pid').read_text())

    def views(self): return self.ipc('scottland/layout-state')['views']

    def view(self, title):
        return next((v for v in self.views() if v['title'] == title), None)

    def wait_view(self, title, limit=10):
        end = time.monotonic() + limit
        while time.monotonic() < end:
            v = self.view(title)
            if v: return v
            time.sleep(.2)
        raise AssertionError(f'window {title} never appeared')

    # Each app paints a solid color of its own: what is on screen is checked in captured pixels,
    # not only in the plugin's `hidden` field.
    palette = ['c81e64', '1e9650', '3264c8', 'c8a01e', '8c3cc8', '1ec8c8', 'e06414', '64c81e']
    def app(self, title, app_id=None):
        color = self.palette[len(self.colors) % len(self.palette)]
        self.run('foot', *(['--app-id', app_id] if app_id else []), '-o', f'colors.background={color}', '-o', 'cursor.color=' + color + ' ' + color,
                 '-T', title, '-W', '40x8', 'sh', '-c', 'exec sleep 3600')
        v = self.wait_view(title)
        self.colors[v['id']] = tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))
        return v

    def painted(self, view_id):
        """The fraction of the window's inner frame showing its color on screen (grim capture)."""
        v = next((v for v in self.views() if v['id'] == view_id), None)
        if not v: return 0.0
        f = v['frame']; screen = self.ipc('window-rules/list-outputs')[0]['geometry']
        x1, y1 = int(max(0, f['x'] + 12)), int(max(0, f['y'] + 12))
        x2, y2 = int(min(screen['width'], f['x'] + f['width'] - 12)), int(min(screen['height'], f['y'] + f['height'] - 12))
        if x2 - x1 < 4 or y2 - y1 < 4: return 0.0
        path = work / f'capture-{self.name}.ppm'
        self.headless('run', 'grim', '-t', 'ppm', '-g', f'{x1},{y1} {x2 - x1}x{y2 - y1}', str(path))
        data = path.read_bytes(); path.unlink(missing_ok=True)
        fields, at = [], 0
        while len(fields) < 4:  # P6 width height maxval
            while data[at:at + 1].isspace(): at += 1
            end = at
            while not data[end:end + 1].isspace(): end += 1
            fields.append(data[at:end]); at = end
        pixels = data[at + 1:]
        want = self.colors[view_id]; n = len(pixels) // 3
        match = sum(1 for i in range(0, n * 3, 3) if all(abs(pixels[i + c] - want[c]) <= 6 for c in range(3)))
        return match / max(1, n)

    def shown(self, view_id):
        """On screen in pixels: True (mostly its color), False (none of it), None (in between)."""
        p = self.painted(view_id)
        return True if p > .5 else False if p < .02 else None

    def pointer(self, x, y): self.ipc('stipc/move_cursor', {'x': round(x), 'y': round(y)})

    def super_drag(self, x1, y1, x2, y2):
        self.pointer(x1, y1); time.sleep(.2)
        self.ipc('stipc/feed_key', {'key': 'KEY_LEFTMETA', 'state': True})
        self.ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'press'})
        for i in range(1, 11):
            self.pointer(x1 + (x2 - x1) * i / 10, y1 + (y2 - y1) * i / 10); time.sleep(.03)
        time.sleep(.3)
        self.ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'release'})
        self.ipc('stipc/feed_key', {'key': 'KEY_LEFTMETA', 'state': False})

    placed = 0
    def widgetize(self, title, wait=3.0, pending=False):
        # Somewhere clear first: cards and earlier windows cover where a new window opens.
        v = self.view(title)
        Session.placed += 1
        screen = self.ipc('window-rules/list-outputs')[0]['geometry']
        self.ipc('window-rules/configure-view', {'id': v['id'], 'geometry': {
            'x': screen['width'] // 2 - 400 + 200 * (Session.placed % 4), 'y': screen['height'] - 115,
            'width': 180, 'height': 100}})
        time.sleep(.4)
        v = self.view(title); f = v['frame']
        width = self.ipc('window-rules/list-outputs')[0]['geometry']['width']
        self.super_drag(f['x'] + f['width'] / 2, f['y'] + f['height'] / 2, width - 8, f['y'] + f['height'] / 2)
        end = time.monotonic() + max(wait, 8)
        while time.monotonic() < end and not (v['id'] in self.links() and (pending or self.links()[v['id']]['widget_view'] > 0)):
            time.sleep(.2)
        if pending: return v['id']
        if v['id'] not in self.links():
            raise AssertionError(f'{title} did not become a widget: {[(x["id"], x["title"], x["frame"]) for x in self.views()]}')
        time.sleep(max(0, wait - 1))
        return v['id']

    def links(self): return {w['window']: w for w in self.ipc('scottland/widgets')['widgets']}

    def minimize(self, view_id, on=True): self.ipc('wm-actions/set-minimized', {'view_id': view_id, 'state': on})

    def hidden(self, view_id):
        v = next((v for v in self.views() if v['id'] == view_id), None)
        return v['hidden'] if v else None

    def pidfds(self):
        """The compositor's pidfds: {fd: Pid}."""
        found = {}
        base = Path(f'/proc/{self.compositor()}')
        for fd in (base / 'fd').iterdir():
            try:
                if os.readlink(fd) != 'anon_inode:[pidfd]': continue
                pid = next(l for l in (base / 'fdinfo' / fd.name).read_text().splitlines() if l.startswith('Pid:'))
                found[int(fd.name)] = int(pid.split()[1])
            except (OSError, StopIteration):
                pass
        return found

    def faults(self, *names):
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / 'handover-faults').write_text(''.join(n + '\n' for n in names))

    def broker_delay(self, seconds):
        self.state.mkdir(parents=True, exist_ok=True)
        path = self.state / 'broker-reply-delay'
        if seconds: path.write_text(str(seconds))
        else: path.unlink(missing_ok=True)

    def reload(self, timeout=None, hook=None, fail_set=False, source=None):
        env = ['env', f'SCOTTLAND_TEST_RELOAD_DIR={self.dir}',
               f'SCOTTLAND_RELOAD_SOURCE={source or repo / "build/libscottland.so"}']
        if timeout: env.append(f'SCOTTLAND_RELOAD_TIMEOUT={timeout}')
        if hook: env.append(f'SCOTTLAND_RELOAD_TEST_HOOK={hook}')
        if fail_set: env.append('SCOTTLAND_RELOAD_TEST_FAIL_SET=1')
        p = subprocess.run([str(self.checkout / 'tests/headless.sh'), 'run', *env, str(repo / 'core/session/scottland-reload')],
                           env=self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=180)
        log(f'    reload -> {p.returncode}: {p.stdout.strip()[-300:]}')
        return p.returncode, p.stdout

    def file(self, suffix): return runtime / f'{self.display}{suffix}'

    def records(self):
        return [s for s in ('.reload-attempt', '.reload-receipt', '.reload-importing', '.reload-ack',
                            '.reload-failed', '.reloading', '.widget-handover.json') if self.file(s).exists()]

    def loaded(self):
        try:
            r = self.ipc('scottland/desktop-model', timeout=3)
            return 'error' not in r
        except (OSError, EOFError):
            return None

    restore_xml = None
    def stop(self):
        if self.restore_xml:
            shutil.copy(*self.restore_xml); self.restore_xml[0].unlink()
        keep = work / 'artifacts' / self.name; keep.mkdir(parents=True, exist_ok=True)
        try:
            ring = subprocess.run([str(repo / 'build/scottland-loop-read'), '--file', str(runtime / f'{self.display}.loop')],
                                  capture_output=True, text=True, timeout=10)
            (keep / 'loop.txt').write_text(ring.stdout + ring.stderr)
            shutil.copy(self.dir / 'wayfire.log', keep / 'wayfire.log')
        except (OSError, subprocess.SubprocessError):
            pass
        for p in self.apps:
            try: os.killpg(p.pid, signal.SIGTERM)
            except ProcessLookupError: pass
        self.headless('stop', check=False)


def hook_script(name, body):
    path = work / f'hook-{name}.sh'
    path.write_text('#!/bin/bash\n' + body + '\n'); path.chmod(0o755)
    return path


class Case:
    def __init__(self, name):
        self.name = name; self.fails = 0
    def check(self, what, ok, detail=''):
        results.append((self.name, what, bool(ok)))
        log(f"{'PASS' if ok else 'FAIL'}  [{self.name}] {what}" + ('' if ok else f'  {detail}'))
        if not ok: self.fails += 1


def standard(s, launch_daemon=True):
    """Two cards (one launched by a process that has exited), an unrelated minimized window and a
    widgetized window that another owner also disabled."""
    # One at a time: a new window opens where the last one was, so a drag from its middle picks it.
    a = s.app('carry-alive')['id']; s.widgetize('carry-alive', wait=2.5)
    b = None
    if launch_daemon:
        b = s.app('carry-daemon', 'scottland-test-daemon')['id']; s.widgetize('carry-daemon', wait=2.5)
    d = s.app('doubly')['id']; s.widgetize('doubly', wait=2.5)
    c = s.app('unrelated')['id']
    s.minimize(c); s.minimize(d)
    time.sleep(2.5)  # the daemon widget's launcher exits after 2 s
    return {'alive': a, 'daemon': b, 'unrelated': c, 'doubly': d}


def raise_window(s, view_id):
    """Front and center at full scale, so its pixels can be checked unoccluded (a hidden window
    stays hidden: geometry and focus don't return a lease)."""
    screen = s.ipc('window-rules/list-outputs')[0]['geometry']
    v = next((v for v in s.views() if v['id'] == view_id), None)
    if not v or v.get('hidden') or view_id in s.links(): return
    s.ipc('window-rules/configure-view', {'id': view_id, 'geometry': {
        'x': screen['width'] // 2 - 200, 'y': screen['height'] // 2 - 150, 'width': 400, 'height': 300}})
    s.ipc('window-rules/focus-view', {'id': view_id}); time.sleep(.6)


def check_balance(t, s, ids, imported, label):
    """Leases held exactly where Scottland holds them: a window is hidden exactly while it is a
    widget (or minimized by its other owner). When nothing was carried over, the new copy may make
    a window on a rail a widget again (WG1, a new card): that takes a new lease, which is fine."""
    # A window made a widget again waits visibly for its new card (WG22): let launches finish.
    end = time.monotonic() + 10
    while time.monotonic() < end and any(w['widget_view'] <= 0 for w in s.links().values()):
        time.sleep(.2)
    time.sleep(.5)
    links = s.links()
    for key in ('alive', 'daemon', 'doubly'):
        if ids.get(key) is None: continue
        if imported:
            t.check(f'{label}: {key} is still a widget', ids[key] in links, links.keys())
        elif key != 'doubly':
            t.check(f'{label}: {key} is hidden exactly while it is a widget', s.hidden(ids[key]) == (ids[key] in links),
                    (s.hidden(ids[key]), ids[key] in links))
        if key != 'doubly':
            raise_window(s, ids[key])
            t.check(f'{label}: {key} is on screen (pixels) exactly while it is not a widget', s.shown(ids[key]) == (ids[key] not in links),
                    (s.painted(ids[key]), ids[key] in links))
    t.check(f'{label}: the unrelated minimized window is still hidden', s.hidden(ids['unrelated']) is True and s.shown(ids['unrelated']) is False,
            s.painted(ids['unrelated']))
    t.check(f'{label}: the doubly disabled window is hidden', s.hidden(ids['doubly']) is True and s.shown(ids['doubly']) is False,
            s.painted(ids['doubly']))
    s.minimize(ids['doubly'], False); time.sleep(.5)
    raise_window(s, ids['doubly'])
    t.check(f'{label}: un-minimizing it leaves it hidden exactly while Scottland holds its lease',
            s.hidden(ids['doubly']) == (ids['doubly'] in s.links()) and s.shown(ids['doubly']) == (ids['doubly'] not in s.links()),
            (s.hidden(ids['doubly']), s.painted(ids['doubly']), ids['doubly'] in s.links()))
    if imported:
        t.check(f'{label}: (it is still a widget)', ids['doubly'] in s.links())
    s.minimize(ids['doubly'], True); time.sleep(.3)


def finish_balance(t, s, ids):
    """Restore every widget: each app comes back exactly once (a double return would show it while
    minimized; a lost one would keep it hidden)."""
    for key in ('alive', 'daemon'):
        if ids.get(key) is None or ids[key] not in s.links(): continue
        s.ipc('scottland/widget-action', {'id': str(ids[key]), 'action': 'restore'}); time.sleep(1.5)
        raise_window(s, ids[key])
        t.check(f'restoring {key} shows its window', s.hidden(ids[key]) is False and s.shown(ids[key]) is True, s.painted(ids[key]))
    if ids['doubly'] in s.links():
        s.ipc('scottland/widget-action', {'id': str(ids['doubly']), 'action': 'restore'}); time.sleep(1.5)
    # Restoring focuses the window, which un-minimizes it (Wayfire). Probe the reference count: a
    # lease Scottland still held would keep it hidden; one returned twice would keep it shown.
    s.minimize(ids['doubly'], False); time.sleep(.5)
    raise_window(s, ids['doubly'])
    t.check('the doubly disabled window shows once nobody disables it', s.hidden(ids['doubly']) is False and s.shown(ids['doubly']) is True,
            s.painted(ids['doubly']))
    s.minimize(ids['doubly'], True); time.sleep(.5)
    t.check('one disable hides it again (no lease returned twice)', s.hidden(ids['doubly']) is True and s.shown(ids['doubly']) is False,
            s.painted(ids['doubly']))
    s.minimize(ids['doubly'], False); time.sleep(.3)
    t.check('the unrelated window is still minimized', s.hidden(ids['unrelated']) is True)
    s.minimize(ids['unrelated'], False); time.sleep(.3)
    raise_window(s, ids['unrelated'])
    t.check('and shows once un-minimized', s.hidden(ids['unrelated']) is False and s.shown(ids['unrelated']) is True, s.painted(ids['unrelated']))


def handles_match_links(t, s, label, extra=()):
    # Restored widgets' processes are ended over a few seconds (WG5): wait for their handles.
    end = time.monotonic() + 8
    while time.monotonic() < end:
        fds = s.pidfds(); links = s.links()
        if len(fds) <= sum(1 for w in links.values() if w.get('launcher_pid', 0) > 0) + len(extra) + len(links): break
        time.sleep(.3)
    fds = s.pidfds()
    links = s.links()
    expected = sum(1 for w in links.values() if w.get('launcher_pid', 0) > 0) + len(extra)
    t.check(f'{label}: pidfds held = live launcher handles + sentinels', len(fds) >= expected and len(fds) <= expected + len(links),
            f'{fds} links={len(links)} extra={extra}')


cases = {}
def case(fn): cases[fn.__name__] = fn; return fn


def plugin_identity(s):
    """The Scottland plugin files the compositor has mapped now: (path, sha256), deleted ones marked."""
    import hashlib
    found = set()
    for line in Path(f'/proc/{s.compositor()}/maps').read_text().splitlines():
        parts = line.split(None, 5)
        if len(parts) == 6 and 'libscottland' in parts[5]:
            found.add(parts[5])
    out = []
    for path in sorted(found):
        live = path.endswith(' (deleted)') is False and Path(path).exists()
        out.append((path, hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16] if live else 'deleted'))
    return out


def sha(path):
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


def hold_gestures(s, view_id):
    """Start a Super+drag on the window (button held) and put a finger down elsewhere; both stay
    held across the reload that follows."""
    f = next(v for v in s.views() if v['id'] == view_id)['frame']
    cx, cy = f['x'] + f['width'] / 2, f['y'] + f['height'] / 2
    s.pointer(cx, cy); time.sleep(.2)
    s.ipc('stipc/feed_key', {'key': 'KEY_LEFTMETA', 'state': True})
    s.ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'press'})
    for i in range(1, 6): s.pointer(cx + 12 * i, cy + 6 * i); time.sleep(.03)
    s.ipc('stipc/touch', {'finger': 0, 'x': 200, 'y': 200})
    return cx + 60, cy + 30


def release_gestures(s, x, y):
    for i in range(1, 6): s.pointer(x + 10 * i, y); time.sleep(.03)
    s.ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'release'})
    s.ipc('stipc/feed_key', {'key': 'KEY_LEFTMETA', 'state': False})
    s.ipc('stipc/touch_release', {'finger': 0})
    time.sleep(.5)


@case
def upgrade():
    """The installed build (--from) to this build, then this build to itself, each swap with a
    Super+drag and a touch held through it and goo work in flight (the second with readings and a
    shrink job held by test switches). Both builds' identities are checked in the compositor."""
    t = Case('upgrade'); s = Session('upgrade')
    try:
        ids = standard(s)
        held = s.app('upgrade-held')['id']
        raise_window(s, held)
        starting = plugin_identity(s)
        t.check(f'the session runs the starting build ({origin.name}: {sha(origin / "build/libscottland.so")})',
                any(h == sha(origin / 'build/libscottland.so') for _, h in starting), starting)
        before = s.pidfds(); links_before = s.links()
        # Goo work in flight: a window moved just before the swap wakes the goo.
        s.ipc('window-rules/configure-view', {'id': held, 'geometry': {'x': 420, 'y': 260, 'width': 400, 'height': 300}})
        x, y = hold_gestures(s, held)
        code, out = s.reload()
        new_build = sha(repo / 'build/libscottland.so')
        mapped = plugin_identity(s)
        # Wayfire keeps an unloaded plugin's image mapped (seen with the installed build alone too);
        # what counts is that the attempt's copy, this build, is mapped and answering.
        copies = [(p, h) for p, h in mapped if '/plugins/libscottland-' in p]
        t.check(f'reload from the starting build succeeds and carries every widget (now {new_build})',
                code == 0 and 'imported 3 of 3' in out, out)
        t.check("the compositor maps this attempt's copy, this build", bool(copies) and copies[-1][1] == new_build and
                s.ipc('scottland/loop-stats').get('reload', {}).get('load', 0) >= 1, mapped)
        release_gestures(s, x, y)
        t.check('the held drag and touch end normally after the swap: the compositor answers', s.loaded() is True)
        f0 = next(v for v in s.views() if v['id'] == held)['frame']
        s.super_drag(f0['x'] + f0['width'] / 2, f0['y'] + f0['height'] / 2, f0['x'] + f0['width'] / 2 + 80, f0['y'] + f0['height'] / 2)
        time.sleep(1)
        f1 = next(v for v in s.views() if v['id'] == held)['frame']
        t.check('a new drag after the swap moves the window (no stale grab)', abs(f1['x'] - f0['x']) > 30, (f0, f1))
        raise_window(s, held)
        t.check('the held window is on screen in pixels after the swap', s.shown(held) is True, s.painted(held))
        t.check('the same widget windows stay linked', {k: v['widget_view'] for k, v in s.links().items()} ==
                {k: v['widget_view'] for k, v in links_before.items()})
        t.check('no reload records are left', not s.records(), s.records())
        after = s.pidfds()
        t.check('the same number of pidfds, for the same processes', sorted(before.values()) == sorted(after.values()), (before, after))
        check_balance(t, s, ids, True, 'after the upgrade')
        threads = [Path(f'/proc/{s.compositor()}/task/{x}/comm').read_text().strip() for x in os.listdir(f'/proc/{s.compositor()}/task')]
        t.check('one shrink worker and one watchdog thread after the upgrade',
                threads.count('scottland-shrin') == 1 and threads.count('scottland-wd') == 1, threads)

        # This build to itself, with readings and a shrink job held in flight.
        s.ipc('scottland/goo-state', {'readback_fault': 'hold', 'shrink_hold': True})
        s.ipc('window-rules/configure-view', {'id': held, 'geometry': {'x': 380, 'y': 240, 'width': 400, 'height': 300}})
        time.sleep(1.5)
        g = s.ipc('scottland/goo-state')['screens'][0]
        t.check('before the second swap: goo readings are in flight', g.get('readings_in_flight', 0) >= 1, g.get('readings_in_flight'))
        x, y = hold_gestures(s, held)
        code, out = s.reload()
        t.check('a second reload (this build to itself) carries every widget', code == 0 and 'imported 3 of 3' in out, out)
        release_gestures(s, x, y)
        end = time.monotonic() + 30
        while time.monotonic() < end and not s.ipc('scottland/goo-state')['screens'][0]['sleeping']: time.sleep(.2)
        g = s.ipc('scottland/goo-state')['screens'][0]
        t.check('the new copy starts fresh (no held switch) and the goo settles on its own readings',
                g['sleeping'] and g['readback'] == 'async' and g['readings_applied'] >= 1, (g['sleeping'], g['readback'], g['readings_applied']))
        check_balance(t, s, ids, True, 'after the second reload')
        t.check('pidfds unchanged again', sorted(s.pidfds().values()) == sorted(after.values()))
        threads = [Path(f'/proc/{s.compositor()}/task/{x}/comm').read_text().strip() for x in os.listdir(f'/proc/{s.compositor()}/task')]
        t.check('still one shrink worker and one watchdog thread',
                threads.count('scottland-shrin') == 1 and threads.count('scottland-wd') == 1, threads)
        finish_balance(t, s, ids)
    finally:
        s.stop()
    return t


@case
def rollback():
    """This build to an older one (a rollback through this scottland-reload) and back. The older
    build acknowledges nothing: the helper resolves the attempt by its own copy being listed and
    mapped, not by elapsed time; going back to this build imports what the older one handed over."""
    t = Case('rollback')
    if not older:
        t.check('skipped: no --older checkout given', True)
        return t
    s = Session('rollback')
    try:
        ids = standard(s, launch_daemon=False)
        code, out = s.reload(source=older / 'build/libscottland.so', timeout=10)
        t.check('reloading into the older build completes without waiting out the timeout',
                code == 0 and 'older build that does not report reloads' in out, out)
        t.check('no reload records are left', not s.records(), s.records())
        t.check('the older build answers', s.loaded() is True)
        code, out = s.reload()
        t.check('and back to this build: the older build\'s handover is imported', code == 0 and 'imported' in out, out)
        check_balance(t, s, ids, True, 'after the round trip')
        finish_balance(t, s, ids)
    finally:
        s.stop()
    return t


@case
def presentation():
    """Widgets collapsed and peeking when the reload happens (design 3.8: the writer normalizes
    every handed-over app's lease whatever its presentation)."""
    t = Case('presentation'); s = Session('presentation')
    try:
        ids = standard(s, launch_daemon=False)
        s.ipc('scottland/widget-action', {'id': str(ids['alive']), 'action': 'minimize'}); time.sleep(1)
        s.ipc('scottland/widget-action', {'id': str(ids['doubly']), 'action': 'test-peek', 'peek': True}); time.sleep(.5)
        before = s.links()
        code, out = s.reload()
        t.check('a reload with a collapsed and a peeking widget carries both', code == 0 and 'imported 2 of 2' in out, out)
        t.check('the collapsed widget is still collapsed', s.links()[ids['alive']]['collapsed'] == before[ids['alive']]['collapsed'],
                (before[ids['alive']], s.links()[ids['alive']]))
        check_balance(t, s, ids, True, 'after the reload')
        finish_balance(t, s, ids)
    finally:
        s.stop()
    return t


@case
def launch_states():
    """A launch still pending (window not mapped), a card mapped before the broker's reply, and an
    ordinary one."""
    t = Case('launch-states'); s = Session('launch')
    try:
        ordinary = s.app('ordinary')['id']; s.widgetize('ordinary')
        s.broker_delay(8)
        early = s.app('early-card')['id']; s.widgetize('early-card', wait=3)
        t.check('the early card is docked before the broker answered', early in s.links() and s.links()[early]['launcher_pid'] == 0,
                s.links().get(early))
        # Still inside the 8 s delay: the broker has the slow launch queued behind that reply.
        slow = s.app('slow-app', 'scottland-test-slow')['id']; s.widgetize('slow-app', pending=True)
        t.check('the slow launch is pending (no card yet)', s.links()[slow]['widget_view'] <= 0, s.links().get(slow))
        s.broker_delay(0)
        code, out = s.reload()
        links = s.links()
        t.check('reload succeeds', code == 0, out)
        t.check('the ordinary widget is carried with its process handle', ordinary in links and links[ordinary]['launcher_pid'] > 0)
        t.check('the early card is carried with its unit and no process handle',
                early in links and links[early]['launcher_pid'] == 0 and links[early]['widget_view'] > 0, links.get(early))
        # Not handed over: restored and its launch cancelled. Its window is still on the rail, so
        # the new copy makes it a widget again (WG1, a new launch); the old launch never shows.
        t.check('the pending launch is not carried (a new launch at most)',
                slow not in links or links[slow]['launcher_pid'] != 0 or links[slow]['widget_view'] <= 0, links.get(slow))
        time.sleep(9)
        cards = [v['id'] for v in s.views() if v['title'] == 'slow-card']
        link = s.links().get(slow)
        t.check('the cancelled launch never shows a card: at most the new launch\'s',
                len(cards) <= 1 and (not cards or (link and link['widget_view'] == cards[0])), (cards, link))
        card = links[early]['widget_view'] if early in links else None
        s.ipc('scottland/widget-action', {'id': str(early), 'action': 'restore'}); time.sleep(3)
        t.check('restoring the imported early card shows its app', s.hidden(early) is False)
        t.check('and stops its card through the new broker', card is None or all(v['id'] != card for v in s.views()))
    finally:
        s.broker_delay(0); s.stop()
    return t


def failure_case(name, prepare, expect_code, expect_imported, after=None, timeout=None, fail_set=False, hook=None):
    t = Case(name); s = Session(name.replace('/', '-'))
    try:
        ids = standard(s, launch_daemon=False)
        sentinels = {kind: s.ipc('scottland/test-handover', {'open': kind})['fd'] for kind in ('live', 'dead', 'file')}
        identity = {fd: s.pidfds().get(fd) for fd in sentinels.values()}
        prepare(s)
        code, out = s.reload(timeout=timeout, fail_set=fail_set, hook=hook(s) if hook else None)
        t.check(f'reload exits {expect_code}', code == expect_code, out)
        if after: after(t, s, ids, out)
        else:
            s.faults()
            check_balance(t, s, ids, expect_imported, 'after the reload')
        current = s.pidfds()
        t.check('sentinel descriptors are untouched', all(current.get(fd) == identity[fd] for fd in identity if identity[fd] is not None)
                and Path(f'/proc/{s.compositor()}/fd/{sentinels["file"]}').exists(), (identity, current))
        handles_match_links(t, s, 'descriptors', extra=[fd for fd in identity if identity[fd] is not None])
    finally:
        s.faults(); s.stop()
    return t


def recover_then_finish(expected_reason=None):
    def after(t, s, ids, out):
        if expected_reason: t.check(f'the outcome says {expected_reason!r}', expected_reason in out, out)
        s.faults()
        check_balance(t, s, ids, False, 'after the failed carry-over')
        code, out2 = s.reload()
        t.check('a later reload completes', code == 0, out2)
        t.check('no reload records are left', not s.records(), s.records())
    return after


@case
def receipt_missing():
    return failure_case('receipt-missing', lambda s: None, 2, False, timeout=4,
        hook=lambda s: hook_script('rm-receipt', f'[ "$1" = prepared ] && rm -f {s.file(".reload-receipt")}'),
        after=recover_then_finish())

@case
def receipt_unreadable():
    return failure_case('receipt-unreadable', lambda s: None, 2, False, timeout=4,
        hook=lambda s: hook_script('bad-receipt', f'[ "$1" = prepared ] && echo garbage >{s.file(".reload-receipt")}'),
        after=recover_then_finish())

@case
def receipt_other_session():
    return failure_case('receipt-other-session', lambda s: None, 0, False,
        hook=lambda s: hook_script('other-session', f'[ "$1" = prepared ] && python3 -c "import json,sys; p=sys.argv[1]; r=json.load(open(p)); '
                                   f'r[\'session\']=\'someone-else\'; json.dump(r, open(p, \'w\'))" {s.file(".reload-receipt")}'),
        after=recover_then_finish('another session'))

@case
def publication_fails():
    return failure_case('setenv-fails', lambda s: s.faults('setenv'), 0, False, after=recover_then_finish('nothing to import'))

@case
def write_fails():
    return failure_case('write-fails', lambda s: s.faults('write'), 0, False, after=recover_then_finish('nothing to import'))

@case
def dup_fails():
    return failure_case('dup-fails', lambda s: s.faults('dup'), 0, False, after=recover_then_finish('nothing to import'))

@case
def version_setenv_fails():
    return failure_case('version-setenv-fails', lambda s: s.faults('version-setenv'), 0, False, after=recover_then_finish('nothing to import'))

@case
def export_throws():
    """An allocation failure while preparing the handover (after a handle was duplicated): the
    widgets unload as in an ordinary unload and the duplicated handles are closed."""
    return failure_case('export-throw', lambda s: s.faults('export-throw'), 0, False, after=recover_then_finish('nothing to import'))

@case
def adopt_throws():
    """An allocation failure while adopting each link: each lease is returned once, each handle
    closed, each card closed; the apps come back as after an ordinary unload."""
    def after(t, s, ids, out):
        t.check('the outcome counts no import', 'imported 0 of 2' in out, out)
        s.faults()
        check_balance(t, s, ids, False, 'after the failed adoptions')
        code, out2 = s.reload()
        t.check('a later reload completes', code == 0, out2)
        t.check('no reload records are left', not s.records(), s.records())
    return failure_case('adopt-throw', lambda s: s.faults('adopt-throw'), 0, False, after=after)

@case
def corrupt_file():
    return failure_case('corrupt-file', lambda s: s.faults('corrupt-file'), 0, False, after=recover_then_finish('unreadable'))

@case
def wrong_id():
    return failure_case('wrong-id', lambda s: s.faults('wrong-id'), 0, False, after=recover_then_finish('does not match'))

@case
def bad_pid():
    def after(t, s, ids, out):
        s.faults()
        links = s.links()
        t.check('a link whose recorded pid does not match is imported without a handle',
                ids['alive'] in links and links[ids['alive']]['launcher_pid'] == 0, links.get(ids['alive']))
        check_balance(t, s, ids, True, 'after the reload')
        finish_balance(t, s, ids)
    return failure_case('bad-pid', lambda s: s.faults('bad-pid'), 0, True, after=after)

@case
def init_fails_before_adoption():
    def after(t, s, ids, out):
        t.check('the failure is reported', 'failed' in out, out)
        t.check('Scottland is not loaded', s.loaded() is False)
        s.faults()
        code, out2 = s.reload()
        t.check('a later reload loads Scottland again', code == 0 and s.loaded(), out2)
        t.check('no reload records are left', not s.records(), s.records())
        check_balance(t, s, ids, False, 'after recovering')
    return failure_case('init-fails-before', lambda s: s.faults('init-fail-before-adoption'), 1, False, after=after)

@case
def init_fails_after_adoption():
    def after(t, s, ids, out):
        t.check('the failure is reported', 'failed' in out, out)
        t.check('Scottland is not loaded', s.loaded() is False)
        s.faults()
        code, out2 = s.reload()
        t.check('a later reload loads Scottland again', code == 0 and s.loaded(), out2)
        check_balance(t, s, ids, False, 'after recovering')
    return failure_case('init-fails-after', lambda s: s.faults('init-fail-after-adoption'), 1, False, after=after)

@case
def load_delayed():
    def hook(s):
        return hook_script('delay', f'[ "$1" = after-set ] && {{ kill -STOP {s.compositor()}; sleep 5; kill -CONT {s.compositor()}; }}')
    def after(t, s, ids, out):
        t.check('a load delayed by 5 s still completes the attempt', 'imported 2 of 2' in out, out)
        check_balance(t, s, ids, True, 'after the delayed reload')
    return failure_case('load-delayed', lambda s: None, 0, True, hook=hook, after=after)

@case
def swap_beyond_timeout():
    def hook(s):
        return hook_script('stall', f'[ "$1" = after-set ] && {{ kill -STOP {s.compositor()}; setsid -f sh -c "sleep 16; kill -CONT {s.compositor()}" >/dev/null 2>&1 </dev/null; }}')
    def after(t, s, ids, out):
        t.check('the timeout keeps every record', {'.reload-attempt', '.reload-receipt', '.reloading'} <= set(s.records()), s.records())
        code, out2 = s.reload(timeout=3)
        t.check('a second request while the compositor does not answer changes nothing', code == 2 and 'nothing changed' in out2, out2)
        time.sleep(12)
        code, out3 = s.reload()
        t.check('once it answers, the earlier attempt is found complete and a new one runs',
                code == 0 and 'had completed' in out3, out3)
        check_balance(t, s, ids, True, 'after the resumed reloads')
    return failure_case('swap-beyond-timeout', lambda s: None, 2, True, timeout=3, hook=hook, after=after)

@case
def ipc_error_old_answers():
    """An IPC error before the swap was requested, and the old copy keeps answering: the attempt
    is never declared unswapped by elapsed time (Astra, finding 2); it is preserved and driven again."""
    def after(t, s, ids, out):
        t.check('an IPC error after the swap could start keeps every record',
                {'.reload-attempt', '.reload-receipt', '.reloading'} <= set(s.records()), s.records())
        nonce = json.loads(s.file('.reload-attempt').read_text())['nonce']
        code, out2 = s.reload(timeout=3)
        t.check('a second request is refused while the old copy answers', code == 3, out2)
        t.check('... and keeps every record', {'.reload-attempt', '.reload-receipt', '.reloading'} <= set(s.records()), s.records())
        time.sleep(4)
        code, out3 = s.reload()
        loaded = s.ipc('scottland/loop-stats')['reload']
        t.check('past the timeout with the same copy answering, the same attempt is driven again, not abandoned',
                code == 0 and f'resuming the earlier reload ({nonce})' in out3 and 'never swapped' not in out3, out3)
        t.check('the loaded copy consumed that attempt\'s receipt and carried the widgets',
                loaded['nonce'] == nonce and 'imported 2 of 2' in loaded['outcome'], loaded)
        check_balance(t, s, ids, True, 'after the resumed attempt')
        finish_balance(t, s, ids)
    return failure_case('ipc-error-no-swap', lambda s: None, 2, True, timeout=3, fail_set=True, after=after)

@case
def queued_config():
    """Astra's probe with widgets: the new copy is queued in the session's config file behind
    Wayfire's 500 ms debounce, which continual config writes keep resetting past the helper's
    timeout, and the option IPC reports an error. The old copy answers throughout."""
    import threading
    stop = threading.Event()
    def write_config(s):
        while not stop.wait(.05):
            with (s.dir / 'wayfire.ini').open('a') as cfg:
                cfg.write('\n# reload-handover-test: another config write within the debounce interval\n')
    def prepare(s):
        s.ipc('wayfire/set-config-options', {'workarounds/config_reload_delay': 500})
        s.first_load = s.ipc('scottland/loop-stats')['reload']['load']
        s.writer = threading.Thread(target=write_config, args=(s,)); s.writer.start()
    def hook(s):
        path = work / 'hook-queue-config.py'
        path.write_text(f'''#!/usr/bin/env python3
import json, re, sys
from pathlib import Path
if sys.argv[1] == 'before-set':
    target = json.loads(Path({str(s.file(".reload-attempt"))!r}).read_text())['target']
    cfg = Path({str(s.dir / "wayfire.ini")!r})
    new, n = re.subn(r'(?m)^(\\s*)scottland(\\s*\\\\)$', lambda m: m[1] + target + m[2], cfg.read_text())
    assert n == 1, n
    cfg.write_text(new)
'''); path.chmod(0o755)
        return path
    def after(t, s, ids, out):
        try:
            nonce = json.loads(s.file('.reload-attempt').read_text())['nonce']
            attempt = json.loads(s.file('.reload-attempt').read_text())
            same = True
            while time.time() < attempt['created'] + attempt['timeout'] + .5:
                same &= s.ipc('scottland/loop-stats')['reload']['load'] == s.first_load
                time.sleep(.1)
            t.check('the old copy answers past the helper timeout with the swap still queued', same)
            code, out2 = s.reload(timeout=10)
            t.check('the next request drives the same attempt (no "never swapped", no fresh attempt)',
                    code == 0 and f'resuming the earlier reload ({nonce})' in out2 and 'never swapped' not in out2, out2)
        finally:
            stop.set(); s.writer.join()
        time.sleep(2)  # the debounced config reload names the same copy: no second swap
        loaded = s.ipc('scottland/loop-stats')['reload']
        t.check('once config writes stop, the queued reload changes nothing: one swap, this attempt\'s receipt, widgets carried',
                loaded['load'] == s.first_load + 1 and loaded['nonce'] == nonce and 'imported 2 of 2' in loaded['outcome'], loaded)
        t.check('no reload records are left', not s.records(), s.records())
        check_balance(t, s, ids, True, 'after the queued reload')
        finish_balance(t, s, ids)
    try:
        return failure_case('queued-config', prepare, 2, True, timeout=3, fail_set=True, hook=hook, after=after)
    finally:
        stop.set()

@case
def ipc_error_load_queued():
    """R6-1 A: the swap was queued, then the option IPC reported an error."""
    def hook(s):
        return hook_script('queue', f'''[ "$1" = before-set ] || exit 0
target=$(python3 -c "import json; print(json.load(open('{s.file(".reload-attempt")}'))['target'])")
plugins=$(python3 {repo}/tests/wfipc.py wayfire/get-config-option '{{"option":"core/plugins"}}' | python3 -c "import json,sys; print(json.load(sys.stdin)['value'])")
python3 {repo}/tests/wfipc.py wayfire/set-config-options "$(python3 -c "import json,sys; print(json.dumps({{'core/plugins': ' '.join(sys.argv[2] if p == 'scottland' or '/libscottland' in p else p for p in sys.argv[1].split())}}))" "$plugins" "$target")" >/dev/null''')
    def after(t, s, ids, out):
        time.sleep(1)
        t.check('the queued swap still completed and carried the widgets', all(ids[k] in s.links() for k in ('alive', 'doubly')))
        code, out2 = s.reload()
        t.check('the next request finds that attempt complete and runs a new one', code == 0 and 'had completed' in out2, out2)
        check_balance(t, s, ids, True, 'after both')
    return failure_case('ipc-error-load-queued', lambda s: None, 2, True, fail_set=True, hook=hook, after=after)

@case
def failed_load_retry():
    """R6-1 B: the configured copy fails to load; the next request retries that same copy."""
    def hook(s):
        return hook_script('break-copy', f'''[ "$1" = before-set ] || exit 0
target=$(python3 -c "import json; print(json.load(open('{s.file(".reload-attempt")}'))['target'])")
cp "$target" "$target.good"; : >"$target"''')
    def after(t, s, ids, out):
        t.check('no Scottland copy answers', s.loaded() is False)
        t.check('the attempt and its transfer are kept', {'.reload-attempt', '.reloading', '.widget-handover.json'} <= set(s.records()), s.records())
        target = json.loads(s.file('.reload-attempt').read_text())['target']
        shutil.move(target + '.good', target)
        code, out2 = s.reload()
        t.check('the next request resumes the same attempt and the retried load carries the widgets',
                code == 0 and 'resuming' in out2 and 'imported 2 of 2' in out2, out2)
        check_balance(t, s, ids, True, 'after the retried load')
        finish_balance(t, s, ids)
    return failure_case('failed-load-retry', lambda s: None, 2, True, timeout=4, hook=hook, after=after)

@case
def replay():
    """Astra's replay: a handover file re-created after its reload authorizes nothing."""
    t = Case('replay'); s = Session('replay')
    try:
        ids = standard(s, launch_daemon=False)
        sentinels = {kind: s.ipc('scottland/test-handover', {'open': kind})['fd'] for kind in ('live', 'dead', 'file')}
        identity = {fd: s.pidfds().get(fd) for fd in sentinels.values()}
        code, out = s.reload()
        t.check('reload succeeds', code == 0, out)
        # Re-create files naming the sentinels, in both formats, and load a copy without a receipt.
        session = s.ipc('scottland/desktop-model')['session']
        fake = {'format': 2, 'id': 'replayed', 'pid': s.compositor(), 'session': session, 'version': 1, 'windows': [],
                'links': [{'window': ids['alive'], 'widget': 99999, 'pid': 1, 'pidfd': fd, 'unit': 'x', 'rail': 'right',
                           'x': 0, 'y': 0, 'minimized': False} for fd in sentinels.values()]}
        for legacy in (False, True):
            if legacy: fake = {k: v for k, v in fake.items() if k not in ('format', 'id', 'pid')}
            s.file('.widget-handover.json').write_text(json.dumps(fake))
            copy = s.dir / f'replay-{legacy}.so'; shutil.copyfile(repo / 'build/libscottland.so', copy)
            plugins = s.ipc('wayfire/get-config-option', {'option': 'core/plugins'})['value']
            s.ipc('wayfire/set-config-options', {'core/plugins': ' '.join(str(copy) if '/libscottland' in p or 'replay-' in p or p == 'scottland' else p
                                                                         for p in plugins.split())})
            time.sleep(2)
            current = s.pidfds()
            t.check(f'a re-created {"legacy" if legacy else "new-format"} file touches no descriptor',
                    all(current.get(fd) == identity[fd] for fd in identity if identity[fd] is not None) and
                    Path(f'/proc/{s.compositor()}/fd/{sentinels["file"]}').exists(), (identity, current))
        t.check('the re-created file was removed unused', not s.file('.widget-handover.json').exists())
    finally:
        s.stop()
    return t


selected = [name for name in cases if not only or name in only]
log(f'reload handover matrix from {origin} to {repo}: {", ".join(selected)}')
for name in selected:
    log(f'--- {name}')
    try:
        cases[name]()
    except Exception as error:
        import traceback; traceback.print_exc()
        results.append((name, f'raised {error!r}', False)); log(f'FAIL  [{name}] raised {error!r}')
failed = [r for r in results if not r[2]]
log(f'\n{len(results) - len(failed)} passed, {len(failed)} failed')
for r in failed: log(f'  FAIL [{r[0]}] {r[1]}')
sys.exit(1 if failed else 0)
