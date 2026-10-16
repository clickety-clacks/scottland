#!/usr/bin/env python3
"""Gather input, queueing, and output isolation through the real Window-mode key path."""
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time

assert os.environ.get('SCOTTLAND_TEST_MODEL') == '1'
artifacts = Path(sys.argv[1])
artifacts.mkdir(parents=True, exist_ok=True)
request_path = os.environ['WAYFIRE_SOCKET']
clients = []
passed = failed = 0

def ipc(method, data=None):
    with socket.socket(socket.AF_UNIX) as request:
        request.settimeout(10)
        request.connect(request_path)
        body = json.dumps({'method': method, 'data': data or {}}).encode()
        request.sendall(struct.pack('<I', len(body)) + body)
        def receive(size):
            result = b''
            while len(result) < size:
                chunk = request.recv(size - len(result))
                if not chunk:
                    raise ConnectionError('compositor disconnected')
                result += chunk
            return result
        return json.loads(receive(struct.unpack('<I', receive(4))[0]))

def wait_for(predicate, timeout=5, label='compositor state'):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        result = predicate()
        if result:
            return result
        time.sleep(.05)
    raise RuntimeError('timed out waiting for ' + label)

def check(ok, name, observed=None):
    global passed, failed
    print(('PASS  ' if ok else 'FAIL  ') + name, flush=True)
    if not ok and observed is not None:
        print('      observed: ' + json.dumps(observed, sort_keys=True), flush=True)
    passed += bool(ok)
    failed += not ok

def key(code, down):
    ipc('stipc/feed_key', {'key': 'KEY_' + code, 'state': down})

def tap(code):
    key(code, True)
    key(code, False)

def hints():
    return ipc('scottland/hints')

def spread():
    return ipc('scottland/spread-state')

def raw_views():
    return ipc('window-rules/list-views')

def raw_view(identifier):
    return next(v for v in raw_views() if v['id'] == identifier)

def views():
    state = ipc('scottland/layout-state')['views']
    geometry = {v['id']: v['geometry'] for v in raw_views()}
    for v in state:
        v['geometry'] = geometry[v['id']]
    return state

def view(name):
    return next((v for v in views() if v['title'] == name), None)

def center(v):
    g = v['geometry']
    return (g['x'] + g['width'] / 2, g['y'] + g['height'] / 2)

def output_id(identifier):
    return raw_view(identifier)['output-id']

def focus(identifier):
    ipc('window-rules/focus-view', {'id': identifier})

def hint(identifier):
    return next(h for h in hints()['hints'] if h['window'] == identifier)

def hold():
    key('LEFTALT', True)
    wait_for(lambda: hints()['active'], label='Window mode')
    time.sleep(.35)

def release():
    key('LEFTALT', False)
    wait_for(lambda: not hints()['active'], label='Window mode to end')

def choose(identifier):
    for letter in hint(identifier)['hint']:
        tap(letter.upper())

def launch(title):
    log = artifacts / (title + '.keys')
    log.write_text('')
    process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('windowing-key-recorder.py')),
                                title, str(log)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    clients.append(process)
    wait_for(lambda: view(title), label='window ' + title)
    return view(title)['id']

def place_on_output(identifier, output, x_fraction, y_fraction=.5):
    g = raw_view(identifier)['geometry']
    screen = output['geometry']
    placement = {'x': round(screen['width'] * x_fraction - g['width'] / 2),
                 'y': round(screen['height'] * y_fraction - g['height'] / 2),
                 'width': g['width'], 'height': g['height']}
    ipc('window-rules/configure-view', {'id': identifier, 'output_id': output['id'],
                                       'geometry': placement})
    wait_for(lambda: output_id(identifier) == output['id'], timeout=5,
             label='window to move to its fixture output')

def settle(identifiers, timeout=5):
    end = time.monotonic() + timeout
    previous = None
    while time.monotonic() < end:
        by_id = {v['id']: v for v in views()}
        current = []
        for identifier in identifiers:
            v = by_id[identifier]
            g, f = v['geometry'], v['frame']
            current.append((g['x'], g['y'], round(v['applied_scale'], 4),
                            tuple(round(f[k], 3) for k in ('x', 'y', 'width', 'height'))))
        if current == previous:
            return
        previous = current
        time.sleep(.15)
    raise RuntimeError(f'window layout did not settle: {previous}')

def key_events(title):
    return [json.loads(line) for line in (artifacts / (title + '.keys')).read_text().splitlines()]

def widget_snapshot(identifier):
    widget = next(w for w in ipc('scottland/widgets')['widgets'] if int(w['id']) == identifier)
    rendered = next(v for v in views() if v['id'] == widget['widget_view'])
    frame = rendered['frame']
    return {
        'widget': {key: widget.get(key) for key in
                   ('widget_view', 'rail', 'place', 'collapsed', 'away', 'focused', 'lifecycle')},
        'geometry': rendered['geometry'],
        'scale': rendered['applied_scale'],
        'frame': tuple(frame[k] for k in ('x', 'y', 'width', 'height')),
    }

try:
    ipc('wayfire/set-config-options', {
        'scottland/sounds': False,
        'scottland/center_width': 30.0,
        'output:HEADLESS-1/mode': '1280x720@60000',
        'output:HEADLESS-1/position': '0, 0',
        'output:HEADLESS-2/mode': '1280x720@60000',
        'output:HEADLESS-2/position': '1280, 0',
    })
    outputs = wait_for(lambda: (lambda current: current if len(current) == 2 else None)(
        sorted(ipc('window-rules/list-outputs'), key=lambda o: o['geometry']['x'])),
        label='two distinct outputs')
    output0, output1 = outputs
    width = output0['geometry']['width']
    center_width = float(ipc('wayfire/get-config-option', {'option': 'scottland/center_width'})['value'])
    check(output0['id'] != output1['id'] and output0['geometry']['x'] == 0 and
          output1['geometry']['x'] == width, 'Gather fixture has two distinct positioned outputs', outputs)

    chord = launch('GatherChord')
    place_on_output(chord, output0, .5)
    focus(chord)
    before_chord = spread()['solves']
    key('LEFTALT', True); tap('SPACE'); key('LEFTALT', False)
    wait_for(lambda: any(e['key'] == 'space' and e['modifiers'] & 8 for e in key_events('GatherChord')),
             label='quick Alt+Space app delivery')
    check(any(e['key'] == 'space' and e['modifiers'] & 8 for e in key_events('GatherChord')) and
          not hints()['active'] and spread()['solves'] == before_chord,
          'GA-2 quick Alt+Space reaches the app without entering Window mode or gathering',
          {'events': key_events('GatherChord'), 'spread': spread(), 'hints_active': hints()['active']})
    ipc('window-rules/close-view', {'id': chord})
    wait_for(lambda: view('GatherChord') is None, label='chord fixture to close')

    pending = launch('GatherPending')
    place_on_output(pending, output0, .5)
    focus(pending); hold()
    pending_hint = hint(pending)['hint']
    check(len(pending_hint) == 1, 'GA-3 focused hint fixture has one letter', pending_hint)
    if len(pending_hint) != 1:
        raise RuntimeError('GA-3 focused hint fixture is not a single key')
    pending_key = pending_hint.upper()
    before_pending = spread()['solves']
    key(pending_key, True); key('SPACE', True); key('SPACE', False); key(pending_key, False)
    wait_for(lambda: (lambda state: state if state['solves'] >= before_pending + len(outputs) and
        not state['running'] and state['last'].get('purpose') == 'gather' else None)(spread()),
        timeout=10, label='pending-hint Gather completion')
    settle([pending])
    pending_state = spread()['last']
    pending_moves = {int(m['id']) for r in pending_state.get('screens', []) for m in r.get('moves', [])}
    pending_after = view('GatherPending')
    cx = center(pending_after)[0]
    center_half = width * center_width / 200
    if cx < width / 2:
        inner, border = cx + pending_after['geometry']['width'] * pending_after['applied_scale'] / 2, width / 2 - center_half
    else:
        inner, border = cx - pending_after['geometry']['width'] * pending_after['applied_scale'] / 2, width / 2 + center_half
    check(pending in pending_moves and pending_after['zone'] == 'continuous' and abs(inner - border) < 3,
          'GA-3 pending focused hint acts before Gather places the new periphery window',
          {'moves': sorted(pending_moves), 'window': pending_after, 'inner': inner, 'border': border})
    committed = (pending_after['geometry'].copy(), pending_after['applied_scale'])
    release()
    pending_after_release = view('GatherPending')
    check(pending_after_release['geometry'] == committed[0] and
          pending_after_release['applied_scale'] == committed[1],
          'GA-13 Gather remains committed after leaving Window mode')
    ipc('window-rules/close-view', {'id': pending})
    wait_for(lambda: view('GatherPending') is None, label='pending fixture to close')

    widget = launch('GatherWidget')
    place_on_output(widget, output0, .86)
    focus(widget); hold(); choose(widget); choose(widget)
    wait_for(lambda: view('GatherWidget')['widgetized'], label='rail widget fixture')
    release()

    left = launch('GatherLeft'); place_on_output(left, output0, .14)
    right = launch('GatherRight'); place_on_output(right, output0, .86)
    center0 = launch('GatherCenter'); place_on_output(center0, output0, .5)
    center1 = launch('GatherOtherCenter'); place_on_output(center1, output1, .5)
    other = launch('GatherOther'); place_on_output(other, output1, .86)
    expected_output = {left: output0['id'], right: output0['id'], center0: output0['id'],
                       center1: output1['id'], other: output1['id']}
    actual_output = {identifier: output_id(identifier)
                     for identifier in (left, right, center0, center1, other)}
    check(actual_output == expected_output, 'GA-15 fixtures map to their intended outputs',
          {'expected': expected_output, 'actual': actual_output})

    focus(left); hold()
    settle([left, right, other, center0, center1])
    before_left, before_right, before_other = view('GatherLeft'), view('GatherRight'), view('GatherOther')
    before_center0, before_center1 = view('GatherCenter'), view('GatherOtherCenter')
    before_widget = widget_snapshot(widget)
    before_gather = spread()['solves']
    tap('SPACE')
    wait_for(lambda: (lambda state: state if state['solves'] >= before_gather + len(outputs) and
        not state['running'] and state['last'].get('purpose') == 'gather' else None)(spread()),
        timeout=10, label='two-output Gather completion')
    settle([left, right, other, center0, center1])
    gather_state = spread()['last']
    screens = gather_state.get('screens', [])
    records = {result.get('output'): result for result in screens}
    output_names = {output0['name'], output1['name']}
    check(len(screens) == len(outputs) and set(records) == output_names and
          {result.get('screen_index') for result in screens} == set(range(len(outputs))) and
          all(result.get('purpose') == 'gather' and 'status' in result and 'work' in result and
              'moves' in result for result in screens),
          'GA-15 records each distinct output exactly once', gather_state)
    moves0 = {int(m['id']) for m in records.get(output0['name'], {}).get('moves', [])}
    moves1 = {int(m['id']) for m in records.get(output1['name'], {}).get('moves', [])}
    check(moves0 == {left, right} and moves1 == {other},
          'GA-15 each output moves only its own periphery windows',
          {'screen_moves': {output0['name']: sorted(moves0), output1['name']: sorted(moves1)},
           'expected': {'output0': sorted((left, right)), 'output1': other}})
    after_left, after_right, after_other = view('GatherLeft'), view('GatherRight'), view('GatherOther')
    check(hints()['active'], 'Space gathers without leaving Window mode')
    check(before_left['zone'] == 'continuous' and before_right['zone'] == 'continuous' and
          before_other['zone'] == 'continuous' and
          after_left['applied_scale'] > before_left['applied_scale'] + .05 and
          after_right['applied_scale'] > before_right['applied_scale'] + .05 and
          after_other['applied_scale'] > before_other['applied_scale'] + .05,
          'Space gathers periphery windows on both sides and both outputs toward their borders',
          {'before': [before_left, before_right, before_other],
           'after': [after_left, after_right, after_other]})
    after_center0, after_center1 = view('GatherCenter'), view('GatherOtherCenter')
    check(before_center0['geometry'] == after_center0['geometry'] and
          before_center1['geometry'] == after_center1['geometry'] and
          before_center0['applied_scale'] == after_center0['applied_scale'] and
          before_center1['applied_scale'] == after_center1['applied_scale'],
          'GA-5 center windows remain unchanged on both outputs',
          {'before': [before_center0, before_center1], 'after': [after_center0, after_center1]})
    after_widget = widget_snapshot(widget)
    check(before_widget == after_widget and view('GatherWidget')['widgetized'],
          'GA-5 rail widget remains unchanged during Gather',
          {'before': before_widget, 'after': after_widget})

    solves_before_modified = spread()['solves']
    key('LEFTCTRL', True); tap('SPACE'); key('LEFTCTRL', False)
    check(spread()['solves'] == solves_before_modified, 'modified Space is consumed without gathering')
    solves_before_repeat = spread()['solves']
    key('SPACE', True)
    wait_for(lambda: (lambda state: state if state['solves'] >= solves_before_repeat + len(outputs) and
        not state['running'] else None)(spread()), timeout=10, label='held-Space Gather completion')
    key('SPACE', True)
    check(spread()['solves'] == solves_before_repeat + len(outputs),
          'GA-1 held Space auto-repeat gathers only once')
    key('SPACE', False)
    release()
except Exception as error:
    check(False, 'suite exception: ' + repr(error))
    import traceback
    traceback.print_exc()
finally:
    try:
        key('SPACE', False); key('LEFTALT', False); key('LEFTCTRL', False)
    except Exception:
        pass
    print(f'{passed} passed, {failed} failed', flush=True)
    for process in clients:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
sys.exit(bool(failed))
