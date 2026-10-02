#!/usr/bin/env python3
"""Real stipc keyboard/pointer input, app delivery, compositor state, and reload checks."""
import json
import math
import os
import select
import socket
import struct
from pathlib import Path
import subprocess
import sys
import time

artifacts = Path(sys.argv[1])
passed = failed = 0
clients = []

def ipc(method, data=None):
    args = ['tests/headless.sh', 'ipc', method]
    if data is not None:
        args.append(json.dumps(data))
    result = subprocess.run(args, text=True, capture_output=True, check=True)
    return json.loads(result.stdout)

class ModelWatch:
    def __init__(self):
        # Ask the checkout's session helper for its socket; never inherit another desktop's.
        path = subprocess.check_output(['tests/headless.sh', 'run', 'python3', '-c',
                "import os;print(os.environ['WAYFIRE_SOCKET'])"], text=True).strip()
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.settimeout(5)
        self.sock.connect(path)
        body = json.dumps({'method': 'scottland/subscribe', 'data': {'slice': 'desktop'}}).encode()
        self.sock.sendall(struct.pack('<I', len(body)) + body)
        self.initial = self.receive()

    def exactly(self, count):
        data = b''
        while len(data) < count:
            chunk = self.sock.recv(count - len(data))
            if not chunk: raise ConnectionError('compositor disconnected')
            data += chunk
        return data

    def receive(self):
        return json.loads(self.exactly(struct.unpack('<I', self.exactly(4))[0]))

    def latest(self):
        state = self.initial
        while select.select([self.sock], [], [], .1)[0]: state = self.receive()
        return state

def check(ok, name):
    global passed, failed
    print(('PASS  ' if ok else 'FAIL  ') + name, flush=True)
    passed += bool(ok)
    failed += not ok

def wait_for(predicate, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        result = predicate()
        if result:
            return result
        time.sleep(.05)
    raise RuntimeError('timed out waiting for compositor/client state')

def views():
    state = ipc('scottland/layout-state')['views']
    geometries = {v['id']: v['geometry'] for v in ipc('window-rules/list-views')}
    for v in state:
        v['geometry'] = geometries[v['id']]
    return state

def view(name):
    return next((v for v in views() if v['title'] == name), None)

def hints():
    return ipc('scottland/hints')

def hint(identifier):
    return next(h for h in hints()['hints'] if h['window'] == identifier)

def focused():
    return ipc('window-rules/get-focused-view')['info']['id']

def center(v):
    g = v['geometry']
    return (g['x'] + g['width']/2, g['y'] + g['height']/2)

def near(a, b, eps=2):
    return math.dist(a, b) <= eps

def key(code, down):
    return ipc('stipc/feed_key', {'key': 'KEY_' + code, 'state': down})

def tap(code):
    key(code, True)
    key(code, False)

def hold():
    key('LEFTALT', True)
    wait_for(lambda: hints()['active'])
    time.sleep(.4)

def release():
    key('LEFTALT', False)
    time.sleep(.5)

def choose(identifier):
    for letter in hint(identifier)['hint']:
        tap(letter.upper())
    time.sleep(.65)

def rapid_hint(identifier, count, interval=0):
    # One persistent stipc connection avoids shell/Python startup between physical presses.
    # The helper inherits the recorded session environment through headless.sh run.
    script = """
import json, os, socket, struct, sys, time
sock = socket.socket(socket.AF_UNIX); sock.connect(os.environ['WAYFIRE_SOCKET'])
def receive(size):
    data = b''
    while len(data) < size: data += sock.recv(size-len(data))
    return data
for index in range(int(sys.argv[2])):
    if index: time.sleep(float(sys.argv[3]))
    for letter in sys.argv[1]:
        for down in (True, False):
            body = json.dumps({'method':'stipc/feed_key',
                'data':{'key':'KEY_'+letter.upper(),'state':down}}).encode()
            sock.sendall(struct.pack('<I',len(body))+body)
            reply = json.loads(receive(struct.unpack('<I',receive(4))[0]))
            if 'error' in reply: raise RuntimeError(reply)
"""
    subprocess.run(['tests/headless.sh', 'run', 'python3', '-c', script,
                    hint(identifier)['hint'], str(count), str(interval)], check=True)

def launch(name, x=None, y=None):
    if x is not None:
        ipc('wayfire/set-config-options', {'place/mode': 'pointer'})
        ipc('stipc/move_cursor', {'x': round(x), 'y': round(y)})
    log = artifacts / (name + '.keys')
    log.write_text('')
    p = subprocess.Popen(['tests/headless.sh', 'run', 'python3',
                          str(Path('tests/windowing-key-recorder.py').resolve()), name, str(log)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    clients.append(p)
    v = wait_for(lambda: view(name))
    time.sleep(.4)
    return v['id']

def delivered(name):
    return [json.loads(line)['key'] for line in (artifacts/(name+'.keys')).read_text().splitlines()]

def focus(identifier):
    ipc('window-rules/focus-view', {'id': identifier})
    time.sleep(.1)

def drag(name, x, y):
    v = view(name)
    if v['widgetized']:
        link = next(w for w in ipc('scottland/widgets')['widgets'] if int(w['id']) == v['id'])
        v = next(item for item in views() if item['id'] == link['widget_view'])
    f = v['frame']
    cx, cy = f['x'] + f['width']/2, f['y'] + f['height']/2
    ipc('stipc/move_cursor', {'x': round(cx), 'y': round(cy)})
    key('LEFTMETA', True)
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'press'})
    for step in range(1, 11):
        ipc('stipc/move_cursor', {'x': round(cx+(x-cx)*step/10), 'y': round(cy+(y-cy)*step/10)})
        time.sleep(.025)
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'release'})
    key('LEFTMETA', False)
    time.sleep(.5)

def close_all():
    for v in views():
        if not v['widget']:
            ipc('window-rules/close-view', {'id': v['id']})
    wait_for(lambda: not views())
    time.sleep(.2)

try:
    ipc('wayfire/set-config-options', {'scottland/sounds': False, 'scottland/alt_hold_delay': 300})
    output = ipc('window-rules/list-outputs')[0]['geometry']
    width, height = output['width'], output['height']
    a = launch('Alpha')
    b = launch('Beta')
    check(hint(a)['hint'] == 'a' and hint(b)['hint'] == 's', 'home-row hints assigned in opening order')
    focus(a)
    key('LEFTALT', True)
    time.sleep(.08)
    check(not hints()['active'], 'Alt hints absent before 300 ms hold')
    tap('B')
    time.sleep(.35)
    check(not hints()['active'], 'quick Alt+letter prevents hint entry for the whole chord')
    release()
    check('b' in delivered('Alpha'), 'quick Alt+B reaches the application')
    check(any(e['key']=='b' and e['modifiers'] & 8 for e in map(json.loads,(artifacts/'Alpha.keys').read_text().splitlines())), 'quick Alt+B preserves the application Alt modifier')
    before = delivered('Alpha').count('f')
    key('LEFTALT', True)
    tap('F')
    release()
    check(delivered('Alpha').count('f') == before+1, 'quick Alt+F reaches the application')
    key('LEFTALT', True); tap('TAB'); time.sleep(.35)
    check(not hints()['active'], 'quick Alt+Tab keeps existing routing and cancels delayed entry')
    release(); focus(a)
    memories_before = {h['window']: h['memories'] for h in hints()['hints']}
    hold()
    check(all(h['visible'] for h in hints()['hints']), 'Alt-alone hold shows every window hint')
    saved = center(view('Alpha')), center(view('Beta'))
    offsets = [(hint(i)['dx'], hint(i)['dy']) for i in (a,b)]
    check(any(math.hypot(*p) > 10 for p in offsets), 'coincident windows visually displace')
    check(near(saved[0], saved[1]), 'declutter leaves real window geometry unchanged')
    check(all(w['placement']['positions'] == memories_before[w['id']] for w in ipc('scottland/desktop-model')['windows']
              if w['id'] in memories_before), 'visual declutter leaves published zone memories unchanged')
    subprocess.run(['tests/headless.sh', 'run', 'grim', str(artifacts/'hints.png')], check=True)
    before = sum(delivered(n).count('f') for n in ('Alpha','Beta'))
    tap('F')
    check(sum(delivered(n).count('f') for n in ('Alpha','Beta')) == before, 'letters in window mode never reach apps')
    tap('TAB')
    check(focused() == b and hints()['selected'] == b, 'held-Alt Tab selects next hint')
    key('LEFTSHIFT', True); tap('TAB'); key('LEFTSHIFT', False)
    check(focused() == a and hints()['selected'] == a, 'held-Alt Shift+Tab selects previous hint')
    choose(b)
    check(focused() == b and near(center(view('Beta')), saved[1]), 'unselected window first press selects without moving')
    choose(a)
    check(focused() == a and near(center(view('Alpha')), saved[0]), 'switching hints resets selection without moving')
    key('LEFTCTRL', True); key('LEFTMETA', True); tap('F')
    check(hints()['active'], 'Ctrl and Super added after entry stay inside window mode')
    key('LEFTCTRL', False); key('LEFTMETA', False)
    tap('ESC')
    check(not hints()['active'], 'Esc leaves window mode')
    before = delivered('Alpha').count('b')
    tap('B'); time.sleep(.1)
    check(delivered('Alpha').count('b') == before, 'Esc keeps remaining Alt chord captured')
    release()
    check(near(center(view('Alpha')), saved[0]) and near(center(view('Beta')), saved[1]), 'declutter restores original geometry')
    check(all(abs(h['dx'])+abs(h['dy']) < .1 and not h['visible'] for h in hints()['hints']), 'visual offsets and hints clear after release')
    focus(a); tap('X'); time.sleep(.1)
    check(any(e['key']=='x' and e['modifiers']==0 for e in map(json.loads,(artifacts/'Alpha.keys').read_text().splitlines())), 'mode release leaves no stuck modifiers in the app')
    for modifier in ('LEFTMETA','LEFTCTRL','LEFTSHIFT'):
        key(modifier, True); key('LEFTALT', True); time.sleep(.4)
        check(not hints()['active'], modifier+' before Alt suppresses hints (including resize chord)')
        key('LEFTALT', False); key(modifier, False)
    key('X', True); key('LEFTALT', True); time.sleep(.4)
    check(not hints()['active'], 'nonmodifier key held first prevents Alt-alone entry')
    key('LEFTALT', False); key('X', False)
    # L31 and L20 use real pointer/key input, with no window mode racing the drag.
    focus(a)
    f = view('Alpha')['frame']
    cx, cy = f['x'] + f['width']/2, f['y'] + f['height']/2
    ipc('stipc/move_cursor', {'x': round(cx), 'y': round(cy)})
    key('LEFTMETA', True)
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'press'})
    ipc('stipc/move_cursor', {'x': round(cx + 10), 'y': round(cy)})
    key('LEFTMETA', False)
    key('LEFTALT', True)
    time.sleep(.45)
    check(not hints()['active'], 'Alt alone during an ongoing drag never opens hints')
    ipc('stipc/move_cursor', {'x': round(width*.2), 'y': round(height*.4)})
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'release'})
    time.sleep(.45)
    pinned = view('Alpha')['applied_scale']
    check(pinned > .999 and view('Alpha')['zone'] == 'continuous', 'Alt drag pins full scale when dropped in periphery')
    check(not hints()['active'], 'Alt drag chord cannot enter hints after drop')
    release()
    check(abs(view('Alpha')['applied_scale'] - pinned) < .003, 'Alt drop pin persists after Alt release')
    state = ipc('scottland/desktop-model')['windows']
    check(next(w for w in state if w['id'] == a).get('pinned_scale') == pinned, 'desktop model publishes the drag scale pin')
    hold(); choose(a); release()
    check(view('Alpha')['zone'] == 'center' and view('Alpha')['applied_scale'] > .999 and
          'pinned_scale' not in next(w for w in ipc('scottland/desktop-model')['windows'] if w['id'] == a),
          'explicit cycle clears pin and restores full-size center')
    f = view('Alpha')['frame']
    cx, cy = f['x'] + f['width']/2, f['y'] + f['height']/2
    old = view('Alpha')['geometry']
    ipc('stipc/move_cursor', {'x': round(cx), 'y': round(cy)})
    key('LEFTMETA', True); key('LEFTALT', True)
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'press'})
    for step in range(1, 6):
        ipc('stipc/move_cursor', {'x': round(cx+step*8), 'y': round(cy-step*5)})
        time.sleep(.07)
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'release'})
    key('LEFTALT', False); key('LEFTMETA', False)
    time.sleep(.4)
    check(view('Alpha')['geometry']['width'] > old['width'] and not hints()['active'] and
          near(center(view('Alpha')), (cx, cy)), 'Super+Alt real drag resizes around center without hints')
    # Stable assignment after an earlier window closes; Tab/F4 use real input.
    hold(); choose(b); tap('F4')
    wait_for(lambda: view('Beta') is None)
    check(view('Alpha') is not None, 'F4 closes only the selected window')
    check(not any(w['id'] == b for w in ipc('scottland/desktop-model')['windows']), 'closing forgets placement with its model window')
    release()
    c = launch('Gamma')
    check(hint(a)['hint'] == 'a' and hint(c)['hint'] == 's', 'closed hint reused without changing surviving letters')
    close_all()

    a = launch('Cycle')
    watch = ModelWatch()
    drag('Cycle', width*.53, height*.36)
    center_memory = center(view('Cycle'))
    published = watch.latest()
    spot = next(w for w in published['windows'] if w['id'] == a)['placement']['positions'][0]
    check(published['version'] > watch.initial['version'] and spot['set'] and
          near((spot['x'] * width, spot['y'] * height), center_memory),
          'real drop publishes a newer complete snapshot with normalized center memory')
    watch.sock.close()
    hold()
    key('A', True); key('A', True); key('A', False); time.sleep(.65)
    check(not view('Cycle')['widgetized'], 'held-key repeat cannot trigger double-tap')
    p = center(view('Cycle'))
    check(view('Cycle')['zone'] == 'continuous', 'selected center first press skips select and moves to periphery')
    choose(a)
    wait_for(lambda: view('Cycle')['widgetized'])
    check(view('Cycle')['hidden'], 'slow center second press widgetizes')
    choose(a)
    check(not view('Cycle')['widgetized'] and near(center(view('Cycle')), center_memory), 'center full loop returns to exact center memory')
    choose(a)
    check(near(center(view('Cycle')), p), 'center loop repeats with exact periphery memory')
    release(); hold(); choose(a)
    check(near(center(view('Cycle')), center_memory), 'selected periphery skips select after a new hold')
    choose(a)
    check(view('Cycle')['widgetized'], 'slow periphery second press goes to widget')
    choose(a)
    check(not view('Cycle')['widgetized'] and near(center(view('Cycle')), p), 'periphery full loop restores exact periphery memory directly from widget')
    choose(a); choose(a); choose(a)
    check(not view('Cycle')['widgetized'] and near(center(view('Cycle')), p), 'periphery second full loop returns to its starting memory again')
    choose(a); choose(a); release()
    rail_memory = hint(a)['memories']
    hold(); choose(a)
    check(near(center(view('Cycle')), center_memory) and not view('Cycle')['widgetized'], 'widget first hint press restores center')
    choose(a)
    check(near(center(view('Cycle')), p), 'widget second press moves to periphery')
    choose(a); release()
    check(view('Cycle')['widgetized'] and hint(a)['memories'] == rail_memory, 'widget full loop returns to widget and retains all memories')
    # A rapid second press requests the rail from either window zone.
    hold(); choose(a); release()
    hold(); rapid_hint(a, 3); time.sleep(.8)
    check(view('Cycle')['widgetized'], 'double tap from selected center sends to rail; third rapid press leaves it there')
    release()
    # A longer configurable interval permits inspecting the mapped widget between rapid presses.
    ipc('wayfire/set-config-options', {'scottland/window_double_tap_delay': 2000})
    hold(); rapid_hint(a, 2); time.sleep(.8)
    widget_before = next(w for w in ipc('scottland/widgets')['widgets'] if int(w['id']) == a)
    memories_before = hint(a)['memories']
    rapid_hint(a, 1); time.sleep(.4)
    widget_after = next(w for w in ipc('scottland/widgets')['widgets'] if int(w['id']) == a)
    check(widget_after['widget_view'] == widget_before['widget_view'] and hint(a)['memories'] == memories_before,
          'rapid repeated hint on a widget does nothing: same widget and memories')
    ipc('wayfire/set-config-options', {'scottland/window_double_tap_delay': 300})
    time.sleep(.35); choose(a)
    check(not view('Cycle')['widgetized'] and near(center(view('Cycle')), center_memory), 'slow press after double tap resumes widget-start loop')
    choose(a); release()
    hold(); rapid_hint(a, 2); time.sleep(.8); release()
    check(view('Cycle')['widgetized'], 'double tap from selected periphery sends window to rail')
    hold(); rapid_hint(a, 2); time.sleep(.8); release()
    check(view('Cycle')['widgetized'], 'double tap starting on widget returns to rail')
    # Changing the setting makes these same real presses ordinary slow cycling.
    ipc('wayfire/set-config-options', {'scottland/window_double_tap_delay': 1})
    hold(); rapid_hint(a, 2, .03); time.sleep(.65)
    check(not view('Cycle')['widgetized'] and near(center(view('Cycle')), p), '1 ms setting makes 30 ms presses cycle instead of double-tap')
    choose(a); release()
    check(view('Cycle')['widgetized'], 'configured interval preserves widget-start full loop')
    ipc('wayfire/set-config-options', {'scottland/window_double_tap_delay': 300})
    # Actual card click and collapsed-icon hint.
    key('LEFTMETA', True); tap('M'); key('LEFTMETA', False); time.sleep(.6)
    hold()
    widget = next(v for v in views() if v['widget'])
    check(hint(a)['visible'] and widget['geometry']['width'] < 120, 'collapsed widget has a hint over its icon')
    release()
    f = widget['frame']
    ipc('stipc/move_cursor', {'x': round(f['x']+f['width']/2), 'y': round(f['y']+f['height']/2)})
    ipc('stipc/feed_button', {'combo':'BTN_LEFT','mode':'press'})
    ipc('stipc/feed_button', {'combo':'BTN_LEFT','mode':'release'})
    time.sleep(.8)
    check(not view('Cycle')['widgetized'] and near(center(view('Cycle')), center_memory), 'WG17 real card click restores remembered center')
    key('LEFTMETA', True); tap('M'); key('LEFTMETA', False)
    drag('Cycle', 6, height*.27)
    wait_for(lambda: any(v['widget'] for v in views()))
    time.sleep(.5)
    left_rail = hint(a)['memories'][3]
    drag('Cycle', width-6, height*.73)
    right_rail = hint(a)['memories'][4]
    check(left_rail['set'] and right_rail['set'], 'real widget drag remembers both rails independently')
    hold(); choose(a); choose(a); choose(a); release()
    check(hint(a)['memories'][3] == left_rail and hint(a)['memories'][4] == right_rail, 'rail cycle returns to exact most recent remembered rail')
    hold(); choose(a); release()
    # Occupy the remembered center. Memory wins over the obstacle.
    b = launch('Blocker')
    drag('Blocker', *center_memory)
    focus(a); hold(); choose(a); choose(a); choose(a); release()
    check(near(center(view('Cycle')), center_memory), 'occupied center memory wins over contention')
    # In full screen, asking with Alt temporarily reveals widgets without ending focus mode.
    hold(); choose(a); choose(a); release()
    full_memory = hint(b)['memories'][0]
    ipc('wm-actions/set-fullscreen', {'view_id': b, 'state': True})
    time.sleep(.7)
    check(next(v for v in views() if v['widget'])['hidden'], 'full screen hides rail widgets')
    hold()
    check(hint(a)['visible'] and not next(v for v in views() if v['widget'])['hidden'], 'Alt in full screen reveals widgets and hints')
    release()
    check(next(v for v in views() if v['widget'])['hidden'], 'Alt release restores full-screen widget hiding')
    focus(b); hold(); choose(b); release()
    check(view('Blocker')['zone'] == 'continuous' and hint(b)['memories'][0] == full_memory, 'cycling out of full screen preserves the prior center memory')
    ipc('wm-actions/set-fullscreen', {'view_id': b, 'state': False})
    time.sleep(.5)
    ipc('wm-actions/set-fullscreen', {'view_id': b, 'state': True})
    time.sleep(.5)
    hold(); choose(b)
    # Physical presses closer together than the fullscreen exit transaction must retain order.
    rapid_hint(b, 2)
    time.sleep(.8)
    check(view('Blocker')['widgetized'] and hint(b)['memories'][0] == full_memory, 'rapid fullscreen cycle queues each step without corrupting center memory')
    release()

    # New periphery-born windows have no center memory; card click uses the shared algorithm.
    close_all()
    a = launch('Peripheral', width*.18, height*.5)
    check(not hint(a)['memories'][0]['set'], 'periphery-born window starts without center memory')
    b = launch('CenterBlock', width*.5, height*.5)
    focus(a); hold(); choose(a); release()
    v = view('Peripheral')
    check(v['zone'] == 'center' and v['applied_scale'] > .999, 'new center placement is always full size')
    ga, gb = v['geometry'], view('CenterBlock')['geometry']
    overlap = max(0,min(ga['x']+ga['width'],gb['x']+gb['width'])-max(ga['x'],gb['x']))*max(0,min(ga['y']+ga['height'],gb['y']+gb['height'])-max(ga['y'],gb['y']))
    check(overlap < 1, 'unremembered center placement avoids occupied space')
    # Both sides remembered independently by real drops, most recent side decides future cycles.
    drag('Peripheral', width*.2, height*.28)
    left = center(view('Peripheral'))
    drag('Peripheral', width*.8, height*.72)
    right = center(view('Peripheral'))
    drag('Peripheral', width*.5, height*.4)
    hold(); choose(a)
    check(near(center(view('Peripheral')), right), 'most recently used side memory wins')
    release()
    memories = hint(a)['memories']
    check(memories[1]['set'] and memories[2]['set'], 'drag drops retain independent left/right memories')
    # Fresh center window chooses the least contended side without inherited side memory.
    close_all()
    launch('LeftHigh', width*.17, height*.25)
    launch('LeftLow', width*.17, height*.75)
    a = launch('SideChoice', width*.49, height*.5)
    hold(); choose(a); release()
    check(center(view('SideChoice'))[0] > width/2, 'no side memory chooses larger contiguous free opening')
    close_all()
    a = launch('NearSide', width*.48, height*.5)
    hold(); choose(a); release()
    check(center(view('NearSide'))[0] < width/2, 'about-equal free sides choose nearer side')
    close_all()
    a = launch('CardFresh', width*.18, height*.5)
    b = launch('CardBlock', width*.5, height*.5)
    focus(a); hold(); choose(a); choose(a); choose(a); release()
    # Periphery -> center creates memory; use a fresh periphery window widgetized by a drag.
    close_all()
    a = launch('CardFresh', width*.18, height*.5)
    b = launch('CardBlock', width*.5, height*.5)
    focus(a); drag('CardFresh', 6, height*.5)
    wait_for(lambda: any(v['widget'] for v in views()))
    time.sleep(.7)
    check(not hint(a)['memories'][0]['set'], 'rail drag from periphery preserves absence of center memory')
    widget = next(v for v in views() if v['widget'])
    f = widget['frame']
    ipc('stipc/move_cursor', {'x': round(f['x']+f['width']/2), 'y': round(f['y']+f['height']/2)})
    ipc('stipc/feed_button', {'combo':'BTN_LEFT','mode':'press'})
    ipc('stipc/feed_button', {'combo':'BTN_LEFT','mode':'release'})
    time.sleep(.8)
    ga, gb = view('CardFresh')['geometry'], view('CardBlock')['geometry']
    overlap = max(0,min(ga['x']+ga['width'],gb['x']+gb['width'])-max(ga['x'],gb['x']))*max(0,min(ga['y']+ga['height'],gb['y']+gb['height'])-max(ga['y'],gb['y']))
    check(overlap < 1 and view('CardFresh')['applied_scale'] > .999, 'WG17 new center uses least-overlap full-size placement')
    model = ipc('scottland/desktop-model')
    check(all(next(w for w in model['windows'] if w['id'] == h['window'])['placement']['positions'] == h['memories']
              for h in hints()['hints']), 'desktop snapshot publishes the same authoritative memories as hints')
    check(all('placement' not in w and 'pending_rail' not in w for slice_name in ('widgets', 'attention')
              for w in ipc('scottland/desktop-model', {'slice': slice_name})['windows']),
          'external slices omit placement and pending rail geometry')
    # Reload into a new library copy, keeping open widgets, positions, assignments, render state.
    hold(); rapid_hint(a, 2); time.sleep(.8); release()
    wait_for(lambda: any(v['widget'] for v in views()))
    time.sleep(.5)
    before = hints()['hints']
    runtime = Path(os.environ['XDG_RUNTIME_DIR'])
    display = (Path(os.environ['SCOTTLAND_HEADLESS_DIR'])/'display').read_text().strip()
    mark = runtime/'scottland'/(display+'.reloading')
    mark.touch()
    fresh = Path(os.environ['SCOTTLAND_HEADLESS_DIR'])/'libscottland-windowing-reload.so'
    subprocess.run(['cp','build/libscottland.so',str(fresh)],check=True)
    plugins = ipc('wayfire/get-config-option', {'option':'core/plugins'})['value']
    ipc('wayfire/set-config-options', {'core/plugins':' '.join(str(fresh) if p=='scottland' else p for p in plugins.split())})
    time.sleep(1)
    mark.unlink()
    after = hints()['hints']
    check([(h['window'],h['hint'],h['memories']) for h in before] == [(h['window'],h['hint'],h['memories']) for h in after], 'reload preserves zone memories and hint assignments')
    hold()
    check(hints()['active'] and all(h['visible'] for h in hints()['hints']), 'reloaded plugin renders hints and accepts physical Alt input')
    release()
    close_all()
    # Capacity changes the label width, not the stable assignment slots.
    ids = [launch(f'Overflow{i:02}') for i in range(27)]
    check(hint(ids[0])['hint'] == 'aa' and hint(ids[-1])['hint'] == 'sa', '27 open windows use prefix-free two-letter hints')
    focus(ids[0]); hold()
    subprocess.run(['tests/headless.sh', 'run', 'grim', str(artifacts/'double-hints.png')], check=True)
    choose(ids[-1])
    check(focused() == ids[-1], 'real two-letter hint selects the overflow window')
    rapid_hint(ids[-1], 2); time.sleep(.8)
    check(view('Overflow26')['widgetized'], 'repeating complete two-letter hint sends overflow window to rail')
    release()
    ipc('window-rules/close-view', {'id': ids[-1]})
    time.sleep(.3)
    check(hint(ids[0])['hint'] == 'aa', 'two-letter labels stay stable when overflow window closes')
    close_all()

    # WP7: a window Scottland places keeps off the screen's edges by the halo width plus 5 pt.
    pad = 32 / 3 + 5
    blockers = [launch('PadBlock%d' % i, width * x, height * .5) for i, x in enumerate((.3, .5, .7))]
    p = launch('PadMe', width * .5, height * .2)
    focus(p); hold(); choose(p); release()   # already selected: straight to periphery (no memory)
    time.sleep(.8)
    f = view('PadMe')['frame']
    inside = f['x'] >= pad - 1 and f['y'] >= pad - 1 and f['x'] + f['width'] <= width - pad + 1 \
        and f['y'] + f['height'] <= height - pad + 1
    check(inside, 'WP7 a placed window keeps the screen padding (halo + 5 pt)')
    close_all()
except Exception as error:
    check(False, 'suite exception: '+repr(error))
finally:
    print(f'{passed} passed, {failed} failed', flush=True)
    for process in clients:
        try:
            process.wait(timeout=.2)
        except subprocess.TimeoutExpired:
            pass  # session owner stops its clients; never kill by process-name pattern
sys.exit(bool(failed))
