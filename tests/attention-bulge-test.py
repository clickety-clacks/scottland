#!/usr/bin/env python3
"""Measure the rendered attention shore, rather than counting changing light pixels.

Run inside a private --widgets headless session on the test host. Artifacts include
held trough/peak images and sleeping simulation/cache counters.
"""
import json
import math
import os
from pathlib import Path
import socket
import statistics
import struct
import subprocess
import sys
import time
import gi
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf

assert os.environ.get('SCOTTLAND_TEST_MODEL') == '1'
art = Path(sys.argv[1]).resolve(); art.mkdir(parents=True, exist_ok=True)
sock = socket.socket(socket.AF_UNIX); sock.connect(os.environ['WAYFIRE_SOCKET'])
def ipc(method, data=None):
    body = json.dumps({'method':method, 'data':data or {}}).encode()
    sock.sendall(struct.pack('<I',len(body))+body)
    def read(n):
        out=b''
        while len(out)<n:
            more=sock.recv(n-len(out))
            if not more: raise RuntimeError('compositor disconnected')
            out+=more
        return out
    out=json.loads(read(struct.unpack('<I',read(4))[0]))
    if 'error' in out: raise RuntimeError(out)
    return out
def views(): return ipc('scottland/layout-state')['views']
def view(title): return next(v for v in views() if v['title']==title)
def state(data=None): return ipc('scottland/goo-state',data)['screens'][0]
def pointer(x,y): ipc('stipc/move_cursor',{'x':round(x),'y':round(y)})
def button(mode): ipc('stipc/feed_button',{'combo':'BTN_LEFT','mode':mode})
def key(down): ipc('stipc/feed_key',{'key':'KEY_LEFTMETA','state':down})
def move(title,x,y):
    f=view(title)['frame']; sx,sy=f['x']+f['width']/2,f['y']+f['height']/2
    pointer(sx,sy); key(True); button('press')
    for i in range(1,31):
        pointer(sx+(x-sx)*i/30,sy+(y-sy)*i/30); time.sleep(.02)
    button('release'); key(False); pointer(800,950); time.sleep(1)
def settle():
    time.sleep(1)
    deadline=time.monotonic()+65
    while time.monotonic()<deadline:
        s=state()
        if s['sleeping'] and not s.get('breath_loose'): return s
        time.sleep(.1)
    raise AssertionError(('did not settle',state()))
def shot(name):
    path=art/(name+'.png'); subprocess.run(['grim',str(path)],check=True)
    return GdkPixbuf.Pixbuf.new_from_file(str(path))
def shore(image,frame):
    pixels=image.get_pixels(); stride=image.get_rowstride(); channels=image.get_n_channels()
    background=pixels[:3]
    shores=[]
    for y in range(round(frame['y']+frame['height']*.35),round(frame['y']+frame['height']*.65)):
        for x in range(max(0,math.floor(frame['x']-60)),math.floor(frame['x'])):
            rgb=pixels[y*stride+x*channels:y*stride+x*channels+3]
            if max(abs(a-b) for a,b in zip(rgb,background))>=10:
                shores.append(x); break
    assert len(shores)>10, ('no visible shore',frame)
    return statistics.median(shores)
results=[]
clients=[]
try:
    ipc('wayfire/set-config-options',{'output:HEADLESS-1/mode':'1600x1000@60000'})
    for title,x in (('pulse-breather',350),('pulse-focus',900)):
        clients.append(subprocess.Popen(['foot','-c','/dev/null','-T',title,'sleep','600'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL))
        deadline=time.monotonic()+10
        while time.monotonic()<deadline and not any(v['title']==title for v in views()): time.sleep(.1)
        ipc('window-rules/configure-view',{'id':view(title)['id'],'geometry':{'x':x,'y':300,'width':400,'height':340}})
    time.sleep(2)
    pointer(1050,450); button('press'); button('release'); pointer(800,950)
    breather=view('pulse-breather')['id']
    ipc('scottland/attention',{'window':breather,'attention':True,'source':'bulge-test'})
    def verify(title,label):
        for exact in (False,True):
            before=settle()
            state({'breath_exact':exact,'breath_hold':0}); time.sleep(.5)
            frame=view(title)['frame']; trough=shore(shot(label+('-exact' if exact else '-keys')+'-trough'),frame)
            state({'breath_hold':1}); time.sleep(.5)
            peak=shore(shot(label+('-exact' if exact else '-keys')+'-peak'),frame)
            after=state()
            result={'case':label,'exact':exact,'shore_travel':trough-peak,
                    'steps':after['steps']-before['steps'],
                    'keyframes':after['breath_keyframes_active'],
                    'refreshes':after['breath_refreshes']-before['breath_refreshes'],
                    'reuses':after['backdrop_reuses']-before['backdrop_reuses']}
            results.append(result); print(json.dumps(result),flush=True)
            (art/'results.json').write_text(json.dumps(results,indent=2))
            assert trough-peak>=4, 'light changes without a visible bulge: '+str(result)
            assert result['steps']==0 and after['sleeping'], result
            if not exact: assert result['keyframes'] and result['reuses']>0, result
    verify('pulse-breather','window')
    state({'breath_hold':-1})
    ipc('wayfire/set-config-options',{'scottland/goo':False})
    thickness=[]; shots={}; deadline=time.monotonic()+11
    while time.monotonic()<deadline:
        frame=view('pulse-breather')['frame']; thickness.append(frame['thickness'])
        label='trough' if frame['swell']<.005 else 'peak' if frame['swell']>.44 else None
        if label and label not in shots: shots[label]=shore(shot('fallback-'+label),frame)
        time.sleep(.03)
    result={'case':'fallback','thickness_travel':max(thickness)-min(thickness),'shore_travel':shots.get('trough',0)-shots.get('peak',0)}
    results.append(result); print(json.dumps(result),flush=True)
    assert len(shots)==2 and result['shore_travel']>=4 and result['thickness_travel']>=4.8, result
    ipc('wayfire/set-config-options',{'scottland/goo':True})
    move('pulse-breather',15,500)
    deadline=time.monotonic()+15
    while time.monotonic()<deadline and not any(v['widget'] for v in views()): time.sleep(.1)
    widget=next(v for v in views() if v['widget'])
    pointer(1050,450); button('press'); button('release'); pointer(800,950)
    ipc('scottland/attention',{'window':breather,'attention':True,'source':'bulge-test'})
    verify(widget['title'],'widget')
    state({'breath_hold':-1,'breath_exact':False})
    (art/'results.json').write_text(json.dumps(results,indent=2))
finally:
    ipc('wayfire/set-config-options',{'scottland/goo':True})
    state({'breath_hold':-1,'breath_exact':False})
    for client in clients: client.terminate()
    for client in clients: client.wait(timeout=5)
