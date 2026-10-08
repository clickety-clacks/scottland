#!/usr/bin/env python3
"""Upgrading the card package under a docked card leaves the card and its app open (WG5).

Run through tests/widget-card-upgrade-test.sh in a private headless --widgets session:
  widget-card-upgrade-test.py PACKAGE ARTIFACTS
PACKAGE is a scratch copy of the card package, found first on SCOTTLAND_WIDGET_PATH.

A package upgrade rewrites the card's shell.qml in place, the way pacman does: unlink, then
write the new file. The test changes the card window's Wayland title to add an upgrade marker.
This makes QML code adoption observable even if a soft reload reuses the same window: the
running card must keep its old title, while a newly launched card must show the marker. The
app/client and its original card window must also remain open and visually unchanged. After the
widget service restarts, the original card must still follow Super+M under the upgraded Scottland.
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
upgrade_marker = ' [upgrade-marker]'
# Allow time for a watched-file reload while polling for its observable title marker or closure.
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


def card_view(app_title):
    old_title = ': ' + app_title
    upgraded_title = old_title + upgrade_marker
    return next((view for view in t.views() if view['widget'] and
                 (view['title'].endswith(old_title) or view['title'].endswith(upgraded_title))), None)


def launch_fresh_card(app_title, y):
    process = subprocess.Popen(['foot', '--app-id', 'foot', '-T', app_title, '-W', '40x8',
                                'sh', '-c', 'exec sleep 600'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t.owned.append((app_title, process))
    app = t.wait_for(lambda: t.app(app_title))
    time.sleep(.4)
    t.drag_begin(app, t.screen['width'] - 6, y)
    t.drag_end()
    return t.wait_for(lambda: (view := card_view(app_title)) and not view['preview'] and view)


try:
    t.ipc.call('wayfire/set-config-options', {'scottland/sounds': False})
    t.launch(title, rail='right')
    process = t.owned[-1][1]
    app, card = t.app(title), t.card(title)
    before_card_title = card['title']
    if not card_processes():
        raise AssertionError('the card did not start from the scratch package ' + str(package))
    t.wait_for(lambda: t.ipc.call('scottland/layout-state')['widget_transition_count'] == 0)
    before = card_pixels('before', card['frame'])

    source = package/'shell.qml'
    code = source.read_bytes()
    title_line = b'    title: "Scottland widget: " + appTitle\n'
    if title_line not in code:
        raise AssertionError('the card no longer exposes its app title on the window')
    upgraded = code.replace(title_line,
                            title_line[:-1] + b' + " [upgrade-marker]"\n', 1)
    source.unlink()
    source.write_bytes(upgraded)

    # A QML reload changes the externally observed toplevel title. Wait for that marker or for
    # the linked app/card to disappear; on the fixed card the old title remains through the hold.
    deadline = time.monotonic() + HOLD
    while time.monotonic() < deadline:
        current_app, current_card = t.app(title), card_view(title)
        if (process.poll() is not None or current_app is None or current_card is None or
                current_card['id'] != card['id'] or current_card['title'] != before_card_title):
            break
        time.sleep(.05)

    after_app, after_card = t.app(title), card_view(title)
    check('the app is still running after the upgrade', process.poll() is None,
          {'exit': process.poll()})
    check('the app keeps its window', after_app is not None and after_app['id'] == app['id'],
          {'before': app['id'], 'after': after_app and after_app['id']})
    check('the docked card keeps its window', after_card is not None and after_card['id'] == card['id'],
          {'before': card['id'], 'after': after_card and after_card['id']})
    check('the running card keeps its pre-upgrade QML',
          after_card is not None and after_card['title'] == before_card_title,
          {'before': before_card_title, 'after': after_card and after_card['title']})
    if after_card is not None:
        t.wait_for(lambda: t.ipc.call('scottland/layout-state')['widget_transition_count'] == 0)
        after = card_pixels('after', after_card['frame'])
        changed = sum(1 for a, b in zip(before, after) if abs(a - b) > 8)
        check('the card looks as it did before the upgrade',
              len(after) == len(before) and changed <= len(before) // 100,
              {'channels_changed': changed, 'channels': len(before)})
    else:
        check('the card looks as it did before the upgrade', False, 'the card is gone')

    # Observe fresh code adoption before restarting the widget service; that restart is a
    # separate check and must not be able to explain the package-replacement result.
    fresh_title = 'card-upgrade-fresh-launch'
    fresh_card, fresh_error = None, None
    try:
        fresh_card = launch_fresh_card(fresh_title, y=420)
    except AssertionError as error:
        fresh_error = str(error)
    check('a fresh launch adopts the upgraded QML',
          fresh_card is not None and fresh_card['title'].endswith(': ' + fresh_title + upgrade_marker),
          {'expected_suffix': ': ' + fresh_title + upgrade_marker,
           'actual': fresh_card and fresh_card['title'], 'launch_error': fresh_error})

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

    # A real Super+M tap collapses the card to the square around its icon, and expanding again
    # restores its width after the widget service has been replaced.
    collapsed = expanded = None
    if card_view(title):
        try:
            t.tap_mode_key()
            collapsed = t.wait_for(lambda: (c := card_view(title)) and abs(c['frame']['width'] - 96) < 1 and c)
            t.set_widget_mode('expanded')
            expanded = t.wait_for(lambda: (c := card_view(title)) and c['frame']['width'] > 120 and c)
        except AssertionError as e:
            print('card did not follow Super+M: ' + str(e), flush=True)
    check('the card still collapses and expands with Super+M',
          collapsed is not None and expanded is not None and expanded['id'] == card['id'],
          {'collapsed_width': collapsed and collapsed['frame']['width'],
           'expanded_width': expanded and expanded['frame']['width']})

finally:
    t.cleanup()
    (out/'checks.json').write_text(json.dumps(checks, indent=2))

passed = sum(c['ok'] for c in checks)
print(f'{passed}/{len(checks)} checks passed', flush=True)
sys.exit(0 if checks and passed == len(checks) else 1)
