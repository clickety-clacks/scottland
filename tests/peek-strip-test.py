#!/usr/bin/env python3
"""WK13/P12/P13: the peeking strip with real input on a headless session.

Each window is a solid color, so what a window shows is measured from screenshots, not from
solver state: a rear window must show a strip of at least 24 x 100 pt x text scale of its own
color. Also: Window mode makes room for full hints and returns to the peek layout, 1 px drags stay
calm, dense stacks hide nothing, zone limits hold, a window with no room shows only its hint, and
the engram case (a top sliver) gets its hint out from under the front window.
"""
import json
import math
import re
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

artifacts = Path(sys.argv[1]).resolve()
clients = {}
passed = failed = 0
socket_path = subprocess.check_output(['tests/headless.sh', 'run', 'python3', '-c',
    "import os;print(os.environ['WAYFIRE_SOCKET'])"], text=True).strip()
palette_path = Path(subprocess.check_output(['tests/headless.sh', 'run', 'python3', '-c',
    "import os;print(os.path.join(os.environ['XDG_RUNTIME_DIR'],'scottland',os.environ['WAYLAND_DISPLAY']+'.palette.json'))"],
    text=True).strip())
solve_max_ms = 0.0
slice_units_max = 0
pass_slices_max = 0


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


def check(ok, name, detail=''):
    global passed, failed
    print(('PASS  ' if ok else 'FAIL  ')+name+(f'  [{detail}]' if detail else ''), flush=True)
    passed += bool(ok)
    failed += not ok


def key(name, down):
    ipc('stipc/feed_key', {'key': 'KEY_'+name, 'state': down})


def views():
    return {v['id']: v for v in ipc('scottland/layout-state')['views']}


def state():
    global solve_max_ms, slice_units_max, pass_slices_max
    s = ipc('scottland/hints')
    solve_max_ms = max(solve_max_ms, s.get('avoidance_solve_max_ms', 0))
    slice_units_max = max(slice_units_max, s.get('avoidance_slice_units_max', 0))
    pass_slices_max = max(pass_slices_max, s.get('avoidance_pass_slices_max', 0))
    return s


def hints():
    return {h['window']: h for h in state()['hints']}


def wait(predicate, timeout=10):
    until = time.monotonic()+timeout
    while time.monotonic() < until:
        result = predicate()
        if result:
            return result
        time.sleep(.05)
    raise RuntimeError('timed out waiting for headless state')


def settled(timeout=10):
    """Offsets at their targets, no pass pending, badges still."""
    def still():
        a = state()
        time.sleep(.15)
        b = state()
        hs = {h['window']: h for h in b['hints']}
        steady = all(abs(h['dx']-h['target_dx']) < .1 and abs(h['dy']-h['target_dy']) < .1 for h in hs.values())
        same = all(x.get('badge') == y.get('badge') for x, y in zip(a['hints'], b['hints']))
        return hs if steady and same and not b['avoidance_solve_pending'] else None
    return wait(still, timeout)


def set_palette(text_scale):
    colors = {'scheme': 'dark', 'background': '#1f232c', 'foreground': '#d8deea',
              'accent': '#81a1c1', 'text_scale': text_scale, 'reduced_motion': False}
    palette_path.write_text(json.dumps(colors))
    time.sleep(.6)


def spawn(name, w, h, color):
    palette = artifacts/f'app-{name}.json'
    palette.write_text(json.dumps({'background': color}))
    clients[name] = subprocess.Popen(['tests/headless.sh', 'run', 'python3',
        str(Path('tests/hint-style-app.py').resolve()), name, str(w), str(h), str(palette)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    v = wait(lambda: next((v for v in views().values() if v['title'] == name and 'frame' in v), None))
    time.sleep(.4)
    return v['id']


def close_all():
    names = set(clients)
    for client in clients.values():
        client.terminate()
    clients.clear()
    wait(lambda: not any(v.get('title') in names for v in views().values()))
    time.sleep(.8)


def drag(identifier, x, y, steps=10):
    """Super+drag the window's center to (x, y): a real move."""
    ipc('window-rules/focus-view', {'id': identifier})
    h = hints()[identifier]
    d = h['drawn']
    cx, cy = d['x']+d['width']/2, d['y']+d['height']/2
    ipc('stipc/move_cursor', {'x': round(cx), 'y': round(cy)})
    key('LEFTMETA', True)
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'press'})
    for i in range(1, steps+1):
        ipc('stipc/move_cursor', {'x': round(cx+(x-cx)*i/steps), 'y': round(cy+(y-cy)*i/steps)})
        time.sleep(.03)
    time.sleep(.2)
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'release'})
    key('LEFTMETA', False)
    ipc('stipc/move_cursor', {'x': 640, 'y': 715})
    time.sleep(.8)


def drawn(h):
    d = h['drawn']
    return (d['x'], d['y'], d['width'], d['height'])


def overlap(a, b):
    return max(0, min(a[0]+a[2], b[0]+b[2])-max(a[0], b[0])) * max(0, min(a[1]+a[3], b[1]+b[3])-max(a[1], b[1]))


def order(hs):
    return [h['window'] for h in sorted(hs.values(), key=lambda h: h['avoidance_order']) if h['avoidance_order'] >= 0]


def screenshot(name):
    png = artifacts/(name+'.png')
    subprocess.run(['tests/headless.sh', 'run', 'grim', str(png)], check=True)
    ppm = artifacts/(name+'.ppm')
    subprocess.run(['tests/headless.sh', 'run', 'grim', '-t', 'ppm', str(ppm)], check=True)
    data = ppm.read_bytes()
    parts, pos = [], 0
    while len(parts) < 4:
        while data[pos:pos+1].isspace():
            pos += 1
        end = pos
        while not data[end:end+1].isspace():
            end += 1
        parts.append(data[pos:end])
        pos = end
    w, h = int(parts[1]), int(parts[2])
    ppm.unlink()
    return w, h, data[pos+1:]


NEUTRALS = ['#181818', '#2a2a2a', '#505058', '#6e6e78', '#8c8c96', '#b4b4be', '#e6e6ee']


def classify(image, colors):
    """Label every pixel with the nearest of the known colors (the windows' and the neutrals of
    the background and the goo), so gloss and shading inside a window still count as it."""
    w, h, px = image
    refs = [tuple(int(c[i:i+2], 16) for i in (1, 3, 5)) for c in list(colors)+NEUTRALS]
    cache = {}
    labels = []
    for y in range(h):
        row = px[y*w*3:(y+1)*w*3]
        line = bytearray(w)
        for x in range(w):
            p = row[3*x:3*x+3]
            label = cache.get(p)
            if label is None:
                r, g, b = p
                best = min(range(len(refs)), key=lambda k: (refs[k][0]-r)**2+(refs[k][1]-g)**2+(refs[k][2]-b)**2)
                label = cache[p] = best+1 if best < len(colors) else 0
            line[x] = label
        labels.append(bytes(line))
    return labels


def strip(labels, index, depth, length, frame=None):
    """Does the window labelled `index` (from classify) show a length x depth or depth x length
    block? A strip longer than the window in a dimension is reduced to the window's size there
    (`frame`). Returns (found, pixels of it)."""
    table = bytes(1 if k == index+1 else 0 for k in range(256))
    mask = [line.translate(table) for line in labels]
    count = sum(line.count(1) for line in mask)
    if not count:
        return False, 0
    h = len(mask)
    def fits(dw, dh):
        dw, dh = max(1, math.ceil(dw)), max(1, math.ceil(dh))
        pattern = re.compile(b'\\x01{%d,}' % dw)
        rows = [[(m.start()+dw-1, m.end()) for m in pattern.finditer(line)] if 1 in line else [] for line in mask]
        for y in range(h-dh+1):
            current = rows[y]
            k = 1
            while current and k < dh:
                current = [(max(a, c), min(b, d)) for a, b in current for c, d in rows[y+k] if max(a, c) < min(b, d)]
                k += 1
            if current:
                return True
        return False
    fw, fh = (frame[2], frame[3]) if frame else (10**6, 10**6)
    return fits(min(length, fw), min(depth, fh)) or fits(min(depth, fw), min(length, fh)), count


EDGE = 2  # px: the window frame's own antialiased outline


def check_strips(name, image, colors, hs, ids, s):
    labels = classify(image, colors.values())
    names = list(colors)
    depth, length = 24*s - EDGE, 100*s - EDGE
    for title, identifier in ids.items():
        h = hs[identifier]
        found, count = strip(labels, names.index(title), depth, length, drawn(h))
        check(found, f'{name}: {title} shows a {24*s:.0f} x {100*s:.0f} strip of itself on screen',
              f'{h.get("outcome")}/{h.get("rung")} target ({h["target_dx"]:.1f},{h["target_dy"]:.1f}) '
              f'{count} px of its color')


try:
    # Strips are measured on the goo without its shine, relief and overlap film: those bands of
    # light change a 24 px strip's color. D also checks the shipped look.
    shipped_look = {'scottland/goo_shine': .75, 'scottland/goo_relief': 5.0, 'scottland/goo_overlap_film': 4.0}
    for option in shipped_look:
        try:
            reply = ipc('wayfire/get-config-option', {'option': option})
            shipped_look[option] = float(reply['value'])
        except (KeyError, ValueError, TypeError):
            pass
    print(f'      shipped goo look: {shipped_look}', flush=True)
    flat_look = {'scottland/goo_shine': 0.0, 'scottland/goo_relief': 0.5, 'scottland/goo_overlap_film': 0.0}
    ipc('wayfire/set-config-options', {'scottland/sounds': False, 'place/mode': 'pointer',
        'scottland/window_avoidance_always': True, 'scottland/goo': True, **flat_look})
    screen = (0, 0, 1280, 720)

    # A. Always on: three stacked center windows, at text scale 1 and 1.64.
    colors = {'Back': '#2850c8', 'Mid': '#28b450', 'Front': '#c83c3c'}
    for s in (1, 1.64):
        set_palette(s)
        ids = {}
        # Back and Mid are entirely behind Front: they must move to peek.
        for title, w, h, x, y in [('Back', 600, 400, 630, 350), ('Mid', 700, 460, 650, 370),
                                  ('Front', 900, 600, 640, 360)]:
            ids[title] = spawn(title, w, h, colors[title])
            drag(ids[title], x, y)
        hs = settled()
        image = screenshot(f'A-always-{s}')
        for title in ('Back', 'Mid'):
            print(f'      A {s}: {title} outcome {hs[ids[title]]["outcome"]} rung {hs[ids[title]]["rung"]} '
                  f'target ({hs[ids[title]]["target_dx"]:.1f},{hs[ids[title]]["target_dy"]:.1f})', flush=True)
        check_strips(f'A always-on, text {s}', image, colors, hs, {t: ids[t] for t in ('Back', 'Mid')}, s)
        peek_targets = {t: (hs[i]['target_dx'], hs[i]['target_dy']) for t, i in ids.items()}

        # B. Window mode: rear windows get full room for their hints; Alt up returns them.
        key('LEFTALT', True)
        wait(lambda: state()['active'])
        time.sleep(1.2)
        hs = settled()
        screenshot(f'B-window-mode-{s}')
        front = drawn(hs[ids['Front']])
        for title in ('Back', 'Mid'):
            h = hs[ids[title]]
            b = h['badge']
            c = (b['x']+b['size']/2, b['y']+b['size']/2)
            r = b['size']/2
            covers = [drawn(hs[j]) for j in order(hs)[:order(hs).index(ids[title])]]
            clear = all(math.dist(c, (min(max(c[0], o[0]), o[0]+o[2]), min(max(c[1], o[1]), o[1]+o[3]))) >= r - .5
                        for o in covers)
            if h['rung'] in ('full', 'minimum'):
                check(clear, f'B Window mode, text {s}: {title} has room for its hint, clear of the windows in front',
                      f'rung {h["rung"]} rule {h["rule"]} badge {b["size"]:.0f}px target ({h["target_dx"]:.1f},{h["target_dy"]:.1f})')
            else:
                # Decision 5: no room inside its limits, so the hint sits on its strip and
                # overlaps the front window's edge: its center is on the window's visible part.
                inside = [o for o in covers if o[0] <= c[0] <= o[0]+o[2] and o[1] <= c[1] <= o[1]+o[3]]
                check(h['rung'] == 'peek' and not inside and not clear,
                      f'B Window mode, text {s}: {title} has no room for a hint; its hint is on its strip over the front edge',
                      f'rung {h["rung"]} rule {h["rule"]} badge at ({c[0]:.0f},{c[1]:.0f}) target ({h["target_dx"]:.1f},{h["target_dy"]:.1f})')
            minimum = 48*s
            sized = (abs(b['size'] - h['hint_size']) < 1 if h['rung'] == 'full' else
                     minimum - 1 <= b['size'] <= h['hint_size'] + 1 if h['rung'] == 'minimum' else
                     abs(b['size'] - minimum) < 1)
            check(sized, f'B Window mode, text {s}: {title} hint is the size its rung ({h["rung"]}) allows',
                  f'{b["size"]:.1f}; full {h["hint_size"]:.1f}, minimum {minimum:.1f}')
        key('LEFTALT', False)
        time.sleep(.5)
        hs = settled()
        back = all(abs(hs[i]['target_dx']-peek_targets[t][0]) <= 1 and abs(hs[i]['target_dy']-peek_targets[t][1]) <= 1
                   for t, i in ids.items())
        check(back, f'B Alt up, text {s}: every window returns to its peek offset within 1 px',
              str({t: (round(hs[i]['target_dx'], 1), round(hs[i]['target_dy'], 1)) for t, i in ids.items()}))

        # B2. Full room: a front window narrower than the rear one, with space at the sides
        # inside the rear window's zone. (The stack above leaves 60 px above and below and 190 px
        # at the sides of a 900 x 600 front window: full room would push the rear window's center
        # out of the center zone, so minimum room is right there.)
        close_all()
        ids = {'Rear': spawn('Rear', 600, 400, colors['Back'])}
        drag(ids['Rear'], 640, 360)
        ids['Narrow'] = spawn('Narrow', 500, 400, colors['Front'])
        drag(ids['Narrow'], 640, 360)
        settled()
        key('LEFTALT', True)
        wait(lambda: state()['active'])
        time.sleep(1.2)
        hs = settled()
        screenshot(f'B2-full-room-{s}')
        h, front = hs[ids['Rear']], drawn(hs[ids['Narrow']])
        b = h['badge']
        c = (b['x']+b['size']/2, b['y']+b['size']/2)
        clear = math.dist(c, (min(max(c[0], front[0]), front[0]+front[2]),
                              min(max(c[1], front[1]), front[1]+front[3]))) >= b['size']/2 - .5
        check(h['rung'] == 'full' and abs(b['size'] - h['hint_size']) < 1 and clear,
              f'B2 Window mode, text {s}: with space, the rear window gets full room and a full-size hint clear of the front',
              f'rung {h["rung"]} badge {b["size"]:.1f} of {h["hint_size"]:.1f}, target ({h["target_dx"]:.1f},{h["target_dy"]:.1f})')
        key('LEFTALT', False)
        time.sleep(.5)
        hs = settled()
        check(hs[ids['Rear']]['rung'] == 'peek' or hs[ids['Rear']]['outcome'] in ('visible', 'moved'),
              f'B2 Alt up, text {s}: back to the peek layout', f"{hs[ids['Rear']]['outcome']}/{hs[ids['Rear']]['rung']}")
        close_all()
        if s == 1.64:
            # Rebuild the three-window stack for the drag cases.
            ids = {}
            for title, w, h_, x, y in [('Back', 600, 400, 630, 350), ('Mid', 700, 460, 650, 370),
                                       ('Front', 900, 600, 640, 360)]:
                ids[title] = spawn(title, w, h_, colors[title])
                drag(ids[title], x, y)
            hs = settled()

        if s == 1.64:
            # C. 1 px drags of the front window across the rear ones: calm.
            f = drawn(hs[ids['Front']])
            cx, cy = f[0]+f[2]/2, f[1]+f[3]/2
            ipc('window-rules/focus-view', {'id': ids['Front']})
            ipc('stipc/move_cursor', {'x': round(cx), 'y': round(cy)})
            key('LEFTMETA', True)
            ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'press'})
            previous = {t: drawn(hs[ids[t]]) for t in ('Back', 'Mid')}
            last_move = {t: (0, 0) for t in previous}
            largest, fastest, reversals, started = 0, 0, 0, time.monotonic()
            sampled = time.monotonic()
            for step in range(1, 301):
                ipc('stipc/move_cursor', {'x': round(cx-150+step), 'y': round(cy)})
                time.sleep(.016)
                now = hints()
                at = time.monotonic()
                interval, sampled = at-sampled, at
                for t in previous:
                    d = drawn(now[ids[t]])
                    move = (d[0]-previous[t][0], d[1]-previous[t][1])
                    largest = max(largest, math.hypot(*move))
                    fastest = max(fastest, math.hypot(*move)/interval)
                    lm = last_move[t]
                    if math.hypot(*move) > 2 and math.hypot(*lm) > 2 and move[0]*lm[0]+move[1]*lm[1] < 0:
                        reversals += 1
                    if math.hypot(*move) > .01:
                        last_move[t] = move
                    previous[t] = d
            seconds = time.monotonic()-started
            ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'release'})
            key('LEFTMETA', False)
            ipc('stipc/move_cursor', {'x': 640, 'y': 715})
            moved_front = drawn(hints()[ids['Front']])[0]-f[0]
            check(abs(moved_front-150) < 20, 'C the front window really followed the 300 1 px steps',
                  f'moved {moved_front:.0f} px')
            # The easing cap is 1000 px/s (20 px per step at 50 steps/s). The compositor measures its
            # own easing speed per tick; IPC sample times only approximate frame times.
            eased = state()['avoidance_max_easing_speed_px_s']
            check(eased <= 1000.5, 'C 300 1 px drag steps: no window eases faster than the 1000 px/s cap',
                  f'compositor max {eased:.0f} px/s; sampled largest {largest:.1f} px per step, '
                  f'{fastest:.0f} px/s over sample intervals, {300/seconds:.0f} steps/s')
            check(reversals == 0, 'C no window reverses direction between consecutive steps', f'{reversals} reversals')
            hs = settled()
            image = screenshot('C-after-drag')
            check_strips('C after the drop', image, colors, hs,
                         {t: ids[t] for t in ('Back', 'Mid') if order(hs).index(ids[t]) > 0}, s)
            after_drag = {t: (hs[i]['target_dx'], hs[i]['target_dy']) for t, i in ids.items()}
            # Avoidance off: every offset returns to zero.
            ipc('wayfire/set-config-options', {'scottland/window_avoidance_always': False})
            time.sleep(.3)
            hs = settled()
            check(all(abs(h['dx']) < .1 and abs(h['dy']) < .1 for h in hs.values()),
                  'C avoidance off: every offset eases back to zero')
            # On again from zero: a fresh solve of the same layout. At rest the least offset wins
            # (a kept way only within the 6 px switch margin), so it matches the post-drag result.
            ipc('wayfire/set-config-options', {'scottland/window_avoidance_always': True})
            time.sleep(.3)
            hs = settled()
            fresh = {t: (hs[i]['target_dx'], hs[i]['target_dy']) for t, i in ids.items()}
            check(all(abs(math.hypot(*after_drag[t])-math.hypot(*fresh[t])) <= 6*2+1e-6 for t in ids),
                  'C post-drag targets match a fresh solve of the same layout (within the switch margin)',
                  f'after drag {after_drag}; fresh {fresh}')
        close_all()

    # D. Dense stacks of 12 and 16 identical windows: nothing is completely hidden.
    set_palette(1)
    for count in (12, 16):
        names = [f'D{k:02}' for k in range(count)]
        dense_colors = {}
        ids = {}
        for k, name in enumerate(names):
            hue = k/count
            r, g, b = (int(255*max(0, min(1, abs((hue*6+o) % 6-3)-1))) for o in (0, 4, 2))
            # Neighboring hues alternate light and dark so the classifier tells them apart.
            v = .8 if k % 2 == 0 else .45
            dense_colors[name] = f'#{int(r*v)+30:02x}{int(g*v)+30:02x}{int(b*v)+30:02x}'
            ids[name] = spawn(name, 700, 450, dense_colors[name])
            drag(ids[name], 640, 360, steps=4)
        hs = settled(20)
        image = screenshot(f'D-dense-{count}')
        labels = classify(image, dense_colors.values())
        outcomes = {}
        short, hidden = [], []
        for k, name in enumerate(names):
            h = hs[ids[name]]
            outcomes[h['outcome']] = outcomes.get(h['outcome'], 0)+1
            found, pixels = strip(labels, k, 24-EDGE, 100-EDGE, drawn(h))
            if not pixels:
                hidden.append(name)
            if h['outcome'] != 'no_room' and order(hs).index(ids[name]) > 0 and not found:
                short.append(f'{name}:{pixels}px')
        check(not hidden, f'D {count} windows: none is completely hidden (from the screenshot)', f'hidden {hidden}; {outcomes}')
        check(not short, f'D {count}: every rear window that is not no_room shows a 24 x 100 strip', f'{short}; {outcomes}')
        # The shipped look: still nothing completely hidden.
        ipc('wayfire/set-config-options', shipped_look)
        time.sleep(1.5)
        labels = classify(screenshot(f'D-dense-{count}-shipped'), dense_colors.values())
        hidden = [name for k, name in enumerate(names) if not strip(labels, k, 1, 1)[1]]
        ipc('wayfire/set-config-options', flat_look)
        time.sleep(.5)
        check(not hidden, f'D {count}, shipped goo look: none is completely hidden (from the screenshot)', f'hidden {hidden}')
        # Settled: no more solves and no tick, outside and inside Window mode.
        for mode in ('always-on', 'Window mode'):
            if mode == 'Window mode':
                key('LEFTALT', True)
                wait(lambda: state()['active'])
                time.sleep(1)
                settled(20)
                time.sleep(1)
            a = state()
            time.sleep(1.5)
            b = state()
            check(b['avoidance_solve_count'] == a['avoidance_solve_count'] and b['hint_step_count'] == a['hint_step_count'],
                  f'D {count}, {mode}: a settled layout stops solving and ticking',
                  f"solves {a['avoidance_solve_count']} -> {b['avoidance_solve_count']}, "
                  f"ticks {a['hint_step_count']} -> {b['hint_step_count']}")
            if mode == 'Window mode':
                key('LEFTALT', False)
                time.sleep(.8)
        # P8 from outside: a second connection pings the compositor about every millisecond while
        # the front window is dragged 1 px at a time across the dense stack; a ping can only come
        # back when the main loop is free.
        pings, stop = [], [False]
        def pinger():
            with socket.socket(socket.AF_UNIX) as ping:
                ping.connect(socket_path)
                body = json.dumps({'method': 'stipc/ping', 'data': {}}).encode()
                message = struct.pack('<I', len(body))+body
                def read(n):
                    out = b''
                    while len(out) < n:
                        out += ping.recv(n-len(out))
                    return out
                while not stop[0]:
                    started = time.monotonic()
                    ping.sendall(message)
                    read(struct.unpack('<I', read(4))[0])
                    pings.append((time.monotonic()-started)*1000)
                    time.sleep(.001)
        import threading
        thread = threading.Thread(target=pinger, daemon=True)
        thread.start()
        front_id = order(hs)[0]
        fd = drawn(hints()[front_id])
        fx, fy = fd[0]+fd[2]/2, fd[1]+fd[3]/2
        ipc('window-rules/focus-view', {'id': front_id})
        ipc('stipc/move_cursor', {'x': round(fx), 'y': round(fy)})
        key('LEFTMETA', True)
        ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'press'})
        solve_max_before = state()['avoidance_solve_max_ms']
        for step in range(1, 201):
            ipc('stipc/move_cursor', {'x': round(fx+step), 'y': round(fy)})
            time.sleep(.008)
        ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'release'})
        key('LEFTMETA', False)
        ipc('stipc/move_cursor', {'x': 640, 'y': 715})
        stop[0] = True
        thread.join(timeout=2)
        settled(20)
        ordered = sorted(pings)
        p50 = ordered[len(ordered)//2] if ordered else 0
        p99 = ordered[int(len(ordered)*.99)] if ordered else 0
        worst = ordered[-1] if ordered else 0
        check(ordered and worst < 100, f'D {count}: during a 200-step live drag over the stack the main loop answers within 100 ms',
              f'{len(ordered)} pings: median {p50:.2f} ms, p99 {p99:.2f} ms, worst {worst:.2f} ms; '
              f'avoidance refresh max {state()["avoidance_solve_max_ms"]:.3f} ms')
        s_ = state()
        print(f'      D {count}: outcomes {outcomes}; last pass {s_["avoidance_pass_units"]} units in '
              f'{s_["avoidance_pass_slices"]} slice(s); slice max {s_["avoidance_solve_max_ms"]:.3f} ms', flush=True)
        close_all()

    # E. Zone limits. A left-periphery window covered by a center window peeks without its
    # displayed center leaving the periphery; a window with no room anywhere stays put and shows
    # only its hint.
    set_palette(1)
    zone_colors = {'Left': '#c8a028', 'Big': '#6e6e78'}
    ids = {'Left': spawn('Left', 420, 320, zone_colors['Left'])}
    drag(ids['Left'], 300, 360)
    ids['Big'] = spawn('Big', 1100, 640, zone_colors['Big'])
    drag(ids['Big'], 640, 360)
    hs = settled()
    image = screenshot('E-periphery')
    width = 1280
    half = width*33.333/200
    left = hs[ids['Left']]
    d = drawn(left)
    true_center = left['solve_frame']['x']+left['solve_frame']['width']/2
    shown_center = d[0]+d[2]/2
    check(true_center < width/2-half and shown_center <= width/2-half, 'E the periphery window\'s displayed center stays in its zone',
          f'true {true_center:.1f}, shown {shown_center:.1f}, zone edge {width/2-half:.1f}; {left["outcome"]}')
    check_strips('E periphery', image, zone_colors, hs, {'Left': ids['Left']}, 1)
    close_all()
    # Near the right rail, at about 30% zone scale: the strip is not shrunk with the window.
    small_colors = {'Small': '#b43c8c', 'Cap': '#3c8cb4'}
    ids = {'Small': spawn('Small', 600, 420, small_colors['Small'])}
    drag(ids['Small'], 1185, 360)
    ids['Cap'] = spawn('Cap', 600, 420, small_colors['Cap'])
    drag(ids['Cap'], 1185, 360)
    hs = settled()
    image = screenshot('E-small-scale')
    scale = drawn(hs[ids['Small']])[2]/600
    print(f'      E small: zone scale {scale:.2f}, drawn {drawn(hs[ids["Small"]])}, {hs[ids["Small"]]["outcome"]}', flush=True)
    check(scale < .45, 'E the rear periphery window is drawn at a small zone scale', f'{scale:.2f}')
    check_strips(f'E at zone scale {scale:.2f}', image, small_colors, hs, {'Small': ids['Small']}, 1)
    close_all()
    ids = {'Under': spawn('Under', 600, 400, '#3cb4b4')}
    drag(ids['Under'], 640, 360)
    ids['Cover'] = spawn('Cover', 1280, 720, '#6e6e78')
    drag(ids['Cover'], 640, 360)
    hs = settled()
    under = hs[ids['Under']]
    check(under['outcome'] == 'no_room' and abs(under['target_dx']) < .01 and abs(under['target_dy']) < .01,
          'E a window with no room in its zone does not move', f'{under["outcome"]} ({under["target_dx"]:.1f},{under["target_dy"]:.1f})')
    key('LEFTALT', True)
    wait(lambda: state()['active'])
    time.sleep(1.2)
    hs = settled()
    screenshot('E-no-room-hint')
    check('badge' in hs[ids['Under']] and hs[ids['Under']]['rendered'],
          'E in Window mode its hint is still placed where it can be seen')
    key('LEFTALT', False)
    time.sleep(.5)
    close_all()

    # F. The engram case from the daily machine: a window at the top of the screen, covered by the focused front
    # window except a sliver along its top. Window mode makes room for its hint; with no room,
    # its hint sits on the sliver over the front window's edge, not at its center.
    set_palette(1.64)
    for variant, front_w in (('room', 760), ('no-room', 1280)):
        ids = {'Engram': spawn('Engram', 700, 600, '#7850b4')}
        drag(ids['Engram'], 640, 300)
        ids['Focus'] = spawn('Focus', front_w, 664, '#b47828')
        hs = hints()
        e = drawn(hs[ids['Engram']])
        drag(ids['Focus'], 640, e[1]+56+332)
        hs = settled()
        e, f = drawn(hs[ids['Engram']]), drawn(hs[ids['Focus']])
        print(f'      F {variant}: engram {e}, front {f}, sliver {f[1]-e[1]:.1f} px', flush=True)
        key('LEFTALT', True)
        wait(lambda: state()['active'])
        time.sleep(1.2)
        hs = settled()
        screenshot(f'F-engram-{variant}')
        h = hs[ids['Engram']]
        b = h['badge']
        c = (b['x']+b['size']/2, b['y']+b['size']/2)
        f = drawn(hs[ids['Focus']])
        e_true = drawn(hs[ids['Engram']])
        center = (e_true[0]+e_true[2]/2, e_true[1]+e_true[3]/2)
        if variant == 'room':
            clear = math.dist(c, (min(max(c[0], f[0]), f[0]+f[2]), min(max(c[1], f[1]), f[1]+f[3]))) >= b['size']/2-.5
            check(h['rung'] in ('full', 'minimum') and clear,
                  'F engram: Window mode makes room; its hint is clear of the front window',
                  f'{h["rung"]} target ({h["target_dx"]:.1f},{h["target_dy"]:.1f}) badge at ({c[0]:.0f},{c[1]:.0f})')
        else:
            check(h['rung'] == 'peek' and c[1] < f[1] and c[1]+b['size']/2 > f[1] and math.dist(c, center) > 100,
                  'F engram, no room: its hint sits on the visible sliver, overlapping the front window\'s top edge',
                  f'{h["rung"]} badge center y {c[1]:.0f}, front top {f[1]:.0f}, window center y {center[1]:.0f}')
        key('LEFTALT', False)
        time.sleep(.5)
        close_all()

    # G. Avoidance slice cost, reported for the performance lane (P8's 2 ms is not a functional gate here).
    print(f'      G avoidance refresh max {solve_max_ms:.3f} ms; largest slice {slice_units_max} units; '
          f'most slices per pass {pass_slices_max}', flush=True)
finally:
    for client in clients.values():
        client.terminate()
    print(f'{passed} passed, {failed} failed', flush=True)
sys.exit(1 if failed else 0)
