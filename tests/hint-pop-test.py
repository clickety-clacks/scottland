#!/usr/bin/env python3
"""WK28: real Alt holds, animated screenshots and shared circle/widget liquid.
Run via tests/headless.sh run in a private --widgets session. Artifacts are required.
"""
import json
import math
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time
import threading
import gi
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf

art = Path(sys.argv[1]); art.mkdir(parents=True, exist_ok=True)
sock = socket.socket(socket.AF_UNIX); sock.connect(os.environ['WAYFIRE_SOCKET'])
clients = []; passed = failed = 0

def ipc(method, data=None, connection=None):
    connection = connection or sock
    body = json.dumps({'method': method, 'data': data or {}}).encode()
    connection.sendall(struct.pack('<I', len(body)) + body)
    def read(n):
        out = b''
        while len(out) < n:
            part = connection.recv(n-len(out))
            if not part: raise RuntimeError('compositor disconnected')
            out += part
        return out
    result = json.loads(read(struct.unpack('<I', read(4))[0]))
    if isinstance(result, dict) and 'error' in result: raise RuntimeError(result)
    return result

def check(ok, name):
    global passed, failed
    print(('PASS ' if ok else 'FAIL ') + name, flush=True)
    passed += bool(ok); failed += not ok

def key(name, down): ipc('stipc/feed_key', {'key': 'KEY_'+name, 'state': down})
def hints(): return ipc('scottland/hints')['hints']
def views(): return ipc('scottland/layout-state')['views']
def wait(predicate):
    end = time.monotonic() + 8
    while time.monotonic() < end:
        value = predicate()
        if value: return value
        time.sleep(.005)
    raise RuntimeError('state timeout')
def shot(name):
    path = art/(name+'.png'); subprocess.run(['grim', '-l', '1', str(path)], check=True)
    return GdkPixbuf.Pixbuf.new_from_file(str(path))
def pixel(image, x, y):
    x, y = round(x), round(y)
    i = y*image.get_rowstride() + x*image.get_n_channels()
    return tuple(image.get_pixels()[i:i+3])
def dyed(p, color):
    dominant = max(range(3), key=lambda i: color[i])
    return p[dominant] > min(p) + 12

def capture_pop(label, entering):
    # Poll motion independently of PNG encoding/readback; a grim subprocess can
    # take longer than the entire 100ms exit on a concurrently used test GPU.
    motion = []; stop = threading.Event(); start = time.monotonic()
    def sample_motion():
        connection = socket.socket(socket.AF_UNIX); connection.connect(os.environ['WAYFIRE_SOCKET'])
        try:
            while not stop.is_set():
                state = ipc('scottland/hints', connection=connection)
                motion.append({'ms': (time.monotonic()-start)*1000, 'hints': state['hints']})
                stop.wait(.004)
        finally: connection.close()
    sampler = threading.Thread(target=sample_motion, daemon=True); sampler.start()
    if entering:
        key('LEFTALT', True); wait(lambda: ipc('scottland/hints')['active'])
    else: key('LEFTALT', False)
    frames = []; samples = []
    for i in range(9):
        state = hints(); frames.append(shot(f'{label}-{i:02}'))
        samples.append({'ms': (time.monotonic()-start)*1000, 'hints': state})
        time.sleep(.012)
    stop.set(); sampler.join()
    (art/(label+'.json')).write_text(json.dumps(samples, indent=2))
    (art/(label+'-motion.json')).write_text(json.dumps(motion, indent=2))
    strip = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 360*len(frames), 300)
    # Fixed crop covers the same exterior widget hint and its attachment in every frame.
    for i, frame in enumerate(frames): frame.copy_area(0, 180, 360, 300, strip, i*360, 0)
    strip.savev(str(art/(label+'-strip.png')), 'png', [], [])
    return motion

palette_path = Path(os.environ['XDG_RUNTIME_DIR'])/'scottland'/(os.environ['WAYLAND_DISPLAY']+'.palette.json')
palette = {'scheme':'dark','background':'#1f232c','foreground':'#d8deea','accent':'#81a1c1'}
def theme(reduced=False):
    temporary = art/'palette.json'
    temporary.write_text(json.dumps({**palette, 'reduced_motion': reduced}))
    # The tiny session palette is protocol state, not a screenshot/log artifact.
    palette_path.write_text(temporary.read_text())

try:
    theme()
    ipc('wayfire/set-config-options', {'scottland/sounds':False, 'scottland/color_scheme':'dark',
        'scottland/alt_hold_delay':100, 'place/mode':'pointer'})
    for title, x in [('Pop card', 400), ('Pop window', 850)]:
        ipc('stipc/move_cursor', {'x':x,'y':350})
        clients.append(subprocess.Popen(['foot','-c','/dev/null','-T',title,'-W','40x15','sleep','600'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        wait(lambda: any(v['title']==title for v in views())); time.sleep(.5)
    v = next(v for v in views() if v['title']=='Pop card'); f=v['frame']
    cx,cy=f['x']+f['width']/2,f['y']+f['height']/2
    ipc('stipc/move_cursor', {'x':round(cx),'y':round(cy)}); key('LEFTMETA', True)
    ipc('stipc/feed_button', {'combo':'BTN_LEFT','mode':'press'})
    for i in range(1,21):
        ipc('stipc/move_cursor', {'x':round(cx+(12-cx)*i/20),'y':330}); time.sleep(.02)
    ipc('stipc/feed_button', {'combo':'BTN_LEFT','mode':'release'}); key('LEFTMETA', False)
    wait(lambda: ipc('scottland/widgets')['widgets']); time.sleep(1)
    ipc('stipc/move_cursor', {'x':640,'y':10})
    for goo in (True, False):
        label = 'goo' if goo else 'fallback'
        ipc('wayfire/set-config-options', {'scottland/goo':goo}); time.sleep(.5)
        using_goo = goo and bool(ipc('scottland/goo-state')['screens'])
        if goo and not using_goo: label = 'fallback-unavailable'
        samples = capture_pop(label+'-enter', True)
        pops = [h.get('pop',0) for s in samples for h in s['hints']]
        check(any(.001 < p < .95 for p in pops), label+': intermediate frame has a growing circle')
        check(any(p > 1.005 for p in pops), label+': one short spring overshoot is visible')
        time.sleep(.3)
        check(all(abs(h['pop']-1)<.001 for h in hints()), label+': settled at full size')
        image = shot(label+'-settled'); state = hints()
        (art/(label+'-settled.json')).write_text(json.dumps({'hints':state,'views':views()},indent=2))
        for h in state:
            b=h['badge']; radius=b['size']/2; x=b['x']+radius; y=b['y']+radius
            check(any(dyed(pixel(image,x+radius+d,y),h['color']) for d in range(2,9)),
                label+': dyed pixels outside circle '+h['hint'])
        widget = next(v for v in views() if v['widget']); h=next(h for h in state if h['window']==v['id'])
        b=h['badge']; radius=b['size']/2; x=b['x']+radius; y=b['y']+radius
        edge=widget['frame']['x']+widget['frame']['width']+h['dx']
        if using_goo:
            # Outside the opaque circle, along its upper-left arc toward the card's edge.
            a=(edge+4,y-radius*.94); z=(x-radius*.55,y-radius*1.03)
            line=[(a[0]+(z[0]-a[0])*t/12,a[1]+(z[1]-a[1])*t/12) for t in range(13)]
            check(all(dyed(pixel(image,*p),h['color']) for p in line), 'goo: continuous dyed connection from card to exterior circle')
            check(ipc('scottland/goo-state')['screens'][0]['sources']==4, 'goo: two circles join two presented window/widget sources')
        if using_goo:
            ipc('wayfire/set-config-options', {'scottland/goo_overlap_film':0})
            time.sleep(.4); no_film = shot('goo-zero-window-film')
            ordinary = next(h for h in hints() if h['window'] != v['id'])
            b = ordinary['badge']; radius = b['size']/2
            check(any(dyed(pixel(no_film,b['x']+b['size']+d,b['y']+radius),ordinary['color'])
                for d in range(2,9)), 'goo: circle keeps its liquid with window film set to zero')
            ipc('wayfire/set-config-options', {'scottland/goo_overlap_film':4})
        samples = capture_pop(label+'-exit', False)
        pops=[h.get('pop',0) for s in samples for h in s['hints']]
        check(any(.01 < p < .99 for p in pops), label+': pop-out has shrinking intermediate circle')
        check(all(not h.get('rendered',False) for h in hints()), label+': no circle remains after exit')
    theme(True); time.sleep(.3)
    key('LEFTALT',True); wait(lambda: ipc('scottland/hints')['active'])
    check(all(h.get('pop')==1 for h in hints()), 'reduced motion: immediate full-size circles')
    shot('reduced-motion'); key('LEFTALT',False); time.sleep(.03)
    check(all(not h.get('rendered',False) for h in hints()), 'reduced motion: immediate removal')
finally:
    key('LEFTALT',False); key('LEFTMETA',False)
    for client in clients: client.terminate()
    print(f'{passed} passed, {failed} failed', flush=True)
sys.exit(bool(failed))
