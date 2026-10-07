#!/usr/bin/env python3
"""Hold forms, hold offers and the hold ring (WK35/WK36/WK39; Mike 2026-10-04/05).

Every gesture that drags a window, held still for the hold delay instead of dragged, offers a solo
of the focused window or a pair of an unfocused one; the offer shows the real result while the
button or fingers stay down, is taken by the owner's own release within the hotspot, and is
refused by leaving the hotspot (the drag carries on), by Esc, or by the gesture ending without a
lift.

Input: stipc keys, pointer motion and buttons (Super + press, plain press, halo press) and the
test-only virtual touchpad (scottland/test-touchpad), a wlroots pointer device that emits
libinput's hold, swipe and button events into Wayfire's input path. It bypasses libinput itself
(its gesture recognition, thresholds and palm/tap detection), so resting fingers are modeled as
the hold/swipe sequences libinput sends; a physical touchpad remains unverified.

Oracles: Wayfire's own geometry (window-rules/list-views, get-focused-view) and captured pixels
(grim). Light GTK windows sit on the dark desktop, so a window's drawn extent is read from a
pixel row or column. Scottland's IPC is used only to set up fixtures and to name what to wait for
(an offer being shown); every verdict is geometry or pixels.

Usage: hold-forms-test.py ARTIFACTS [--outputs2]
"""
import json, math, os, random, socket, struct, subprocess, sys, time
from pathlib import Path

assert os.environ.get('SCOTTLAND_TEST_MODEL') == '1'
art = Path(sys.argv[1]).resolve(); art.mkdir(parents=True, exist_ok=True)
TWO = '--outputs2' in sys.argv
HALO = 32 / 3; PAD = HALO + 5
random.seed(39)
sock = socket.socket(socket.AF_UNIX); sock.settimeout(15); sock.connect(os.environ['WAYFIRE_SOCKET'])

def ipc(method, data=None):
    body = json.dumps({'method': method, 'data': data or {}}).encode()
    sock.sendall(struct.pack('<I', len(body)) + body)
    def read(n):
        out = b''
        while len(out) < n:
            chunk = sock.recv(n - len(out))
            if not chunk: raise RuntimeError('compositor disconnected')
            out += chunk
        return out
    reply = json.loads(read(struct.unpack('<I', read(4))[0]))
    if isinstance(reply, dict) and 'error' in reply: raise RuntimeError(reply)
    return reply

passed = failed = 0
def check(ok, name, detail=''):
    global passed, failed
    print(('PASS ' if ok else 'FAIL ') + name + (f'  [{detail}]' if detail and not ok else ''), flush=True)
    if ok: passed += 1
    else: failed += 1

class Abort(Exception): pass
def need(predicate, seconds, what):
    """Wait for a newer state; a deadline aborts the scenario with the last observation."""
    end = time.monotonic() + seconds; last = None
    while time.monotonic() < end:
        last = predicate()
        if last: return last
        time.sleep(.03)
    raise Abort(f'{what}: not within {seconds} s; last {last!r}')
def verify(predicate, seconds, name):
    """A verdict on a newer state: waits up to `seconds`, then records pass or fail with the last observation."""
    end = time.monotonic() + seconds; last = None
    while time.monotonic() < end:
        last = predicate()
        if last: break
        time.sleep(.03)
    check(bool(last), name, f'last {last!r}')
def holds(predicate, seconds):
    """A bounded observation that something does NOT happen: it must stay true for `seconds`."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = predicate()
        if not value: return False, value
        time.sleep(.05)
    return True, None

# ---------------------------------------------------------------- input (released on exit)
held = {'keys': set(), 'button': False, 'pad': None}
def key(code, down):
    ipc('stipc/feed_key', {'key': 'KEY_' + code, 'state': down})
    (held['keys'].add if down else held['keys'].discard)(code)
def pointer(x, y): ipc('stipc/move_cursor', {'x': round(x), 'y': round(y)})
def button(down):
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'press' if down else 'release'}); held['button'] = down
def pad(event, **data):
    if event in ('hold_begin', 'swipe_begin'): held['pad'] = event.split('_')[0]
    if event in ('hold_end', 'swipe_end'): held['pad'] = None
    return ipc('scottland/test-touchpad', dict(event=event, **data))
def release_all():
    for code in list(held['keys']): key(code, False)
    if held['button']: button(False)
    if held['pad'] == 'hold': pad('hold_end', cancelled=True)
    if held['pad'] == 'swipe': pad('swipe_end', cancelled=True)

# ---------------------------------------------------------------- Wayfire's own state
def views(): return ipc('window-rules/list-views')
def raw(id): return next(v for v in views() if v['id'] == id)
def geometry(id): g = raw(id)['geometry']; return (g['x'], g['y'], g['width'], g['height'])
def frames(*ids): return {i: geometry(i) for i in ids}
def focused(): return ipc('window-rules/get-focused-view').get('info', {}).get('id')
def outputs(): return sorted(ipc('window-rules/list-outputs'), key=lambda o: o['geometry']['x'])
def output_of(id): return next(o for o in outputs() if o['id'] == raw(id)['output-id'])
def stable(*ids, seconds=4):
    """Wait until Wayfire's geometry of these windows stops changing (3 reads, 100 ms apart)."""
    last = None; same = 0; end = time.monotonic() + seconds
    while time.monotonic() < end:
        now = frames(*ids)
        same = same + 1 if now == last else 0
        if same >= 2: return now
        last = now; time.sleep(.1)
    raise Abort(f'geometry still changing: {last}')
def center(id):
    x, y, w, h = geometry(id); o = output_of(id)['geometry']
    return o['x'] + x + w / 2, o['y'] + y + h / 2

# ---------------------------------------------------------------- pixels
def capture(x, y, w, h, name='cap.png'):
    out = art / name
    subprocess.run(['grim', '-g', f'{int(x)},{int(y)} {int(w)}x{int(h)}', str(out)], check=True)
    width = int(subprocess.run(['magick', 'identify', '-format', '%w', str(out)], check=True,
        capture_output=True, text=True).stdout)
    raw_px = subprocess.run(['magick', str(out), '-depth', '8', 'rgb:-'], check=True, capture_output=True).stdout
    return raw_px, width, width / max(1, int(w))  # device pixels per logical px
def light(rgb): return sum(rgb) > 450  # a GTK window's light background, not the dark desktop
def px(x, y):
    data, _, _ = capture(x, y, 1, 1); return tuple(data[:3])
def run_along(x, y, horizontal=True, span=1600):
    """The light run through (x, y) along a row or column, in logical px: (start, end) or None."""
    if horizontal: data, w, d = capture(max(0, x - span / 2), y, span, 1); x0 = max(0, x - span / 2)
    else: data, w, d = capture(x, max(0, y - span / 2), 1, span); x0 = max(0, y - span / 2)
    n = len(data) // 3
    lit = [light(data[3 * i: 3 * i + 3]) for i in range(n)]
    i = int((x if horizontal else y) - x0) * int(round(d))
    if not (0 <= i < n) or not lit[i]: return None
    a = b = i
    while a > 0 and lit[a - 1]: a -= 1
    while b < n - 1 and lit[b + 1]: b += 1
    return (x0 + a / d, x0 + (b + 1) / d)
def drawn_size(x, y):
    """The light rectangle drawn around (x, y): (width, height) in logical px."""
    h = run_along(x, y, True); v = run_along(x, y, False)
    return (h[1] - h[0], v[1] - v[0]) if h and v else None

# ---------------------------------------------------------------- fixtures
GTK_APP = """import sys, gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk
app = Gtk.Application(application_id='org.scottland.HoldTest.' + sys.argv[1])
def activate(a):
    w = Gtk.ApplicationWindow(application=a, title=sys.argv[1]); w.set_default_size(400, 300); w.present()
app.connect('activate', activate); app.run([])
"""
clients = []
def launch(title):
    clients.append(subprocess.Popen([sys.executable, '-c', GTK_APP, title], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    # Listed before it maps: wait for the map, which may also take focus (setup settles focus after).
    return need(lambda: next((v['id'] for v in views() if v.get('title') == title and v.get('mapped')), None), 10,
                'launch ' + title)
def place(id, x, y, w, h, output=None):
    data = {'id': id, 'geometry': {'x': x, 'y': y, 'width': w, 'height': h}}
    if output is not None: data['output_id'] = output
    ipc('window-rules/configure-view', data)
    need(lambda: geometry(id) == (x, y, w, h), 3, f'fixture {id} placed')
def scottland_view(id): return next((v for v in ipc('scottland/layout-state')['views'] if v['id'] == id), {})
def setup(spec, focus):
    """spec: [(id, x, y, w, h[, output])] back to front; `focus` is focused, in front."""
    for id, *_ in spec:
        if scottland_view(id).get('widgetized'):
            ipc('scottland/present', {'window': id})
            need(lambda: not scottland_view(id).get('widgetized'), 3, f'fixture {id} restored')
    for id, x, y, w, h, *output in spec: place(id, x, y, w, h, *(output or [None]))
    for id, *_ in spec: ipc('window-rules/focus-view', {'id': id})
    ipc('window-rules/focus-view', {'id': focus})
    need(lambda: focused() == focus, 2, 'fixture focus')
    stable(*[s[0] for s in spec])
    ok, _ = holds(lambda: focused() == focus, .3)  # nothing late (a client mapping) takes it away
    if not ok: raise Abort(f'fixture focus moved to {focused()}')

def expected(left, right, area):
    """Mirror of fit_pair: ({id: (x, y) top-left output-local}, scale)."""
    (li, (lw, lh)), (ri, (rw, rh)) = left, right
    total = lw + rw; scale = 1.0; gap = 0.0
    if total + HALO + 2 * PAD <= area['width']: gap = HALO
    elif total + 2 * PAD <= area['width']: gap = area['width'] - 2 * PAD - total
    elif total > area['width']: scale = max(.05, area['width'] / total)
    width = total * scale + gap; x0 = (area['width'] - width) / 2; cy = area['height'] / 2
    lc = x0 + lw * scale / 2; rc = x0 + lw * scale + gap + rw * scale / 2
    r = lambda v: math.floor(v + .5)
    return {li: (r(lc - lw / 2), r(cy - lh / 2)), ri: (r(rc - rw / 2), r(cy - rh / 2))}, scale
def pair_plan(left, right, area):
    lg, rg = geometry(left), geometry(right)
    return expected((left, (lg[2], lg[3])), (right, (rg[2], rg[3])), area)
def paired(left, right, area, want=None):
    want = want or pair_plan(left, right, area)[0]
    return all(abs(geometry(i)[0] - want[i][0]) <= 1 and abs(geometry(i)[1] - want[i][1]) <= 1 for i in want)
def in_center(id, area):
    x, _, w, _ = geometry(id); cx = x + w / 2; edge = area['width'] / 3
    return edge < cx < area['width'] - edge
def offer(): return ipc('scottland/spread-state')['hold_offer']  # what to wait for, never a verdict

scenarios = []
def scenario(fn): scenarios.append(fn); return fn
def run_all():
    for fn in scenarios:
        name = fn.__doc__ or fn.__name__
        try: fn()
        except Abort as error: check(False, f'{name} (aborted)', str(error))
        except Exception as error: check(False, f'{name} (error)', repr(error))
        finally: release_all(); time.sleep(.3)

# ---------------------------------------------------------------- scenarios
def super_press(x, y): pointer(x, y); time.sleep(.05); key('LEFTMETA', True); button(True)
def super_release(): button(False); key('LEFTMETA', False)

@scenario
def offer_preview_and_take():
    'Super + press-and-hold on an unfocused window: the pair is previewed, taken on release'
    setup(PAIR, A); f0 = frames(A, B, C)
    want, _ = pair_plan(A, B, area)
    b_old = (center(B)[0], center(B)[1] + 40)                 # inside B (drawn scaled), outside its pair spot
    b_new = (want[B][0] + 210, want[B][1] + 150)
    check(light(px(*b_old)) and not light(px(*b_new)), 'fixture: B drawn at its place, its pair spot empty')
    super_press(*center(B))
    need(lambda: not light(px(*b_old)) and light(px(*b_new)), 3, 'pair preview on screen')
    check(frames(A, B, C) == f0, 'the preview changes no true geometry while held (Wayfire)', str(frames(A, B, C)))
    size = drawn_size(*b_new)
    # Within 8 px: the neighbor's halo across the halo gap may cover an edge; any periphery
    # scale would be far smaller.
    check(size and abs(size[0] - 420) <= 8 and abs(size[1] - 300) <= 8, 'B is previewed at its own size (pixels)', str(size))
    capture(0, 0, area['width'], area['height'], 'offer-preview.png')
    super_release()
    need(lambda: paired(A, B, area, want), 3, 'pair taken')
    check(geometry(C) == f0[C], 'taking the offer: A and B paired at the planned spots, C untouched (Wayfire)')
    size = drawn_size(*b_new)
    check(size and abs(size[0] - 420) <= 3 and abs(size[1] - 300) <= 3, 'after taking it B is drawn at 100% (pixels)', str(size))

@scenario
def offer_with_always_on_avoidance():
    'with window avoidance always on, a Super hold still previews and takes the pair'
    ipc('wayfire/set-config-options', {'scottland/window_avoidance_always': True})
    try:
        setup(PAIR, A); want, _ = pair_plan(A, B, area)
        super_press(*center(B))
        need(lambda: light(px(want[B][0] + 210, want[B][1] + 150)), 3, 'preview with avoidance')
        super_release()
        verify(lambda: paired(A, B, area, want), 3, 'always-on avoidance: the offer is shown and taken (Wayfire)')
    finally: ipc('wayfire/set-config-options', {'scottland/window_avoidance_always': False})

@scenario
def offer_solo():
    'Super + press-and-hold on the focused window: the solo is previewed, taken on release'
    setup(SOLO, A); f0 = frames(A, C)
    check(in_center(C, area), 'fixture: C is in the center beside A')
    c_spot = (f0[C][0] + 350, f0[C][1] + 250)
    check(light(px(*c_spot)), 'fixture: C drawn there')
    super_press(*center(A))
    need(lambda: not light(px(*c_spot)), 3, 'solo preview: C leaves its spot on screen')
    check(frames(A, C) == f0, 'the solo preview changes no true geometry (Wayfire)')
    super_release()
    need(lambda: not in_center(C, area), 3, 'solo taken')
    check(geometry(A) == f0[A], 'taking the solo: C left the center, A stayed (Wayfire)', str(frames(A, C)))

@scenario
def offer_refused_by_leaving_the_hotspot():
    'starting to drag (beyond the hotspot) cancels the audition; the held window drags on under the pointer'
    setup(PAIR, A); f0 = frames(A, B); want, _ = pair_plan(A, B, area)
    x, y = center(B); super_press(x, y)
    need(lambda: light(px(want[B][0] + 210, want[B][1] + 150)), 3, 'preview before leaving')
    for i in range(1, 11): pointer(x - 8 * i, y); time.sleep(.02)   # 80 px, out of the 50 pt hotspot
    need(lambda: not light(px(want[B][0] + 210, want[B][1] + 150)), 2, 'preview gone')
    time.sleep(.25)                                                   # still before letting go: no coast (L32)
    super_release()
    g = need(lambda: (lambda g: g if abs(g[0] - (f0[B][0] - 80)) <= 2 else None)(stable(B)[B]), 3, 'B dropped where dragged')
    check(geometry(A) == f0[A] and g[1] == f0[B][1], 'refused: A untouched, B dropped 80 px left where the pointer took it (Wayfire)',
          f'A {geometry(A)} B {g}')

@scenario
def offer_inside_hotspot_past_wobble():
    'moving past the wobble but within the hotspot after the hold fired still takes the offer'
    setup(PAIR, A); want, _ = pair_plan(A, B, area)
    x, y = center(B); super_press(x, y)
    need(lambda: light(px(want[B][0] + 210, want[B][1] + 150)), 3, 'preview')
    for i in range(1, 9): pointer(x + 4 * i, y); time.sleep(.02)    # 32 px
    super_release()
    verify(lambda: paired(A, B, area, want), 3, 'inside the hotspot after firing: the pair is taken (Wayfire)')

@scenario
def esc_refuses_offer():
    'Esc during an offer changes nothing'
    setup(PAIR, A); f0 = frames(A, B); want, _ = pair_plan(A, B, area)
    super_press(*center(B))
    need(lambda: light(px(want[B][0] + 210, want[B][1] + 150)), 3, 'preview before Esc')
    key('ESC', True); key('ESC', False); super_release()
    need(lambda: not light(px(want[B][0] + 210, want[B][1] + 150)), 2, 'preview gone after Esc')
    check(stable(A, B) == f0, 'Esc: no true geometry changed (Wayfire)', str(frames(A, B)))

@scenario
def drag_before_hold():
    'a Super press that moves past the wobble first is a drag: it follows the pointer, nothing pairs'
    setup(PAIR, A); f0 = frames(A, B)
    x, y = center(B); super_press(x, y)
    for i in range(1, 9): pointer(x - 6 * i, y); time.sleep(.02)    # 48 px at once
    time.sleep(.7)                                                    # longer than the hold delay
    check(light(px(x - 48 + 100, y)) and not light(px(x + 190, y)), 'the dragged window follows the pointer (pixels)')
    super_release()
    g = stable(A, B)
    check(abs(g[B][0] - (f0[B][0] - 48)) <= 2 and g[A] == f0[A], 'dropped 48 px left, A untouched (Wayfire)', str(g))

@scenario
def quick_click_and_plain_press():
    'a quick Super click, and a plain press-and-hold without Super, move nothing'
    setup(PAIR, A); f0 = frames(A, B)
    x, y = center(B); super_press(x, y); time.sleep(.12); super_release()
    ok, _ = holds(lambda: frames(A, B) == f0, .8)
    check(ok, 'a quick Super click moves nothing (Wayfire)')
    pointer(x, y); time.sleep(.05); button(True); time.sleep(.8); button(False)
    ok, _ = holds(lambda: frames(A, B) == f0, .8)
    check(ok and focused() == B, 'a plain press-and-hold is the app\'s: it focuses B and moves nothing (Wayfire)')

@scenario
def drag_lock_pairs():
    'Super + double-tap-and-hold (a touchpad tap-and-drag) pairs the unfocused window'
    setup(PAIR, A); want, _ = pair_plan(A, B, area)
    x, y = center(B); pointer(x, y); time.sleep(.05); key('LEFTMETA', True)
    pad('button', button='left', pressed=True); time.sleep(.06); pad('button', button='left', pressed=False)
    time.sleep(.12)
    pad('button', button='left', pressed=True); time.sleep(.8); pad('button', button='left', pressed=False)
    key('LEFTMETA', False)
    verify(lambda: paired(A, B, area, want), 3, 'drag lock: B paired with A, not soloed (Wayfire)')

@scenario
def chain_broken_by_another_click():
    'a click on another window between the tap and the hold makes that window the partner'
    setup(PAIR, A); f0 = frames(A, B, C)
    bx, by = center(B); cx, cy = center(C)
    pointer(bx, by); time.sleep(.05); key('LEFTMETA', True); button(True); time.sleep(.06); button(False)
    key('LEFTMETA', False)
    pointer(cx, cy); button(True); time.sleep(.05); button(False)
    need(lambda: focused() == C, 1, 'C focused by the click')
    pointer(bx, by); key('LEFTMETA', True); button(True); time.sleep(.8); super_release()
    want, _ = pair_plan(B, C, area) if center(B)[0] < center(C)[0] else pair_plan(C, B, area)
    need(lambda: all(abs(geometry(i)[0] - want[i][0]) <= 1 for i in want), 3, 'pair with C')
    check(geometry(A) == f0[A], 'after clicking C, holding B pairs it with C and leaves A (Wayfire)', str(frames(A, B, C)))

@scenario
def halo_hold():
    'holding the halo of an unfocused window pairs it'
    setup(PAIR, A); want, _ = pair_plan(A, B, area)
    fr = scottland_view(B)['frame']                      # where the halo band is (setup only)
    pointer(fr['x'] - fr['thickness'] / 2, fr['y'] + fr['height'] / 2); time.sleep(.1)
    button(True); time.sleep(.8); button(False)
    verify(lambda: paired(A, B, area, want), 3, 'halo hold: B paired with A (Wayfire)')

@scenario
def rail_boundary_hold():
    'a hold that wobbles into the rail is taken without any widget shape left (pixels)'
    ipc('wayfire/set-config-options', {'scottland/min_scale': 1.0})
    try:
        setup([(C, 1100, 40, 420, 200), (B, 1060, 500, 420, 300), (A, 200, 140, 520, 360)], A)
        place(B, 1280, 500, 420, 300); ipc('window-rules/focus-view', {'id': A}); stable(A, B)
        want, _ = pair_plan(A, B, area)
        pointer(1564, 650); time.sleep(.05); key('LEFTMETA', True); button(True); time.sleep(.05)
        pointer(1572, 650); time.sleep(.75); super_release()
        need(lambda: paired(A, B, area, want), 3, 'rail-boundary pair')
        stable(A, B)
        size = drawn_size(want[B][0] + 210, want[B][1] + 150)
        check(size and abs(size[0] - 420) <= 3 and abs(size[1] - 300) <= 3,
              'held at the rail boundary: B drawn at its full size at its pair spot, not a widget strip (pixels)', str(size))
        capture(0, 0, area['width'], area['height'], 'rail-hold-taken.png')
    finally: ipc('wayfire/set-config-options', {'scottland/min_scale': .2})

@scenario
def widget_offer_preview():
    'a Super hold on a widget card previews its app at its pair spot, then opens it there'
    setup(PAIR, A); want, _ = pair_plan(A, B, area)
    ipc('window-rules/focus-view', {'id': B}); stable(B)
    alt_down()
    label = next(h['hint'] for h in ipc('scottland/hints')['hints'] if h['window'] == B)
    for _ in range(2): key(label.upper(), True); time.sleep(.06); key(label.upper(), False); time.sleep(.08)
    key('LEFTALT', False)
    card = need(lambda: next((w['widget_view'] for w in ipc('scottland/widgets')['widgets']
        if int(w['id']) == B and w['widget_view'] > 0), None), 3, 'widget card')
    ipc('window-rules/focus-view', {'id': A}); need(lambda: focused() == A, 2, 'A focused')
    need(lambda: not light(px(want[B][0] + 210, want[B][1] + 150)), 3, 'B gone from its pair spot as a widget')
    cg = need(lambda: next((v['geometry'] for v in views() if v['id'] == card), None), 2, 'card geometry')
    co = output_of(card)['geometry']
    super_press(co['x'] + cg['x'] + cg['width'] / 2, co['y'] + cg['y'] + cg['height'] / 2)
    need(lambda: light(px(want[B][0] + 210, want[B][1] + 150)), 3, 'the app previewed at its pair spot')
    verify(lambda: (lambda size: size if size and abs(size[0] - 420) <= 8 and abs(size[1] - 300) <= 8 else None)(
           drawn_size(want[B][0] + 210, want[B][1] + 150)), 2,
           'the widget is previewed, once its morph settles, as its app at the app\'s size at its pair spot (pixels)')
    capture(0, 0, area['width'], area['height'], 'widget-offer.png')
    super_release()
    verify(lambda: not scottland_view(B).get('widgetized') and paired(A, B, area, want), 3, 'taking it opens the app there, paired with A (Wayfire)')

def alt_down():
    key('LEFTALT', True)
    need(lambda: ipc('scottland/hints')['active'], 2, 'window mode')

@scenario
def other_device_cannot_take_offer():
    'another device (a two-finger touchpad hold, a touchpad swipe) neither ends nor takes a mouse offer'
    setup(PAIR, A); f0 = frames(A, B); want, _ = pair_plan(A, B, area)
    super_press(*center(B))
    need(lambda: light(px(want[B][0] + 210, want[B][1] + 150)), 3, 'mouse offer shown')
    pad('hold_begin', fingers=2); time.sleep(.1); pad('hold_end', cancelled=False)
    pad('swipe_begin', fingers=3); pad('swipe_update', fingers=3, dx=60, dy=0); pad('swipe_end')
    ok, _ = holds(lambda: frames(A, B) == f0 and light(px(want[B][0] + 210, want[B][1] + 150)), .8)
    check(ok, 'the touchpad events change nothing: still offered, nothing committed (Wayfire, pixels)')
    super_release()
    verify(lambda: paired(A, B, area, want), 3, 'the mouse\'s own release takes it (Wayfire)')

@scenario
def cancelled_touchpad_hold_never_commits():
    'a touchpad hold that libinput cancels with no swipe and no lift is refused, never taken'
    setup(PAIR, A); f0 = frames(A, B); want, _ = pair_plan(A, B, area)
    pointer(*center(B)); time.sleep(.1)
    pad('hold_begin', fingers=3)
    need(lambda: light(px(want[B][0] + 210, want[B][1] + 150)), 3, 'touchpad offer shown')
    pad('hold_end', cancelled=True)
    need(lambda: not light(px(want[B][0] + 210, want[B][1] + 150)), 2, 'the preview returns')
    ok, _ = holds(lambda: frames(A, B) == f0, 1.0)
    check(ok, 'cancelled without a lift: nothing committed (Wayfire)', str(frames(A, B)))

@scenario
def touchpad_offer_lift_and_swipe():
    'three fingers: lifting takes the offer; a two-finger swipe meanwhile is ignored'
    setup(PAIR, A); want, _ = pair_plan(A, B, area)
    pointer(*center(B)); time.sleep(.1)
    pad('hold_begin', fingers=3)
    need(lambda: light(px(want[B][0] + 210, want[B][1] + 150)), 3, 'offer')
    pad('swipe_begin', fingers=2); pad('swipe_update', fingers=2, dx=90, dy=0); pad('swipe_end')
    ok, _ = holds(lambda: light(px(want[B][0] + 210, want[B][1] + 150)), .5)
    check(ok, 'a two-finger swipe during the offer is ignored (pixels)')
    pad('hold_end', cancelled=False)
    verify(lambda: paired(A, B, area, want), 3, 'lifting three fingers takes the pair (Wayfire)')

@scenario
def touchpad_cancel_then_swipe_continues():
    'libinput cancelling the hold into a swipe of the same fingers continues the offer; lifting takes it'
    setup(PAIR, A); want, _ = pair_plan(A, B, area)
    pointer(*center(B)); time.sleep(.1)
    pad('hold_begin', fingers=3)
    need(lambda: light(px(want[B][0] + 210, want[B][1] + 150)), 3, 'offer')
    pad('hold_end', cancelled=True); pad('swipe_begin', fingers=3)
    for _ in range(5): pad('swipe_update', fingers=3, dx=2, dy=1); time.sleep(.03)
    pad('swipe_end')
    verify(lambda: paired(A, B, area, want), 3, 'the same fingers swiping inside the hotspot, then lifting, take the pair (Wayfire)')

@scenario
def touchpad_jitter_holds():
    'resting fingers that jitter within the wobble (a swipe, no libinput hold) offer, lifting takes it'
    setup(PAIR, A); want, _ = pair_plan(A, B, area)
    pointer(*center(B)); time.sleep(.1)
    pad('swipe_begin', fingers=3)
    end = time.monotonic() + .8
    while time.monotonic() < end: pad('swipe_update', fingers=3, dx=random.uniform(-2, 2), dy=random.uniform(-2, 2)); time.sleep(.03)
    pad('swipe_end')
    verify(lambda: paired(A, B, area, want), 3, 'jittering three fingers pair B with A (Wayfire)')

@scenario
def slow_deliberate_swipes_stay_drags():
    'a slow deliberate three-finger swipe of (14, 9) or (10, 9), then still, stays a drag'
    for dx, dy in ((14, 9), (10, 9)):
        setup(PAIR, A); f0 = frames(A, B)
        pointer(*center(B)); time.sleep(.1)
        pad('swipe_begin', fingers=3)
        for _ in range(10): pad('swipe_update', fingers=3, dx=dx / 10, dy=dy / 10); time.sleep(.03)
        time.sleep(.8)                                                # still, longer than the hold delay
        pad('swipe_end')
        g = stable(A, B)
        check(abs(g[B][0] - f0[B][0] - dx) <= 2 and abs(g[B][1] - f0[B][1] - dy) <= 2 and g[A] == f0[A],
              f'a ({dx}, {dy}) swipe is a drag: B moved by it, nothing paired (Wayfire)', str(g))

@scenario
def esc_cancels_pending_touchpad_hold():
    'Esc before a three-finger hold fires cancels it'
    setup(PAIR, A); f0 = frames(A, B)
    pointer(*center(B)); time.sleep(.1)
    pad('hold_begin', fingers=3); time.sleep(.2); key('ESC', True); key('ESC', False)
    time.sleep(.6); pad('hold_end', cancelled=False)
    ok, _ = holds(lambda: frames(A, B) == f0, .8)
    check(ok, 'Esc then lift: nothing changes (Wayfire)')

@scenario
def hint_hold_commits_outright():
    'the hint-key hold stays an outright commit, paired while the key is still down'
    setup(PAIR, A); want, _ = pair_plan(A, B, area)
    alt_down()
    label = next(h['hint'] for h in ipc('scottland/hints')['hints'] if h['window'] == B)
    key(label.upper(), True)
    verify(lambda: paired(A, B, area, want), 3, 'paired while the hint key is still down (Wayfire)')
    key(label.upper(), False); key('LEFTALT', False)

def ring_pixels(cx, cy, radius, rgb, angles, name):
    size = int(radius * 2 + 24); x0, y0 = int(cx - size / 2), int(cy - size / 2)
    data, w, d = capture(x0, y0, size, size, name)
    want = [round(c * 255) for c in rgb]; found = []
    for a in angles:
        qx = (cx + radius * math.sin(math.radians(a)) - x0) * d; qy = (cy - radius * math.cos(math.radians(a)) - y0) * d
        hit = False
        for oy in (-1, 0, 1):
            for ox in (-1, 0, 1):
                i = (int(qy) + oy) * w + int(qx) + ox
                if 0 <= i < len(data) // 3:
                    hit |= all(abs(data[3 * i + k] - want[k]) <= 40 for k in range(3))
        found.append(hit)
    return found
def ring(): return ipc('scottland/hints')['hold_ring']

@scenario
def hold_ring():
    'the hold ring fills clockwise from the top mid-hold, is complete at the fire, gone on a cancel (pixels)'
    ipc('wayfire/set-config-options', {'scottland/window_hold_delay': 1500})
    try:
        setup(PAIR, A); x, y = center(B)
        super_press(x, y)
        r = need(lambda: (lambda r: r if r['visible'] and r['progress'] > .3 else None)(ring()), 2, 'ring filling')
        shown = ring_pixels(r['x'], r['y'], r['radius'], r['color'], (60, 270), 'ring-mid.png')
        check(shown == [True, False] and abs(r['x'] - x) < 1 and abs(r['y'] - y) < 1,
              'mid-hold: drawn at the pointer, 60° filled, 270° not yet', f'{shown} {r}')
        f = need(lambda: (lambda r: r if r.get('fired') else None)(ring()), 3, 'ring fired')
        shown = ring_pixels(f['x'], f['y'], f['radius'], f['color'], (60, 150, 240, 330), 'ring-fired.png')
        check(all(shown), 'complete when the hold fires', str(shown))
        super_release()
        need(lambda: not ring()['visible'], 2, 'ring removed after firing')
        setup(PAIR, A); x, y = center(B)
        super_press(x, y)
        r = need(lambda: (lambda r: r if r['visible'] and r['progress'] > .2 else None)(ring()), 2, 'ring before cancel')
        super_release()
        need(lambda: not ring()['visible'], 1, 'ring gone on release')
        shown = ring_pixels(r['x'], r['y'], r['radius'], r['color'], (30, 60), 'ring-cancelled.png')
        check(shown == [False, False], 'releasing early removes the ring at once', str(shown))
    finally: ipc('wayfire/set-config-options', {'scottland/window_hold_delay': 500})

@scenario
def cross_output_offer():
    'a held window on the other screen is previewed on the focused window\'s screen, then joins it'
    o1, o2 = outputs()
    setup([(B, 300, 300, 420, 300, o1['id']), (A, 700, 200, 520, 360, o2['id'])], A)  # B's pair spot there is free
    area2 = {'width': o2['geometry']['width'], 'height': o2['geometry']['height']}
    want, _ = pair_plan(B, A, area2)
    target = (o2['geometry']['x'] + want[B][0] + 210, o2['geometry']['y'] + want[B][1] + 150)
    check(not light(px(*target)), 'fixture: B\'s pair spot on the other screen is empty')
    super_press(*center(B))
    need(lambda: light(px(*target)), 3, 'B previewed on the other screen')
    # B's light run joins A's through the liquid between them, so judge B by its height and its
    # own left edge (its pair spot's), not the joined run's width.
    v = run_along(*target, horizontal=False); h = run_along(*target, horizontal=True)
    left = o2['geometry']['x'] + want[B][0]
    check(v and h and abs((v[1] - v[0]) - 300) <= 8 and abs(h[0] - left) <= 8,
          'B is previewed at its size, at its pair spot on the focused window\'s screen (pixels)', f'{v} {h} left {left}')
    super_release()
    verify(lambda: raw(B)['output-id'] == o2['id'] and paired(B, A, area2, want), 3, 'taking it moves B to that screen, paired with A (Wayfire)')

try:
    ipc('wayfire/set-config-options', {'scottland/sounds': False, 'scottland/alt_hold_delay': 300,
        'scottland/window_hold_delay': 500, 'scottland/window_double_tap_delay': 300,
        'scottland/window_avoidance_always': False, 'scottland/solo_audition_hotspot': 50,
        'output:HEADLESS-1/mode': '1600x1000@60000', 'output:HEADLESS-1/position': '0, 0'})
    if TWO:
        ipc('wayfire/set-config-options', {'output:HEADLESS-2/mode': '1280x800@60000', 'output:HEADLESS-2/position': '1600, 0'})
    need(lambda: outputs()[0]['geometry']['width'] == 1600 and (not TWO or len(outputs()) == 2), 5, 'outputs configured')
    area = {'width': 1600, 'height': 1000}
    A, B, C = launch('HoldA'), launch('HoldB'), launch('HoldC')
    PAIR = [(C, 1100, 40, 420, 200), (B, 1060, 560, 420, 300), (A, 200, 140, 520, 360)]
    SOLO = [(C, 600, 560, 420, 300), (B, 1100, 80, 420, 300), (A, 560, 140, 520, 360)]
    if TWO: scenarios[:] = [cross_output_offer]
    else: scenarios.remove(cross_output_offer)
    run_all()
finally:
    try: release_all()
    except Exception: pass
    for client in clients:
        if client.poll() is None: client.terminate()
    for client in clients:
        try: client.wait(timeout=5)
        except Exception: client.kill(); client.wait()
print(f'{passed} passed, {failed} failed ({len(scenarios)} scenarios)', flush=True)
sys.exit(1 if failed else 0)
