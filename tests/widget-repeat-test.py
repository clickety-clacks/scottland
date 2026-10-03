#!/usr/bin/env python3
"""Repeated rail adoption with attention and real stipc input; IPC has a 5s bound.

Run inside an empty headless --widgets session. The caller owns/cleans the session.
"""
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
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)
out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
title = 'repeat-attention-terminal'
t.launch(title, rail=None)
window = t.app(title)['id']

def attention():
    t.ipc.call('scottland/attention', {'window': window, 'attention': True, 'source': 'repeat-test'})

def tap(name):
    t.key(name, True); t.key(name, False)

def hint():
    t.key('LEFTALT', True)
    t.wait_for(lambda: t.ipc.call('scottland/hints')['active'])
    return next(h['hint'].upper() for h in t.ipc.call('scottland/hints')['hints'] if h['window'] == window)

def dock(path, rail):
    attention()
    if path == 'drag':
        t.drag_begin(t.app(title), 6 if rail == 'left' else t.screen['width'] - 6, 320)
        # Renew while the drag preview/card is starting too.
        attention()
        t.drag_end()
    else:
        name = hint()
        if path == 'double':
            tap(name); time.sleep(.04); tap(name)
        else:
            # A slow start-relative cycle must eventually reach the rail.
            for _ in range(4):
                tap(name)
                time.sleep(.34)
                if t.widgets(): break
        t.key('LEFTALT', False)
    t.wait_for(lambda: t.card(title) and not t.card(title)['preview'])

def restore(path):
    t.wait_for(lambda: t.ipc.call('scottland/layout-state')['widget_transition_count'] == 0)
    time.sleep(.15)
    if path == 'click':
        f = t.card(title)['frame']
        t.move(f['x'] + f['width']/2, f['y'] + f['height']/2)
        for mode in ('press', 'release'):
            t.ipc.call('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': mode})
    elif path == 'hint':
        name = hint()
        for _ in range(3):
            tap(name); time.sleep(.34)
            if not t.widgets(): break
        t.key('LEFTALT', False)
    else:
        t.drag_begin(t.card(title), t.screen['width']/2, 320)
        t.drag_end()
    t.wait_for(lambda: not t.widgets() and t.app(title) and not t.app(title)['hidden'])

try:
    t.ipc.call('wayfire/set-config-options', {'scottland/sounds': False})
    for i in range(36):
        path = ('drag', 'double', 'cycle')[i % 3]
        opening = ('click', 'drag', 'hint')[(i // 3) % 3]
        delay = (0, .1, .6, 2.6)[(i // 9) % 4]
        print(f'ROUND {i}: {path} -> {opening}, delay={delay}', flush=True)
        dock(path, 'left' if i % 2 else 'right')
        time.sleep(delay)
        if i % 9 == 0:
            subprocess.run(['grim', str(out / f'docked-{i}.png')], check=True, timeout=5)
        restore(opening)
        time.sleep(delay)
        # Query on every round, including during the previous handoff's animation.
        (out / 'last-state.json').write_text(json.dumps(t.ipc.call('scottland/layout-state'), indent=2))
        print(f'PASS repeated widget lifecycle {i}', flush=True)
finally:
    # A timeout must escape without cleanup trying more IPC against a hung compositor.
    for _, process in t.owned:
        if process.poll() is None: process.terminate()
