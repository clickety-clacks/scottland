#!/usr/bin/env python3
"""No backdrop inside a window: a breathing strip must never paint over a front window's content.

Run through tests/goo-strip-test.sh in a private headless session on the test machine.

The scene is Mike's desktop of 2026-10-04 (2560x1600): a window asking for attention
behind a stack of center windows, one of which covers the breather's left edge. The
breather is first shown alone, so the goo's cached backdrop holds wallpaper beside it;
then the front windows arrive. While the goo sleeps and breathes, frames the compositor
renders by itself are read back and the front window's interior is compared with the same
scene repainted without backdrop reuse and with the goo off: wallpaper showing inside the
window is the bug.
"""
import json, os, signal, socket, struct, subprocess, sys, time
from pathlib import Path

root = Path(__file__).resolve().parents[1]
out = Path(sys.argv[1]).resolve(); out.mkdir(parents=True, exist_ok=True)
assert os.environ.get('SCOTTLAND_TEST_MODEL') == '1', 'private headless session required'
frame_file = Path(os.environ['SCOTTLAND_TEST_STATE'])/'render-frame.ppm'
sock = socket.socket(socket.AF_UNIX); sock.connect(os.environ['WAYFIRE_SOCKET'])
OUTPUT, W, H = 'HEADLESS-1', 2560, 1600

def ipc(method, data=None):
    payload = json.dumps({'method': method, 'data': data or {}}).encode()
    sock.sendall(struct.pack('<I', len(payload)) + payload)
    def read(n):
        b = b''
        while len(b) < n:
            chunk = sock.recv(n-len(b))
            if not chunk: raise RuntimeError('compositor disconnected')
            b += chunk
        return b
    result = json.loads(read(struct.unpack('<I', read(4))[0]))
    if isinstance(result, dict) and 'error' in result: raise RuntimeError(result)
    return result
def views(): return ipc('scottland/layout-state')['views']
def view(title): return next(v for v in views() if v['title'] == title)
def state(data=None): return ipc('scottland/goo-state', data)['screens'][0]
def click(x, y):
    ipc('stipc/move_cursor', {'x': round(x), 'y': round(y)})
    for mode in ('press', 'release'): ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': mode})
def settle(what):
    time.sleep(1)
    for _ in range(900):
        s = state()
        if s['sleeping'] and not s.get('breath_loose'): return s
        time.sleep(.1)
    raise AssertionError('goo did not settle: ' + what)
checks = []
def check(name, ok, detail=None):
    checks.append((name, bool(ok)))
    print(('PASS ' if ok else 'FAIL ') + name + (' ' + json.dumps(detail) if detail is not None and not ok else ''), flush=True)

def frame(label, reused=None):
    """The next frame the output renders by itself (a screenshot request can force a full
    repaint and hide the mistake). With `reused`, insist the goo did or did not reuse."""
    for _ in range(40):
        before = state() if reused is not None else None
        count = ipc('scottland/layout-state', {'capture_next_frame': OUTPUT})['captured_frames']
        for _ in range(300):
            if ipc('scottland/layout-state')['captured_frames'] > count: break
            time.sleep(.01)
        else: raise AssertionError('no frame rendered to capture: ' + label)
        pixels = frame_file.read_bytes()
        if reused is None:
            (out/(label+'.ppm')).write_bytes(pixels)
            return pixels
        after = state()
        draws, reuses = after['draws']-before['draws'], after['backdrop_reuses']-before['backdrop_reuses']
        if (reused and draws and reuses == draws) or (not reused and not reuses):
            (out/(label+'.ppm')).write_bytes(pixels)
            return pixels
        time.sleep(.05)
    raise AssertionError(f'never captured a frame with reuse={reused}: {label}: ' + json.dumps(
        {k: state().get(k) for k in ('reuse_blocked', 'draws', 'backdrop_reuses', 'sleeping', 'breath_loose')}))

def differing(a, b, boxes):
    """Pixels that differ by more than 2 levels inside the boxes (x1, y1, x2, y2)."""
    header = a.index(b'\n255\n')+5
    n = 0
    for x1, y1, x2, y2 in boxes:
        for y in range(y1, y2):
            ra, rb = a[header+3*(y*W+x1):header+3*(y*W+x2)], b[header+3*(y*W+x1):header+3*(y*W+x2)]
            if ra != rb:
                n += sum(1 for i in range(0, len(ra), 3) if max(abs(ra[i+c]-rb[i+c]) for c in range(3)) > 2)
    return n

clients = []
def spawn(title):
    clients.append(subprocess.Popen(['foot', '-c', '/dev/null', '-o', 'resize-by-cells=no', '-T', title, 'python3', '-u', '-c',
        'import time\nprint("\\033[?25l"+"\\n".join(f"line {i:03d} window content that must stay visible "*4 for i in range(80)), end="", flush=True)\ntime.sleep(900)'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
    time.sleep(.6)
def place(title, x, y, w, h):
    ipc('window-rules/configure-view', {'id': view(title)['id'], 'geometry': {'x': x, 'y': y, 'width': w, 'height': h}})
    time.sleep(.3)

try:
    ipc('wayfire/set-config-options', {f'output:{OUTPUT}/mode': f'{W}x{H}@60000',
        'scottland/goo_thickness': 22., 'scottland/goo_reach': 33., 'scottland/goo_thinning': .27,
        'scottland/goo_swell': .68, 'scottland/goo_soak': .9, 'scottland/goo_overlap_film': 10.,
        'scottland/center_opacity_unfocused': .91})
    time.sleep(1.5)
    with (out/'wallpaper.log').open('w') as f:
        clients.append(subprocess.Popen(['quickshell', '-p', str(root/'tests/GooWallpaper.qml')],
                                        stdout=f, stderr=subprocess.STDOUT, start_new_session=True))
    time.sleep(1)
    # The breather alone on the wallpaper: the backdrop cache now holds wallpaper around it.
    spawn('strip-breather'); time.sleep(1)
    place('strip-breather', 831, 152, 1143, 1339)
    settle('breather alone'); time.sleep(2)
    # The rest of the stack, back to front; the last covers the breather's left edge.
    natural = len(sys.argv) > 3 and sys.argv[3] == 'natural'
    scene = [('strip-decision', 1190, 274, 936, 1248), ('strip-agent', 880, 521, 800, 600),
             ('strip-pin', 1180, 765, 200, 125), ('strip-front', 730, 336, 1184, 1051)]
    if natural:  # Mike's desktop at the second sighting: three windows over the breather
        place('strip-breather', 796, 196, 1272, 1113)
        scene = [('strip-wide', 138, 258, 1375, 1035), ('strip-decision', 864, 374, 833, 896),
                 ('strip-front', 1567, 534, 577, 770)]
    for title, *g in scene:
        spawn(title); place(title, *g)
        f = view(title)['frame']; click(f['x']+f['width']*.5, f['y']+f['height']-12); time.sleep(.3)
    ipc('stipc/move_cursor', {'x': 400, 'y': 1500})
    # Two windows ask for attention (Mike had a window and a widget): more strips than the
    # output's damage can hold as separate rectangles, so they are merged.
    for title in ('strip-breather', 'strip-decision'):
        ipc('scottland/attention', {'window': view(title)['id'], 'attention': True, 'source': 'strip-test'})
    time.sleep(.5)
    b = view('strip-breather')
    check('the breather is behind and still asking for attention', b['frame']['attention'] and not b['frame']['focus'], b['frame'])
    settle('whole scene')
    # Few strips allowed (test hook), so they merge right across the front window; 16 is the shipped limit.
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    state({'breath_max_rects': limit})
    s = settle('strip limit')
    front = view('strip-front')['frame']
    # The front window's whole interior, clear of its own edge goo and rounded corners:
    # nothing is in front of it, so no goo may show there at all.
    x1, y1 = round(front['x'])+24, round(front['y'])+24
    x2, y2 = round(front['x']+front['width'])-24, round(front['y']+front['height'])-24
    whole = [(0, 0, W, H)]
    boxes = [(x1, y1, x2, y2)]
    (out/'fixture.json').write_text(json.dumps({'views': views(), 'state': s, 'boxes': boxes}, indent=1))
    print(json.dumps({'strip_count': len(s['breath_damage']), 'strips': s['breath_damage'], 'box': boxes, 'dry_pixels': s.get('dry_pixels'),
                      'strip_dry_pixels': s.get('strip_dry_pixels'), 'views': [(v['title'], {k: round(v['frame'][k]) for k in ('x', 'y', 'width', 'height')}) for v in views() if v['title'].startswith('strip-')]}), flush=True)
    subprocess.run(['grim', str(out/'scene.png')])
    for hold in (0., .5, 1.):
        state({'breath_hold': hold, 'breath_reuse': False}); time.sleep(.4)
        repainted = frame(f'repainted-{hold}', reused=False)
        state({'breath_reuse': True}); time.sleep(.4)
        reused = frame(f'reused-{hold}', reused=True)
        n = differing(repainted, reused, boxes)
        check(f'breath {hold}: the front window\'s interior is the same with the backdrop reused', n == 0, {'differing_pixels': n})
        n = differing(repainted, reused, whole)
        check(f'breath {hold}: the whole screen is the same with the backdrop reused', n == 0, {'differing_pixels': n})
    state({'breath_hold': -1})
    # Breathing freely for three seconds: no frame may put anything else inside the window.
    ipc('wayfire/set-config-options', {'scottland/goo': False}); time.sleep(1)
    plain = frame('goo-off')
    ipc('wayfire/set-config-options', {'scottland/goo': True})
    settle('goo back on')
    state({'breath_max_rects': limit})
    s = settle('strip limit again'); time.sleep(1.5)
    check('the merged strips still cover dry window content (the case under test)', s.get('strip_dry_pixels', 1e9) > 100000 or limit > 3,
          {'strip_dry_pixels': s.get('strip_dry_pixels')})
    before = state()
    worst = 0
    for i in range(30):
        worst = max(worst, differing(plain, frame(f'free-{i:02d}'), boxes)); time.sleep(.1)
    after = state()
    check('breathing freely: the window interior always shows the window (against the goo-off picture)', worst == 0,
          {'most_differing_pixels': worst})
    check('and the breath still reuses the backdrop outside the window',
          after['backdrop_reuses']-before['backdrop_reuses'] > 20, {'reuses': after['backdrop_reuses']-before['backdrop_reuses'], 'blocked': after['reuse_blocked']})
    failed = [n for n, ok in checks if not ok]
    print(f'RESULT {len(checks)-len(failed)} passed, {len(failed)} failed', flush=True)
    sys.exit(1 if failed else 0)
finally:
    for c in clients:
        try: os.killpg(c.pid, signal.SIGTERM)
        except ProcessLookupError: pass
    sock.close()
