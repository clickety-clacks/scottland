#!/usr/bin/env python3
"""Called by goo-bench.sh inside its private session; fixtures then real-input drag."""
import json, math, os, socket, struct, subprocess, sys, time
from pathlib import Path
runtime, duration = Path(sys.argv[1]), float(sys.argv[2])
sock = socket.socket(socket.AF_UNIX); sock.connect(os.environ['WAYFIRE_SOCKET'])
def ipc(method, data=None):
    b=json.dumps({'method':method,'data':data or {}}).encode(); sock.sendall(struct.pack('<I',len(b))+b)
    def read(n):
        b=b''
        while len(b)<n:
            more=sock.recv(n-len(b))
            if not more: raise RuntimeError('compositor disconnected')
            b+=more
        return b
    out=json.loads(read(struct.unpack('<I',read(4))[0]))
    if 'error' in out: raise RuntimeError(out)
    return out
def views(): return ipc('scottland/layout-state')['views']
def pointer(x,y): ipc('stipc/move_cursor',{'x':round(x),'y':round(y)})
def key(down): ipc('stipc/feed_key',{'key':'KEY_LEFTMETA','state':down})
def button(mode): ipc('stipc/feed_button',{'combo':'BTN_LEFT','mode':mode})
def move(title,x,y):
    v=next(v for v in views() if v['title']==title); f=v['frame']; cx=f['x']+f['width']/2; cy=f['y']+f['height']/2
    pointer(cx,cy); key(True); button('press')
    for i in range(1,31): pointer(cx+(x-cx)*i/30,cy+(y-cy)*i/30); time.sleep(.02)
    button('release'); key(False); time.sleep(1)
def state():
    screens=ipc('scottland/goo-state')['screens']
    return screens[0] if screens else {'gpu_ms':0,'steps':0,'sleeping':True}
# The bus wrapper owns the harness pid; fdinfo must be read from Wayfire itself.
pid=int((runtime/'pid').read_text())
children=Path(f'/proc/{pid}/task/{pid}/children').read_text().split()
for child in children:
    if Path(f'/proc/{child}/comm').read_text().strip()=='wayfire': pid=int(child); break
art=Path.cwd()/'build'/('bench-'+runtime.name); art.mkdir(parents=True,exist_ok=True)
def measure(label, drag=False):
    before=state(); start=time.monotonic()
    proc=subprocess.Popen([sys.executable,str(Path(__file__).with_name('gpu-sample.py')),str(pid),str(duration)],stdout=subprocess.PIPE,text=True)
    samples=[]
    while time.monotonic()-start<duration:
        if drag:
            t=time.monotonic()-start; pointer(1150+120*math.sin(t*2),550+80*math.cos(t*2))
        samples.append(state()['gpu_ms']); time.sleep(1/60 if drag else .1)
    output=proc.communicate()[0].strip(); after=state()
    print(json.dumps({'case':label,'cost':output,'gpu_ms_median':sorted(samples)[len(samples)//2] if after['steps']!=before['steps'] else None, 'steps':after['steps']-before['steps'],'sleeping':after['sleeping'],'overlapping':after.get('overlapping'),'highlighting':after.get('highlighting'),'energy':after.get('energy')}),flush=True)
    subprocess.run(['grim',str(art/(label+'.png'))],check=True)
ipc('wayfire/set-config-options',{'output:HEADLESS-1/mode':'2560x1600@60000'})
time.sleep(1)
clients=[]
for i in range(6):
    clients.append(subprocess.Popen(['foot','-c','/dev/null','-T',f'perf-{i}','sleep','600'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)); time.sleep(.25)
time.sleep(2)
geo=[(200,150,900,600),(1200,150,1100,700),(250,850,700,500),(1050,950,600,450),(1750,950,600,450),(1000,420,400,260)]
for i,g in enumerate(geo):
    v=next(v for v in views() if v['title']==f'perf-{i}')
    ipc('window-rules/configure-view',{'id':v['id'],'geometry':dict(zip(('x','y','width','height'),g))})
    time.sleep(.2)
# Two actual rail widgets, moved with input, retain attention until answered (WG15).
move('perf-0',15,350); move('perf-1',2540,350)
pointer(1200,550); button("press"); button("release"); pointer(1280,800)
for _ in range(450):
    if state()['sleeping']: break
    time.sleep(.1)
measure('settled')
widgets=ipc('scottland/widgets')['widgets']
(art/'fixtures.json').write_text(json.dumps({'views':views(),'widgets':widgets},indent=2))
assert len(widgets)==2, widgets
for w in widgets: ipc('scottland/attention',{'window':w['window'],'attention':True,'source':'perf-bench'})
time.sleep(3); measure('attention')
for w in widgets: ipc('scottland/attention',{'window':w['window'],'attention':False,'source':'perf-bench'})
v=next(v for v in views() if v['title']=='perf-5'); f=v['frame']
pointer(f['x']+f['width']/2,f['y']+f['height']/2); key(True); button('press')
measure('drag',True); button('release'); key(False)
for w in widgets: ipc('scottland/attention',{'window':w['window'],'attention':True,'source':'perf-bench'})
ipc('wayfire/set-config-options',{'scottland/goo':False}); time.sleep(3); measure('off-breathing')
# Optional paired WK28 workload; the four original GO10 cases above are unchanged.
if len(sys.argv) > 3 and sys.argv[3] == '--hints':
    ipc('wayfire/set-config-options', {'scottland/goo':True})
    ipc('stipc/feed_key', {'key':'KEY_LEFTALT','state':True})
    time.sleep(3); measure('hints-attention')
    for w in widgets: ipc('scottland/attention',{'window':w['window'],'attention':False,'source':'perf-bench'})
    for _ in range(450):
        if state()['sleeping']: break
        time.sleep(.1)
    measure('hints-settled')
    assert state()['sleeping'], 'held hints did not settle within 45 seconds'
    ipc('stipc/feed_key', {'key':'KEY_LEFTALT','state':False})
