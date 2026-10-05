#!/usr/bin/env python3
"""Coalesced model publication (docs/desktop-model.md DM2/DM5/DM8, main-loop design 2.1). Run on a
test host: starts and stops its own headless session.

A change marks the model dirty and a timer publishes at most once per 8 ms (always with a trailing
flush); replies that carry the model flush first, after their handler's last mutation, so a reply
never pairs new state with an old version."""
import json, math, os, select, socket, struct, subprocess, sys, time
from pathlib import Path

repo = Path(__file__).resolve().parents[1]
work = repo / 'build/model-publish'; work.mkdir(parents=True, exist_ok=True)
runtime = Path(os.environ.get('XDG_RUNTIME_DIR') or f'/run/user/{os.getuid()}') / 'scottland'
env = dict(os.environ, SCOTTLAND_HEADLESS_DIR=str(work / 'hl'), TMPDIR=str(work))
fails = 0
def check(what, ok, detail=''):
    global fails
    print(f"{'PASS' if ok else 'FAIL'}  {what}" + ('' if ok else f'  {detail}'), flush=True)
    fails += not ok

class Conn:
    def __init__(self, path):
        self.s = socket.socket(socket.AF_UNIX); self.s.connect(path); self.s.settimeout(5); self.buf = b''
    def send(self, method, data=None):
        b = json.dumps({'method': method, 'data': data or {}}).encode()
        self.s.sendall(struct.pack('<I', len(b)) + b)
    def read(self, timeout=5):
        self.s.settimeout(timeout)
        def need(n):
            while len(self.buf) < n:
                more = self.s.recv(65536)
                if not more: raise EOFError
                self.buf += more
        need(4); n = struct.unpack('<I', self.buf[:4])[0]; need(4 + n)
        msg, self.buf = self.buf[4:4 + n], self.buf[4 + n:]
        return json.loads(msg)
    def call(self, method, data=None):
        self.send(method, data); return self.read()
    def pending(self, wait):
        if self.buf: return True
        return bool(select.select([self.s], [], [], wait)[0])

subprocess.run([str(repo / 'tests/headless.sh'), 'stop'], env=env, capture_output=True)
subprocess.run([str(repo / 'tests/headless.sh'), 'start', '--widgets'], env=env, check=True, capture_output=True)
display = (work / 'hl' / 'display').read_text().strip()
entries = (runtime / f'{display}.env').read_bytes().split(b'\0')
path = next(e.split(b'=', 1)[1] for e in entries if e.startswith(b'WAYFIRE_SOCKET=')).decode()
apps = []
try:
    for title in ('pub-a', 'pub-b'):
        apps.append(subprocess.Popen([str(repo / 'tests/headless.sh'), 'run', 'foot', '-T', title, 'sh', '-c', 'exec sleep 600'],
                                     env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
        time.sleep(1.2)
    c = Conn(path)
    views = c.call('scottland/layout-state')['views']
    a = next(v['id'] for v in views if v['title'] == 'pub-a')
    time.sleep(.5)

    # Subscribe: the initial snapshot is current.
    sub = Conn(path)
    first = sub.call('scottland/subscribe', {'slice': 'attention'})
    now = c.call('scottland/desktop-model', {'slice': 'attention'})
    check('subscribe: the initial snapshot carries the current version', first.get('version') == now.get('version'), (first.get('version'), now.get('version')))

    # A reply that mutates: new state with the next version, before the trailing timer could fire.
    before = c.call('scottland/desktop-model')['version']
    reply = c.call('scottland/attention', {'window': a, 'attention': True, 'source': 'pubtest'})
    w = next((x for x in reply.get('windows', []) if x.get('id') == a), {})
    check('attention reply has the new state', 'pubtest' in json.dumps(w), w)
    check('... and a version one higher than before', reply.get('version') == before + 1, (before, reply.get('version')))
    event = sub.read(2)
    check('subscribers get that same version as an event', event.get('version') == reply.get('version'), event.get('version'))

    # Nothing changed: a barrier does not publish.
    v1 = c.call('scottland/desktop-model')['version']; v2 = c.call('scottland/desktop-model')['version']
    check('a flush with nothing changed keeps the version', v1 == v2, (v1, v2))

    # widget-traits returns no version: the event it causes carries the new state and the next version.
    b = next(v for v in c.call('scottland/layout-state')['views'] if v['title'] == 'pub-b')
    f = b['frame']; width = c.call('window-rules/list-outputs')[0]['geometry']['width']
    c.call('stipc/move_cursor', {'x': f['x'] + f['width'] / 2, 'y': f['y'] + f['height'] / 2})
    c.call('stipc/feed_key', {'key': 'KEY_LEFTMETA', 'state': True}); c.call('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'press'})
    for i in range(1, 11):
        c.call('stipc/move_cursor', {'x': f['x'] + f['width'] / 2 + (width - 8 - f['x'] - f['width'] / 2) * i / 10, 'y': f['y'] + f['height'] / 2}); time.sleep(.03)
    c.call('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'release'}); c.call('stipc/feed_key', {'key': 'KEY_LEFTMETA', 'state': False})
    link = None
    for _ in range(40):
        link = next((w for w in c.call('scottland/widgets')['widgets'] if w['window'] == b['id'] and w['widget_view'] > 0), None)
        if link: break
        time.sleep(.2)
    check('a widget to change traits on', link is not None)
    if link:
        time.sleep(1)
        sub2 = Conn(path); s0 = sub2.call('scottland/subscribe', {'slice': 'widgets'})
        reply = c.call('scottland/widget-traits', {'window': b['id'], 'unit': link['widget_unit'], 'touch_drag': not link['touch_drag'],
                                                   'desktop': link.get('desktop', ''), 'name': 'pubtest-name', 'icon': link.get('icon', '')})
        check('widget-traits succeeds and returns no version', 'error' not in reply and 'version' not in reply, reply)
        got = sub2.read(2)
        mine = next((w for w in got.get('widgets', []) if w.get('window') == b['id']), {})
        check('its event carries the new state and the next version',
              got.get('version') == s0.get('version') + 1 and mine.get('name') == 'pubtest-name', (s0.get('version'), got.get('version'), mine))

    # A drag publishes at most once per 8 ms, not per motion event.
    drain_until = time.monotonic() + .3
    while time.monotonic() < drain_until and sub.pending(.05): sub.read()
    v = c.call('scottland/layout-state')['views']; f = next(x['frame'] for x in v if x['id'] == a)
    cx, cy = f['x'] + f['width'] / 2, f['y'] + f['height'] / 2
    c.call('stipc/move_cursor', {'x': cx, 'y': cy}); c.call('stipc/feed_key', {'key': 'KEY_LEFTMETA', 'state': True})
    c.call('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'press'})
    c.call('scottland/loop-stats', {'reset': True})
    sent = 0; t0 = time.monotonic()
    while time.monotonic() - t0 < 2:
        for k in range(10):
            c.send('stipc/move_cursor', {'x': cx + 300 * math.sin(sent / 40), 'y': cy + 200 * math.cos(sent / 40)}); sent += 1
        for k in range(10): c.read()
    stats = c.call('scottland/loop-stats')
    c.call('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'release'}); c.call('stipc/feed_key', {'key': 'KEY_LEFTMETA', 'state': False})
    timer = stats['scopes'].get('publish_timer', {}).get('calls', 0)
    flushes = stats['scopes'].get('publish_model', {}).get('calls', 0)
    elapsed = time.monotonic() - t0
    check(f'a drag publishes at most once per 8 ms: {flushes} flushes for {sent} motion events in {elapsed:.1f} s',
          flushes <= elapsed * 1000 / 8 + 5 and timer <= flushes + 1 and flushes < sent, stats['scopes'].get('publish_model'))
    # After the motion stops, the trailing timer publishes the last change by itself: the next
    # reply needs no flush of its own (its version is the last event's).
    time.sleep(1.5)
    whole = Conn(path); first = whole.call('scottland/subscribe', {'slice': 'desktop'})
    time.sleep(.3)
    last = first
    while whole.pending(.2): last = whole.read()
    now = c.call('scottland/desktop-model')['version']
    check('the trailing flush publishes the last change without a barrier', last is not None and last.get('version') == now,
          (last and last.get('version'), now))
finally:
    for p in apps:
        try: os.killpg(p.pid, 15)
        except ProcessLookupError: pass
    subprocess.run([str(repo / 'tests/headless.sh'), 'stop'], env=env, capture_output=True)
print('all publication checks passed' if not fails else f'{fails} check(s) failed')
sys.exit(1 if fails else 0)
