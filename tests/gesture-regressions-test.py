#!/usr/bin/env python3
"""Regressions for every gesture and goo system the hold/solo/pair code shares (Mike, 2026-10-05:
"regression tests on any other gesture/goo system hold-solo-pair touches").

Each scenario drives an existing behavior through real input and judges it by Wayfire's own
geometry or captured pixels, exactly as it behaved before the hold forms; the suite must pass on
unmodified main as well as on the hold-forms branch. Inputs avoid the hold forms themselves
(a drag moves past the 12 px wobble at once; nothing is held still for the hold delay unless the
scenario is about holds), since those are hold-forms-test's subject.

Input layers: stipc keys, pointer, buttons and touch; the test-only virtual touchpad
(scottland/test-touchpad, libinput's events into Wayfire's input path, libinput itself bypassed).
Scottland's IPC only sets fixtures up and says where to aim (a halo's band, a peek offset).
Usage: gesture-regressions-test.py ARTIFACTS
"""
from pathlib import Path
_src = open(Path(__file__).with_name('hold-forms-test.py')).read()
exec(_src[:_src.index('# ---------------------------------------------------------------- scenarios')])

def super_press(x, y, shift=False):
    pointer(x, y); time.sleep(.05); key('LEFTMETA', True)
    if shift: key('LEFTSHIFT', True)
    button(True)
def super_release(shift=False):
    button(False)
    if shift: key('LEFTSHIFT', False)
    key('LEFTMETA', False)
def glide(x0, y0, x1, y1, steps=12, pause=.02, move=None):
    move = move or pointer
    for i in range(1, steps + 1): move(x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps); time.sleep(pause)
def bg(): return px(5, 995)  # the desktop, far from every window
def differs(a, b, by=24): return sum(abs(x - y) for x, y in zip(a, b)) > by
def halo_run(x, y, step):
    """Logical px going outward from (x, y), away from the window, until the bare desktop: the
    halo band (a swollen or glowing halo may be bright)."""
    base = bg(); n = 0
    for i in range(1, 48):
        if not differs(px(x + step * i, y), base): break
        n += 1
    return n

@scenario
def super_drag():
    'Super + drag: the window follows the pointer at its grab point and drops where let go (L8)'
    setup(LAYOUT, A); f0 = frames(A, B)
    x, y = center(B); super_press(x, y)
    glide(x, y, x + 120, y)
    need(lambda: light(px(x + 120, y)) and not light(px(f0[B][0] + 6, y)), 2, 'drawn at the pointer')
    check(light(px(x + 120 - 150, y)), 'mid-drag: B drawn around the pointer, its old left edge empty (pixels)')
    time.sleep(.25); super_release()
    g = stable(A, B)
    check(abs(g[B][0] - (f0[B][0] + 120)) <= 2 and g[B][1] == f0[B][1] and g[A] == f0[A],
          'dropped 120 px right of where it was, A untouched (Wayfire)', str(g))

@scenario
def super_flick_coasts():
    'a Super + drag flick coasts on after release (L32)'
    setup(LAYOUT, A); f0 = frames(B)
    x, y = center(B); super_press(x, y)
    glide(x, y, x + 150, y, steps=6, pause=.016)
    super_release()
    g = stable(B)
    check(g[B][0] - f0[B][0] > 170, 'released while moving: it coasted past the release point (Wayfire)', str(g))

@scenario
def shift_drag_keeps_scale():
    'Super + Shift + drag keeps the scale it had into the periphery; an ordinary drag scales down (L31)'
    setup(LAYOUT, A)
    x, y = center(B); super_press(x, y, shift=True)
    glide(x, y, 1330, y); time.sleep(.25); super_release(shift=True)
    stable(B)
    size = drawn_size(*center(B))
    check(size and abs(size[0] - 420) <= 8, 'Shift-dragged into the periphery: still drawn at 100% (pixels)', str(size))
    setup(LAYOUT, A)
    x, y = center(B); super_press(x, y)
    glide(x, y, 1330, y); time.sleep(.25); super_release()
    stable(B); time.sleep(.3)
    size = drawn_size(*center(B))
    check(size and size[0] < 405, 'an ordinary drag there is drawn scaled down (pixels)', str(size))

@scenario
def halo_move():
    'dragging the halo moves the window (A5)'
    setup(LAYOUT, A); f0 = frames(B)
    fr = scottland_view(B)['frame']
    hx, hy = fr['x'] - fr['thickness'] / 2, fr['y'] + fr['height'] / 2
    pointer(hx, hy); time.sleep(.15); button(True)
    glide(hx, hy, hx - 100, hy); time.sleep(.25); button(False)
    g = stable(B)
    check(abs(g[B][0] - (f0[B][0] - 100)) <= 2 and g[B][1] == f0[B][1], 'moved 100 px left by its halo (Wayfire)', str(g))

@scenario
def halo_corner_resize():
    'dragging a halo corner resizes around the center (A-corners, L20)'
    setup(LAYOUT, A); x0, y0, w0, h0 = geometry(B)
    fr = scottland_view(B)['frame']; t = fr['thickness']
    cx, cy = fr['x'] + fr['width'] + t / 2, fr['y'] + fr['height'] + t / 2   # bottom-right, outside
    pointer(cx, cy); time.sleep(.15); button(True)
    glide(cx, cy, cx + 30, cy + 20); time.sleep(.25); button(False)
    x, y, w, h = stable(B)[B]
    check(w > w0 + 20 and h > h0 + 10 and abs((x + w / 2) - (x0 + w0 / 2)) <= 2 and abs((y + h / 2) - (y0 + h0 / 2)) <= 2,
          'grew, keeping its center (Wayfire)', str((x, y, w, h)))

@scenario
def halo_close_dot():
    'the close dot on the bottom shore closes the window when clicked (A-close)'
    D = launch('GestureClose'); place(D, 600, 300, 400, 260); ipc('window-rules/focus-view', {'id': A}); stable(D)
    fr = scottland_view(D)['frame']
    dx, dy = fr['x'] + fr['width'] / 2, fr['y'] + fr['height'] + fr['thickness'] / 2
    pointer(dx, dy - 30); time.sleep(.2); pointer(dx, dy); time.sleep(.6)   # hover: the dot shows
    button(True); time.sleep(.05); button(False)
    verify(lambda: not any(v['id'] == D for v in views()), 3, 'clicking the close dot closes it (Wayfire: the view is gone)')

@scenario
def plain_click_focuses():
    'a plain click focuses the window under it and moves nothing'
    setup(LAYOUT, A); f0 = frames(A, B)
    pointer(*center(B)); time.sleep(.05); button(True); time.sleep(.05); button(False)
    verify(lambda: focused() == B, 1, 'the click focused B (Wayfire)')
    ok, _ = holds(lambda: frames(A, B) == f0, .6)
    check(ok, 'and moved nothing (Wayfire)')

@scenario
def esc_returns():
    'Esc during a drag returns the window to where it was picked up (L27)'
    setup(LAYOUT, A); f0 = frames(B)
    x, y = center(B); super_press(x, y)
    glide(x, y, x + 150, y - 60); time.sleep(.15)
    key('ESC', True); key('ESC', False); super_release()
    verify(lambda: stable(B) == f0, 3, 'Esc: back exactly where it was (Wayfire)')

@scenario
def rail_widgetize_and_back():
    'dragging onto the rail makes a widget; dragging the card off brings the app back (WG1, WG22)'
    setup(LAYOUT, A); f0 = frames(B)
    x, y = center(B); super_press(x, y)
    glide(x, y, 1596, y, steps=20); time.sleep(.3); super_release()
    card = need(lambda: next((w['widget_view'] for w in ipc('scottland/widgets')['widgets']
        if int(w['id']) == B and w['widget_view'] > 0), None), 4, 'a card appears')
    need(lambda: any(v['id'] == card and v['mapped'] for v in views()), 3, 'card mapped')
    check(not light(px(f0[B][0] + 210, f0[B][1] + 150)), 'B no longer drawn at its place (pixels)')
    cg = need(lambda: next((v['geometry'] for v in views() if v['id'] == card), None), 2, 'card geometry')
    co = output_of(card)['geometry']
    cx, cy = co['x'] + cg['x'] + cg['width'] / 2, co['y'] + cg['y'] + cg['height'] / 2
    check(co['x'] + cg['x'] + cg['width'] > 1550, 'the card sits on the right rail (Wayfire)', str(cg))
    super_press(cx, cy); glide(cx, cy, 800, 500, steps=20); time.sleep(.3); super_release()
    verify(lambda: raw(B)['mapped'] and light(px(800, 500)) and not any(v['id'] == card for v in views()), 4,
           'dragged off the rail: B is a window again where it was dropped, the card gone (Wayfire, pixels)')

@scenario
def three_finger_swipe_moves():
    'a three-finger swipe moves the window under the pointer (L23)'
    setup(LAYOUT, A); f0 = frames(B)
    pointer(*center(B)); time.sleep(.1)
    pad('swipe_begin', fingers=3)
    for _ in range(10): pad('swipe_update', fingers=3, dx=10, dy=0); time.sleep(.02)
    time.sleep(.25); pad('swipe_end')
    g = stable(B)
    check(abs(g[B][0] - (f0[B][0] + 100)) <= 3 and abs(g[B][1] - f0[B][1]) <= 3, 'moved 100 px with the fingers (Wayfire)', str(g))

@scenario
def three_finger_click_drag_resizes():
    'a three-finger click-drag resizes around the center; a still click moves nothing (L24)'
    setup(LAYOUT, A); x0, y0, w0, h0 = geometry(B)
    x, y = center(B); pointer(x, y); time.sleep(.1)
    pad('button', button='middle', pressed=True)
    glide(x, y, x + 40, y + 30); time.sleep(.2)
    pad('button', button='middle', pressed=False)
    gx, gy, w, h = stable(B)[B]
    check(w != w0 and h != h0 and abs((gx + w / 2) - (x0 + w0 / 2)) <= 2, 'resized around its center (Wayfire)', str((gx, gy, w, h)))
    setup(LAYOUT, A); f0 = frames(B)
    pointer(*center(B)); time.sleep(.1)
    pad('button', button='middle', pressed=True); time.sleep(.1); pad('button', button='middle', pressed=False)
    ok, _ = holds(lambda: frames(B) == f0, .6)
    check(ok, 'a still three-finger click moves nothing (Wayfire)')

@scenario
def touch_long_press_drag():
    'a touchscreen long press lifts the window and it follows the finger (L25)'
    setup(LAYOUT, A); f0 = frames(B)
    x, y = center(B)
    ipc('stipc/touch', {'finger': 0, 'x': round(x), 'y': round(y)}); time.sleep(.55)
    glide(x, y, x - 120, y, move=lambda a, b: ipc('stipc/touch', {'finger': 0, 'x': round(a), 'y': round(b)}))
    time.sleep(.25); ipc('stipc/touch_release', {'finger': 0})
    g = stable(B)
    check(abs(g[B][0] - (f0[B][0] - 120)) <= 3, 'dragged 120 px left by the finger (Wayfire)', str(g))
    setup(LAYOUT, A); f0 = frames(B); x, y = center(B)
    ipc('stipc/touch', {'finger': 0, 'x': round(x), 'y': round(y)}); time.sleep(.08); ipc('stipc/touch_release', {'finger': 0})
    ok, _ = holds(lambda: frames(B) == f0, .6)
    check(ok, 'a quick tap moves nothing (Wayfire)')

@scenario
def grab_peeking_window():
    'grabbing a peeking window keeps it where it is drawn (peek strip, decision 9)'
    ipc('wayfire/set-config-options', {'scottland/window_avoidance_always': True})
    try:
        setup([(C, 1100, 40, 420, 200), (B, 640, 360, 380, 260), (A, 540, 300, 520, 360)], A)  # B wholly behind A
        def offset():  # where avoidance draws B (aiming only)
            h = next(x for x in ipc('scottland/hints')['hints'] if x['window'] == B); return h['dx'], h['dy']
        dx, dy = need(lambda: (lambda o: o if abs(o[0]) + abs(o[1]) > 20 else None)(offset()), 4, 'B peeks')
        x0, y0, _, _ = geometry(B)
        # A point of B as drawn, 4 px inside its far edge on the side it peeks out of: clear of
        # A's halo, whose grab band belongs to A.
        if abs(dy) >= abs(dx): tx, ty = x0 + 190 + dx, y0 + (4 if dy < 0 else 256) + dy
        else: tx, ty = x0 + (4 if dx < 0 else 376) + dx, y0 + 130 + dy
        check(light(px(tx, ty)), 'fixture: B is drawn at its peek offset (pixels)')
        super_press(tx, ty); glide(tx, ty, tx + 40, ty); glide(tx + 40, ty, tx, ty); time.sleep(.3); super_release()
        x1, y1, _, _ = stable(B)[B]
        check(abs(x1 - (x0 + dx)) <= 3 and abs(y1 - (y0 + dy)) <= 3,
              'dropped where it was grabbed: its geometry is now where it was drawn (Wayfire)', str((x1, y1, dx, dy)))
    finally: ipc('wayfire/set-config-options', {'scottland/window_avoidance_always': False})

@scenario
def spread_audition():
    'a drag that rests in the center for the audition delay offers the solo; dropping takes it (SP7)'
    setup([(C, 560, 560, 420, 300), (B, 1150, 120, 420, 300), (A, 300, 120, 520, 360)], A)
    check(in_center(C, area), 'fixture: C in the center')
    x, y = center(B); super_press(x, y)
    glide(x, y, 800, 300, steps=20)
    time.sleep(3.4)                                                   # the 3 s audition pause
    super_release()
    verify(lambda: not in_center(C, area), 3, 'the audition was taken: C left the center (Wayfire)')

@scenario
def hint_cycle_and_hold():
    'Window mode: a tap on the focused hint cycles it; a hold on an unfocused hint pairs (WK6, WK36)'
    setup(LAYOUT, A); f0 = frames(A)
    alt_down = lambda: (key('LEFTALT', True), need(lambda: ipc('scottland/hints')['active'], 2, 'window mode'))
    alt_down()
    label = next(h['hint'] for h in ipc('scottland/hints')['hints'] if h['window'] == A)
    key(label.upper(), True); time.sleep(.08); key(label.upper(), False)
    verify(lambda: not in_center(A, area), 3, 'a tap on the focused window\'s hint sends it to the periphery (Wayfire)')
    key('LEFTALT', False)
    setup(LAYOUT, A); want, _ = pair_plan(A, B, area) if center(A)[0] < center(B)[0] else pair_plan(B, A, area)
    alt_down()
    label = next(h['hint'] for h in ipc('scottland/hints')['hints'] if h['window'] == B)
    key(label.upper(), True); time.sleep(.75); key(label.upper(), False); key('LEFTALT', False)
    verify(lambda: all(abs(geometry(i)[0] - want[i][0]) <= 1 for i in want), 3, 'holding B\'s hint pairs it with A (Wayfire)')

@scenario
def goo_follows_drag_and_hover():
    'the goo: the halo follows a dragged window, and swells near the pointer (GO hover/lift)'
    setup(LAYOUT, A)
    x0, y0, w0, h0 = geometry(B)
    pointer(1580, 980); time.sleep(.8)                                  # far away: the halo at rest
    rest = halo_run(x0 - 1, y0 + h0 / 2, -1)
    pointer(x0 - 30, y0 + h0 / 2); time.sleep(.8)                        # near its left side
    near = halo_run(x0 - 1, y0 + h0 / 2, -1)
    check(rest > 0 and near > rest, 'the halo is drawn at rest and swells near the pointer (pixels)', f'rest {rest} near {near}')
    x, y = center(B); super_press(x, y); glide(x, y, x + 140, y); time.sleep(.3)
    moved = halo_run(x0 + 140 - 1, y0 + h0 / 2, -1)
    old = differs(px(x0 - 4, y0 + h0 / 2), bg()) and not light(px(x0 - 4, y0 + h0 / 2))
    check(moved > 0 and not old, 'mid-drag the halo is around the window where it is drawn, not where it was (pixels)',
          f'moved {moved} old {old}')
    time.sleep(.2); super_release()

try:
    ipc('wayfire/set-config-options', {'scottland/sounds': False, 'scottland/alt_hold_delay': 300,
        'scottland/window_double_tap_delay': 300, 'scottland/window_avoidance_always': False,
        'scottland/solo_audition_hotspot': 50, 'output:HEADLESS-1/mode': '1600x1000@60000',
        'output:HEADLESS-1/position': '0, 0'})
    try: ipc('wayfire/set-config-options', {'scottland/window_hold_delay': 500})
    except RuntimeError: pass  # a build without hint holds has no such option
    need(lambda: outputs()[0]['geometry']['width'] == 1600, 5, 'output configured')
    area = {'width': 1600, 'height': 1000}
    A, B, C = launch('GestureA'), launch('GestureB'), launch('GestureC')
    LAYOUT = [(C, 1100, 40, 420, 200), (B, 700, 560, 420, 300), (A, 480, 120, 520, 360)]  # A focused, B center, right of A
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
