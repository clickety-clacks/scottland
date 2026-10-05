#!/usr/bin/env python3
"""Reload with a spread solve in flight (final.md section 4: part of the headless reload
rehearsal). Runs inside a headless session; argv: ARTIFACTS PLUGIN_SO."""
import importlib.util, shutil, sys, time
from pathlib import Path

spec = importlib.util.spec_from_file_location('spread_test_helpers', Path(__file__).with_name('spread-test.py'))
art = Path(sys.argv[1]).resolve(); art.mkdir(parents=True, exist_ok=True)
plugin = Path(sys.argv[2]).resolve()
# Reuse the helpers of spread-test.py without running its scenarios.
source = Path(__file__).with_name('spread-test.py').read_text().split('\ntry:\n', 1)[0]
sys.argv = [sys.argv[0], str(art)]
helpers = {}
exec(compile(source, 'spread-test.py', 'exec'), helpers)
h = type('h', (), helpers)
ipc, check, wait, geometry, layout, spread = h.ipc, h.check, h.wait, h.geometry, h.layout, h.spread

reloads = 0
def reload():
    global reloads
    reloads += 1
    fresh = art / f'libscottland-reload-{reloads}-{time.time_ns()}.so'
    shutil.copy(plugin, fresh)
    plugins = ipc('wayfire/get-config-option', {'option': 'core/plugins'})['value']
    swapped = ' '.join(str(fresh) if p == 'scottland' or '/libscottland' in p else p for p in plugins.split())
    ipc('wayfire/set-config-options', {'core/plugins': swapped})
    time.sleep(2)

def alive():
    return len(h.views()) > 0 and 'solves' in spread()

try:
    ipc('wayfire/set-config-options', {'scottland/sounds': False, 'scottland/window_hold_delay': 500,
        'scottland/window_avoidance_always': False, 'scottland/hint_avoidance_always': False,
        'output:HEADLESS-1/mode': '2560x1440@60000'})
    time.sleep(1)
    ids = [h.launch(f'Reload{i}') for i in range(10)]
    S = ids[0]
    scene = [(ids[1], 60, 80, 600, 400), (ids[2], 60, 520, 600, 400), (ids[3], 1900, 80, 600, 400),
             (ids[4], 1900, 520, 600, 400), (ids[5], 900, 100, 500, 350), (ids[6], 1200, 300, 500, 350),
             (ids[7], 950, 800, 500, 350), (ids[8], 1300, 900, 500, 350), (ids[9], 60, 980, 600, 400),
             (S, 830, 370, 900, 700)]
    h.setup(scene, S)
    before = {i: geometry(i) for i in ids}

    # 1. A keyboard solo whose solve is still running when the plugin is swapped.
    ipc('scottland/spread-state', {'slow': True})
    h.alt(True); h.press_hint(S, hold=.75)
    running = wait(lambda: (lambda st: st['running'] and st['running_slices'] > 3 and st)(spread()), 3, 'solve in flight')
    check(running['running'], f'reload: a solve is in flight ({running["running_slices"]} slices so far)')
    reload()
    h.key('LEFTALT', False); time.sleep(.5)
    check(alive(), 'reload with a solve in flight: the session survives and the new plugin answers')
    check(not spread()['running'], 'reload: the new plugin starts with no solve running')
    after = {i: geometry(i) for i in ids}
    check(after == before, 'reload: the cancelled solve applied nothing (no half-applied layout)',
          str({i: (before[i], after[i]) for i in ids if before[i] != after[i]}))
    h.shot('reload-solve.png')

    # 2. The new plugin solos normally.
    ipc('scottland/spread-state', {'slow': False})
    h.setup(scene, S)
    n = spread()['solves']
    h.alt(True); h.press_hint(S, hold=.75); h.alt(False)
    result = h.wait_solve(n)
    check(result['purpose'] == 'solo' and result['status'] in ('clear', 'overlap: search exhausted'),
          f'after the reloads a solo works ({result["status"]})')
    h.shot('reload-after.png')
finally:
    for c in h.clients: c.terminate()
    print(f"{helpers['passed']} passed, {helpers['failed']} failed", flush=True)
sys.exit(1 if helpers['failed'] else 0)
