#!/usr/bin/env python3
"""Real held Alt, displayed sizing, palette replacement and screenshot pixel verification."""
import colorsys
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import gi
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf

class Screenshot:
    def __init__(self, path):
        self.image = GdkPixbuf.Pixbuf.new_from_file(str(path))
        self.pixels = self.image.get_pixels()
        self.stride = self.image.get_rowstride()
        self.channels = self.image.get_n_channels()

    def getpixel(self, point):
        x, y = point
        offset = y*self.stride + x*self.channels
        return tuple(self.pixels[offset:offset+3])

    def crop_bytes(self, box):
        x1, y1, x2, y2 = box
        return b''.join(self.pixels[y*self.stride+x1*self.channels:y*self.stride+x2*self.channels]
                       for y in range(y1, y2))

artifacts = Path(sys.argv[1])
passed = failed = 0
clients = []

def check(ok, name):
    global passed, failed
    print(('PASS  ' if ok else 'FAIL  ') + name, flush=True)
    passed += bool(ok)
    failed += not ok

def ipc(method, data=None):
    args = ['tests/headless.sh', 'ipc', method]
    if data is not None:
        args.append(json.dumps(data))
    return json.loads(subprocess.check_output(args, text=True))

def run(*args):
    return subprocess.check_output(['tests/headless.sh', 'run', *args], text=True).strip()

def wait(predicate, timeout=8):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        result = predicate()
        if result:
            return result
        time.sleep(.05)
    raise RuntimeError('timed out waiting for test state')

def views():
    return ipc('scottland/layout-state')['views']

def hints():
    return ipc('scottland/hints')['hints']

def key(code, state):
    ipc('stipc/feed_key', {'key': 'KEY_' + code, 'state': state})

def drag(v, x, y):
    f = v['frame']
    cx, cy = f['x'] + f['width']/2, f['y'] + f['height']/2
    ipc('stipc/move_cursor', {'x': round(cx), 'y': round(cy)})
    key('LEFTMETA', True)
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'press'})
    for i in range(1, 11):
        ipc('stipc/move_cursor', {'x': round(cx + (x-cx)*i/10), 'y': round(cy + (y-cy)*i/10)})
        time.sleep(.03)
    ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'release'})
    key('LEFTMETA', False)
    time.sleep(.6)

def hold():
    key('LEFTALT', True)
    wait(lambda: ipc('scottland/hints')['active'])
    time.sleep(1.5)

# Goo is on by default; SCOTTLAND_TEST_GOO=0 runs the classic halo.
GOO = os.environ.get('SCOTTLAND_TEST_GOO') != '0'

def dyed(image, x, y, color):
    """Within a few px of (x, y), a pixel whose strongest channel is the dye's strongest."""
    dominant = max(range(3), key=lambda channel: color[channel])
    for dy in range(-2, 3):
        p = image.getpixel((x, y+dy))
        if p[dominant] > max(p[channel] for channel in range(3) if channel != dominant) + 8:
            return True
    return False

def screenshot(name):
    path = artifacts / (name + '.png')
    run('grim', str(path))
    return Screenshot(path)

def luminance(c):
    def linear(v):
        return v/12.92 if v <= .04045 else ((v+.055)/1.055)**2.4
    return sum(linear(v)*w for v, w in zip(c, (.2126, .7152, .0722)))

def contrast(a, b):
    x, y = sorted((luminance(a), luminance(b)))
    return (y+.05)/(x+.05)

def mix(a, b, amount):
    return tuple(x*(1-amount)+y*amount for x, y in zip(a, b))

def rgb(hex_color):
    return tuple(int(hex_color[i:i+2], 16)/255 for i in (1, 3, 5))

palette_path = Path(run('python3', '-c',
    "import os;print(os.path.join(os.environ['XDG_RUNTIME_DIR'],'scottland',os.environ['WAYLAND_DISPLAY']+'.palette.json'))"))
palettes = {
    'dark': {'scheme': 'dark', 'background': '#1f232c', 'foreground': '#d8deea', 'accent': '#81a1c1'},
    'light': {'scheme': 'light', 'background': '#f4f5f7', 'foreground': '#232a35', 'accent': '#3b6ea8'},
}

def theme(name):
    temporary = palette_path.with_suffix('.hint-style-test.tmp')
    temporary.write_text(json.dumps(palettes[name]))
    temporary.replace(palette_path)
    ipc('wayfire/set-config-options', {'scottland/color_scheme': name,
        'scottland/accent_color': palettes[name]['accent']+'ff'})
    time.sleep(.6)

try:
    ipc('wayfire/set-config-options', {'scottland/sounds': False, 'place/mode': 'pointer'})
    theme('dark')
    for name, width, height, x, y in [('Small', 140, 100, 350, 130),
            ('Large', 500, 420, 810, 430), ('Scaled', 300, 280, 180, 450),
            ('Card', 300, 180, 800, 110)]:
        ipc('stipc/move_cursor', {'x': x, 'y': y})
        p = subprocess.Popen(['tests/headless.sh', 'run', 'python3',
            str(Path('tests/hint-style-app.py').resolve()), name, str(width), str(height), str(palette_path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        clients.append(p)
        v = wait(lambda: next((v for v in views() if v['title'] == name and 'frame' in v), None))
        time.sleep(.4)
        drag(v, x if name != 'Card' else 1270, y)
    wait(lambda: any(v['widget'] for v in views()))
    ipc('stipc/move_cursor', {'x': 640, 'y': 20})
    for scheme in ('dark', 'light'):
        theme(scheme)
        baseline = screenshot(scheme + '-before')
        hold()
        image = screenshot(scheme + '-hints')
        state = hints()
        (artifacts/(scheme+'-state.json')).write_text(json.dumps({'hints': state, 'views': views()}, indent=2))
        check(len(state) == 4 and all(h['visible'] for h in state), scheme+': every window and card has a hint')
        sizes = [h['badge']['size'] for h in state]
        check(min(sizes) == 48 and max(sizes) == 132, scheme+': widget badges reach 48px and window badges reach 132px')
        represented = {v['id']: v for v in views()}
        links = {int(w['id']): w['widget_view'] for w in ipc('scottland/widgets')['widgets']}
        all_sizes = True
        for h in state:
            v = represented[links.get(h['window'], h['window'])]
            f = v['frame']
            all_sizes &= h['badge']['size'] == round((2/3 if v['widget'] else 1) * max(72, min(132, min(f['width'], f['height'])*.34)))
        check(all_sizes, scheme+': sizing uses displayed dimensions including scaled windows and cards')
        if scheme == 'dark':
            # WK5: badges follow the desktop's text size (text_scale in the palette file).
            key('LEFTALT', False); time.sleep(.3)
            scaled = dict(palettes['dark'], text_scale=1.5)
            temporary = palette_path.with_suffix('.hint-style-test.tmp')
            temporary.write_text(json.dumps(scaled)); temporary.replace(palette_path)
            time.sleep(.8)
            hold()
            ok = True
            for h in hints():
                v = represented[links.get(h['window'], h['window'])]
                f = v['frame']
                ok &= h['badge']['size'] == round((2/3 if v['widget'] else 1) * max(108, min(198, min(f['width'], f['height'])*.34*1.5)))
            check(ok, 'badges scale with desktop text size, retaining the widget 2/3 factor')
            key('LEFTALT', False); time.sleep(.3)
            theme('dark')
            time.sleep(.8)
            hold()
        colors = [h['color'] for h in state]
        hue = [colorsys.rgb_to_hls(*c)[0]*360 for c in colors]
        accent_hue = colorsys.rgb_to_hls(*rgb(palettes[scheme]['accent']))[0]*360
        check(all(abs((h-accent_hue+180)%360-180) >= 100-1e-6 for h in hue), scheme+': hues lie opposite the accent')
        check(all(abs((a-b+180)%360-180) >= 60 for a, b in zip(hue, hue[1:])), scheme+': adjacent hints are at least 60 degrees apart')
        check(all(contrast(c, mix(mix(rgb(palettes[scheme]['background']), c, .07), c, .21)) >= 3 for c in colors),
            scheme+': letters contrast at least 3:1 against the composited badge')
        for h in state:
            v = represented[links.get(h['window'], h['window'])]
            f, badge, c = v['frame'], h['badge'], h['color']
            # Sample a solid interior away from label, circle, corners, and app content.
            x, y = round(f['x']+5+h['dx']), round(f['y']+f['height']/2+h['dy'])
            if v['widget']:
                x = round(f['x']+f['width']-18+h['dx'])
                y = round(f['y']+f['height']/2+h['dy'])
            before = tuple(n/255 for n in baseline.getpixel((round(x-h['dx']), round(y-h['dy']))))
            after = tuple(n/255 for n in image.getpixel((x, y)))
            check(max(abs(a-b) for a, b in zip(after, mix(before, c, .07))) < .025,
                scheme+': 7% surface tint on '+v['title'])
            if GOO:
                # With the goo, window mode tints the goo itself; there is no separate rim.
                check(dyed(image, round(f['x']+f['width']/2+h['dx']), round(f['y']-4+h['dy']), c),
                    scheme+': the goo takes the hint dye on '+v['title'])
            else:
                bx, by = round(f['x']+f['width']/2+h['dx']), round(f['y']-1+h['dy'])
                rim = tuple(n/255 for n in image.getpixel((bx, by)))
                check(max(abs(a-b) for a, b in zip(rim, c)) < .08,
                    scheme+': full-color 2px rounded rim on '+v['title'])
            # Side of the circle avoids the central bold glyph and the card icon/title.
            cx, cy = badge['x']+badge['size']/2, badge['y']+badge['size']/2
            outward = -1 if v['widget'] and f['x'] > 640 else 1
            px, py = round(cx+outward*badge['size']*.39), round(cy)
            fill = tuple(n/255 for n in image.getpixel((px, py)))
            if not v['widget']:
                check(max(abs(a-b) for a, b in zip(fill, mix(mix(rgb(palettes[scheme]['background']), c, .07), c, .21))) < .025,
                    scheme+': 21% badge fill on '+v['title'])
            else:
                check(max(abs(a-b) for a, b in zip(fill, mix(rgb(palettes[scheme]['background']), c, .21))) < .025
                      and contrast(c, fill) >= 3,
                    scheme+': exterior widget badge has a theme background and readable 21% tint')
        # Replace the palette without releasing Alt or restarting: colors must change live.
        other = 'light' if scheme == 'dark' else 'dark'
        previous = {h['window']: (h['hint'], h['color']) for h in state}
        theme(other)
        changed = wait(lambda: hints() if any(h['color'] != previous[h['window']][1] for h in hints()) else None)
        check(all(h['hint'] == previous[h['window']][0] and h['color'] != previous[h['window']][1] for h in changed),
            scheme+': live theme change recolors every hint while retaining letters')
        screenshot(scheme+'-to-'+other+'-held')
        key('LEFTALT', False)
        time.sleep(.8)
        screenshot(other+'-released')
        check(not any(h['visible'] for h in hints()), scheme+': release removes hints')
        theme(scheme)
        restored = screenshot(scheme+'-restored')
        # Check a whole surface strip, away from animated halo and frame edges.
        check(baseline.crop_bytes((760, 250, 860, 280)) == restored.crop_bytes((760, 250, 860, 280)),
            scheme+': release restores untinted contents')
    # Retaining letters across population changes also retains colors.
    hold()
    previous = {h['window']: (h['hint'], h['color']) for h in hints()}
    extra = subprocess.Popen(['tests/headless.sh', 'run', 'python3',
        str(Path('tests/hint-style-app.py').resolve()), 'Extra', '200', '120', str(palette_path)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    clients.append(extra)
    wait(lambda: len(hints()) == 5)
    check(all((h['hint'], h['color']) == previous[h['window']] for h in hints() if h['window'] in previous),
        'adding a window retains every existing letter/color')
    key('LEFTALT', False)
    time.sleep(.8)
    # A palette-file-only accent change must work even if compositor options do not change.
    hold()
    before_colors = {h['window']: h['color'] for h in hints()}
    custom = dict(palettes['light'], accent='#d15baa')
    temporary = palette_path.with_suffix('.hint-style-test.tmp')
    temporary.write_text(json.dumps(custom)); temporary.replace(palette_path)
    changed = wait(lambda: hints() if all(h['color'] != before_colors[h['window']] for h in hints()) else None)
    accent_hue = colorsys.rgb_to_hls(*rgb(custom['accent']))[0]*360
    check(all(abs((colorsys.rgb_to_hls(*h['color'])[0]*360-accent_hue+180)%360-180) >= 100-1e-6
              for h in changed), 'palette file alone rotates the live complementary hues')
    represented = {v['id']: v for v in views()}
    sampled = [(h, represented[h['window']]['frame']) for h in changed[:2]]
    recolored = screenshot('light-accent-file-only-held')
    # Goo dye spreads in from the edge over a moment rather than switching in one frame.
    deadline = time.time() + (3 if GOO else 0)
    while GOO and time.time() < deadline and not all(
            dyed(recolored, round(f['x']-5+h['dx']), round(f['y']+f['height']/2+h['dy']), h['color'])
            for h, f in sampled):
        time.sleep(.25)
        recolored = screenshot('light-accent-file-only-held')
    for h in changed[:2]:
        v = represented[h['window']]; f = v['frame']
        point = (round(f['x']-5+h['dx']), round(f['y']+f['height']/2+h['dy']))
        pixel = recolored.getpixel(point)
        dominant = max(range(3), key=lambda channel: h['color'][channel])
        check(pixel[dominant] > max(pixel[channel] for channel in range(3) if channel != dominant),
              'palette change repaints the outer halo dye on '+v['title'])
    key('ESC', True); key('ESC', False)
    time.sleep(.8)
    escaped = screenshot('light-escaped')
    check(not any(h['visible'] for h in hints()) and
          max(abs(a-b) for a,b in zip(escaped.getpixel((800,260)), (244,245,247))) <= 1,
          'Esc removes hints and surface dye')
    key('LEFTALT', False)
    theme('light')
    large = next(v for v in views() if v['title'] == 'Large')
    ipc('wm-actions/set-fullscreen', {'view_id': large['id'], 'state': True})
    ipc('window-rules/focus-view', {'id': large['id']})
    time.sleep(.8)
    full_before = screenshot('fullscreen-before')
    hold()
    full_hint = next(h for h in hints() if h['window'] == large['id'])
    full = screenshot('fullscreen-hints')
    c = full_hint['color']
    before = tuple(n/255 for n in full_before.getpixel((700,600)))
    after = tuple(n/255 for n in full.getpixel((700,600)))
    check(max(abs(a-b) for a, b in zip(after,mix(before,c,.07))) < .025,
          'fullscreen surface receives the 7% tint without a frame')
    rim = tuple(n/255 for n in full.getpixel((0,300)))
    check(max(abs(a-b) for a, b in zip(rim,c)) < .025, 'fullscreen receives an inset 2px full-color rim')
    key('LEFTALT', False)
    time.sleep(.8)
    restored = screenshot('fullscreen-restored')
    check(restored.getpixel((700,600)) == full_before.getpixel((700,600)),
          'fullscreen release removes its tint')
    ipc('wm-actions/set-fullscreen', {'view_id': large['id'], 'state': False})

    time.sleep(.6)
    ipc('wayfire/set-config-options', {'scottland/min_scale': .05, 'scottland/max_scale': .05,
        'scottland/scale_curve': '', 'scottland/blend_width': 0})
    ipc('stipc/move_cursor', {'x': 180, 'y': 550})
    tiny = subprocess.Popen(['tests/headless.sh', 'run', 'python3',
        str(Path('tests/hint-style-app.py').resolve()), 'TinyScale', '2000', '1000', str(palette_path)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    clients.append(tiny)
    v = wait(lambda: next((v for v in views() if v['title'] == 'TinyScale' and 'frame' in v), None))
    time.sleep(.5)
    drag(v, 180, 550)
    ipc('stipc/move_cursor', {'x': 640, 'y': 20})
    wait(lambda: any(v['title'] == 'TinyScale' and v.get('frame', {}).get('thickness', 100) < 1
                     and abs(v['frame']['swell']) < .005 for v in views()))
    hold()
    v = next(v for v in views() if v['title'] == 'TinyScale')
    h = next(h for h in hints() if h['window'] == v['id'])
    (artifacts/'minimum-window-scale-state.json').write_text(json.dumps({'view': v, 'hint': h}, indent=2))
    check(v['applied_scale'] < .051 and v['frame']['thickness'] < 1 and h['badge']['size'] == 72,
          '5% displayed scale keeps the minimum 72px badge')
    image = screenshot('minimum-window-scale-hints')
    f = v['frame']
    # Near the top-right straight edge, beyond both badge circles and the narrow resting halo.
    point = (round(f['x']+f['width']-6+h['dx']), math.floor(f['y']+h['dy'])-1)
    if GOO:
        # WK28: the minimum circle can enclose the whole tiny window. Its foreground
        # island hides the old top-edge probe; sample the outside of their shared silhouette.
        badge = h['badge']
        check(dyed(image, math.ceil(max(f['x']+f['width']+h['dx'], badge['x']+badge['size']))+2,
                    round(badge['y']+badge['size']/2), h['color']),
              'the goo takes the hint dye at the supported 5% window scale')
    else:
        rim = tuple(n/255 for n in image.getpixel(point))
        check(max(abs(a-b) for a,b in zip(rim,h['color'])) < .08,
              '2px logical border stays full-color at the supported 5% window scale')
    key('LEFTALT', False)

finally:
    key('LEFTALT', False)
    for client in clients:
        if client.poll() is None:
            client.terminate()
            client.wait(timeout=5)
    print(f'{passed} passed, {failed} failed', flush=True)
if failed:
    raise SystemExit(1)
