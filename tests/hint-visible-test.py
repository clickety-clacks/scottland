#!/usr/bin/env python3
"""Pixel masks against foreground rectangles, real Alt/drag input and stable attachments."""
import json
import math
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path
import gi
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf

artifacts = Path(sys.argv[1]).resolve()
clients = []
passed = failed = 0
socket_path = subprocess.check_output(['tests/headless.sh', 'run', 'python3', '-c',
    "import os;print(os.environ['WAYFIRE_SOCKET'])"], text=True).strip()


def ipc(method, data=None):
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(10)
        s.connect(socket_path)
        def read(n):
            result = b''
            while len(result) < n:
                chunk = s.recv(n-len(result))
                if not chunk:
                    raise ConnectionError('headless compositor disconnected')
                result += chunk
            return result
        body = json.dumps({'method': method, 'data': data or {}}).encode()
        s.sendall(struct.pack('<I', len(body))+body)
        return json.loads(read(struct.unpack('<I', read(4))[0]))


def check(ok, name):
    global passed, failed
    print(('PASS  ' if ok else 'FAIL  ')+name, flush=True)
    passed += bool(ok)
    failed += not ok


def key(name, down):
    ipc('stipc/feed_key', {'key': 'KEY_'+name, 'state': down})


def views():
    return {v['id']: v for v in ipc('scottland/layout-state')['views']}


def hints():
    return {h['window']: h for h in ipc('scottland/hints')['hints']}


def wait(predicate):
    until = time.monotonic()+10
    while time.monotonic() < until:
        result = predicate()
        if result:
            return result
        time.sleep(.05)
    raise RuntimeError('timed out waiting for headless state')


def drag(identifier, x, y):
    ipc('window-rules/focus-view', {'id': identifier})
    wait(lambda: abs(hints()[identifier]['dx']) + abs(hints()[identifier]['dy']) < .5)
    f = views()[identifier]['frame']
    cx, cy = f['x']+f['width']/2, f['y']+f['height']/2
    ipc('stipc/move_cursor', {'x': round(cx), 'y': round(cy)})
    key('LEFTMETA', True)
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'press'})
    for i in range(1, 11):
        ipc('stipc/move_cursor', {'x': round(cx+(x-cx)*i/10), 'y': round(cy+(y-cy)*i/10)})
        time.sleep(.03)
    # Rest before drop to avoid testing drag inertia instead of a stationary stack.
    time.sleep(.2)
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'release'})
    key('LEFTMETA', False)
    ipc('stipc/move_cursor', {'x': 640, 'y': 10})
    time.sleep(.8)


def hold():
    key('LEFTALT', True)
    wait(lambda: ipc('scottland/hints')['active'])
    time.sleep(1.5)


def release():
    key('LEFTALT', False)
    time.sleep(.8)


def rect(v, h=None):
    f = v['frame']
    return (f['x']+(h['dx'] if h else 0), f['y']+(h['dy'] if h else 0), f['width'], f['height'])


def area(a, b):
    return max(0, min(a[0]+a[2], b[0]+b[2])-max(a[0], b[0])) * max(
        0, min(a[1]+a[3], b[1]+b[3])-max(a[1], b[1]))


def overlap_sum(rects):
    return sum(area(a, b) for i, a in enumerate(rects) for b in rects[:i])


def capture(name, order):
    vs, hs = views(), hints()
    (artifacts/(name+'.json')).write_text(json.dumps({'views': vs, 'hints': hs, 'front_to_back': order}, indent=2))
    path = artifacts/(name+'.png')
    subprocess.run(['tests/headless.sh', 'run', 'grim', str(path)], check=True)
    image = GdkPixbuf.Pixbuf.new_from_file(str(path))
    pixels, stride, channels = image.get_pixels(), image.get_rowstride(), image.get_n_channels()
    foreground = []
    for identifier in order:
        h, v = hs[identifier], vs[identifier]
        b = h['badge']
        radius = b['size']/2
        cx, cy = b['x']+radius, b['y']+radius
        r = rect(v, h)
        inside = outside = True
        # Every raster pixel of the circle (excluding its antialiased boundary) must lie
        # in this window's drawn footprint and outside every foreground footprint.
        for y in range(math.floor(b['y']), math.ceil(b['y']+b['size'])):
            for x in range(math.floor(b['x']), math.ceil(b['x']+b['size'])):
                if math.hypot(x+.5-cx, y+.5-cy) > radius-1:
                    continue
                inside &= r[0] <= x+.5 <= r[0]+r[2] and r[1] <= y+.5 <= r[1]+r[3]
                outside &= all(not (o[0] <= x+.5 <= o[0]+o[2] and o[1] <= y+.5 <= o[1]+o[3]) for o in foreground)
        # Confirm the screenshot actually has the assigned solid letter dye within the
        # same visible circle; geometry alone cannot establish that a label rendered.
        color = tuple(round(c*255) for c in h['color'])
        count = 0
        for y in range(max(0, math.floor(b['y'])), min(720, math.ceil(b['y']+b['size']))):
            for x in range(max(0, math.floor(b['x'])), min(1280, math.ceil(b['x']+b['size']))):
                p = pixels[y*stride+x*channels:y*stride+x*channels+3]
                count += max(abs(a-c) for a, c in zip(p, color)) < 12
        check(count > 20, name+': '+v['title']+' letter dye is present in screenshot')
        desired = max(72, min(132, min(r[2:])*.34))
        fit = math.floor(max(0, 2*(h['clearance']-1)/1.06))
        minimum = 48
        check(b['size'] == max(minimum, round(min(desired, fit))),
              name+': '+v['title']+' size respects the 48px minimum and available clearance')
        if fit >= minimum:
            check(inside and outside and not h['edge_label'],
                  name+': '+v['title']+' minimum-sized circle stays in its exposed window')
        else:
            check(b['size'] == minimum,
                  name+': '+v['title']+' hint stays visible at minimum when no clear spot fits')
        foreground.append(r)
    return vs, hs


try:
    ipc('wayfire/set-config-options', {'scottland/sounds': False, 'place/mode': 'pointer'})
    palette = artifacts/'palette.json'
    colors = {'scheme':'dark', 'background':'#1f232c', 'foreground':'#d8deea',
              'accent':'#81a1c1', 'text_scale':1, 'reduced_motion':False}
    palette.write_text(json.dumps(colors))
    # A reused Wayland display name can retain a previous suite's palette (e.g. 3×
    # text). Pin only this fresh session's palette; never change desktop preferences.
    palette_path = Path(subprocess.check_output(['tests/headless.sh', 'run', 'python3', '-c',
        "import os;print(os.path.join(os.environ['XDG_RUNTIME_DIR'],'scottland',os.environ['WAYLAND_DISPLAY']+'.palette.json'))"],
        text=True).strip())
    temporary = artifacts/'compositor-palette.json'
    temporary.write_text(json.dumps(colors))
    palette_path.write_text(temporary.read_text())
    ids = []
    specs = [('Back', 980, 620), ('Middle', 780, 520), ('Front', 540, 380)]
    if sys.argv[2:] == ['--baseline-medium']:
        specs = [('Back', 400, 280), ('Middle', 480, 300), ('Front', 540, 380)]
    for name, w, h in specs:
        clients.append(subprocess.Popen(['tests/headless.sh', 'run', 'python3',
            str(Path('tests/hint-style-app.py').resolve()), name, str(w), str(h), str(palette)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        v = wait(lambda: next((v for v in views().values() if v['title'] == name and 'frame' in v), None))
        time.sleep(.5)
        ids.append(v['id'])
        drag(v['id'], 640, 360)
    before = views()
    before_hints = hints()
    original_overlap = overlap_sum([rect(before[i]) for i in ids])
    subprocess.run(['tests/headless.sh', 'run', 'grim', str(artifacts/'stack-before.png')], check=True)
    hold()
    if sys.argv[2:] in (['--baseline'], ['--baseline-medium']):
        # Run this same real-input fixture in an archived baseline checkout. Its old IPC
        # has no WK31 clearance fields; retain geometry/screenshot comparison evidence.
        state, drawn = hints(), views()
        old_overlap = overlap_sum([rect(drawn[i], state[i]) for i in ids])
        (artifacts/'baseline.json').write_text(json.dumps({'views': drawn, 'hints': state,
            'unheld_overlap': original_overlap, 'held_overlap': old_overlap}, indent=2))
        subprocess.run(['tests/headless.sh', 'run', 'grim', str(artifacts/'baseline-held.png')], check=True)
        print(f'Baseline real-Alt fixture: unheld {original_overlap}, held {old_overlap}', flush=True)
        raise SystemExit(0)
    vs, hs = capture('stack-held', list(reversed(ids)))
    reduced = overlap_sum([rect(vs[i], hs[i]) for i in ids])
    check(0 < reduced <= original_overlap, 'oversubscribed stack moves only to expose hints while retaining overlap')
    check(all(rect(vs[i]) == rect(before[i]) and hs[i]['memories'] == before_hints[i]['memories'] for i in ids),
          'visual separation preserves geometry, scale and memories')
    stable = {i: (h['badge'], h['dx'], h['dy']) for i, h in hs.items()}
    time.sleep(1)
    check(stable == {i: (h['badge'], h['dx'], h['dy']) for i, h in hints().items()},
          'hint anchors and sizes stay exactly stable during an unchanged Alt hold')
    # Explicitly raising a rear window must update occlusion without changing its letter.
    def centers(state):
        return {i: (h['badge']['x']+h['badge']['size']/2, h['badge']['y']+h['badge']['size']/2)
                for i, h in state.items() if h.get('visible') and 'badge' in h}
    samples = [centers(hints())]
    ipc('window-rules/focus-view', {'id': ids[0]})
    interim_valid = True
    invalid_sample = None
    for _ in range(10):
        current, drawn_now = hints(), views()
        samples.append(centers(current))
        for i, h in current.items():
            if not h.get('visible') or 'badge' not in h:
                continue
            b = h['badge']; radius = b['size']/2
            box = rect(drawn_now[i], h)
            foreground = [rect(drawn_now[j], current[j]) for j in ([ids[0]] if i != ids[0] else [])]
            for angle in range(8):
                x = b['x']+radius+math.cos(angle*math.pi/4)*radius*.95
                y = b['y']+radius+math.sin(angle*math.pi/4)*radius*.95
                valid = (box[0] <= x <= box[0]+box[2] and box[1] <= y <= box[1]+box[3]
                         and all(not (o[0] <= x <= o[0]+o[2] and o[1] <= y <= o[1]+o[3])
                                 for o in foreground))
                if not valid and invalid_sample is None:
                    invalid_sample = {'window': i, 'hint': h, 'point': (x,y),
                                      'own': box, 'foreground': foreground}
                interim_valid &= valid
        time.sleep(.025)
    if invalid_sample:
        (artifacts/'raise-invalid.json').write_text(json.dumps(invalid_sample, indent=2))
    check(interim_valid, 'every drawn hint stays inside its visible window during a raise')
    time.sleep(1)
    _, raised = capture('stack-raised', [ids[0], ids[2], ids[1]])
    check(all(raised[i]['hint'] == hs[i]['hint'] for i in ids), 'raising changes visible regions while retaining assignments')
    final = centers(raised)
    traveler = max(ids, key=lambda i: math.dist(samples[0][i], final[i]))
    distance = math.dist(samples[0][traveler], final[traveler])
    steps = [math.dist(a[traveler], b[traveler]) for a, b in zip(samples, samples[1:])
             if traveler in a and traveler in b]
    check(distance > 20 and (any(traveler not in sample for sample in samples[1:]) or
                              (steps and max(steps) < distance*.9)),
          'changed attachment eases or briefly waits for a wholly visible path')
    (artifacts/'raise-motion.json').write_text(json.dumps(samples, indent=2))
    key('ESC', True)
    key('ESC', False)
    wait(lambda: all(not h['visible'] for h in hints().values()))
    wait(lambda: all(abs(h['dx'])+abs(h['dy']) < .1 for h in hints().values()))
    escaped = hints()
    check(not ipc('scottland/hints')['active'] and
          all(not h['visible'] for h in escaped.values()) and
          all(abs(h['dx'])+abs(h['dy']) < .1 for h in escaped.values()) and
          all(rect(views()[i], escaped[i]) == rect(views()[i]) for i in ids),
          'Esc eases every hint offset back to exact true geometry')
    release()
    hold()
    release()
    check(all(rect(views()[i]) == rect(before[i]) and
              abs(hints()[i]['dx'])+abs(hints()[i]['dy']) < .1 for i in ids),
          'Alt release preserves true geometry and leaves no visual offset')
    (artifacts/'overlap.json').write_text(json.dumps({'before': original_overlap, 'held': reduced}, indent=2))
    for client in clients:
        client.terminate()
        client.wait(timeout=5)
    clients.clear()
    wait(lambda: not views())
    medium = []
    for name, w, h in [('Small', 400, 280), ('Medium', 480, 300), ('Larger', 540, 380)]:
        clients.append(subprocess.Popen(['tests/headless.sh', 'run', 'python3',
            str(Path('tests/hint-style-app.py').resolve()), name, str(w), str(h), str(palette)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        v = wait(lambda: next((v for v in views().values() if v['title'] == name and 'frame' in v), None))
        time.sleep(.5)
        medium.append(v['id'])
        drag(v['id'], 640, 360)
    medium_before = overlap_sum([rect(views()[i]) for i in medium])
    hold()
    drawn, state = capture('fitting-stack-held', list(reversed(medium)))
    medium_held = overlap_sum([rect(drawn[i], state[i]) for i in medium])
    check(medium_before > 0 and 0 < medium_held < medium_before,
          'windows retain overlap once their hint circles fit')
    check([state[i]['badge']['size'] for i in medium] == [95, 102, 129],
          'different displayed window sizes produce proportionally different circles')
    (artifacts/'fitting-overlap.json').write_text(json.dumps({'before': medium_before, 'held': medium_held}, indent=2))
    release()
    for client in clients:
        client.terminate()
        client.wait(timeout=5)
    clients.clear()
    wait(lambda: not views())
    # Mike's layout: a large front window covers almost all of a back window, leaving
    # an 80px left strip. Only enough visual movement to fit the 132px circle is allowed.
    peeking = []
    for name, x in [('PeekingBack', 640), ('CoveringFront', 720)]:
        clients.append(subprocess.Popen(['tests/headless.sh', 'run', 'python3',
            str(Path('tests/hint-style-app.py').resolve()), name, '800', '520', str(palette)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        v = wait(lambda: next((v for v in views().values() if v['title'] == name and 'frame' in v), None))
        time.sleep(.5)
        peeking.append(v['id'])
        drag(v['id'], x, 360)
    before_peek = views()
    hold()
    drawn, state = capture('mike-left-strip', list(reversed(peeking)))
    back, front = peeking
    check(abs(state[front]['dx']) + abs(state[front]['dy']) < 1 and
          -66 < state[back]['dx'] < -58 and abs(state[back]['dy']) < 1,
          'left-strip case moves only the rear window by the ~62px needed for its circle')
    check(state[back]['badge']['x']+state[back]['badge']['size'] <= rect(drawn[front],state[front])[0]+1 and
          state[back]['badge']['size'] == 132 and state[front]['badge']['size'] == 132,
          'rear hint sits in its left visible strip and front hint remains inside the front window')
    check(all(rect(drawn[i]) == rect(before_peek[i]) for i in peeking),
          'left-strip declutter changes neither real window geometry nor scale')
    release()
    for client in clients:
        client.terminate()
        client.wait(timeout=5)
    clients.clear()
    wait(lambda: not views())
    # Fully covered rear windows can move around the focused front window.
    huge = []
    for name in ('HugeBack', 'HugeMiddle', 'HugeFront'):
        clients.append(subprocess.Popen(['tests/headless.sh', 'run', 'python3',
            str(Path('tests/hint-style-app.py').resolve()), name, '1000', '600', str(palette)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        v = wait(lambda: next((v for v in views().values() if v['title'] == name and 'frame' in v), None))
        time.sleep(.5)
        huge.append(v['id'])
        drag(v['id'], 640, 360)
    hold()
    drawn, state = capture('fully-hidden', list(reversed(huge)))
    check(all(state[i]['visible'] and not state[i]['edge_label'] and
              120 <= state[i]['badge']['size'] <= 132 for i in huge),
          'all three formerly covered windows have clearance-limited interior circles')
    check(abs(state[huge[-1]]['dx'])+abs(state[huge[-1]]['dy']) < 1 and
          any(math.hypot(state[i]['dx'],state[i]['dy']) > 100 for i in huge[:-1]),
          'focused front stays at true geometry while fully covered rear windows emerge')
    (artifacts/'fully-hidden.json').write_text(json.dumps({'views': views(), 'hints': state}, indent=2))
    before_select = rect(views()[huge[0]])
    key('A', True); key('A', False)
    wait(lambda: ipc('window-rules/get-focused-view')['info']['id'] == huge[0])
    time.sleep(1.3)
    selected = hints()
    check(abs(selected[huge[0]]['dx'])+abs(selected[huge[0]]['dy']) < .2 and
          rect(views()[huge[0]]) == before_select,
          'selecting a shifted window returns it to true geometry as the new anchor')
    capture('fully-hidden-selected', [huge[0], huge[2], huge[1]])
    # Selection exposes the chosen window at true geometry; the move itself is real.
    before_push = rect(views()[huge[0]])
    key('RIGHT', True)
    key('RIGHT', False)
    motion = []
    for _ in range(12):
        motion.append((views()[huge[0]], hints()))
        time.sleep(.035)
    check(all(abs(h[huge[0]]['dx'])+abs(h[huge[0]]['dy']) < .2 for _, h in motion),
          'focused window has no avoidance offset during a real arrow push and coast')
    check(len({round(v['frame']['x']) for v, _ in motion}) > 1,
          'real arrow input moved the anchored window while avoidance stayed active')
    check(any(len({round(h[i]['dx'], 1) for _, h in motion}) > 1 for i in huge[1:]),
          'other windows re-shift live around the focused arrow-driven window')
    time.sleep(1)
    capture('fully-hidden-pushed', [huge[0], huge[2], huge[1]])
    release()
    unheld = hints()
    check(not ipc('scottland/hints')['active'] and
          all(abs(unheld[i]['dx'])+abs(unheld[i]['dy']) < .1 for i in huge) and
          abs(rect(views()[huge[0]])[0]-before_push[0]) > 20,
          'Alt release clears every offset and keeps the explicitly pushed window move')
    # Start a real Super-drag on the exposed hint window while its temporary offset is
    # visible. Drag start ends this Alt hint chord; the dropped geometry remains real.
    hold()
    held = hints()
    dragged = max(huge[1:], key=lambda i: math.hypot(held[i]['dx'], held[i]['dy']))
    drag_hint = held[dragged]
    drag_before = rect(views()[dragged])
    drag_x = round(drag_hint['badge']['x']+drag_hint['badge']['size']/2)
    drag_y = round(drag_hint['badge']['y']+drag_hint['badge']['size']/2)
    subprocess.run(['tests/headless.sh', 'run', 'grim', str(artifacts/'hint-drag-before.png')], check=True)
    ipc('stipc/move_cursor', {'x': drag_x, 'y': drag_y})
    key('LEFTMETA', True)
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'press'})
    for step in range(1, 10):
        ipc('stipc/move_cursor', {'x': drag_x+step*12, 'y': drag_y})
        time.sleep(.035)
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'release'})
    key('LEFTMETA', False)
    release()
    check(not ipc('scottland/hints')['active'] and
          abs(rect(views()[dragged])[0]-drag_before[0]) > 20 and
          all(abs(h['dx'])+abs(h['dy']) < .1 for h in hints().values()),
          'dragging a shifted window keeps its drop and returns every hint offset to zero')
    subprocess.run(['tests/headless.sh', 'run', 'grim', str(artifacts/'hint-drag-after.png')], check=True)
    subprocess.run(['tests/headless.sh', 'run', 'grim', str(artifacts/'no-avoidance-before-drag.png')], check=True)
    real_before = rect(views()[huge[0]])
    check(real_before[2] > 700, 'pointer fixture keeps the focused surface an ordinary window')
    cx, cy = real_before[0]+real_before[2]/2, real_before[1]+real_before[3]/2
    ipc('stipc/move_cursor', {'x': round(cx), 'y': round(cy)})
    key('LEFTMETA', True)
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'press'})
    drag_samples = []
    drag_diagnostics = []
    for step in range(1, 9):
        ipc('stipc/move_cursor', {'x': round(cx+step*12), 'y': round(cy)})
        time.sleep(.045)
        sample = hints()
        drag_samples.append(sample)
        drag_diagnostics.append({'offsets': {i: (h['dx'], h['dy']) for i, h in sample.items()},
            'frame': rect(views()[huge[0]]), 'drag': ipc('scottland/desktop-model').get('drag')})
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'release'})
    key('LEFTMETA', False)
    (artifacts/'no-avoidance-drag.json').write_text(json.dumps({'before': real_before,
        'samples': drag_diagnostics, 'after': rect(views()[huge[0]])}, indent=2))
    check(all(abs(s[i]['dx'])+abs(s[i]['dy']) < .1 for s in drag_samples for i in huge),
          'pointer drag outside window mode creates no hint avoidance offsets')
    time.sleep(.8)
    check(abs(rect(views()[huge[0]])[0] - real_before[0]) > 20 and
          all(abs(h['dx'])+abs(h['dy']) < .1 for h in hints().values()),
          'after pointer drop, the real move remains with no hint offsets')
    subprocess.run(['tests/headless.sh', 'run', 'grim', str(artifacts/'no-avoidance-after-drag.png')], check=True)
    # Esc also stops a live coast without undoing the movement the user requested.
    before_esc_move = rect(views()[huge[0]])
    hold()
    key('LEFT', True); key('LEFT', False)
    wait(lambda: abs(rect(views()[huge[0]])[0]-before_esc_move[0]) > 5)
    key('ESC', True); key('ESC', False)
    wait(lambda: not ipc('scottland/hints')['active'] and
         all(abs(h['dx'])+abs(h['dy']) < .1 for h in hints().values()))
    release()
    after_esc_move = rect(views()[huge[0]])
    check(abs(after_esc_move[0]-before_esc_move[0]) > 5 and
          all(rect(views()[i], hints()[i]) == rect(views()[i]) for i in huge),
          'Esc keeps an explicit arrow move and clears all hint offsets to true geometry')
finally:
    key('LEFTALT', False)
    for client in clients:
        if client.poll() is None:
            client.terminate()
            client.wait(timeout=5)
    print(f'{passed} passed, {failed} failed', flush=True)
if failed:
    raise SystemExit(1)
