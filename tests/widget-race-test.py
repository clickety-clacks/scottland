#!/usr/bin/env python3
"""Race rail previews, cancellation, card mapping and re-entry using real input."""
import importlib.util
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
terminal = sys.argv[2] if len(sys.argv) > 2 else 'foot'
title = 'race-attention-' + terminal
p = subprocess.Popen(([terminal, '-c', '/dev/null', '-T', title, '-W', '90x28'] if terminal == 'foot' else
                      [terminal, '--title=' + title, '--window-width=90', '--window-height=28']) + ['-e', 'sleep', '600'],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
t.owned.append((title, p)); t.wait_for(lambda: t.app(title)); time.sleep(.5)
window = t.app(title)['id']
t.launch('race-background', rail=None)

def tap(name): t.key(name, True); t.key(name, False)
def wait_hint():
    t.key('LEFTALT', True)
    t.wait_for(lambda: t.ipc.call('scottland/hints')['active'])
    return next(h['hint'].upper() for h in t.ipc.call('scottland/hints')['hints'] if h['window'] == window)
def start(view):
    f = view.get('scene_frame', view['frame'])
    t.move(f['x'] + f['width']/2, f['y'] + f['height']/2)
    t.key('LEFTMETA', True)
    t.ipc.call('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'press'})
def end():
    t.ipc.call('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'release'})
    t.key('LEFTMETA', False)
def marking():
    t.ipc.call('window-rules/focus-view', {'id': t.app('race-background')['id']})
    t.ipc.call('scottland/attention', {'window': window, 'attention': True, 'source': 'race'})

try:
    t.ipc.call('wayfire/set-config-options', {'scottland/sounds': False})
    for i in range(72):
        delay = (0, .02, .08, .2, .4, .8)[i % 6]
        x = 6 if i % 2 else t.screen['width'] - 6
        print(f'ROUND {i}: {terminal}, map delay={delay}', flush=True)
        # A preview starts; Esc dismisses it before or after it maps, then re-enter.
        marking(); start(t.app(title)); t.move(x, 330); time.sleep(delay)
        tap('ESC'); end()
        t.wait_for(lambda: not t.widgets())
        name = wait_hint(); tap(name); time.sleep(.02); tap(name)
        t.key('LEFTALT', False)
        t.wait_for(lambda: t.card(title) and not t.card(title)['preview'])
        time.sleep(delay)
        marking()
        # Undock using its real hint during the entry morph; no settled-card wait.
        name = wait_hint()
        for _ in range(3):
            tap(name); time.sleep(.32)
            if not t.widgets(): break
        t.key('LEFTALT', False)
        t.wait_for(lambda: not t.widgets())
        if i == 23:
            # Toggle collapsed mode with a docked widget in the next round.
            name = wait_hint(); tap(name); time.sleep(.02); tap(name); t.key('LEFTALT', False)
            t.wait_for(lambda: t.widgets())
            t.toggle(); time.sleep(.1)
            name = wait_hint()
            for _ in range(3):
                tap(name); time.sleep(.32)
                if not t.widgets(): break
            t.key('LEFTALT', False)
            t.wait_for(lambda: not t.widgets())
        (out / 'last-race.json').write_text(json.dumps(t.ipc.call('scottland/layout-state'), indent=2))
        print(f'PASS race lifecycle {i}', flush=True)
finally:
    for _, p in t.owned:
        if p.poll() is None: p.terminate()
