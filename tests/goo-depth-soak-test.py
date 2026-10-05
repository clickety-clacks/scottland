#!/usr/bin/env python3
"""GO14/GO15: run inside a fresh isolated headless session. Artifacts in ARG.
Geometry IPC arranges fixtures; focus/drag uses stipc. Dye probes read GPU history,
so a final-pass wallpaper tint cannot satisfy the soak assertions.
"""
import json, os, signal, socket, struct, subprocess, sys, time
from pathlib import Path
import gi
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf
repo = Path(__file__).resolve().parents[1]
art = Path(sys.argv[1]); art.mkdir(parents=True, exist_ok=True)
s = socket.socket(socket.AF_UNIX); s.connect(os.environ['WAYFIRE_SOCKET'])
clients = []; wallpapers = []; failed = 0; passed = 0

def ipc(method, data=None):
    b = json.dumps({'method': method, 'data': data or {}}).encode()
    s.sendall(struct.pack('<I', len(b))+b)
    def read(n):
        b = b''
        while len(b) < n:
            c = s.recv(n-len(b))
            if not c: raise RuntimeError('compositor disconnected')
            b += c
        return b
    out = json.loads(read(struct.unpack('<I', read(4))[0]))
    if 'error' in out: raise RuntimeError(out)
    return out

def options(**values): ipc('wayfire/set-config-options', {'scottland/'+k:v for k,v in values.items()})
def views(): return ipc('scottland/layout-state')['views']
def state(x=400,y=234): return ipc('scottland/goo-state', {'x':x,'y':y})['screens'][0]
def dye(x=400,y=234):
    v=state(x,y); return [v[k] for k in ('red','green','blue')]
def check(label, condition):
    global failed, passed
    print(('PASS ' if condition else 'FAIL ')+label, flush=True)
    failed += not condition; passed += bool(condition)
def settle():
    for _ in range(400):
        if state()['sleeping']: return True
        time.sleep(.1)
    return False

def shot(name):
    subprocess.run(['grim',str(art/(name+'.png'))],check=True)
    (art/(name+'.json')).write_text(json.dumps({'views':views(),'state':state()},indent=2))

def stop(p):
    # The headless quickshell wrapper uses bubblewrap, which forks the client.
    # Stop this fixture's whole private group so wallpaper really unmaps.
    try:
        if p in wallpapers: os.killpg(p.pid, signal.SIGTERM)
        elif p.poll() is None: p.terminate()
    except ProcessLookupError:
        pass
    try: p.wait(timeout=5)
    except subprocess.TimeoutExpired: p.kill(); p.wait()

def wallpaper(color, bottom=""):
    log=open(art/('wallpaper-'+color[1:]+'.log'),'w')
    p=subprocess.Popen(['quickshell','-p',str(repo/'tests/GooWallpaper.qml')],
                       env=dict(os.environ,GOO_WALLPAPER_COLOR=color,GOO_WALLPAPER_BOTTOM=bottom),stdout=log,stderr=log,
                       start_new_session=True)
    clients.append(p); wallpapers.append(p); time.sleep(1)
    return p

def spawn(title, x, y, animation=False):
    command=['python3','-u','-c',"import time\nwhile True:\n print('\\033]11;#00ff00\\007',end='',flush=True); time.sleep(.1)\n print('\\033]11;#ff00ff\\007',end='',flush=True); time.sleep(.1)"] if animation else ['sleep','600']
    p=subprocess.Popen(['foot','-c','/dev/null','-T',title,*command],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    clients.append(p)
    for _ in range(100):
        if any(v['title']==title for v in views()): break
        time.sleep(.05)
    v=next(v for v in views() if v['title']==title)
    ipc('window-rules/configure-view',{'id':v['id'],'geometry':{'x':x,'y':y,'width':320,'height':200}})
    time.sleep(.5)
    return v['id']

try:
    ipc('wayfire/set-config-options',{'output:HEADLESS-1/mode':'1280x720@60000'})
    options(center_width=90,rail_width=0,min_scale=1,max_scale=1,scale_curve='0:1 1:1',
            goo_noise=0,goo_drift=0,goo_wave_height=0,goo_swirl=0,goo_soak=0)
    red=wallpaper('#e02020','#2020e0')
    spawn('depth-front',270,240)
    ipc('stipc/move_cursor',{'x':400,'y':300})
    for mode in ('press','release'): ipc('stipc/feed_button',{'combo':'BTN_LEFT','mode':mode})
    ipc('stipc/move_cursor',{'x':20,'y':20})
    check('zero soak settles',settle()); neutral=dye()
    shot('01-zero-soak')
    options(goo_soak=1)
    check('full soak settles',settle()); red_dye=dye(); shot('02-red-soak')
    from gi.repository import GdkPixbuf
    def pixel(name, x, y):
        pix=GdkPixbuf.Pixbuf.new_from_file(str(art/(name+'.png')))
        data=pix.get_pixels(); at=y*pix.get_rowstride()+x*pix.get_n_channels()
        return list(data[at:at+3])
    check('goo remains visible after a wallpaper maps',
          sum(abs(a-b) for a,b in zip(pixel('02-red-soak',400,234),pixel('02-red-soak',400,100)))>30
          and sum(abs(a-b) for a,b in zip(pixel('02-red-soak',400,446),pixel('02-red-soak',400,500)))>30)
    # (Both samples 6 pt out from the wall. GO28: on blue paper the focused goo's blue mixes
    # with the paper's, so at the wall itself it can match the paper.)
    # GO28 retired GO15's state-dominant wall and wall fade: focus now mixes with the paper
    # across the whole band (tests/goo-one-dye-test.py checks that instead).
    check('wallpaper red enters GPU dye history',
          (red_dye[0]-red_dye[2])-(neutral[0]-neutral[2])>.08
          and red_dye[1]<neutral[1]-.04)
    bottom_dye=dye(400,441)
    (art/'watercolor-orientation.json').write_text(json.dumps({'top':red_dye,'bottom':bottom_dye},indent=2))
    # Focus blue may stay dominant on red paper; compare the two wet bands.
    check('wallpaper cache preserves vertical orientation',
          red_dye[0]-red_dye[2] > bottom_dye[0]-bottom_dye[2]+.15
          and bottom_dye[2]>bottom_dye[0]+.1)
    blue=wallpaper('#2020e0'); stop(red)
    time.sleep(.2)
    check('wallpaper replacement wakes simulation',not state()['sleeping'])
    check('replacement wallpaper settles',settle()); blue_dye=dye(); shot('03-blue-soak')
    check('background replacement changes dye hue',
          (red_dye[0]-red_dye[2])-(blue_dye[0]-blue_dye[2])>.12)
    # Put animated content behind the front window's right-hand overlap film.
    spawn('depth-back-animated',500,330,True)
    ipc('stipc/move_cursor',{'x':400,'y':300})
    for mode in ('press','release'): ipc('stipc/feed_button',{'combo':'BTN_LEFT','mode':mode})
    ipc('stipc/move_cursor',{'x':20,'y':20})
    check('animated window contents do not prevent sleep',settle())
    before=state()['steps']; time.sleep(2)
    check('window redraws do not step the simulation',state()['steps']==before)
    # GO28: film now picks up the window beneath it, in bounded pickup coasts; the old
    # wallpaper-only and never-the-window checks are retired (tests/goo-one-dye-test.py).
    shot('04-film-over-animated-content')
    stop(blue)
    # Layer-shell unmap may finish a compositor close transition first.
    for _ in range(30):
        if not state()['sleeping']: break
        time.sleep(.1)
    woke = not state()['sleeping']
    check('removing wallpaper wakes and removes its dye source',settle() and woke)
    removed=dye()
    (art/'watercolor-removal.json').write_text(json.dumps({'neutral':neutral,'blue':blue_dye,'removed':removed},indent=2))
    def color_distance(a,b): return sum(abs(x-y) for x,y in zip(a,b))
    check('removed wallpaper dye returns toward window state',
          color_distance(removed,neutral)<color_distance(blue_dye,neutral)-.02)
    # Pixel evidence of curvature along a straight edge, separated from corners.
    options(goo_soak=0,goo_depth=0); check('flat surface settles',settle()); shot('05-flat')
    options(goo_depth=6); check('rounded surface settles',settle()); shot('06-rounded')
    def pixels(name):
        p=GdkPixbuf.Pixbuf.new_from_file(str(art/(name+'.png')))
        data=p.get_pixels(); stride=p.get_rowstride(); channels=p.get_n_channels()
        return [[sum(data[y*stride+x*channels:y*stride+x*channels+3])/3 for y in range(228,240)] for x in range(350,450)]
    flat=pixels('05-flat'); rounded=pixels('06-rounded')
    delta=[sum(b[i]-a[i] for a,b in zip(flat,rounded))/len(flat) for i in range(12)]
    (art/'straight-edge-lighting.json').write_text(json.dumps(delta))
    check('depth changes lighting across the straight band, not just corners',max(delta)-min(delta)>15)
    options(goo_profile=0); settle(); shot('07-free-bead')
    ctl=json.loads(subprocess.check_output([str(repo/'core/libexec/scottland-ctl'),'get']))
    check('depth/profile/soak exposed by scottland-ctl',all(k in ctl and k not in ctl['unsupported'] for k in ('goo_depth','goo_profile','goo_soak')))
    # A real grab/drop wakes the new surface; it subsequently settles again.
    ipc('stipc/move_cursor',{'x':400,'y':300})
    ipc('stipc/feed_key',{'key':'KEY_LEFTMETA','state':True})
    ipc('stipc/feed_button',{'combo':'BTN_LEFT','mode':'press'})
    for i in range(20): ipc('stipc/move_cursor',{'x':400-i*2,'y':300-i}); time.sleep(.02)
    check('real held drag wakes the surface',not state()['sleeping']); shot('08-held-drag')
    ipc('stipc/feed_button',{'combo':'BTN_LEFT','mode':'release'})
    ipc('stipc/feed_key',{'key':'KEY_LEFTMETA','state':False})
    ipc('stipc/move_cursor',{'x':20,'y':20})
    options(goo_soak=.12,goo_noise=.32,goo_drift=.12,goo_wave_height=.55,goo_swirl=.9,goo_profile=.65)
    wallpaper('')
    check('shipped waves, noise, swirl and colorful-wallpaper soak settle after real drop',settle())
    steps=state()['steps'];time.sleep(1)
    check('settled remains zero steps',state()['steps']==steps)
    # Two neighboring windows make a thicker shared pool/bridge on the same red paper.
    # Put the dragged window back before comparing equal-area straight and
    # bridged liquid; the bridge must lie between two exposed shores.
    front=next(v for v in views() if v['title']=='depth-front')
    ipc('window-rules/configure-view',{'id':front['id'],
        'geometry':{'x':270,'y':240,'width':320,'height':200}})
    time.sleep(.5)
    wallpaper('#e02020'); spawn('wash-neighbor',601,240)
    options(goo_noise=0,goo_drift=0,goo_wave_height=0,goo_swirl=0,goo_soak=0)
    ipc('stipc/move_cursor',{'x':20,'y':20}); settle()
    bridge=(594,320); straight=(400,234)
    check('pooled wash fixture has wet bridge and straight edge',
          state(*bridge)['density']>state(*bridge)['threshold']
          and state(*straight)['density']>state(*straight)['threshold'])
    bare_bridge=dye(*bridge); bare_straight=dye(*straight)
    # The shipped pickup: at full pickup pure red paper saturates both readings (GO28 mixing).
    options(goo_soak=.12); settle()
    wash_bridge=dye(*bridge); wash_straight=dye(*straight)
    def red_pickup(after,before):
        return (after[0]-before[0])-(after[1]-before[1]+after[2]-before[2])*.5
    pickup_bridge=red_pickup(wash_bridge,bare_bridge)
    pickup_straight=red_pickup(wash_straight,bare_straight)
    (art/'watercolor-pool.json').write_text(json.dumps({'bridge':pickup_bridge,'straight':pickup_straight},indent=2))
    check('wallpaper hue is stronger in a pooled bridge',pickup_bridge>pickup_straight+.02)
    shot('09-pooled-watercolor')
finally:
    for p in clients: stop(p)
print(f'{passed} passed, {failed} failed',flush=True)
sys.exit(bool(failed))
