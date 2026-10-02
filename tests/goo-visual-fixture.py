#!/usr/bin/env python3
"""Deterministic before/after screenshots, including overlap, bridge and real drag.
Run via headless.sh run; ARG is the artifact directory. Disables only stochastic
appearance for pixel comparison, retaining the shipped falloff and dye equations.
"""
import json, os, socket, struct, subprocess, sys, time
from pathlib import Path
art=Path(sys.argv[1]); art.mkdir(parents=True,exist_ok=True)
s=socket.socket(socket.AF_UNIX); s.connect(os.environ['WAYFIRE_SOCKET'])
def ipc(method,data=None):
    b=json.dumps({'method':method,'data':data or {}}).encode(); s.sendall(struct.pack('<I',len(b))+b)
    def read(n):
        b=b''
        while len(b)<n: b+=s.recv(n-len(b))
        return b
    return json.loads(read(struct.unpack('<I',read(4))[0]))
def views(): return ipc('scottland/layout-state')['views']
def place(title,x,y,w=320,h=200):
    v=next(v for v in views() if v['title']==title)
    ipc('window-rules/configure-view',{'id':v['id'],'geometry':{'x':x,'y':y,'width':w,'height':h}})
def pointer(x,y): ipc('stipc/move_cursor',{'x':x,'y':y})
def shot(name):
    time.sleep(5)
    subprocess.run(['grim',str(art/(name+'.png'))],check=True)
    (art/(name+'.json')).write_text(json.dumps(views(),indent=2))
opts={'center_width':90,'rail_width':0,'min_scale':1,'max_scale':1,'scale_curve':'0:1 1:1',
      'goo_noise':0,'goo_drift':0,'goo_wave_height':0,'goo_swirl':0,'goo_release':1}
ipc('wayfire/set-config-options',{'scottland/'+k:v for k,v in opts.items()})
clients=[]
for t in ('visual-a','visual-b'):
    clients.append(subprocess.Popen(['foot','-c','/dev/null','-T',t,'sleep','600'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL))
    for _ in range(100):
        if any(v['title']==t for v in views()): break
        time.sleep(.05)
    time.sleep(.2)
time.sleep(1)
place('visual-a',270,240); place('visual-b',610,240); time.sleep(.5)
pointer(760,320); ipc('stipc/feed_button',{'combo':'BTN_LEFT','mode':'press'}); ipc('stipc/feed_button',{'combo':'BTN_LEFT','mode':'release'})
pointer(20,20); shot('bridge')
place('visual-b',500,330); shot('overlap')
pointer(400,340); ipc('stipc/feed_key',{'key':'KEY_LEFTMETA','state':True}); ipc('stipc/feed_button',{'combo':'BTN_LEFT','mode':'press'})
for i in range(1,31): pointer(400-i*3,340-i*2); time.sleep(.025)
shot('drag-held')
ipc('stipc/feed_button',{'combo':'BTN_LEFT','mode':'release'}); ipc('stipc/feed_key',{'key':'KEY_LEFTMETA','state':False}); pointer(20,20); shot('drag-dropped')
# Returning to the previous position checks dry dye history and erased old edges.
place('visual-a',270,240); place('visual-b',610,240); shot('returned')
