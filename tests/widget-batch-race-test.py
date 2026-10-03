#!/usr/bin/env python3
"""Concurrent large-window widget handoffs with avoidance and attention."""
import importlib.util
import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

if os.environ.get('SCOTTLAND_TEST_MODEL') != '1':
    sys.exit('requires the isolated headless harness')

spec = importlib.util.spec_from_file_location('widget_input', Path(__file__).with_name('widget-input-test.py'))
t = importlib.util.module_from_spec(spec); spec.loader.exec_module(t)
out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
ids = []

def tap(name): t.key(name, True); t.key(name, False)
def hold():
    t.key('LEFTALT', True); t.wait_for(lambda: t.ipc.call('scottland/hints')['active'])
def hint(window):
    return next(h['hint'].upper() for h in t.ipc.call('scottland/hints')['hints'] if h['window'] == window)
def links(): return {int(w['id']): w for w in t.widgets()}

try:
    t.ipc.call('wayfire/set-config-options', {'scottland/sounds': False})
    for i in range(8):
        title = 'batch-large-' + str(i)
        p = subprocess.Popen(['foot', '-c', '/dev/null', '-T', title, '-W', '120x38', 'sleep', '600'],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        t.owned.append((title, p)); ids.append(t.wait_for(lambda: t.app(title))['id'])
        time.sleep(.3)
    for round in range(6):
        started = time.monotonic()
        print(f'ROUND {round}: 6 concurrent handoffs, 8 large windows', flush=True)
        for window in ids[:6]:
            t.ipc.call('scottland/attention', {'window': window, 'attention': True, 'source': 'batch'})
        # Large-stack exposure can delay IPC-fed presses past 300ms. This live
        # supported setting ensures the fixture requests a rail even under load.
        t.ipc.call('wayfire/set-config-options', {'scottland/window_double_tap_delay': 3000})
        hold()
        for window in ids[:6]:
            name = hint(window); tap(name); time.sleep(.02); tap(name)
        t.key('LEFTALT', False)
        print('requested links=' + repr([(i, w['widget_view']) for i, w in links().items()]), flush=True)
        t.wait_for(lambda: len(links()) == 6 and all(w['widget_view'] > 0 for w in links().values()), timeout=8)
        t.ipc.call('wayfire/set-config-options', {'scottland/window_double_tap_delay': 300})
        if round % 3 == 0: t.toggle()
        for window in ids[:6]:
            t.ipc.call('scottland/attention', {'window': window, 'attention': True, 'source': 'batch'})
        time.sleep((0, .08, .3)[round % 3])
        hold()
        for window in ids[:6]:
            name = hint(window); tap(name); time.sleep(.31); tap(name)
        t.key('LEFTALT', False)
        t.wait_for(lambda: not links())
        (out / 'last-batch.json').write_text(json.dumps(t.ipc.call('scottland/layout-state'), indent=2))
        print(f'PASS batch {round}: mapped and restored 6 cards in {time.monotonic() - started:.2f}s', flush=True)
    subprocess.run(['grim', str(out / 'batch-final.png')], check=True, timeout=5)
except Exception:
    # Preserve the original timeout/disconnect if collecting extra state fails.
    with contextlib.suppress(Exception):
        (out / 'batch-failure.json').write_text(json.dumps({'links': links(),
            'layout': t.ipc.call('scottland/layout-state'), 'hints': t.ipc.call('scottland/hints')}, indent=2))
    raise
finally:
    for _, p in t.owned:
        if p.poll() is None: p.terminate()
