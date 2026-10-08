#!/usr/bin/env python3
"""Held pointer/touch output crossing, Esc, drop, unmap and unload use real input."""
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time
out=Path(sys.argv[1]); out.mkdir(parents=True,exist_ok=True)
sock=socket.socket(socket.AF_UNIX); sock.connect(os.environ['WAYFIRE_SOCKET']); sock.settimeout(5)
def read(n):
    b=b''
    while len(b)<n:
        c=sock.recv(n-len(b))
        if not c: raise RuntimeError('compositor disconnected')
        b+=c
    return b
def ipc(method,data=None):
    b=json.dumps({'method':method,'data':data or {}}).encode(); sock.sendall(struct.pack('<I',len(b))+b)
    result=json.loads(read(struct.unpack('<I',read(4))[0]))
    if isinstance(result,dict) and 'error' in result: raise RuntimeError(result)
    return result
def key(k,v): ipc('stipc/feed_key',{'key':'KEY_'+k,'state':v})
def pointer(x,y): ipc('stipc/move_cursor',{'x':round(x),'y':round(y)})
def button(v): ipc('stipc/feed_button',{'combo':'BTN_LEFT','mode':'press' if v else 'release'})
def view(): return next(v for v in ipc('window-rules/list-views') if v.get('title')=='LiveDrag')
outputs=sorted(ipc('window-rules/list-outputs'),key=lambda o:o['geometry']['x'])
assert len(outputs)==2
passed=0
clients=[]
def check(ok,label):
    global passed
    print(('PASS ' if ok else 'FAIL ')+label,flush=True)
    assert ok,label
    passed+=1

def rectangle_tuple(rectangle):
    return tuple(float(rectangle[key]) for key in ('x', 'y', 'width', 'height'))

def check_capture_rectangles():
    layout = ipc('scottland/layout-state')
    info = view()
    row = next(v for v in layout['views'] if v['id'] == info['id'])
    owner = next(o for o in outputs if o['id'] == info['output-id'])
    output = owner['geometry']
    shown = row.get('scene_frame', row.get('frame'))
    expected = (output['x'] + shown['x'], output['y'] + shown['y'],
        shown['width'], shown['height'])
    rectangles = layout['window_rectangles']
    record_keys = {'id', 'app_id', 'title', 'hidden', 'preview', 'output', 'rect'}
    by_id = {record['id']: record for record in rectangles}
    record = by_id[info['id']]
    check(len(rectangles) == len(layout['views']) and
        len(by_id) == len(rectangles) and
        all(set(record) == record_keys for record in rectangles),
        'layout-state gives every mapped view an explicit identity/output/rectangle record')
    check(record['output'] == owner['name'] and not record['hidden'] and
        rectangle_tuple(record['rect']) == expected,
        'window ID remains paired with its owning output and global drawn rectangle')
    command = str(Path('core/libexec/scottland-ctl').resolve())
    cli = json.loads(subprocess.check_output([command, 'windows', '--json'], text=True))
    check(all(set(window) == record_keys for window in cli) and
        len({window['id'] for window in cli}) == len(cli) and
        {window['id']: window for window in cli} == by_id,
        'scottland-ctl JSON preserves the compositor per-window records')

def launch():
    p=subprocess.Popen([sys.executable,str(Path(__file__).with_name('live-drag-app.py'))],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); clients.append(p)
    for _ in range(100):
        try: view(); time.sleep(.5); return p
        except StopIteration: time.sleep(.05)
    raise RuntimeError('no fixture')
def begin(touch=False):
    v=view(); g=v['geometry']; o=next(o['geometry'] for o in outputs if o['id']==v['output-id'])
    x=o['x']+g['x']+g['width']/2; y=o['y']+g['y']+g['height']/2
    pointer(x,y)
    if touch: ipc('stipc/touch',{'finger':0,'x':round(x),'y':round(y)}); time.sleep(.55)
    else: key('LEFTMETA',True); button(True)
    return x,y,v

def finish(touch=False):
    if touch: ipc('stipc/touch_release',{'finger':0})
    else: button(False); key('LEFTMETA',False)
try:
    p=launch()
    check_capture_rectangles()
    for touch in (False,True):
        time.sleep(2.7)
        x,y,origin=begin(touch)
        other=next(o for o in outputs if o['id']!=origin['output-id']); og=other['geometry']
        tx=og['x']+og['width']/2
        for i in range(1,25):
            xx=x+(tx-x)*i/24
            if touch: ipc('stipc/touch',{'finger':0,'x':round(xx),'y':round(y)})
            else: pointer(xx,y)
            time.sleep(.02)
        time.sleep(.5)
        state=ipc('scottland/test-input')
        check(state['dragging'] and state['drag_renderer']=='scottland-live',f'{touch=}: live grab survives crossing')
        colors=[]
        for i in range(8):
            time.sleep(.09)
            path=out/f'cross-{touch}-{i}.png'; subprocess.run(['grim',str(path)],check=True)
            colors.append(subprocess.check_output(['magick',str(path),'-crop',f'1x1+{round(tx)}+{round(y+40)}','-depth','8','RGB:-']))
        check(len(set(colors))>=6,f'{touch=}: content keeps repainting while held still on adjacent output')
        key('ESC',True); key('ESC',False); finish(touch); time.sleep(.6)
        after=view()
        check(after['output-id']==origin['output-id'] and abs(after['geometry']['x']-origin['geometry']['x'])<2,
              f'{touch=}: Esc restores starting output and position')
        # A fresh held drag commits the output on release, without an inertial flick.
        time.sleep(2.7); x,y,_=begin(touch)
        if touch: ipc('stipc/touch',{'finger':0,'x':round(tx),'y':round(y)})
        else: pointer(tx,y)
        time.sleep(.4); finish(touch); time.sleep(.6)
        check(view()['output-id']==other['id'],f'{touch=}: drop transfers output ownership')
        check_capture_rectangles()
    # A disappearing surface must release every input/scene resource.
    begin(); p.terminate(); p.wait(timeout=5); time.sleep(.4)
    check(not ipc('scottland/test-input')['dragging'],'closing a held window releases the drag')
    finish(); p=launch(); begin()
    check(ipc('scottland/test-input')['dragging'],'a new window can be grabbed after unmap')
    plugins=ipc('wayfire/get-config-option',{'option':'core/plugins'})['value']
    ipc('wayfire/set-config-options',{'core/plugins':' '.join(p for p in plugins.split() if p!='scottland')})
    finish(); ipc('wayfire/set-config-options',{'core/plugins':plugins}); time.sleep(.5)
    check(not ipc('scottland/test-input')['dragging'] and view()['mapped'],'unload during a held drag leaves its window alive and releases input')
finally:
    for p in clients:
        if p.poll() is None: p.terminate(); p.wait(timeout=5)
print(f'{passed} passed',flush=True)
