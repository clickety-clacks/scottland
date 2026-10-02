#!/usr/bin/env python3
"""GPU propagation, fullscreen exclusion and cross-output drag, using real stipc input.
Run in an isolated headless session; SCOTTLAND_TEST_OUTPUTS=2 enables the output check.
"""
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import time

repo = Path(__file__).resolve().parents[1]
art = repo / 'build/goo-flow-evidence'
art.mkdir(exist_ok=True)
sock = socket.socket(socket.AF_UNIX)
sock.connect(os.environ['WAYFIRE_SOCKET'])
clients = []
passed = failed = 0

def ipc(method, data=None):
    body = json.dumps({'method': method, 'data': data or {}}).encode()
    sock.sendall(struct.pack('<I', len(body)) + body)
    def read(n):
        out = b''
        while len(out) < n:
            chunk = sock.recv(n-len(out))
            if not chunk: raise RuntimeError('compositor disconnected')
            out += chunk
        return out
    return json.loads(read(struct.unpack('<I', read(4))[0]))

def check(name, ok):
    global passed, failed
    print(('PASS ' if ok else 'FAIL ') + name, flush=True)
    passed += bool(ok); failed += not ok

def views(): return ipc('scottland/layout-state')['views']
def view(title): return next(v for v in views() if v['title'] == title)
def pointer(x,y): ipc('stipc/move_cursor', {'x':round(x), 'y':round(y)})
def key(down): ipc('stipc/feed_key', {'key':'KEY_LEFTMETA','state':down})
def button(down): ipc('stipc/feed_button', {'combo':'BTN_LEFT','mode':'press' if down else 'release'})
def center(title):
    f=view(title)['frame']; return f['x']+f['width']/2, f['y']+f['height']/2

def drag(title,dx,dy):
    x,y=center(title); pointer(x,y); key(True); button(True)
    for i in range(1,21): pointer(x+dx*i/20,y+dy*i/20); time.sleep(.03)
    button(False); key(False); pointer(20,20); time.sleep(.5)

def quiet():
    pointer(20,20)
    for _ in range(450):
        if all(s['sleeping'] for s in ipc('scottland/goo-state')['screens']): return True
        time.sleep(.1)
    return False

def pulse_and_measure():
    a=view('flow-a')['frame']; b=view('flow-b')['frame']
    pointer(a['x']-8,a['y']+a['height']/2); time.sleep(.1); button(True)
    samples=[(b['x']+35,b['y']+b['height']+7), (b['x']-7,b['y']+b['height']/2)]
    peak=0
    for _ in range(75):
        for x,y in samples:
            s=ipc('scottland/goo-state', {'x':x,'y':y})['screens'][0]
            peak=max(peak,abs(s['wave']))
        time.sleep(.025)
    button(False); pointer(20,20)
    return peak

try:
    ipc('wayfire/set-config-options', {'scottland/'+k:v for k,v in dict(
        goo=True, goo_falloff='', center_width=90, rail_width=0, min_scale=1, max_scale=1,
        scale_curve='0:1 1:1', goo_noise=0, goo_thickness=18, sounds=False).items()})
    pointer(20,20); button(True); button(False)
    for title in ['flow-a','flow-b']:
        p=subprocess.Popen(['foot','-c','/dev/null','-T',title,'sleep','600'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        clients.append(p)
        for _ in range(50):
            if any(v['title']==title for v in views()):break
            time.sleep(.1)
    for title,x in [('flow-a',250),('flow-b',600)]:
        ipc('window-rules/configure-view',{'id':view(title)['id'],'geometry':{'x':x,'y':230,'width':320,'height':180}})
    print('fixture outputs',ipc('window-rules/list-views'),flush=True)
    check('waves settle before a new grab', quiet())
    connected=pulse_and_measure()
    print('connected wave peak',connected,flush=True)
    check('grab wave propagates through a bridge to the other window', connected>.06)
    drag('flow-b',240,0)
    check('separated waves settle',quiet())
    separated=pulse_and_measure()
    print('separated wave peak',separated,flush=True)
    check('grab wave cannot cross a dry gap',separated<=.032)
    # Goo reaches beyond the old view's drawing bounds; input still reads the shared field.
    ipc('wayfire/set-config-options',{'scottland/goo_reach':70,'scottland/goo_thinning':0,'scottland/goo_thickness':13})
    for title,x in [('flow-a',250),('flow-b',690)]:
        ipc('window-rules/configure-view',{'id':view(title)['id'],'geometry':{'x':x,'y':230,'width':320,'height':180}})
    time.sleep(.6)
    before=center('flow-a'); pointer(626,320); time.sleep(.1); button(True)
    for i in range(1,11):pointer(626,320+i*4);time.sleep(.03)
    button(False);pointer(20,20);time.sleep(.5)
    check('a wide bridge is draggable beyond the legacy drawing bounds',center('flow-a')[1]>before[1]+25)
    ipc('wm-actions/set-fullscreen',{'view_id':view('flow-b')['id'],'state':True}); time.sleep(.6)
    sample=ipc('scottland/goo-state',{'x':100,'y':100})['screens'][0]
    check('fullscreen clips the entire liquid layer',sample['window_distance']<0)
    ipc('scottland/attention',{'window':view('flow-a')['id'],'attention':True,'source':'goo-flow-test'})
    time.sleep(.3); steps=ipc('scottland/goo-state')['screens'][0]['steps'];time.sleep(1)
    state=ipc('scottland/goo-state')['screens'][0]
    check('hidden attention does not run a fullscreen goo simulation',state['sleeping'] and state['steps']==steps)
    ipc('scottland/attention',{'window':view('flow-a')['id'],'attention':False,'source':'goo-flow-test'})
    subprocess.run(['grim',str(art/'fullscreen.png')],check=True)
    ipc('wm-actions/set-fullscreen',{'view_id':view('flow-b')['id'],'state':False}); time.sleep(.5)
    outputs=ipc('window-rules/list-outputs')
    if len(outputs)>1:
        origins=sorted(outputs,key=lambda o:o['geometry']['x'])
        destination=origins[1]['geometry']; x,y=center('flow-b')
        # Hold a real move across the seam: the window intersects both outputs before release.
        # Even a zero-width rail owns the exact edge pixel in the desktop model.
        target=destination['x']+5; pointer(x,y); key(True); button(True)
        for i in range(1,21):pointer(x+(target-x)*i/20,y);time.sleep(.03)
        time.sleep(.4)
        screens=ipc('scottland/goo-state')['screens']
        print('held output sources',screens,flush=True)
        check('one independent field exists on each output',len(screens)==2)
        check('a dragging window contributes on both intersected outputs',all(s['sources']>=1 for s in screens))
        subprocess.run(['grim',str(art/'cross-output-held.png')],check=True)
        pointer(target+180,y); time.sleep(.2); button(False); key(False);pointer(20,20);time.sleep(.7)
        screens=ipc('scottland/goo-state')['screens']
        check('drop moves the source to its destination field',all(s['sources']==1 for s in screens))
        subprocess.run(['grim',str(art/'cross-output-dropped.png')],check=True)
finally:
    for v in views():
        if v['title'].startswith('flow-'):ipc('window-rules/close-view',{'id':v['id']})
    sock.close()
    print(f'RESULT {passed} passed, {failed} failed',flush=True)
raise SystemExit(bool(failed))
