#!/usr/bin/env python3
"""Reload rehearsal (AGENTS.md testing step 4): a headless session started on an older build, with
widgets and peeking windows present, reloaded in place into this checkout's plugin the way
this checkout's scottland-reload does it (receipt, fresh copy, widget hand-over mark). Checks that it survives, keeps every
window and widget, renders, and that the new build's solo works afterwards.

Runs inside the OLD build's headless session (tests/reload-rehearsal-test.sh starts it).
argv: ARTIFACTS NEW_PLUGIN_SO SESSION_METADATA_XML NEW_METADATA_XML"""
import json, os, shutil, subprocess, sys, time
from pathlib import Path

art = Path(sys.argv[1]).resolve(); art.mkdir(parents=True, exist_ok=True)
plugin = Path(sys.argv[2]).resolve()
session_xml, new_xml = Path(sys.argv[3]), Path(sys.argv[4])
source = Path(__file__).with_name('spread-test.py').read_text().split('\ntry:\n', 1)[0]
sys.argv = [sys.argv[0], str(art)]
h = {}
exec(compile(source, 'spread-test.py', 'exec'), h)
ipc, check, wait, geometry, layout = h['ipc'], h['check'], h['wait'], h['geometry'], h['layout']

def widgets():
    return {int(w['id']): w['widget_view'] for w in ipc('scottland/widgets')['widgets']}

def mean_brightness(path):
    data = path.read_bytes()
    # grim writes PNG; sample the raw bytes of the compressed stream is meaningless, so decode
    # with Python's zlib on the IDAT chunks (8-bit RGBA or RGB, no interlace).
    import struct, zlib
    pos, idat, width, height, channels = 8, b'', 0, 0, 4
    while pos < len(data):
        length, kind = struct.unpack('>I4s', data[pos:pos + 8]); body = data[pos + 8:pos + 8 + length]
        if kind == b'IHDR':
            width, height, depth, color = struct.unpack('>IIBB', body[:10]); channels = 4 if color == 6 else 3
        elif kind == b'IDAT': idat += body
        pos += 12 + length
    raw = zlib.decompress(idat); stride = width * channels; total = count = 0
    prev = bytearray(stride)
    for row in range(height):
        f = raw[row * (stride + 1)]; line = bytearray(raw[row * (stride + 1) + 1:(row + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - channels] if i >= channels else 0; b = prev[i]; c = prev[i - channels] if i >= channels else 0
            if f == 1: line[i] = (line[i] + a) & 255
            elif f == 2: line[i] = (line[i] + b) & 255
            elif f == 3: line[i] = (line[i] + (a + b) // 2) & 255
            elif f == 4:
                p = a + b - c; pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        if row % 16 == 0:
            for x in range(0, stride, channels * 16): total += sum(line[x:x + 3]) / 3; count += 1
        prev = line
    return total / max(1, count)

try:
    ipc('wayfire/set-config-options', {'scottland/sounds': False, 'scottland/window_hold_delay': 500,
        'scottland/window_avoidance_always': True, 'output:HEADLESS-1/mode': '2560x1440@60000'})
    time.sleep(1)
    ids = [h['launch'](f'Rehearse{i}') for i in range(7)]
    S = ids[0]
    # Two windows wholly behind the focused one, so the peek engine has to move them aside, an
    # overlapping pair in the periphery, and two widgets.
    h['setup']([(ids[1], 80, 120, 700, 500), (ids[2], 300, 300, 700, 500), (ids[3], 1800, 200, 600, 500),
                (ids[4], 1700, 500, 600, 450), (ids[5], 950, 450, 500, 350), (ids[6], 1150, 650, 500, 350),
                (S, 830, 370, 900, 700)], S)
    for w in (ids[3], ids[4]):
        h['alt'](True); h['press_hint'](w); time.sleep(.1); h['press_hint'](w); h['alt'](False)
        wait(lambda: layout(w)['widgetized'], 6, 'widgetize')
    wait(lambda: all(widgets().get(w, -1) > 0 for w in (ids[3], ids[4])), 10, 'widget views')
    time.sleep(1.5)
    before_views = {v['id'] for v in h['views']()}
    before_geometry = {i: geometry(i) for i in ids}
    before_widgets = {w: widgets()[w] for w in (ids[3], ids[4])}
    peeking = [i for i in ids if i not in (ids[3], ids[4]) and
               (abs(h['hint'](i)['dx']) > 1 or abs(h['hint'](i)['dy']) > 1)]
    check(len(peeking) >= 1, f'before: windows peek out under the old build ({len(peeking)} displaced)')
    h['shot']('before-reload.png')

    # The reload through this checkout's scottland-reload, as dev-install installs them together: the
    # new build's settings metadata (new options) is in place first, as an install puts it; the
    # helper registers it, writes the reload receipt and the hand-over mark, swaps in a fresh copy
    # and waits for the new copy's acknowledgment.
    shutil.copy(new_xml, session_xml)
    helper = plugin.parents[1] / 'core/session/scottland-reload'
    done = subprocess.run([str(helper)], env=dict(os.environ, SCOTTLAND_TEST_RELOAD_DIR=str(art),
                          SCOTTLAND_RELOAD_SOURCE=str(plugin)), capture_output=True, text=True, timeout=120)
    print(f'scottland-reload -> {done.returncode}: {(done.stdout + done.stderr).strip()[-300:]}', flush=True)
    check(done.returncode == 0 and 'imported 2 of 2' in done.stdout, 'reload: the helper reports both widgets carried over',
          done.stdout + done.stderr)
    time.sleep(1)

    check('solves' in ipc('scottland/spread-state'), 'after: the new build answers (spread-state exists)')
    check({v['id'] for v in h['views']()} == before_views, 'after: every window and widget view is still there')
    after_geometry = {i: geometry(i) for i in ids}
    check(after_geometry == before_geometry, 'after: no window moved',
          str({i: (before_geometry[i], after_geometry[i]) for i in ids if before_geometry[i] != after_geometry[i]}))
    w_after = widgets()
    check(all(w_after.get(w) == before_widgets[w] for w in before_widgets) and
          all(layout(w)['widgetized'] for w in before_widgets), 'after: both widgets are kept, linked to their windows',
          f'{before_widgets} {w_after}')
    time.sleep(1.5)
    peeking_after = [i for i in ids if i not in (ids[3], ids[4]) and
                     (abs(h['hint'](i)['dx']) > 1 or abs(h['hint'](i)['dy']) > 1)]
    check(len(peeking_after) >= 1, f'after: the new build\'s peek engine displaces windows again ({len(peeking_after)})')
    h['shot']('after-reload.png')
    brightness = mean_brightness(art / 'after-reload.png')
    check(brightness > 8, f'after: the screen renders (mean brightness {brightness:.1f})')

    # The new build's solo works in the reloaded session, widgets present.
    h['setup']([(ids[1], 80, 120, 700, 500), (ids[5], 900, 150, 600, 450), (ids[6], 1100, 700, 600, 450),
                (S, 830, 370, 900, 700)], S)
    n = ipc('scottland/spread-state')['solves']
    h['alt'](True); h['press_hint'](S, hold=.75); h['alt'](False)
    result = h['wait_solve'](n)
    check(result['purpose'] == 'solo' and result['status'] in ('clear', 'overlap: search exhausted'),
          f'after: a solo works in the reloaded session ({result["status"]})')
    h['settle'](ids)
    check(abs(h['hint'](S)['dx']) + abs(h['hint'](S)['dy']) < .5, 'after: the solo window, in front, has no avoidance offset (offset diagnostic)')
    h['shot']('after-solo.png')
    (art / 'rehearsal.json').write_text(json.dumps({'peeking_before': peeking, 'peeking_after': peeking_after,
                                                    'brightness': brightness}, indent=2))
finally:
    for c in h['clients']: c.terminate()
    print(f"{h['passed']} passed, {h['failed']} failed", flush=True)
sys.exit(1 if h['failed'] else 0)
