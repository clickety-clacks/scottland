#!/usr/bin/env python3
"""Run inside an isolated headless session; sample actual changing content during input grabs."""
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time

out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
sock = socket.socket(socket.AF_UNIX); sock.connect(os.environ['WAYFIRE_SOCKET']); sock.settimeout(5)
def read(n):
    b = b''
    while len(b) < n:
        chunk = sock.recv(n-len(b))
        if not chunk: raise RuntimeError('compositor disconnected')
        b += chunk
    return b
def ipc(method, data=None):
    b = json.dumps({'method': method, 'data': data or {}}).encode()
    sock.sendall(struct.pack('<I',len(b))+b)
    result = json.loads(read(struct.unpack('<I',read(4))[0]))
    if isinstance(result,dict) and 'error' in result: raise RuntimeError(result)
    return result
def pointer(x,y): ipc('stipc/move_cursor', {'x':round(x), 'y':round(y)})
def key(name,down): ipc('stipc/feed_key', {'key':'KEY_'+name, 'state':down})
def button(down): ipc('stipc/feed_button', {'combo':'BTN_LEFT','mode':'press' if down else 'release'})
def view(): return next(v for v in ipc('scottland/layout-state')['views'] if v['title']=='LiveDrag')
client = subprocess.Popen([sys.executable,str(Path(__file__).with_name('live-drag-app.py'))])
results = []
try:
    for _ in range(100):
        try: view(); break
        except StopIteration: time.sleep(.05)
    time.sleep(1)
    for mode in ('super', 'halo', 'touch', 'halo-touch', 'swipe', 'client'):
        time.sleep(2.7)  # independent Esc origin, no previous re-grab chain
        v = view(); f = v['frame']; x = f['x']+f['width']/2; y = f['y']+f['height']/2
        hx,hy = (x,f['y']-5) if mode in ('halo','halo-touch') else (x,y)
        if mode == 'client': hx,hy=x,f['y']+20
        pointer(hx,hy)
        if mode in ('touch','halo-touch'):
            ipc('stipc/touch', {'finger':0,'x':round(hx),'y':round(hy)}); time.sleep(.55)
        elif mode == 'swipe': ipc('scottland/test-input', {'swipe':'begin','fingers':3})
        else:
            if mode == 'super': key('LEFTMETA',True)
            button(True)
        colors=[]
        for i in range(12):
            dx=40+i*3
            if mode in ('touch','halo-touch'): ipc('stipc/touch', {'finger':0,'x':round(hx+dx),'y':round(hy+20)})
            elif mode == 'swipe': ipc('scottland/test-input', {'swipe':'update','dx':40 if i==0 else 3,'dy':20 if i==0 else 0})
            else: pointer(hx+dx,hy+20)
            time.sleep(.09)
            path=out/f'{mode}-{i}.png'
            subprocess.run(['grim',str(path)],check=True)
            # All samples remain well inside the center-zone window, away from the pointer/halo.
            pixel = subprocess.check_output(['magick', str(path), '-crop', f'1x1+{round(x+dx)}+{round(y+45)}', '-depth', '8', 'RGB:-'])
            colors.append(tuple(pixel[:3]))
        drag_state=ipc('scottland/test-input')
        dragging=drag_state['dragging']
        renderer=drag_state.get('drag_renderer', 'wayfire-move')
        key('ESC',True); key('ESC',False)
        if mode in ('touch','halo-touch'): ipc('stipc/touch_release', {'finger':0})
        elif mode == 'swipe': ipc('scottland/test-input', {'swipe':'end'})
        else:
            button(False)
            if mode == 'super': key('LEFTMETA',False)
        unique=len(set(colors)); expected='wayfire-move' if mode=='client' else 'scottland-live'
        ok=dragging and unique>=8 and (renderer==expected or '--baseline' in sys.argv)
        results.append({'mode':mode,'unique_colors':unique,'samples':colors,'dragging':dragging,'renderer':renderer,'pass':ok})
        print(('PASS' if ok else 'FAIL')+f' {mode}: {unique}/12 distinct content colors during held drag',flush=True)
    (out/'results.json').write_text(json.dumps(results,indent=2))
finally:
    client.terminate(); client.wait(timeout=5)
sys.exit(0 if all(r['pass'] for r in results) else 1)
