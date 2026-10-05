#!/usr/bin/env python3
"""Hold forms (WK35/WK36/WK39; Mike 2026-10-04): every gesture that drags a window, held still for
the hold delay instead of dragged, solos the focused window or pairs an unfocused one. And the
hold ring that times it (WK39).

Input: stipc keys, pointer and buttons (Super + press, plain press, halo press) and the test-only
virtual touchpad (scottland/test-touchpad), whose wlroots device emits libinput's hold, swipe and
button events through Wayfire's input path; it bypasses only libinput itself (thresholds, palm and
tap detection), which is why jittery fingers are fed as small swipe deltas. Results are judged by
Wayfire's mapped geometry (the pair's positions, solo's other windows leaving the center) and, for
the ring, by captured pixels. Usage: hold-forms-test.py ARTIFACTS
"""
import json, random, subprocess, time
from pathlib import Path

src = open(Path(__file__).with_name('pairing-test.py')).read()
exec(src[:src.index('\ntry:\n')])  # its helpers: ipc, wait, check, setup, check_pair, pointer, button, ...

random.seed(39)
def pad(event, **data): return ipc('scottland/test-touchpad', dict(event=event, **data))
def center_of(id): x1, y1, x2, y2 = footprint(id); return (x1 + x2) / 2, (y1 + y2) / 2
def frames(*ids): return {i: geometry(i) for i in ids}
def in_center_zone(id, area):
    g = geometry(id); cx = g['x'] + g['width'] / 2
    edge = area['width'] * (1 - 1 / 3) / 2  # the shipped 33.3% center zone
    return edge < cx < area['width'] - edge

def jiggle(seconds, amplitude, move):
    """Sub-wobble wandering for `seconds`: `move(dx, dy)` is called with small steps."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        move(random.uniform(-amplitude, amplitude), random.uniform(-amplitude, amplitude)); time.sleep(.03)

def super_hold(id, seconds, wander=0, at=None):
    x, y = at or center_of(id); pointer(x, y); time.sleep(.05)
    key('LEFTMETA', True); button(True)
    jiggle(seconds, wander, lambda dx, dy: pointer(x + dx, y + dy)) if wander else time.sleep(seconds)
    button(False); key('LEFTMETA', False)

def await_pair(name, left, right, before):
    """The pair's planned positions appear (Wayfire geometry), within a generous deadline."""
    want, _, _ = expected((left, (geometry(left)['width'], geometry(left)['height'])),
                          (right, (geometry(right)['width'], geometry(right)['height'])), area)
    try:
        wait(lambda: all(abs(geometry(i)['x'] - want[i][0]) <= 1 and abs(geometry(i)['y'] - want[i][1]) <= 1
                         for i in want), 3, name)
    except RuntimeError: pass
    check_pair(name, left, right, None, area, before)

# ---- pixels: is the ring drawn at these angles (clockwise from the top)?
def ring_pixels(cx, cy, radius, rgb, angles, png):
    """For each angle, whether a pixel on the ring there has the ring's color (3x3 neighborhood)."""
    size = int(radius * 2 + 24); x0, y0 = int(cx - size / 2), int(cy - size / 2)
    subprocess.run(['grim', '-g', f'{x0},{y0} {size}x{size}', str(png)], check=True)
    raw = subprocess.run(['magick', str(png), '-depth', '8', 'rgb:-'], check=True, capture_output=True).stdout
    want = [round(c * 255) for c in rgb]; found = []
    for a in angles:
        px = cx + radius * math.sin(math.radians(a)) - x0; py = cy - radius * math.cos(math.radians(a)) - y0
        hit = False
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                i = (int(py) + dy) * size + int(px) + dx
                if 0 <= i < size * size:
                    r, g, b = raw[3 * i: 3 * i + 3]
                    hit |= abs(r - want[0]) <= 40 and abs(g - want[1]) <= 40 and abs(b - want[2]) <= 40
        found.append(hit)
    return found

def ring(): return hints()['hold_ring']

def px(x, y):
    """The color drawn at one logical point (a 1x1 capture: what is on screen)."""
    out = art / 'px.png'
    subprocess.run(['grim', '-g', f'{int(x)},{int(y)} 1x1', str(out)], check=True)
    raw = subprocess.run(['magick', str(out), '-depth', '8', 'rgb:-'], check=True, capture_output=True).stdout
    return tuple(raw[:3])
def light(rgb): return sum(rgb) > 450  # a GTK window's light background, not the dark desktop
def offer(): return ipc('scottland/spread-state')['hold_offer']
def until(predicate, seconds, what):
    """Poll a predicate; on the deadline, a failure with the last observation (not an exception)."""
    end = time.monotonic() + seconds; last = None
    while time.monotonic() < end:
        last = predicate()
        if last: return True, last
        time.sleep(.03)
    return False, last

try:
    ipc('wayfire/set-config-options', {'scottland/sounds': False, 'scottland/alt_hold_delay': 300,
        'scottland/window_hold_delay': 500, 'scottland/window_double_tap_delay': 300,
        'scottland/window_avoidance_always': False, 'scottland/solo_audition_delay': 3000,
        'output:HEADLESS-1/mode': '1600x1000@60000'})
    time.sleep(1)
    area = {'x': 0, 'y': 0, 'width': outputs()[0]['geometry']['width'], 'height': outputs()[0]['geometry']['height']}
    A, B, C = launch('HoldA'), launch('HoldB'), launch('HoldC')
    pair_layout = [(C, 1100, 80, 420, 260), (B, 1060, 560, 420, 300), (A, 200, 140, 520, 360)]  # A focused
    solo_layout = [(C, 600, 560, 420, 300), (B, 1060, 120, 420, 300), (A, 560, 140, 520, 360)]  # A and C center

    # ---- Super + press-and-hold (pointer), with and without Super, held and dragged
    setup(pair_layout, A); before = sizes_of(A, B)
    super_hold(B, .8, wander=4)
    await_pair('Super + press-and-hold (hand wobble 4 px) on an unfocused window pairs it', A, B, before)

    setup(solo_layout, A); a0 = geometry(A)
    check(in_center_zone(C, area), 'fixture: a second window in the center for solo')
    super_hold(A, .8, wander=3)
    try: wait(lambda: not in_center_zone(C, area), 3, 'solo')
    except RuntimeError: pass
    check(not in_center_zone(C, area) and in_center_zone(A, area),
          'Super + press-and-hold on the focused window solos it (the other center window leaves the center)',
          f'C {geometry(C)} A {geometry(A)}')

    setup(pair_layout, A); f0 = frames(A, B)
    x, y = center_of(B); pointer(x, y); time.sleep(.05); key('LEFTMETA', True); button(True)
    for i in range(1, 9): pointer(x - 6 * i, y); time.sleep(.02)  # 48 px: past the wobble at once
    time.sleep(.8); button(False); key('LEFTMETA', False)
    try: wait(lambda: geometry(B)['x'] < f0[B]['x'] - 30, 2, 'drag')
    except RuntimeError: pass
    check(geometry(B)['x'] < f0[B]['x'] - 30 and geometry(A) == f0[A],
          'Super + press that moves past the wobble first is a drag: moved, nothing paired', f'B {geometry(B)} A {geometry(A)}')

    setup(pair_layout, A); f0 = frames(A, B)
    super_hold(B, .12); time.sleep(.8)
    check(frames(A, B) == f0, 'Super + quick click: nothing moves')

    setup(pair_layout, A); f0 = frames(A, B)
    x, y = center_of(B); pointer(x, y); time.sleep(.05); button(True); time.sleep(.8); button(False); time.sleep(.6)
    check(frames(A, B) == f0, 'a plain press-and-hold without Super is the app\'s: nothing moves or pairs')

    # ---- Super + double-tap-and-hold: libinput's tap-and-drag arrives as two button presses
    setup(pair_layout, A); before = sizes_of(A, B)
    x, y = center_of(B); pointer(x, y); time.sleep(.05); key('LEFTMETA', True)
    pad('button', button='left', pressed=True); time.sleep(.06); pad('button', button='left', pressed=False)
    time.sleep(.12)
    pad('button', button='left', pressed=True); time.sleep(.8); pad('button', button='left', pressed=False)
    key('LEFTMETA', False)
    await_pair('Super + double-tap-and-hold on the touchpad pairs the unfocused window (not a solo)', A, B, before)

    # ---- the halo: press on the band outside the window and hold
    setup(pair_layout, A); before = sizes_of(A, B)
    fr = layout(B)['frame']; hx = fr['x'] - fr['thickness'] / 2; hy = fr['y'] + fr['height'] / 2
    pointer(hx, hy); time.sleep(.1); button(True); time.sleep(.8); button(False)
    await_pair('holding the halo of an unfocused window pairs it', A, B, before)

    # ---- three fingers: libinput hold, hold turning into a jittery swipe, a jittery swipe alone
    setup(pair_layout, A); before = sizes_of(A, B); x, y = center_of(B); pointer(x, y); time.sleep(.1)
    pad('hold_begin', fingers=3); time.sleep(.15); pad('hold_end', cancelled=True)  # jitter: libinput gives up
    pad('swipe_begin', fingers=3)
    jiggle(.7, 1.5, lambda dx, dy: pad('swipe_update', fingers=3, dx=dx, dy=dy))
    pad('swipe_end')
    await_pair('three resting fingers whose hold libinput cancels into a jittery swipe still pair', A, B, before)

    setup(pair_layout, A); before = sizes_of(A, B); x, y = center_of(B); pointer(x, y); time.sleep(.1)
    pad('swipe_begin', fingers=3)
    jiggle(.8, 1.5, lambda dx, dy: pad('swipe_update', fingers=3, dx=dx, dy=dy))
    pad('swipe_end')
    await_pair('a jittery three-finger swipe within the wobble (no libinput hold at all) pairs', A, B, before)

    setup(pair_layout, A); f0 = frames(A, B); x, y = center_of(B); pointer(x, y); time.sleep(.1)
    pad('swipe_begin', fingers=3)
    for _ in range(10): pad('swipe_update', fingers=3, dx=-5, dy=0); time.sleep(.02)
    time.sleep(.8); pad('swipe_end')
    try: wait(lambda: geometry(B)['x'] < f0[B]['x'] - 30, 2, 'swipe drag')
    except RuntimeError: pass
    check(geometry(B)['x'] < f0[B]['x'] - 30 and geometry(A) == f0[A],
          'a three-finger swipe past the wobble is a drag (L23): moved, nothing paired', f'B {geometry(B)}')

    setup(solo_layout, A); x, y = center_of(A); pointer(x, y); time.sleep(.1)
    pad('hold_begin', fingers=3); time.sleep(.75); pad('hold_end', cancelled=False)
    try: wait(lambda: not in_center_zone(C, area), 3, 'three-finger solo')
    except RuntimeError: pass
    check(not in_center_zone(C, area), 'a three-finger hold on the focused window solos it', f'C {geometry(C)}')

    # ---- three fingers, made reliable: a slow drift under the swipe wobble, a late swipe hand-off
    setup(pair_layout, A); before = sizes_of(A, B); x, y = center_of(B); pointer(x, y); time.sleep(.1)
    pad('swipe_begin', fingers=3)
    for _ in range(18): pad('swipe_update', fingers=3, dx=1, dy=0); time.sleep(.045)  # 18 px over 0.8 s
    pad('swipe_end')
    await_pair('three resting fingers drifting 18 px (more than a hand, less than the swipe wobble) still pair', A, B, before)
    setup(pair_layout, A); before = sizes_of(A, B); x, y = center_of(B); pointer(x, y); time.sleep(.1)
    pad('hold_begin', fingers=3); time.sleep(.1); pad('hold_end', cancelled=True); time.sleep(.25)
    pad('swipe_begin', fingers=3)
    jiggle(.5, 1.5, lambda dx, dy: pad('swipe_update', fingers=3, dx=dx, dy=dy))
    pad('swipe_end')
    await_pair('a swipe arriving 250 ms after libinput cancelled the hold continues it and pairs', A, B, before)

    # ---- offers (WK39; Mike, 2026-10-05): a fired pointer hold previews its result while held,
    # commits on release within the hotspot, and returns everything when dragged out of it.
    # Exact-size GTK windows: light on the dark desktop, so the preview can be read in pixels.
    for client in clients: client.terminate()
    for client in clients: client.wait(timeout=5)
    clients.clear()
    A, B, C = launch_gtk('OfferA'), launch_gtk('OfferB'), launch_gtk('OfferC')
    pair_layout = [(C, 1100, 40, 420, 200), (B, 1060, 560, 420, 300), (A, 200, 140, 520, 360)]
    solo_layout = [(C, 600, 560, 420, 300), (B, 1100, 80, 420, 300), (A, 560, 140, 520, 360)]
    want, _, _ = expected((A, (520, 360)), (B, (420, 300)), area)
    b_old = (1300, 800)  # inside B's place (also when dragged 80 px left), outside its pair spot
    b_new = (want[B][0] + 210, want[B][1] + 150)                           # the middle of B's pair spot
    setup(pair_layout, A); f0 = frames(A, B); before = sizes_of(A, B)
    check(light(px(*b_old)) and not light(px(*b_new)), 'fixture: B drawn at its place, its pair spot empty')
    x, y = center_of(B); pointer(x, y); time.sleep(.05); key('LEFTMETA', True); button(True)
    ok, seen = until(lambda: (lambda o, n: o if (not light(o) and light(n)) else None)(px(*b_old), px(*b_new)), 3, 'preview')
    check(ok, 'a fired Super hold previews the pair while held: B drawn at its pair spot, its place empty', str(seen))
    check(frames(A, B) == f0, 'the preview changes no true geometry while held (Wayfire geometry)', str(frames(A, B)))
    shot('offer-preview.png')
    button(False); key('LEFTMETA', False)
    await_pair('releasing within the hotspot takes the offered pair', A, B, before)

    setup(pair_layout, A); f0 = frames(A, B)
    x, y = center_of(B); pointer(x, y); time.sleep(.05); key('LEFTMETA', True); button(True)
    until(lambda: offer()['active'] and offer()['progress'] > .9, 3, 'offer shown')
    for i in range(1, 11): pointer(x - 8 * i, y); time.sleep(.02)      # 80 px: out of the 50 pt hotspot
    ok, seen = until(lambda: (lambda o: o if light(o) else None)(px(*b_old)), 2, 'B back')
    check(ok and frames(A, B)[A] == f0[A], 'dragging out of the hotspot refuses the offer: the preview returns', str(seen))
    time.sleep(.2); button(False); key('LEFTMETA', False)
    ok, g = until(lambda: (lambda g: g if abs(g['x'] - (f0[B]['x'] - 80)) <= 3 else None)(geometry(B)), 2, 'drag drop')
    check(ok and geometry(A) == f0[A], 'then the gesture carries on as the ordinary drag: B dropped where the pointer took it, A untouched',
          f'B {geometry(B)} (from {f0[B]["x"]}) A {geometry(A)}')

    setup(pair_layout, A); before = sizes_of(A, B)
    x, y = center_of(B); pointer(x, y); time.sleep(.05); key('LEFTMETA', True); button(True)
    until(lambda: offer()['active'], 3, 'offer')
    for i in range(1, 9): pointer(x + 4 * i, y); time.sleep(.02)       # 32 px: past the wobble, inside the hotspot
    time.sleep(.2); button(False); key('LEFTMETA', False)
    await_pair('moving within the hotspot after the hold fired still takes the offer', A, B, before)

    setup(pair_layout, A); f0 = frames(A, B)
    x, y = center_of(B); pointer(x, y); time.sleep(.05); key('LEFTMETA', True); button(True)
    until(lambda: offer()['active'] and offer()['progress'] > .9, 3, 'offer before Esc')
    key('ESC', True); key('ESC', False); button(False); key('LEFTMETA', False)
    ok, seen = until(lambda: (lambda o: o if light(o) else None)(px(*b_old)), 2, 'B back after Esc')
    time.sleep(.6)
    check(ok and frames(A, B) == f0, 'Esc during an offer: nothing changes', str(frames(A, B)))

    setup(solo_layout, A); f0 = frames(A, C)
    c_old = (center_of(C)[0] + 120, center_of(C)[1] + 80)
    check(light(px(*c_old)), 'fixture: C drawn in the center beside A')
    x, y = center_of(A); pointer(x, y); time.sleep(.05); key('LEFTMETA', True); button(True)
    ok, seen = until(lambda: (lambda o: o if not light(o) else None)(px(*c_old)), 3, 'solo preview')
    check(ok and frames(A, C) == f0, 'a fired Super hold on the focused window previews the solo: C leaves the center on screen only', str(seen))
    button(False); key('LEFTMETA', False)
    ok, _ = until(lambda: not in_center_zone(C, area), 3, 'solo commit')
    check(ok, 'releasing within the hotspot takes the solo (C moved to the periphery)', str(geometry(C)))

    setup(pair_layout, A); before = sizes_of(A, B); x, y = center_of(B); pointer(x, y); time.sleep(.1)
    pad('hold_begin', fingers=3)
    ok, seen = until(lambda: (lambda o, n: o if (not light(o) and light(n)) else None)(px(*b_old), px(*b_new)), 3, 'pad preview')
    check(ok and frames(A, B)[B] == geometry(B), 'a three-finger hold previews the pair while the fingers rest', str(seen))
    pad('hold_end', cancelled=False)
    await_pair('lifting the three fingers takes the offer', A, B, before)

    setup(pair_layout, A); f0 = frames(A, B); x, y = center_of(B); pointer(x, y); time.sleep(.1)
    pad('hold_begin', fingers=3)
    until(lambda: offer()['active'] and offer()['progress'] > .9, 3, 'pad offer')
    pad('hold_end', cancelled=True); pad('swipe_begin', fingers=3)
    for _ in range(10): pad('swipe_update', fingers=3, dx=-8, dy=0); time.sleep(.02)
    time.sleep(.3); pad('swipe_end')
    ok, g = until(lambda: (lambda g: g if g['x'] < f0[B]['x'] - 50 else None)(geometry(B)), 2, 'pad drag drop')
    check(ok and geometry(A) == f0[A], 'three fingers moving out of the hotspot refuse the offer and drag B (L23)',
          f'B {geometry(B)} A {geometry(A)}')

    setup(pair_layout, A); before = sizes_of(A, B); alt(True)
    text = hint(B)['hint']; key(text.upper(), True)
    want, _, _ = expected((A, (geometry(A)['width'], geometry(A)['height'])), (B, (geometry(B)['width'], geometry(B)['height'])), area)
    ok, _ = until(lambda: abs(geometry(B)['x'] - want[B][0]) <= 1, 3, 'hint pair while held')
    check(ok, 'the hint-key hold stays an outright commit: paired while the key is still down', str(geometry(B)))
    key(text.upper(), False); alt(False)

    # ---- the hold ring (WK39): a 1.5 s hold so a mid-hold capture is well inside the fill
    ipc('wayfire/set-config-options', {'scottland/window_hold_delay': 1500})
    setup(pair_layout, A); x, y = center_of(B); pointer(x, y); time.sleep(.1)
    key('LEFTMETA', True); button(True); pressed = time.monotonic()
    r = wait(lambda: (lambda r: r['visible'] and r['progress'] > .3 and r)(ring()), 2, 'ring fill')
    shown = ring_pixels(r['x'], r['y'], r['radius'], r['color'], (60, 270), art / 'ring-mid.png')
    check(shown == [True, False], 'Super-hold ring mid-hold: drawn at the pointer, filled clockwise from the top (60° drawn, 270° not yet)',
          f'{shown} progress {r["progress"]:.2f} at {r["x"]:.0f},{r["y"]:.0f}')
    check(abs(r['x'] - x) < 1 and abs(r['y'] - y) < 1, 'the pointer hold\'s ring is centered on the pointer', f'{r} vs {x},{y}')
    fired = wait(lambda: (lambda r: r.get('fired') and r)(ring()), 3, 'ring complete')
    shown = ring_pixels(fired['x'], fired['y'], fired['radius'], fired['color'], (60, 150, 240, 330), art / 'ring-fired.png')
    check(all(shown), 'the ring is complete when the hold fires', f'{shown} after {time.monotonic() - pressed:.2f} s')
    button(False); key('LEFTMETA', False)
    wait(lambda: not ring()['visible'], 2, 'ring removed after firing')

    setup(pair_layout, A); f0 = frames(A, B); x, y = center_of(B); pointer(x, y); time.sleep(.1)
    key('LEFTMETA', True); button(True)
    r = wait(lambda: (lambda r: r['visible'] and r['progress'] > .2 and r)(ring()), 2, 'ring before cancel')
    button(False); key('LEFTMETA', False)
    wait(lambda: not ring()['visible'], 1, 'ring gone on release')
    shown = ring_pixels(r['x'], r['y'], r['radius'], r['color'], (30, 60), art / 'ring-cancelled.png')
    check(shown == [False, False], 'releasing early removes the ring at once (no pixels left)', str(shown))

    setup(pair_layout, A); x, y = center_of(B); pointer(x, y); time.sleep(.1)
    key('LEFTMETA', True); button(True)
    wait(lambda: (lambda r: r['visible'] and r['progress'] > .2 and r)(ring()), 2, 'ring before move')
    for i in range(1, 9): pointer(x - 6 * i, y); time.sleep(.02)
    wait(lambda: not ring()['visible'], 1, 'ring gone on movement')
    shown = ring_pixels(x - 48, y, 22, r['color'], (30, 60), art / 'ring-moved.png')
    check(shown == [False, False], 'moving past the wobble removes the ring', str(shown))
    button(False); key('LEFTMETA', False); time.sleep(.6)

    setup(pair_layout, A); alt(True)
    text = hint(B)['hint']; key(text.upper(), True)
    r = wait(lambda: (lambda r: r['visible'] and r['progress'] > .3 and r)(ring()), 2, 'hint ring')
    b = hint(B)['badge']; bx, by = b['x'] + b['size'] / 2, b['y'] + b['size'] / 2
    check(abs(r['x'] - bx) < 1.5 and abs(r['y'] - by) < 1.5 and r['radius'] > b['size'] / 2,
          'the hint hold\'s ring surrounds that hint\'s badge', f'{r} badge {b}')
    shown = ring_pixels(r['x'], r['y'], r['radius'], r['color'], (60, 270), art / 'hint-ring-mid.png')
    check(shown == [True, False], 'hint ring mid-hold: filled clockwise from the top in the hint color', str(shown))
    key(text.upper(), False)
    wait(lambda: not ring()['visible'], 1, 'hint ring gone on release')
    shown = ring_pixels(r['x'], r['y'], r['radius'], r['color'], (30, 60), art / 'hint-ring-released.png')
    check(shown == [False, False], 'releasing the hint early removes its ring', str(shown))
    alt(False)
    ipc('wayfire/set-config-options', {'scottland/window_hold_delay': 500})
finally:
    try:
        button(False); key('LEFTMETA', False); key('LEFTALT', False)
    except Exception: pass
    for client in clients:
        if client.poll() is None: client.terminate()
print(f'{passed} passed, {failed} failed', flush=True)
sys.exit(1 if failed else 0)
