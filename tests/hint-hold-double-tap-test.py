#!/usr/bin/env python3
"""A hint hold on a double-tap's second press (WK15, WK39; Mike, 2026-10-07).

Mike: "if there's a hold on the second tap, it should automatically expire the double-tap".
Tap a hint to select, then tap and hold it: the hold solos (its window is focused) and nothing
goes to the rail. Two quick taps still send the window to the rail. A hold right after another
hint's tap pairs with the window that tap focused. A hold that does nothing says why in the log.
The selected-widget case follows the widget hint-hold test on bug/hint-hold-solo-20261007.

Input: stipc keys only. Oracles: Wayfire's geometry, mapping and focus, captured pixels and the
compositor's log; Scottland's IPC only places fixtures and names hints and cards to aim at.
Shares hold-forms-test's helpers. Usage: hint-hold-double-tap-test.py ARTIFACTS
"""
from pathlib import Path
_src = open(Path(__file__).with_name('hold-forms-test.py')).read()
exec(_src[:_src.index('# ---------------------------------------------------------------- scenarios')])

TAP, GAP, HOLD = .04, .06, .75  # key strokes; HOLD is past the 500 ms hint hold delay
LOG = Path(os.environ['SCOTTLAND_TEST_STATE']).parent / 'wayfire.log'

def alt(down):
    key('LEFTALT', down)
    need(lambda: ipc('scottland/hints')['active'] == down, 2, 'window mode ' + ('on' if down else 'off'))
def hint(id): return need(lambda: next((h['hint'] for h in ipc('scottland/hints')['hints'] if h['window'] == id), None),
                          2, f'hint for {id}')
def stroke(id, dwell):
    label = hint(id).upper()
    for letter in label[:-1]: key(letter, True); time.sleep(TAP); key(letter, False)
    key(label[-1], True); pressed = time.monotonic()
    time.sleep(dwell); key(label[-1], False)  # the press as held
    return pressed, time.monotonic()
def in_time(first, second):
    gap = second[0] - first[1]  # the repeat's press after the first release, as a double-tap begins
    check(gap < .25, 'fixture: the second press began inside the double-tap interval', f'{gap * 1000:.0f} ms')
def card(id):
    return next((w['widget_view'] for w in ipc('scottland/widgets')['widgets']
                 if int(w['id']) == id and w['widget_view'] > 0), None)
def card_mapped(id): c = card(id); return c and any(v['id'] == c and v['mapped'] for v in views())
def spot(id): x, y, w, h = geometry(id); return x + w / 2, y + h / 2  # one output at 0,0

@scenario
def tap_then_hold_solos():
    'tap A to select it, then tap and hold A: A solos, nothing goes to the rail (WK39, ruling 10-07)'
    setup(SOLO, C)
    check(in_center(A, area) and in_center(C, area), 'fixture: A and C share the center')
    alt(True)
    first = stroke(A, TAP)
    need(lambda: focused() == A, 1, 'the tap focuses A (WK6)')
    f0 = frames(A, C)
    time.sleep(GAP); in_time(first, stroke(A, HOLD))
    verify(lambda: not in_center(C, area), 3, 'the hold soloed A: C left the center (Wayfire)')
    ok, last = holds(lambda: raw(A)['mapped'] and not card(A), 1)
    check(ok, 'A stayed a window: no card, still mapped (Wayfire)', str(last))
    check(geometry(A) == f0[A] and light(px(*spot(A))), 'A stays where it was and is drawn there (Wayfire, pixels)',
          str((frames(A), f0[A])))
    alt(False)

@scenario
def double_tap_goes_to_rail():
    'two quick taps of B still send it to the rail (WK15)'
    setup(SOLO, A); b = spot(B)
    check(light(px(*b)), 'fixture: B drawn at its place')
    alt(True)
    stroke(B, TAP); time.sleep(GAP); stroke(B, TAP)
    verify(card_mapped, 3, 'B became a card (Wayfire)')
    verify(lambda: not light(px(*b)), 2, 'B is no longer drawn at its place (pixels)')
    alt(False)

@scenario
def tap_other_then_hold_pairs():
    'tap C, then quickly hold B: B pairs with C, which the tap focused (WK36)'
    setup(PAIR, A); a0 = geometry(A)
    alt(True)
    stroke(C, TAP)
    need(lambda: focused() == C, 1, 'the tap focuses C (WK6)')
    want, _ = pair_plan(C, B, area) if center(C)[0] < center(B)[0] else pair_plan(B, C, area)
    time.sleep(GAP); stroke(B, HOLD)
    verify(lambda: all(abs(geometry(i)[0] - want[i][0]) <= 1 for i in want), 3, 'B and C paired (Wayfire)')
    check(geometry(A) == a0 and not card(B), 'A untouched and B no card (Wayfire)', str((geometry(A), a0)))
    alt(False)

@scenario
def selected_widget_tap_then_hold_solos():
    'B on the rail: tap its hint, then tap and hold it: B solos as a window (WK30, WK35)'
    setup(SOLO, A)
    alt(True); stroke(B, TAP); time.sleep(GAP); stroke(B, TAP)
    need(card_mapped, 3, 'fixture: B is a card'); alt(False)
    ipc('window-rules/focus-view', {'id': A}); need(lambda: focused() == A, 2, 'fixture: A focused')
    alt(True)
    first = stroke(B, TAP)
    need(lambda: focused() == card(B), 1, 'the tap focuses B\'s card (WK30)')
    time.sleep(GAP); in_time(first, stroke(B, HOLD))
    verify(lambda: raw(B)['mapped'] and not card_mapped(B) and in_center(B, area), 3,
           'B soloed: a window in the center, no card (Wayfire)')
    verify(lambda: light(px(*spot(B))), 2, 'B drawn at its place (pixels)')
    alt(False)

@scenario
def declined_hold_says_why():
    'a hint hold with nothing focused does nothing and logs why (WK39)'
    setup(SOLO, A); f0 = frames(A, B, C)
    try:
        for id in (A, B, C): ipc('wm-actions/set-minimized', {'view_id': id, 'state': True})
        need(lambda: all(raw(i)['minimized'] for i in (A, B, C)), 3, 'fixture: all minimized')
        need(lambda: focused() is None, 2, 'fixture: nothing focused')
        alt(True)
        start = LOG.stat().st_size
        stroke(B, HOLD)
        line = f'scottland: hint hold on {B} declined: nothing had focus to pair with'
        verify(lambda: line in LOG.read_bytes()[start:].decode(errors='replace'), 2, 'the log says why (wayfire.log)')
        alt(False)
        check(all(geometry(i)[:2] == f0[i][:2] for i in (A, C)), 'A and C did not move (Wayfire)', str(frames(A, C)))
    finally:
        for id in (A, B, C): ipc('wm-actions/set-minimized', {'view_id': id, 'state': False})

try:
    ipc('wayfire/set-config-options', {'scottland/sounds': False, 'scottland/alt_hold_delay': 300,
        'scottland/window_hold_delay': 500, 'scottland/window_double_tap_delay': 300,
        'scottland/window_avoidance_always': False, 'output:HEADLESS-1/mode': '1600x1000@60000',
        'output:HEADLESS-1/position': '0, 0'})
    need(lambda: outputs()[0]['geometry']['width'] == 1600, 5, 'output configured')
    area = {'width': 1600, 'height': 1000}
    A, B, C = launch('TapHoldA'), launch('TapHoldB'), launch('TapHoldC')
    PAIR = [(C, 1100, 40, 420, 200), (B, 1060, 560, 420, 300), (A, 200, 140, 520, 360)]
    SOLO = [(C, 600, 560, 420, 300), (B, 1100, 80, 420, 300), (A, 560, 140, 520, 360)]
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
