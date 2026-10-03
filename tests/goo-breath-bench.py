#!/usr/bin/env python3
"""GO17 live-like cost/visual fixture; run inside a private headless session.
Args: headless directory, measurement seconds, optional --verify.
Ten windows, two real-input rail widgets, one attention source, shipped goo defaults.
"""
import atexit, json, os, signal, socket, struct, subprocess, sys, time
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
art=Path.cwd()/'build/go17'/('bench-'+runtime.name); art.mkdir(parents=True,exist_ok=True)
def measure(label):
    before=state(); start=time.monotonic()
    proc=subprocess.Popen([sys.executable,str(Path(__file__).with_name('gpu-sample.py')),str(pid),str(duration)],stdout=subprocess.PIPE,text=True)
    samples=[]; draw_samples=[]
    while time.monotonic()-start<duration:
        sample=state(); samples.append(sample['gpu_ms']); draw_samples.append(sample.get('draw_gpu_ms',0)); time.sleep(.1)
    output=proc.communicate()[0].strip(); after=state()
    print(json.dumps({'case':label,'cost':output,'gpu_ms_median':sorted(samples)[len(samples)//2] if after['steps']!=before['steps'] else None, 'steps':after['steps']-before['steps'],'sleeping':after['sleeping'],'overlapping':after.get('overlapping'),'highlighting':after.get('highlighting'),'energy':after.get('energy'),'breath_draw_ms':sorted(draw_samples)[len(draw_samples)//2] if after.get('breath_ticks',0)!=before.get('breath_ticks',0) else None,'breath_ticks':after.get('breath_ticks',0)-before.get('breath_ticks',0)}),flush=True)
    subprocess.run(['grim',str(art/(label+'.png'))],check=True)
ipc('wayfire/set-config-options',{'output:HEADLESS-1/mode':'2560x1600@60000'})
time.sleep(1)
clients=[]
def stop_clients():
    for client in clients:
        try:
            os.killpg(client.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    for client in clients:
        try:
            client.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(client.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            client.wait()
atexit.register(stop_clients)
clients.append(subprocess.Popen(['quickshell','-p',str(Path(__file__).with_name('GooWallpaper.qml'))],
                                stdout=open(art/'wallpaper.log','w'), stderr=subprocess.STDOUT,
                                start_new_session=True))
for i in range(10):
    clients.append(subprocess.Popen(['foot','-c','/dev/null','-T',f'perf-{i}','sleep','600'],
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)); time.sleep(.25)
time.sleep(2)
geo=[(200,150,900,600),(1200,150,1100,700),(250,850,700,500),(1050,950,600,450),(1750,950,600,450),(1000,420,400,260),(700,400,500,360),(1100,700,520,380),(1700,500,500,380),(400,650,500,340)]
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
w=widgets[0]
ipc('scottland/attention',{'window':w['window'],'attention':True,'source':'perf-bench'})
time.sleep(20); measure('attention')
if '--verify' in sys.argv:
    from goo_breath_checks import verify
    verify(ipc, art)
(art/'attention.json').write_text(json.dumps(state(),indent=2))
ipc('scottland/attention',{'window':w['window'],'attention':False,'source':'perf-bench'})
time.sleep(1)  # let the state transition reach prepare(), before testing sleep
for _ in range(450):
    if state()['sleeping']: break
    time.sleep(.1)
measure('answered')
