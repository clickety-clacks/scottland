#!/usr/bin/env python3
"""WK31/WK13: a cycled window's hint follows it while Alt is still held (Mike, 2026-10-04 bug:
the hint stayed where the window used to be). Real stipc keys cycle the focused window
center -> periphery -> widget -> center, and a second, unfocused window; after each move settles
the badge must sit on what the window now shows: inside its drawn frame, or beside its widget
card (WK26). Runs with window avoidance always-on and off. Usage: hint-follow-test.py ARTIFACTS
"""
import json, time
from pathlib import Path

src = open(Path(__file__).with_name('pairing-test.py')).read()
exec(src[:src.index('\ntry:\n')])  # its helpers: ipc, launch, setup, press_hint, alt, hint, ...

def shown_rect(id):
    """The represented surface as drawn: its true frame plus the avoidance offset."""
    e = hint(id); f = e['solve_frame']
    return (f['x'] + e['dx'], f['y'] + e['dy'], f['x'] + f['width'] + e['dx'], f['y'] + f['height'] + e['dy'])

def badge_follows(id):
    e = hint(id); b = e.get('badge')
    if not (e['visible'] and e.get('rendered') and b): return False, 'no badge'
    bx, by = b['x'] + b['size'] / 2, b['y'] + b['size'] / 2
    x1, y1, x2, y2 = shown_rect(id)
    if layout(id)['widgetized']: # WK26: outside the card's center-facing edge, overlapping it
        reach = b['size']
        ok = x1 - reach <= bx <= x2 + reach and y1 <= by <= y2
    else:
        ok = x1 <= bx <= x2 and y1 <= by <= y2
    return ok, f'badge center {bx:.0f},{by:.0f} drawn frame {x1:.0f},{y1:.0f}..{x2:.0f},{y2:.0f}'

def letter_on_screen(png, rgb, tolerance=14):
    """Centroid of the pixels drawn in this hint's letter color (WK14 colors are distinct)."""
    raw = subprocess.run(['magick', str(png), '-depth', '8', 'rgb:-'], check=True, capture_output=True).stdout
    width = int(subprocess.run(['magick', 'identify', '-format', '%w', str(png)], check=True,
        capture_output=True, text=True).stdout)
    to_logical = outputs()[0]['geometry']['width'] / width # screenshots are in device pixels
    want = [round(c * 255) for c in rgb]; xs = ys = n = 0
    for i in range(0, len(raw), 3 * 2): # every second pixel is plenty for a centroid
        if abs(raw[i] - want[0]) <= tolerance and abs(raw[i + 1] - want[1]) <= tolerance and abs(raw[i + 2] - want[2]) <= tolerance:
            j = i // 3; xs += j % width; ys += j // width; n += 1
    return (xs / n * to_logical, ys / n * to_logical, n) if n >= 20 else None

def drawn_true_frame(id):
    """The window's true frame as drawn at its scale (Wayfire geometry, no avoidance offset)."""
    g = geometry(id); sc = layout(id)['applied_scale']
    cx, cy = g['x'] + g['width'] / 2, g['y'] + g['height'] / 2
    return (cx - g['width'] * sc / 2, cy - g['height'] * sc / 2, cx + g['width'] * sc / 2, cy + g['height'] * sc / 2)

def settle_follow(id, timeout=1.5):
    """True once the badge sits on the window's current drawing (and stays for 0.3 s)."""
    end = time.monotonic() + timeout; since = None; last = ''
    while time.monotonic() < end:
        ok, last = badge_follows(id)
        if ok:
            since = since or time.monotonic()
            if time.monotonic() - since >= .3: return True, last
        else: since = None
        time.sleep(.05)
    return False, last

try:
    SCALE = float(os.environ.get('FOLLOW_SCALE', '1'))
    ipc('wayfire/set-config-options', {'scottland/sounds': False, 'scottland/alt_hold_delay': 300,
        'scottland/window_double_tap_delay': 300,
        'output:HEADLESS-1/mode': f'{round(1600 * SCALE)}x{round(1000 * SCALE)}@60000', 'output:HEADLESS-1/scale': SCALE})
    time.sleep(1)
    A, B, C = launch('FollowA'), launch('FollowB'), launch('FollowC')
    for always in (False, True):
        ipc('wayfire/set-config-options', {'scottland/window_avoidance_always': always})
        setup([(C, 300, 520, 520, 360), (B, 900, 200, 520, 360), (A, 560, 300, 520, 360)], A)
        alt(True); time.sleep(.6)
        ok, why = settle_follow(A)
        check(ok, f'avoidance always={always}: the focused window\'s hint starts on it', why)
        for step, zone in enumerate(('periphery', 'widget', 'center')):
            color, text = hint(A)['color'], hint(A)['hint']
            old = shown_rect(A)
            # Hands off: no Scottland IPC between the key and the screenshot, so nothing but the
            # move itself can bring the hint up to date (the IPC refreshes hint entries).
            key(text.upper(), True); time.sleep(.08); key(text.upper(), False)
            time.sleep(1.2)
            png = art / f'follow-{"always" if always else "mode"}-{step}-{zone}-handsoff.png'; shot(png.name)
            letter = letter_on_screen(png, color)
            if zone != 'widget':
                x1, y1, x2, y2 = drawn_true_frame(A)
                margin = 0 # the focused window is anchored (WK13): its letter is on its true frame
                near_new = bool(letter) and x1 - margin <= letter[0] <= x2 + margin and y1 - margin <= letter[1] <= y2 + margin
                ox, oy = (old[0] + old[2]) / 2, (old[1] + old[3]) / 2
                moved_far = abs((x1 + x2) / 2 - ox) + abs((y1 + y2) / 2 - oy) > 200
                print(f'  {zone}: letter {letter and (round(letter[0]), round(letter[1]), letter[2])} window {x1:.0f},{y1:.0f}..{x2:.0f},{y2:.0f} old center {ox:.0f},{oy:.0f} moved_far {moved_far}', flush=True)
                check(near_new or not moved_far, f'avoidance always={always}: hands off, the drawn {zone} hint is on the moved window',
                      f'letter {letter} window {x1:.0f},{y1:.0f}..{x2:.0f},{y2:.0f} old center {ox:.0f},{oy:.0f}')
            if zone == 'widget': wait(lambda: layout(A)['widgetized'], 3, 'widget')
            ok, why = settle_follow(A)
            check(ok, f'avoidance always={always}: after cycling to the {zone}, the hint follows the window within 1.5 s, Alt held', why)
            shot(f'follow-{"always" if always else "mode"}-{step}-{zone}.png')
        # An unfocused window: its first press selects (no move), the next one cycles it. Time
        # from the cycling key to the badge sitting on the moved window (the glide is 300 ms).
        press_hint(B, hold=.08); time.sleep(.6)
        old = shown_rect(B); text = hint(B)['hint']
        key(text.upper(), True); time.sleep(.08); key(text.upper(), False); pressed = time.monotonic()
        followed = None
        while time.monotonic() - pressed < 2:
            ok, why = badge_follows(B)
            moved = abs(shown_rect(B)[0] - old[0]) + abs(shown_rect(B)[1] - old[1]) > 100
            if ok and moved: followed = time.monotonic() - pressed; break
            time.sleep(.03)
        print(f'  unfocused cycle: hint on the moved window after {followed and round(followed * 1000)} ms', flush=True)
        check(followed is not None and followed <= .8,
              f'avoidance always={always}: an unfocused window cycled by its hint has its hint on it within 0.8 s', why)
        alt(False); time.sleep(.6)
        for id in (A, B):
            if layout(id)['widgetized']:
                ipc('scottland/present', {'window': id}); wait(lambda: not layout(id)['widgetized'], what='restore')
        time.sleep(.6)
finally:
    try: key('LEFTALT', False)
    except Exception: pass
    for client in clients:
        if client.poll() is None: client.terminate()
print(f'{passed} passed, {failed} failed', flush=True)
sys.exit(1 if failed else 0)
