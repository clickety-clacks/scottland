#!/usr/bin/env python3
"""GO26: breath keyframes follow a ceiling-and-scale rule, and the exact path explains itself.

Run in a private headless session on the test machine:
  tests/headless.sh run python3 tests/goo-breath-keys-test.py ARTIFACTS

One window asks for attention beside a focused one. Settings set the size of the breath's
swing (shore travel = 0.643 x thickness x swell x output scale, in device pixels):
  - a small swing uses only the keys it needs at half a pixel;
  - the shipped swing likewise;
  - a huge swing uses the ceiling, with the spacing widened to cover it, and stays on
    keyframes (no exact path);
  - the exact path is used only when keyframes are switched off, in the test override,
    or when the second cache layer cannot be allocated, and goo-state says which; the
    log says so once.
Also counts the full-shader work at the ceiling against the exact path.
"""
import json, math, os, signal, socket, struct, subprocess, sys, time
from pathlib import Path

out = Path(sys.argv[1]).resolve(); out.mkdir(parents=True, exist_ok=True)
assert os.environ.get('SCOTTLAND_TEST_MODEL') == '1', 'private headless session required'
sock = socket.socket(socket.AF_UNIX); sock.connect(os.environ['WAYFIRE_SOCKET'])

def ipc(method, data=None):
    payload = json.dumps({'method': method, 'data': data or {}}).encode()
    sock.sendall(struct.pack('<I', len(payload)) + payload)
    def read(n):
        b = b''
        while len(b) < n:
            chunk = sock.recv(n-len(b))
            if not chunk: raise RuntimeError('compositor disconnected')
            b += chunk
        return b
    result = json.loads(read(struct.unpack('<I', read(4))[0]))
    if isinstance(result, dict) and 'error' in result: raise RuntimeError(result)
    return result
def view(title): return next(v for v in ipc('scottland/layout-state')['views'] if v['title'] == title)
def state(data=None): return ipc('scottland/goo-state', data)['screens'][0]
def options(**values): ipc('wayfire/set-config-options', {'scottland/'+k: v for k, v in values.items()})
def settle(what):
    time.sleep(1)
    for _ in range(600):
        s = state()
        if s['sleeping'] and not s.get('breath_loose') and s['breath_damage'] and not s.get('water_running'): break
        time.sleep(.1)
    else: raise AssertionError('goo did not settle with a breathing strip: ' + what + ' ' + json.dumps(
        {k: state().get(k) for k in ('sleeping', 'breath_loose', 'energy', 'wave_energy', 'dye_energy', 'last_wake', 'wakes', 'tighten_ms')} |
        {'strips': len(state()['breath_damage'])}))
    time.sleep(.6)   # a few breath ticks, so the key plan in goo-state is this state's
    return state()

checks = []
def check(name, ok, detail=None):
    checks.append((name, bool(ok)))
    print(('PASS ' if ok else 'FAIL ') + name + ('' if ok or detail is None else ' ' + json.dumps(detail)), flush=True)
def brief(s): return {k: s.get(k) for k in ('breath_keys', 'breath_key_spacing', 'breath_key_ceiling', 'breath_keyframes_active', 'breath_exact_reason')}
def travel(thickness, swell): return .45*thickness*swell/.7

clients = []
try:
    ipc('wayfire/set-config-options', {'output:HEADLESS-1/mode': '1600x1000@60000'})
    time.sleep(1)
    for title in ('keys-breather', 'keys-focus'):
        clients.append(subprocess.Popen(['foot', '-c', '/dev/null', '-o', 'resize-by-cells=no', '-T', title, 'sleep', '900'],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
        time.sleep(.5)
    time.sleep(1.5)
    ipc('window-rules/configure-view', {'id': view('keys-breather')['id'], 'geometry': {'x': 420, 'y': 180, 'width': 620, 'height': 460}})
    ipc('window-rules/configure-view', {'id': view('keys-focus')['id'], 'geometry': {'x': 820, 'y': 470, 'width': 420, 'height': 320}})
    time.sleep(.5)
    f = view('keys-focus')['frame']
    ipc('stipc/move_cursor', {'x': round(f['x']+f['width']*.7), 'y': round(f['y']+f['height']*.8)})
    for mode in ('press', 'release'): ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': mode})
    time.sleep(.5)
    ipc('scottland/attention', {'window': view('keys-breather')['id'], 'attention': True, 'source': 'keys-test'})

    s = settle('shipped')
    ceiling = s['breath_key_ceiling']
    need = math.ceil(travel(13, .7)/.5)
    check('shipped swing: the keys it needs at half a pixel, no more', s['breath_keyframes_active'] and
          s['breath_keys']-1 == need and s['breath_key_spacing'] <= .5001 and need < ceiling, brief(s))

    options(goo_swell=0.2)
    s = settle('small swing')
    need = math.ceil(travel(13, .2)/.5)
    check('a small swing uses few keys', s['breath_keyframes_active'] and s['breath_keys']-1 == need and need <= 5 and
          s['breath_key_spacing'] <= .5001 and s['breath_exact_reason'] == '', brief(s))

    options(goo_swell=2.0, goo_thickness=40.0)   # the sliders' maxima: a 51-pixel swing
    s = settle('huge swing')
    swing = travel(40, 2.0)
    check('a huge swing uses the ceiling, with the spacing widened to cover it', s['breath_keys']-1 == ceiling and
          abs(s['breath_key_spacing']-swing/ceiling) < .01 and s['breath_key_spacing'] > .5, brief(s) | {'swing': swing})
    check('and stays on keyframes: no exact fallback for the key count',
          s['breath_keyframes_active'] and s['breath_exact_reason'] == '', brief(s))
    # Work at the ceiling against the exact path, one breath each (5 s).
    def work(seconds=5):
        a = state(); time.sleep(seconds); b = state()
        return {k: b[k]-a[k] for k in ('surface_pixels', 'breath_refreshes', 'breath_ticks', 'steps')}
    at_ceiling = work()
    state({'breath_exact': True}); time.sleep(.6)
    s = state()
    check('the test override is reported as the reason for the exact path',
          not s['breath_keyframes_active'] and s['breath_exact_reason'] == 'test override', brief(s))
    exact = work()
    state({'breath_exact': False}); time.sleep(.6)
    print(json.dumps({'one breath at the ceiling': at_ceiling, 'one breath exact': exact, 'ceiling': ceiling}), flush=True)
    check('at the ceiling the breath still shades fewer pixels than the exact path',
          at_ceiling['surface_pixels'] < exact['surface_pixels'] and at_ceiling['steps'] == 0 and
          at_ceiling['breath_refreshes'] <= 2*ceiling+4, {'ceiling': at_ceiling, 'exact': exact})

    options(goo_breath_keys=False); time.sleep(1)
    s = state()
    check('switching keyframes off is reported as the reason', not s['breath_keyframes_active'] and
          'switched off' in s['breath_exact_reason'], brief(s))
    options(goo_breath_keys=True); time.sleep(1)
    check('and switching them on clears it', state()['breath_keyframes_active'] and state()['breath_exact_reason'] == '', brief(state()))

    state({'breath_layer_fail': True}); time.sleep(1)
    s = state()
    check('a failed layer allocation is reported as the reason', not s['breath_keyframes_active'] and
          'could not be allocated' in s['breath_exact_reason'], brief(s))
    time.sleep(2)
    state({'breath_layer_fail': False}); time.sleep(1)
    check('and keyframes return when the layer can be allocated', state()['breath_keyframes_active'], brief(state()))
    state({'surface_cache_fail': True}); time.sleep(1)
    s = state()
    check('an unavailable surface cache is reported as the reason', not s['breath_keyframes_active'] and
          s['breath_exact_reason'] == 'the surface cache is unavailable', brief(s))
    state({'surface_cache_fail': False}); time.sleep(1)
    check('and keyframes return when the surface cache can be allocated', state()['breath_keyframes_active'] and
          state()['breath_exact_reason'] == '', brief(state()))

    # The pictures: a held breath drawn from keys against the exact surface, after the
    # second layer has been released and allocated again (its texture name may be reused).
    import gi
    gi.require_version('GdkPixbuf', '2.0')
    from gi.repository import GdkPixbuf
    def shot(name, hold, exact):
        state({'breath_hold': hold, 'breath_exact': exact}); time.sleep(.5)
        subprocess.run(['grim', str(out/name)], check=True)
        return GdkPixbuf.Pixbuf.new_from_file(str(out/name))
    def far(a, b, levels):
        pa, pb, ch = a.get_pixels(), b.get_pixels(), a.get_n_channels()
        return sum(1 for i in range(0, len(pa), ch) if max(abs(pa[i+c]-pb[i+c]) for c in range(3)) >= levels)
    for label, opts in (('maxima', {}), ('shipped', dict(goo_swell=.7, goo_thickness=13.))):
        if opts: options(**opts); settle('pictures '+label)
        keys = state()['breath_key_values']
        worst = {}
        for hold in (0., keys[len(keys)//2], 1.):
            e, k = shot(f'{label}-exact-{hold:.3f}.png', hold, True), shot(f'{label}-keys-{hold:.3f}.png', hold, False)
            worst[hold] = far(e, k, 16)
        check(f'{label}: at key values the keyframed picture is the exact one (pixels 16+ levels off)',
              max(worst.values()) < 200, worst)
    state({'breath_hold': -1, 'breath_exact': False})

    log = Path(os.environ['SCOTTLAND_TEST_STATE']).parent/'wayfire.log'
    lines = [l for l in log.read_text(errors='replace').splitlines() if 'breathing uses the exact path' in l]
    reasons = [l.split('exact path: ')[1] for l in lines]
    (out/'exact-log.txt').write_text('\n'.join(lines)+'\n')
    check('the log says why, once per change (one line per switch to the exact path, not one per tick)',
          len([r for r in reasons if 'test override' not in r]) == 3 and any('test override' in r for r in reasons) and any('switched off' in r for r in reasons)
          and any('could not be allocated' in r for r in reasons) and any('surface cache is unavailable' in r for r in reasons), reasons)
    failed = [n for n, ok in checks if not ok]
    print(f'RESULT {len(checks)-len(failed)} passed, {len(failed)} failed', flush=True)
    sys.exit(1 if failed else 0)
finally:
    for c in clients:
        try: os.killpg(c.pid, signal.SIGTERM)
        except ProcessLookupError: pass
    sock.close()
