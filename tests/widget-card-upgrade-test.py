#!/usr/bin/env python3
"""Upgrading the card package under a docked card leaves the card and its app open (WG5).

Run through tests/widget-card-upgrade-test.sh in a private headless --widgets session:
  widget-card-upgrade-test.py PACKAGE ARTIFACTS
PACKAGE is a scratch copy of the card package, found first on SCOTTLAND_WIDGET_PATH.

A package upgrade rewrites the card's shell.qml in place, the way pacman does: unlink, then
write the new file. A card that hot-reloaded on that could replace its window, and Scottland
takes a docked card's window going away as the user closing the widget, which closes its app.
The upgrade adds a property to the card's window, as the upgrade that closed apps did: a card
that hot-reloads a change that small to its structure (a comment, identical bytes) keeps its
window, and would not show the failure.
After the upgrade the test holds for longer than a reload takes, watching for the app or the
card to go; then the app's client must still be running, both windows must be the same ones,
and the card must look as it did. The running card keeps its old code until it is relaunched,
so it must still work with the upgraded Scottland: after the reload replaces the widget service,
Super+M must still collapse and expand it.
"""
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

import gi
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf

if os.environ.get('SCOTTLAND_TEST_MODEL') != '1':
    sys.exit('requires the isolated headless harness')

spec = importlib.util.spec_from_file_location('widget_input', Path(__file__).with_name('widget-input-test.py'))
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)
package, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
out.mkdir(parents=True, exist_ok=True)
title = 'card-upgrade'
# Quickshell reloads within a second of a change to a file it watches.
HOLD = 5

checks = []
def check(name, ok, detail=None):
    checks.append({'check': name, 'ok': bool(ok), 'detail': detail})
    print(('PASS ' if ok else 'FAIL ') + name + ' ' + json.dumps(detail), flush=True)


def card_pixels(name, frame):
    path = out/(name + '.png')
    subprocess.run(['grim', str(path)], check=True, timeout=5)
    image = GdkPixbuf.Pixbuf.new_from_file(str(path))
    stride, n, pixels = image.get_rowstride(), image.get_n_channels(), image.get_pixels()
    # Frames are fractional; take every whole pixel inside the card. The halo moves, and shows
    # through the card's rounded corners and badge room, so leave out a corner's radius all round.
    inset = 16
    xs = range(max(math.ceil(frame['x']) + inset, 0), min(math.floor(frame['x'] + frame['width']) - inset, image.get_width()))
    ys = range(max(math.ceil(frame['y']) + inset, 0), min(math.floor(frame['y'] + frame['height']) - inset, image.get_height()))
    return [pixels[y*stride + x*n + c] for y in ys for x in xs for c in range(3)]


def card_processes():
    """Running quickshell processes whose command names the scratch package's shell.qml."""
    found = []
    for proc in Path('/proc').iterdir():
        try:
            argv = (proc/'cmdline').read_bytes().split(b'\0')
        except OSError:
            continue
        if argv and argv[0].endswith(b'quickshell') and str(package/'shell.qml').encode() in argv:
            found.append(int(proc.name))
    return found


try:
    t.ipc.call('wayfire/set-config-options', {'scottland/sounds': False})
    t.launch(title, rail='right')
    process = t.owned[-1][1]
    app, card = t.app(title), t.card(title)
    if not card_processes():
        raise AssertionError('the card did not start from the scratch package ' + str(package))
    t.wait_for(lambda: t.ipc.call('scottland/layout-state')['widget_transition_count'] == 0)
    before = card_pixels('before', card['frame'])

    source = package/'shell.qml'
    code = source.read_bytes()
    window = b'FloatingWindow {\n'
    if window not in code:
        raise AssertionError('the card no longer opens with ' + window.decode().strip())
    upgraded = code.replace(window, window + b'    readonly property int upgradeRoom: 6\n', 1)
    source.unlink()
    source.write_bytes(upgraded)

    # An intended hold: the failure is the app or the card going, which can only be seen by
    # waiting for it.
    deadline = time.monotonic() + HOLD
    while time.monotonic() < deadline:
        if process.poll() is not None or not t.app(title) or not t.card(title): break
        time.sleep(.05)

    after_app, after_card = t.app(title), t.card(title)
    check('the app is still running after the upgrade', process.poll() is None,
          {'exit': process.poll()})
    check('the app keeps its window', after_app is not None and after_app['id'] == app['id'],
          {'before': app['id'], 'after': after_app and after_app['id']})
    check('the docked card keeps its window', after_card is not None and after_card['id'] == card['id'],
          {'before': card['id'], 'after': after_card and after_card['id']})
    if after_card is not None:
        t.wait_for(lambda: t.ipc.call('scottland/layout-state')['widget_transition_count'] == 0)
        after = card_pixels('after', after_card['frame'])
        changed = sum(1 for a, b in zip(before, after) if abs(a - b) > 8)
        check('the card looks as it did before the upgrade',
              len(after) == len(before) and changed <= len(before) // 100,
              {'channels_changed': changed, 'channels': len(before)})
    else:
        check('the card looks as it did before the upgrade', False, 'the card is gone')

    # The reload after an upgrade replaces the widget service that writes the card's state (WG5);
    # stamp the running one as older code so the reload replaces it here too.
    bus_pid_file = Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}'),
                        'scottland', os.environ['WAYLAND_DISPLAY'] + '.widget-bus.pid')
    old_bus = bus_pid_file.read_text().split('\n')[0]
    bus_pid_file.write_text(old_bus + '\nfingerprint-of-older-code\n')
    subprocess.run([os.environ['SCOTTLAND_HOOKS'] + '/reload.d/08-widget-bus'], check=True, timeout=15)
    new_bus = None
    try:
        new_bus = t.wait_for(lambda: (p := bus_pid_file.read_text().split('\n')[0]) != old_bus
                             and Path('/proc', p).exists() and not Path('/proc', old_bus).exists() and p)
    except AssertionError:
        pass
    check('the reload replaces the widget service under the running card', new_bus is not None,
          {'old': old_bus, 'new': new_bus})

    # The card still running its old code still follows the upgraded Scottland: a real Super+M
    # tap collapses it to the square around its icon, and expanding again restores its width.
    collapsed = expanded = None
    if t.card(title):
        try:
            t.tap_mode_key()
            collapsed = t.wait_for(lambda: (c := t.card(title)) and abs(c['frame']['width'] - 96) < 1 and c)
            t.set_widget_mode('expanded')
            expanded = t.wait_for(lambda: (c := t.card(title)) and c['frame']['width'] > 120 and c)
        except AssertionError as e:
            print('card did not follow Super+M: ' + str(e), flush=True)
    check('the upgraded card still collapses and expands with Super+M',
          collapsed is not None and expanded is not None and expanded['id'] == card['id'],
          {'collapsed_width': collapsed and collapsed['frame']['width'],
           'expanded_width': expanded and expanded['frame']['width']})
finally:
    t.cleanup()
    (out/'checks.json').write_text(json.dumps(checks, indent=2))

passed = sum(c['ok'] for c in checks)
print(f'{passed}/{len(checks)} checks passed', flush=True)
sys.exit(0 if checks and passed == len(checks) else 1)
