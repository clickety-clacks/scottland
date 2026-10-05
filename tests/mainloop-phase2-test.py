#!/usr/bin/env python3
"""Main-loop Phase 2: stop repeated work (docs/main-loop.md; design 2.2-2.7, ML8). Real input in
a headless session it starts and stops. Run on a test host. Publication (2.1) has its own test,
tests/model-publish-test.py."""
import json, math, os, socket, struct, subprocess, sys, time
from pathlib import Path

repo = Path(__file__).resolve().parents[1]
work = repo / 'build/mainloop-phase2'; work.mkdir(parents=True, exist_ok=True)
runtime = Path(os.environ.get('XDG_RUNTIME_DIR') or f'/run/user/{os.getuid()}') / 'scottland'
env = dict(os.environ, SCOTTLAND_HEADLESS_DIR=str(work / 'hl'), TMPDIR=str(work))
fails = 0
def check(what, ok, detail=''):
    global fails
    print(f"{'PASS' if ok else 'FAIL'}  {what}" + ('' if ok else f'  {detail}'), flush=True)
    fails += not ok

subprocess.run([str(repo / 'tests/headless.sh'), 'stop'], env=env, capture_output=True)
apps = []
try:
    subprocess.run([str(repo / 'tests/headless.sh'), 'start'], env=env, check=True, capture_output=True)
    display = (work / 'hl' / 'display').read_text().strip()
    entries = (runtime / f'{display}.env').read_bytes().split(b'\0')
    path = next(e.split(b'=', 1)[1] for e in entries if e.startswith(b'WAYFIRE_SOCKET=')).decode()
    sock = socket.socket(socket.AF_UNIX); sock.connect(path)
    def ipc(method, data=None):
        b = json.dumps({'method': method, 'data': data or {}}).encode()
        sock.sendall(struct.pack('<I', len(b)) + b)
        def read(n):
            out = b''
            while len(out) < n: out += sock.recv(n - len(out))
            return out
        return json.loads(read(struct.unpack('<I', read(4))[0]))
    def key(name, down): ipc('stipc/feed_key', {'key': 'KEY_' + name, 'state': down})
    def pointer(x, y): ipc('stipc/move_cursor', {'x': round(x), 'y': round(y)})
    def button(mode): ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': mode})
    def views(): return ipc('scottland/layout-state')['views']
    def view(title): return next(v for v in views() if v['title'] == title)
    def stats(reset=False): return ipc('scottland/loop-stats', {'reset': reset})
    def scope(s, name): return s['scopes'].get(name, {'calls': 0, 'max_ms': 0, 'outermost': 0})
    apps = []
    def spawn(title):
        apps.append(subprocess.Popen([str(repo / 'tests/headless.sh'), 'run', 'foot', '-T', title, 'sh', '-c', 'exec sleep 600'],
                                     env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
        for _ in range(50):
            found = [v for v in views() if v['title'] == title or (len(title) > 256 and v['title'].startswith(title[:200]))]
            if found: return found[0]
            time.sleep(.1)
        raise AssertionError(f'{title} never mapped')

    for i in range(6):
        spawn(f'p2-{i}')
        ipc('window-rules/configure-view', {'id': view(f'p2-{i}')['id'], 'geometry': {'x': 120 + 140 * i, 'y': 120 + 60 * i, 'width': 420, 'height': 260}})
    time.sleep(1.5)

    # ML8: Window mode entry. The hint font was loaded at init(), so the first entry pays no font miss.
    stats(True)
    key('LEFTALT', True); time.sleep(1.2)
    first = stats(True); key('LEFTALT', False); time.sleep(1)
    key('LEFTALT', True); time.sleep(1.2)
    second = stats(True); key('LEFTALT', False); time.sleep(1)
    raster = scope(first, 'hint_raster')['max_ms']
    # Timing is the latency benchmark's (tests/mainloop-latency-test.sh); here only measured.
    print(f"measured: first Window mode entry: hint_raster max {raster:.2f} ms; entry {scope(first, 'alt_hold')['max_ms']:.2f} ms "
          f"first, {scope(second, 'alt_hold')['max_ms']:.2f} ms again (ML8 target 2 ms, open)", flush=True)

    # 2.2/2.3: no /proc and no palette file read during an Alt hold or at a drag start.
    before = stats(True).get('proc_reads', -1)
    key('LEFTALT', True); time.sleep(1.5); key('RIGHT', True); key('RIGHT', False); time.sleep(.5); key('LEFTALT', False)
    time.sleep(.5)
    f = view('p2-2')['frame']
    pointer(f['x'] + f['width'] / 2, f['y'] + f['height'] / 2); key('LEFTMETA', True); button('press')
    for i in range(20): pointer(f['x'] + f['width'] / 2 + 5 * i, f['y'] + f['height'] / 2); time.sleep(.01)
    button('release'); key('LEFTMETA', False); time.sleep(.5)
    after = stats()
    check('no /proc reads during an Alt hold, Window mode keys and a drag start', after.get('proc_reads', -2) == before, (before, after.get('proc_reads')))
    check('no palette file read during them', scope(after, 'palette_read')['calls'] == 0, after['scopes'].get('palette_read'))

    # 2.3: a theme change still reaches hints (the file's watch reads it once).
    palette = runtime / f'{display}.palette.json'
    old = palette.read_text() if palette.exists() else None
    data = json.loads(old) if old else {}
    data['accent'] = '#12ab34'  # a partial palette must not take the compositor down either
    stats(True)
    tmp = palette.with_suffix('.tmp'); tmp.write_text(json.dumps(data)); os.replace(tmp, palette)
    time.sleep(.5)
    check('a palette file change is read once, when it changes', scope(stats(), 'palette_read')['calls'] == 1, stats()['scopes'].get('palette_read'))
    if old is not None:
        tmp.write_text(old); os.replace(tmp, palette)
    else:
        palette.unlink(missing_ok=True)  # nothing of this test outlives its session

    # 2.4: visual proximity runs once per frame, not per pointer event.
    stats(True)
    n = 0
    for i in range(600):
        pointer(640 + 400 * math.cos(i / 30), 400 + 250 * math.sin(i / 30)); n += 1
    time.sleep(.2)
    s = stats()
    check(f"proximity: {scope(s, 'track_pointer')['calls']} runs for {n} pointer events (once per frame at most)",
          scope(s, 'track_pointer')['calls'] < n / 2, s['scopes'].get('track_pointer'))
    print(f"measured: on_motion max {scope(s, 'on_motion')['max_ms']:.2f} ms", flush=True)

    # 2.4 / A5: a thin, scaled halo is grabbed 10 px outside its edge, before and after this phase.
    ipc('wayfire/set-config-options', {'scottland/goo_thickness': 4.0, 'scottland/goo_reach': 6.0, 'scottland/min_scale': 0.25})
    width = ipc('window-rules/list-outputs')[0]['geometry']['width']
    outcomes = {}
    for gap in (10, 14):
        thin = spawn(f'p2-thin-{gap}')
        ipc('window-rules/configure-view', {'id': thin['id'], 'geometry': {'x': width - 180, 'y': 200, 'width': 300, 'height': 200}})
        time.sleep(2)
        v = view(f'p2-thin-{gap}'); f = v['frame']
        # The left edge's middle: the bottom middle holds the halo's controls.
        x, y = f['x'] - gap, f['y'] + f['height'] / 2
        pointer(x, y); time.sleep(.15); button('press')
        for i in range(1, 13): pointer(x + 65 * i / 12, y + 30 * i / 12); time.sleep(.025)
        button('release'); time.sleep(1)
        outcomes[gap] = (v.get('applied_scale', 0), abs(view(f'p2-thin-{gap}')['frame']['x'] - f['x']) > 15)
        ipc('window-rules/close-view', {'id': thin['id']}); time.sleep(.5)
    check(f"A5: a halo at scale {outcomes[10][0]:.2f} with thickness 4 is grabbed 10 px outside its edge", outcomes[10][1], outcomes)
    check('... and not 14 px outside (beyond the 12 pt target)', not outcomes[14][1], outcomes)

    # 2.6: a settings batch lays the windows out once.
    stats(True)
    ipc('wayfire/set-config-options', {'scottland/center_width': 41.0, 'scottland/rail_width': 2.0, 'scottland/blend_width': 41.0,
                                       'scottland/min_scale': 0.3, 'scottland/max_scale': 1.0})
    time.sleep(.2)
    s = stats()
    check(f"one layout per option batch ({scope(s, 'option_layout')['calls']} option callbacks, {scope(s, 'apply_idle')['calls']} layout)",
          scope(s, 'apply_idle')['calls'] == 1, (s['scopes'].get('option_layout'), s['scopes'].get('apply_idle')))

    # 2.7: a 4,096-character title in the switcher, fonts warm: bounded elision.
    long = spawn('L' * 4096)
    ipc('window-rules/configure-view', {'id': long['id'], 'geometry': {'x': 500, 'y': 200, 'width': 500, 'height': 300}})
    time.sleep(1)
    stats(True)
    key('LEFTALT', True); key('TAB', True); key('TAB', False); time.sleep(.3); key('TAB', True); key('TAB', False); time.sleep(.3)
    key('ESC', True); key('ESC', False); key('LEFTALT', False); time.sleep(.5)
    s = stats()
    sw = scope(s, 'switcher_update')
    check(f"the switcher shows a 4,096-character title ({sw['calls']} updates)", sw['calls'] > 0, sw)
    print(f"measured: switcher update max {sw['max_ms']:.2f} ms with that title (target 2 ms)", flush=True)
finally:
    for p in apps:
        try: os.killpg(p.pid, 15)
        except ProcessLookupError: pass
    subprocess.run([str(repo / 'tests/headless.sh'), 'stop'], env=env, capture_output=True)
print('all phase 2 checks passed' if not fails else f'{fails} check(s) failed')
sys.exit(1 if fails else 0)
