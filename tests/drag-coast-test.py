#!/usr/bin/env python3
"""Real timed stipc pointer/touch drags; observe geometry, never configure it."""
import json
import math
import os
from pathlib import Path
import socket
import struct
import statistics
import subprocess
import sys
import time

out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
sock = socket.socket(socket.AF_UNIX); sock.connect(os.environ['WAYFIRE_SOCKET']); sock.settimeout(5)
clients = []
passed = failed = 0

def read(n):
    data = b''
    while len(data) < n:
        chunk = sock.recv(n-len(data))
        if not chunk: raise RuntimeError('compositor disconnected')
        data += chunk
    return data

def ipc(method, data=None):
    payload = json.dumps({'method': method, 'data': data or {}}).encode()
    sock.sendall(struct.pack('<I', len(payload))+payload)
    reply = json.loads(read(struct.unpack('<I', read(4))[0]))
    if isinstance(reply, dict) and 'error' in reply: raise RuntimeError(reply)
    return reply

def check(ok, name):
    global passed, failed
    passed += bool(ok); failed += not ok
    print(('PASS ' if ok else 'FAIL ')+name, flush=True)

def key(code, down): ipc('stipc/feed_key', {'key': 'KEY_'+code, 'state': down})
def tap(code): key(code, True); key(code, False)
def cursor(x, y): ipc('stipc/move_cursor', {'x': round(x), 'y': round(y)})
def button(down): ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'press' if down else 'release'})
def view(name): return next(v for v in ipc('scottland/layout-state')['views'] if v['title'] == name)
def geom(name): return next(v for v in ipc('window-rules/list-views') if v['title'] == name)
def center(name):
    v = geom(name); g = v['geometry']; o = next(o for o in outputs if o['id'] == v['output-id'])['geometry']
    return (g['x']+g['width']/2+o['x'], g['y']+g['height']/2+o['y'])
def launch(name):
    p = subprocess.Popen(['python3','tests/windowing-key-recorder.py',name,str(out/(name+'.keys'))], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    clients.append(p)
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        try:
            id=view(name)['id']; time.sleep(.3); return id
        except StopIteration: time.sleep(.04)
    raise RuntimeError('client did not map: '+name)
def begin(name, touch=False):
    ipc('window-rules/focus-view', {'id':view(name)['id']})
    x,y = center(name)
    if touch:
        ipc('stipc/touch', {'finger':0, 'x':round(x), 'y':round(y)}); time.sleep(.55)
    else:
        cursor(x,y); key('LEFTMETA', True); button(True)
    return x,y

def finish(touch=False):
    if touch: ipc('stipc/touch_release', {'finger':0})
    else: button(False); key('LEFTMETA', False)

def move(x,y,touch=False):
    if touch: ipc('stipc/touch', {'finger':0, 'x':round(x), 'y':round(y)})
    else: cursor(x,y)

def place(name,x,y):
    v=view(name)
    if v['widgetized'] or not 0 < center(name)[1] < h:
        ipc('scottland/present', {'window':v['id']}); time.sleep(.7)
    sx,sy=begin(name)
    for i in range(1,11): cursor(sx+(x-sx)*i/10,sy+(y-sy)*i/10); time.sleep(.012)
    time.sleep(.12); finish(); time.sleep(.25)

def flick(name,dx=40,dy=0,touch=False,pause=0,pin=False,interval=.015):
    x,y=begin(name,touch)
    if pin: key('LEFTSHIFT',True)
    trace=[]
    for i in range(1,7):
        time.sleep(interval); move(x+dx*i/6,y+dy*i/6,touch); trace.append((time.monotonic(),x+dx*i/6,y+dy*i/6))
    time.sleep(pause); finish(touch)
    if pin: key('LEFTSHIFT',False)
    return trace

def sample(name,seconds):
    start=time.monotonic(); result=[]
    while time.monotonic()-start < seconds:
        t=time.monotonic()-start; result.append((t,*center(name))); time.sleep(.018)
    return result

def hold():
    key('LEFTALT',True); time.sleep(.22)

def hint(id): return next(h for h in ipc('scottland/hints')['hints'] if h['window']==id)
def offset(id):
    h=hint(id); return (h['dx'],h['dy'])

try:
    ipc('wayfire/set-config-options', {'scottland/sounds':False,'scottland/alt_hold_delay':100,
        'scottland/key_friction':608.0,'scottland/key_impulse':335.0})
    outputs=sorted(ipc('window-rules/list-outputs'),key=lambda o:o['geometry']['x'])
    w,h=outputs[0]['geometry']['width'],outputs[0]['geometry']['height']
    a=launch('CoastA'); place('CoastA',w/2,h/2)
    if '--two-outputs' in sys.argv:
        place('CoastA',w-170,h/2)
        flick('CoastA',100)
        samples=sample('CoastA',2)
        (out/'crossing.json').write_text(json.dumps(samples))
        check(geom('CoastA')['output-id']==outputs[1]['id'],'drag coast passes to second output')
        check(all(b[1]>=a[1]-.1 for a,b in zip(samples,samples[1:])), 'shared edge does not bounce')
        check(not view('CoastA')['widgetized'],'crossing never widgetizes')
    else:
        for touch in (False, True):
            place('CoastA',w/2,h/2); trace=flick('CoastA',touch=touch)
            samples=sample('CoastA',1)
            (out/('touch.json' if touch else 'pointer.json')).write_text(json.dumps(samples))
            travel=samples[-1][1]-samples[0][1]
            check(travel>40,('touch' if touch else 'pointer')+' flick travels after release')
            # Fit v from the early trajectory with known a; independently check all positions.
            early=[s for s in samples if .03 < s[0]-samples[0][0] < .55]
            ts=[s[0]-samples[0][0] for s in early]
            # Geometry commits and the 8 ms motion clock have independent phases. Fit the
            # position intercept too, rather than treating the first sampled commit as t=0.
            speed,origin=statistics.linear_regression(ts,[s[1]+304*t*t for s,t in zip(early,ts)])
            mt=sum(s[0] for s in trace)/len(trace); mx=sum(s[1] for s in trace)/len(trace)
            input_speed=sum((s[0]-mt)*(s[1]-mx) for s in trace)/sum((s[0]-mt)**2 for s in trace)
            check(abs(speed-input_speed)<input_speed*.12, 'release velocity matches timed input speed')
            error=max(abs(s[1]-(origin+speed*t-304*t*t)) for s,t in zip(early,ts))
            check(error<4 and abs(samples[-1][1]-origin-speed*speed/1216)<5,('touch' if touch else 'pointer')+' trajectory matches constant deceleration and stop distance')
            check(math.dist(center('CoastA'),samples[-1][1:])<.1,'coast stays stopped')
        for pause,dx,label in [(0,10,'slow'),(.12,40,'paused')]:
            place('CoastA',w/2,h/2); flick('CoastA',dx,pause=pause,interval=.08 if label=='slow' else .015)
            before=center('CoastA'); time.sleep(.8)
            check(math.dist(before,center('CoastA'))<.1,label+' release does not coast')
        for dx,dy,label in [(-100,0,'left'),(100,0,'right'),(0,-100,'up'),(0,100,'down')]:
            place('CoastA',w/2,h/2); flick('CoastA',dx,dy)
            samples=sample('CoastA',2)
            (out/(label+'-edge.json')).write_text(json.dumps(samples))
            if dx:
                links=ipc('scottland/widgets')['widgets']
                check(len([v for v in links if int(v['id'])==a and v['rail']==label])==1,label+' flick coast widgetizes once onto matching rail')
            else:
                f=view('CoastA')['frame']; visible=min(h,f['y']+f['height'])-max(0,f['y'])
                check(abs(visible-100)<1.1,label+' flick coast leaves 100 logical pt visible')
                check(all((b[2]-a[2])*dy>=-.1 for a,b in zip(samples,samples[1:])),label+' flick coast stops without reversing')
            subprocess.run(['grim',str(out/(label+'-edge.png'))],check=True)
        place('CoastA',w/2,h/2); flick('CoastA'); time.sleep(.08)
        begin('CoastA'); before=center('CoastA'); time.sleep(.5)
        check(math.dist(before,center('CoastA'))<1,'new grab catches coast immediately'); finish()
        place('CoastA',w/2,h/2); flick('CoastA'); time.sleep(.08); tap('ESC')
        before=center('CoastA'); time.sleep(.7)
        check(math.dist(before,center('CoastA'))<.1,'Esc stops released coast immediately')
        place('CoastA',w*.65,h/2); flick('CoastA',50,pin=True); samples=[]
        for _ in range(25): samples.append(view('CoastA')); time.sleep(.025)
        check(all(abs(s['applied_scale']-1)<.01 for s in samples),'Shift pin survives drag coast')
        tap('ESC'); place('CoastA',w*.64,h/2); flick('CoastA',50)
        samples=[]
        for _ in range(25): samples.append(view('CoastA')); time.sleep(.025)
        check(any(s['applied_scale']<.9 for s in samples) and all(abs(s['applied_scale']-s['scale'])<.02 for s in samples),'drag coast follows live zone scale')
        tap('ESC')
        b=launch('CoastB'); place('CoastA',w/2,h/2); place('CoastB',w/2,h/2)
        hold(); time.sleep(.5)
        initial={a:offset(a), b:offset(b)}
        tracked=max(initial, key=lambda identifier: math.hypot(*initial[identifier]))
        before=initial[tracked]
        check(math.hypot(*before)>1,'overlapping fixture has a displaced hint to track')
        tap('RIGHT'); time.sleep(.12); during=offset(tracked); time.sleep(.25); later=offset(tracked)
        check(math.dist(before,during)<.3 and math.dist(during,later)<.3,'declutter freezes while keyboard coast moves through neighbors')
        offsets=[]
        for _ in range(80): offsets.append(offset(tracked)); time.sleep(.008)
        after=offsets[-1]
        (out/'keyboard-declutter.json').write_text(json.dumps({
            'initial':initial,'tracked':tracked,'before':before,'during':during,
            'later':later,'offsets':offsets,
            'centers':{'A':center('CoastA'),'B':center('CoastB')},
        }))
        check(math.dist(before,after)>1,'declutter resumes after keyboard coast rests')
        jumps=[math.dist(a,b) for a,b in zip(offsets,offsets[1:])]
        check(max(jumps)<math.dist(before,after)*.55 and sum(d>.05 for d in jumps)>3,
              'resumed declutter interpolates across frames without a jump')
        key('LEFTALT',False); time.sleep(.4)
        # A drag ends hints; enter them while the released window is still moving.
        place('CoastA',w/2+175,h/2); place('CoastB',w/2,h/2); flick('CoastB',40); hold()
        during=offset(a); time.sleep(.15); later=offset(a)
        check(math.dist(during,later)<.3,'declutter remains paused on hint entry during drag coast')
        time.sleep(.7); after=offset(a)
        check(math.dist(after,later)>1,'declutter resumes after drag coast rests')
        key('LEFTALT',False); time.sleep(.3)
        # Explicit rail release owns form even with high recent velocity.
        place('CoastB',w*.18,h/2); sx,sy=begin('CoastB')
        for i in range(1,7): time.sleep(.015); cursor(sx+(3-sx)*i/6,sy)
        finish(); time.sleep(.8)
        check(view('CoastB')['widgetized'],'fast pointer rail drop widgetizes instead of coasting')
        subprocess.run(['grim',str(out/'rail-drop.png')],check=True)
except Exception as error:
    check(False,'suite exception: '+repr(error))
finally:
    for p in clients:
        p.terminate()
        try: p.wait(timeout=3)
        except subprocess.TimeoutExpired: p.kill(); p.wait()
    print(f'{passed} passed, {failed} failed',flush=True)
sys.exit(bool(failed))
