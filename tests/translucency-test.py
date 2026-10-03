#!/usr/bin/env python3
"""Opacity transitions under real stipc focus and Window mode input, isolated headless."""
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time

art=Path(sys.argv[1]);art.mkdir(parents=True,exist_ok=True)
assert os.environ['WAYLAND_DISPLAY']!='wayland-1'
sock=socket.socket(socket.AF_UNIX);sock.connect(os.environ['WAYFIRE_SOCKET'])
def ipc(method,data=None):
    body=json.dumps(dict(method=method,data=data or {})).encode()
    sock.sendall(struct.pack('<I',len(body))+body)
    def receive(n):
        result=b''
        while len(result)<n:
            piece=sock.recv(n-len(result))
            if not piece:raise RuntimeError('Wayfire IPC disconnected')
            result+=piece
        return result
    result=json.loads(receive(struct.unpack('<I',receive(4))[0]))
    if 'error' in result:raise RuntimeError(f'{method}: {result}')
    return result

def check(name,condition):
    if not condition:raise AssertionError(name)
    print('PASS',name,flush=True)

def view(name):
    return next((v for v in ipc('scottland/layout-state')['views'] if v['title']==name),None)

def card_for(name):
    return next((v for v in ipc('scottland/layout-state')['views']
                 if v['widget'] and v['title'].endswith(': '+name) and not v['preview']),None)

def wait_card(name):
    for _ in range(160):
        card=card_for(name)
        if card:return card
        time.sleep(.05)
    raise AssertionError(f'{name} did not become a widget')

def wait_view(name):
    for _ in range(80):
        found=view(name)
        if found:return found
        time.sleep(.05)
    raise AssertionError(f'{name} did not map')

def click(x,y):
    ipc('stipc/move_cursor',dict(x=x,y=y));time.sleep(.12)
    ipc('stipc/feed_button',dict(combo='BTN_LEFT',mode='press'))
    ipc('stipc/feed_button',dict(combo='BTN_LEFT',mode='release'))
    time.sleep(.4)

def near(a,b):return abs(a-b)<.03

apps=[]
try:
    for name in ('Opacity A','Opacity B'):
        apps.append(subprocess.Popen(['foot','-c','/dev/null','-T',name,'sh','-c','exec sleep 60'],stdout=(art/(name+'.log')).open('w'),stderr=subprocess.STDOUT))
        wait_view(name)
    a,b=(view(name)['id'] for name in ('Opacity A','Opacity B'))
    ipc('window-rules/configure-view',dict(id=a,geometry=dict(x=460,y=120,width=300,height=210)))
    ipc('window-rules/configure-view',dict(id=b,geometry=dict(x=550,y=415,width=300,height=210)))
    options={'center_opacity_focused':.8,'center_opacity_unfocused':.4,
             'side_opacity_focused':.7,'side_opacity_unfocused':.2,
             'window_mode_opacity_focused':.9,'window_mode_opacity_unfocused':.3}
    ipc('wayfire/set-config-options',{'scottland/'+k:v for k,v in options.items()})
    click(610,225)
    check('real pointer focus eases center pair',near(view('Opacity A')['opacity'],.8) and near(view('Opacity B')['opacity'],.4))
    ipc('window-rules/configure-view',dict(id=b,geometry=dict(x=900,y=415,width=300,height=210)))
    time.sleep(.35)
    check('crossing to side eases unfocused opacity',view('Opacity B')['zone']=='continuous' and near(view('Opacity B')['opacity'],.2))
    frame=view('Opacity B')['frame'];click(round((frame['x']+frame['width']/2)),round((frame['y']+frame['height']/2)))
    check('real focus change eases side pair',near(view('Opacity B')['opacity'],.7) and near(view('Opacity A')['opacity'],.4))
    ipc('stipc/feed_key',dict(key='KEY_LEFTALT',state=True));time.sleep(.55)
    check('Window mode pair overrides zone pairs',ipc('scottland/hints')['active'] and near(view('Opacity B')['opacity'],.9) and near(view('Opacity A')['opacity'],.3))
    ipc('stipc/feed_key',dict(key='KEY_LEFTALT',state=False));time.sleep(.4)
    check('leaving Window mode restores zones',near(view('Opacity B')['opacity'],.7) and near(view('Opacity A')['opacity'],.4))
    ipc('wm-actions/set-fullscreen',dict(view_id=b,state=True));time.sleep(.4)
    check('fullscreen is fully opaque',near(view('Opacity B')['opacity'],1))
    ipc('wm-actions/set-fullscreen',dict(view_id=b,state=False));time.sleep(.4)
    check('unfullscreen restores configured opacity',near(view('Opacity B')['opacity'],.7))
    ipc('wayfire/set-config-options',{'scottland/widget_opacity_focused':.65,
                                      'scottland/widget_opacity_unfocused':.25})
    frame=view('Opacity B')['frame']
    sx=round(frame['x']+frame['width']/2);sy=round(frame['y']+frame['height']/2)
    output=ipc('window-rules/list-outputs')[0]['geometry']
    edge=output['x']+output['width']-8
    ipc('stipc/move_cursor',dict(x=sx,y=sy));time.sleep(.12)
    ipc('stipc/feed_key',dict(key='KEY_LEFTMETA',state=True))
    ipc('stipc/feed_button',dict(combo='BTN_LEFT',mode='press'))
    for step in range(1,21):
        ipc('stipc/move_cursor',dict(x=round(sx+(edge-sx)*step/20),y=sy))
        time.sleep(.025)
    ipc('stipc/feed_button',dict(combo='BTN_LEFT',mode='release'))
    ipc('stipc/feed_key',dict(key='KEY_LEFTMETA',state=False))
    wait_card('Opacity B')
    click(610,225)
    check('real rail drop uses widget unfocused opacity',near(card_for('Opacity B')['opacity'],.25))
    # A card click opens its app, so inject the focus event directly for this remaining
    # rendering branch; the other focus, zone and rail transitions above use real input.
    ipc('window-rules/focus-view',dict(id=card_for('Opacity B')['id']));time.sleep(.4)
    check('widget focused opacity eases after focus event',near(card_for('Opacity B')['opacity'],.65))
finally:
    for app in apps:
        if app.poll() is None:app.terminate();app.wait(timeout=5)
    sock.close()
