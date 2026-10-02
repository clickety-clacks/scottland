#!/usr/bin/env python3
"""Actual stipc keys, held repeats, GTK clients, scene observations; no configure-view motion."""
import json
import math
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time

out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
sock = socket.socket(socket.AF_UNIX); sock.connect(os.environ['WAYFIRE_SOCKET']); sock.settimeout(5)
passed = failed = 0
clients = []
def exactly(n):
    b = b''
    while len(b) < n:
        chunk = sock.recv(n-len(b))
        if not chunk: raise RuntimeError('compositor disconnected')
        b += chunk
    return b

def ipc(method, data=None):
    b = json.dumps({'method': method, 'data': data or {}}).encode()
    sock.sendall(struct.pack('<I',len(b))+b)
    reply = json.loads(exactly(struct.unpack('<I', exactly(4))[0]))
    if isinstance(reply, dict) and 'error' in reply: raise RuntimeError(reply)
    return reply

def check(ok, name):
    global passed, failed
    passed += bool(ok); failed += not ok
    print(('PASS  ' if ok else 'FAIL  ') + name, flush=True)

def wait_for(fn, timeout=5):
    end = time.monotonic()+timeout
    while time.monotonic()<end:
        value=fn()
        if value: return value
        time.sleep(.025)
    raise RuntimeError('timed out waiting for state')

def key(code,down): ipc('stipc/feed_key',{'key':'KEY_'+code,'state':down})
def tap(code): key(code,True); key(code,False)
def hold(): key('LEFTALT',True); wait_for(lambda: ipc('scottland/hints')['active']); time.sleep(.1)
def release(): key('LEFTALT',False)
def state(name):
    v=next((v for v in ipc('scottland/layout-state')['views'] if v['title']==name),None)
    if v:
        v['geometry']=next(g['geometry'] for g in ipc('window-rules/list-views') if g['id']==v['id'])
    return v

def center(v):
    g=v['geometry']; return (g['x']+g['width']/2,g['y']+g['height']/2)
def near(a,b,eps=1): return math.dist(a,b)<=eps
def launch(name):
    (out/(name+'.keys')).write_text('')
    p=subprocess.Popen(['python3','tests/windowing-key-recorder.py',name,str(out/(name+'.keys'))],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    clients.append(p)
    v=wait_for(lambda:state(name)); time.sleep(.3); return v['id']
def delivered(name): return [json.loads(l) for l in (out/(name+'.keys')).read_text().splitlines()]
def focus(id): ipc('window-rules/focus-view',{'id':id}); time.sleep(.05)
def dock(id):
    hint=next(h['hint'] for h in ipc('scottland/hints')['hints'] if h['window']==id)
    for _ in range(2):
        for c in hint: tap(c.upper())
    wait_for(lambda: next(v for v in ipc('scottland/layout-state')['views'] if v['id']==id)['widgetized'])
    time.sleep(.6)

def choose(id):
    h=next(h for h in ipc('scottland/hints')['hints'] if h['window']==id)
    for c in h['hint']: tap(c.upper())
    time.sleep(.4)
def drag(name,x,y):
    v=state(name); f=v['frame']; cx=f['x']+f['width']/2; cy=f['y']+f['height']/2
    ipc('stipc/move_cursor',{'x':round(cx),'y':round(cy)}); key('LEFTMETA',True)
    ipc('stipc/feed_button',{'combo':'BTN_LEFT','mode':'press'})
    for n in range(1,11):
        ipc('stipc/move_cursor',{'x':round(cx+(x-cx)*n/10),'y':round(cy+(y-cy)*n/10)}); time.sleep(.025)
    ipc('stipc/feed_button',{'combo':'BTN_LEFT','mode':'release'}); key('LEFTMETA',False); time.sleep(.5)
def coast(seconds=1.0): time.sleep(seconds)
def screenshot(name): subprocess.run(['grim',str(out/(name+'.png'))],check=True)

def output_info(id):
    return next(v for v in ipc('window-rules/list-views') if v['id']==id)

def global_center(name):
    v=state(name); info=output_info(v['id'])
    output=next(o for o in ipc('window-rules/list-outputs') if o['id']==info['output-id'])
    x,y=center(v); return x+output['geometry']['x'],y+output['geometry']['y']

def two_outputs():
    ipc('wayfire/set-config-options',{'scottland/sounds':False,'scottland/key_impulse':1200.0,
        'scottland/key_friction':608.0,'scottland/key_restitution':.5,'scottland/alt_hold_delay':100})
    outputs=sorted(ipc('window-rules/list-outputs'),key=lambda o:o['geometry']['x'])
    check(len(outputs)==2,'crossing fixture has two isolated outputs')
    left,right=outputs; lg,rg=left['geometry'],right['geometry']
    check(lg['x']+lg['width']==rg['x'],'two-output edges touch')
    name='InertiaCross'; a=launch(name); focus(a)
    # Keep the starting surface away from rails; the real drag establishes its home output.
    drag(name,lg['width']/2,lg['height']/2)
    original=global_center(name); home=output_info(a)['output-id']
    hold(); tap('RIGHT'); release()
    samples=[]; start=time.monotonic()
    while time.monotonic()-start<2.5:
        samples.append((global_center(name),output_info(a)['output-id'])); time.sleep(.02)
    end=global_center(name)
    check(output_info(a)['output-id']==right['id'],'right arrow crosses the shared edge to the second output')
    check(abs(end[0]-original[0]-1200**2/(2*608))<4,'crossing preserves velocity and analytic global travel')
    check(all(b[0][0]>=a[0][0]-.1 for a,b in zip(samples,samples[1:])),'shared edge does not reflect outward velocity')
    check(abs(end[1]-original[1])<1 and not state(name)['widgetized'],'crossing keeps height and ordinary window form')
    # Return with real input and check the opposite passage too.
    hold(); tap('LEFT'); release(); coast(2.5)
    print('crossing return:',original,global_center(name),output_info(a)['output-id'],home,flush=True)
    check(output_info(a)['output-id']==left['id'] and near(global_center(name),original,4),'left arrow crosses back without bounce or lost distance')
    # Esc must restore both geometry and output from Alt-down, not just local coordinates.
    hold(); tap('RIGHT'); wait_for(lambda:output_info(a)['output-id']==right['id']); tap('ESC'); coast(.8); release()
    print('crossing cancel:',original,global_center(name),output_info(a)['output-id'],home,flush=True)
    check(output_info(a)['output-id']==home and near(global_center(name),original,2),'Esc restores the starting output after a keyboard crossing')
    screenshot('two-output-crossing-restored')

try:
    if '--two-outputs' in sys.argv:
        two_outputs()
    else:
        ipc('wayfire/set-config-options',{'scottland/sounds':False,'scottland/alt_hold_delay':300,
            'scottland/key_impulse':335.0,'scottland/key_friction':608.0,'scottland/key_max_velocity':6000.0,
            'input/kb_repeat_delay':300,'input/kb_repeat_rate':10})
        output=ipc('window-rules/list-outputs')[0]['geometry']; w,h=output['width'],output['height']
        distance=335**2/(2*608)
        a=launch('InertiaA'); focus(a); drag('InertiaA',w/2,h/2)
        original=center(state('InertiaA')); hold(); tap('RIGHT'); time.sleep(.12); intermediate=center(state('InertiaA')); release(); coast()
        end=center(state('InertiaA'))
        check(abs(end[0]-original[0]-distance)<1,'single impulse travels v²/(2a) after Alt release')
        check(original[0]<intermediate[0]<end[0],'Alt release lets in-flight coast finish')
        check(abs(end[1]-original[1])<.1,'horizontal impulse leaves vertical axis alone')
        check(not any(e['key']=='Right' for e in delivered('InertiaA')),'window-mode arrow is consumed')
        unchanged=end; coast(.3); check(near(center(state('InertiaA')),unchanged,.05),'window stays still after velocity reaches zero')

        drag('InertiaA',w/2,h/2); original=center(state('InertiaA')); hold(); tap('RIGHT'); time.sleep(.1); tap('RIGHT'); release(); coast(1.5)
        expected=335*.1-608*.1*.1/2+(2*335-608*.1)**2/(2*608)
        actual=center(state('InertiaA'))[0]-original[0]
        check(abs(actual-expected)<8 and actual>2*distance,'repeated physical presses accumulate velocity')

        drag('InertiaA',w/2,h/2); original=center(state('InertiaA')); hold(); key('RIGHT',True); time.sleep(.51); key('RIGHT',False); release(); coast(2)
        actual=center(state('InertiaA'))[0]-original[0]
        check(actual>2*distance,'held arrow adds keyboard auto-repeat impulses')

        drag('InertiaA',w/2,h/2); original=center(state('InertiaA')); hold(); tap('LEFT'); tap('UP'); release(); coast()
        end=center(state('InertiaA'))
        check(abs(original[0]-end[0]-distance)<1 and abs(original[1]-end[1]-distance)<1,'two arrow axes coast diagonally and independently')

        pad=32/3+5
        ipc('wayfire/set-config-options',{'scottland/key_impulse':1000.0,'scottland/key_restitution':.5})
        for code,axis,sign in [('RIGHT',0,1),('LEFT',0,-1),('UP',1,-1),('DOWN',1,1)]:
            drag('InertiaA',w/2,h/2); hold(); tap(code); release()
            samples=[]; start=time.monotonic()
            while time.monotonic()-start<2.2:
                samples.append(state('InertiaA')); time.sleep(.015)
            positions=[center(v)[axis] for v in samples]
            check(any((b-a)*sign < -.1 for a,b in zip(positions,positions[1:])),code+' reverses outward velocity at the boundary')
            check(all(v['frame']['y']>=math.ceil(pad)-1 and v['frame']['y']+v['frame']['height']<=h-math.ceil(pad)+1 for v in samples),code+' stays inside vertical WP7 padding')
            check(all(v['frame']['x']>=max(w*.02,math.ceil(pad))-1.5 and v['frame']['x']+v['frame']['width']<=w-max(w*.02,math.ceil(pad))+1.5 for v in samples),code+' keeps its scaled footprint off exposed rails')
            check(not state('InertiaA')['widgetized'],code+' bounce never widgetizes')
        end=center(state('InertiaA')); coast(.4)
        check(near(end,center(state('InertiaA')),.05),'friction settles the bounced coast')
        screenshot('rail-boundary')
        drag('InertiaA',w/2,h/2)
        ipc('wayfire/set-config-options',{'scottland/key_restitution':0.0})
        hold(); tap('UP'); release(); coast(2)
        check(abs(state('InertiaA')['frame']['y']-math.ceil(pad))<1,'live zero restitution stops at the padded edge')
        ipc('wayfire/set-config-options',{'scottland/key_impulse':335.0,'scottland/key_restitution':.5})

        drag('InertiaA',w/2,h/2); hold(); key('LEFT',True)
        samples=[]
        start=time.monotonic()
        while time.monotonic()-start<.7:
            v=state('InertiaA'); samples.append(v)
            time.sleep(.01)
        key('LEFT',False); release(); coast(1.5)
        check(any(v['applied_scale']<.9 for v in samples),'coast rescales live when it crosses a zone')
        check(all(abs(v['applied_scale']-v['scale'])<.015 for v in samples),'coasting scale follows the actual center, including while Alt is held')
        (out/'zone-samples.json').write_text(json.dumps(samples))

        drag('InertiaA',w/2,h/2); before=state('InertiaA'); origin=center(before)
        hold(); key('LEFTCTRL',True); tap('RIGHT'); tap('UP'); key('LEFTCTRL',False); release(); coast()
        after=state('InertiaA')
        check(abs(after['geometry']['width']-before['geometry']['width']-distance)<2,'Ctrl+Right inertially widens by the impulse distance')
        check(abs(after['geometry']['height']-before['geometry']['height']-distance)<2,'Ctrl+Up inertially makes the window taller')
        check(near(center(after),origin),'resize preserves center across asynchronous client commits')
        hold(); key('LEFTCTRL',True); tap('LEFT'); tap('DOWN'); key('LEFTCTRL',False); release(); coast()
        after=state('InertiaA')
        check(abs(after['geometry']['width']-before['geometry']['width'])<2 and abs(after['geometry']['height']-before['geometry']['height'])<2,'Ctrl+Left and Ctrl+Down shrink the window')
        check(near(center(after),origin),'shrinking also preserves center')

        hold(); key('LEFTCTRL',True); key('RIGHT',True); key('UP',True); time.sleep(2); key('RIGHT',False); key('UP',False); key('LEFTCTRL',False); release(); coast(2)
        after=state('InertiaA')
        check(after['geometry']['width']<=w-2*pad and after['geometry']['height']<=h-2*pad,'resize maximum is screen minus padding')
        check(near(center(after),origin),'maximum resize remains centered')
        screenshot('maximum-resize')
        hold(); key('LEFTCTRL',True); key('LEFT',True); key('DOWN',True); time.sleep(2); key('LEFT',False); key('DOWN',False); key('LEFTCTRL',False); release(); coast(2)
        after=state('InertiaA')
        check(after['geometry']['width']>1 and after['geometry']['height']>1,'resize respects GTK app minimum size')
        print('minimum resize centers:',origin,center(after),after['geometry'],flush=True)
        check(near(center(after),origin),'minimum resize remains centered')

        # Only keyboard resizing recovers an overflowing footprint; a pointer drop stays exact.
        drag('InertiaA',w/2,40); dropped=center(state('InertiaA')); coast(.6)
        check(near(dropped,center(state('InertiaA')),.05),'mouse drop is not corrected by keyboard boundaries')
        drag('InertiaA',w/2,state('InertiaA')['geometry']['height']/2+math.ceil(pad)+10); before=state('InertiaA'); origin=center(before)
        hold(); key('LEFTCTRL',True); tap('UP'); key('LEFTCTRL',False); release(); coast(1.3)
        after=state('InertiaA')
        check(after['frame']['y']>=math.ceil(pad)-1,'keyboard resize pushes overflowing content back inside WP7 padding')
        check(center(after)[1]>origin[1] and abs(center(after)[0]-origin[0])<=1,'resize boundary recovery changes only the overflowing center axis')

        drag('InertiaA',w/2,h/2); before=state('InertiaA'); hold(); tap('RIGHT'); time.sleep(.1); tap('ESC'); coast(.6)
        check(near(center(state('InertiaA')),center(before)),'Esc restores the position captured at Alt-down')
        tap('LEFT'); coast(.6); check(near(center(state('InertiaA')),center(before)),'cancelled hold consumes arrows without another action'); release()
        before=state('InertiaA'); hold(); key('LEFTCTRL',True); tap('RIGHT'); tap('UP'); time.sleep(.1); tap('ESC'); key('LEFTCTRL',False); coast(.7); release()
        after=state('InertiaA')
        check(after['geometry']['width']==before['geometry']['width'] and after['geometry']['height']==before['geometry']['height'] and near(center(after),center(before)),'Esc restores size and center as well as position')

        before=len(delivered('InertiaA')); key('LEFTALT',True); tap('RIGHT'); time.sleep(.4)
        check(not ipc('scottland/hints')['active'],'quick Alt+arrow bypasses window mode'); release(); coast(.1)
        check(any(e['key']=='Right' and e['modifiers']&8 for e in delivered('InertiaA')[before:]),'quick Alt+arrow reaches the app with Alt intact')
        focus(a); ipc('scottland/key-layer',{'action':'set','window':a,'keys':['8:Right','12:Up']})
        before=state('InertiaA'); count=len(delivered('InertiaA')); hold(); tap('RIGHT'); key('LEFTCTRL',True); tap('UP'); key('LEFTCTRL',False); coast(.7)
        after=state('InertiaA')
        check(near(center(before),center(after)) and before['geometry']==after['geometry'],'KL7 claimed movement and resize arrows leave geometry alone')
        check(any(e['key']=='Right' for e in delivered('InertiaA')[count:]) and any(e['key']=='Up' for e in delivered('InertiaA')[count:]),'KL7 claimed arrows reach the focused surface in window mode')
        release(); ipc('scottland/key-layer',{'action':'clear','window':a})

        # Settings are live plugin options, including the speed cap.
        ipc('wayfire/set-config-options',{'scottland/key_impulse':120.0,'scottland/key_friction':240.0,'scottland/key_max_velocity':100.0})
        before=center(state('InertiaA')); hold(); tap('RIGHT'); release(); coast()
        check(abs(center(state('InertiaA'))[0]-before[0]-100**2/(2*240))<1,'live impulse/friction/maximum settings control the coast')
        ipc('wayfire/set-config-options',{'scottland/key_impulse':335.0,'scottland/key_friction':608.0,'scottland/key_max_velocity':6000.0})
        # L20 rounds pixel positions for odd client sizes; it must not drift after settling.
        ipc('wayfire/set-config-options',{'scottland/key_impulse':(2*608*91)**.5})
        before=state('InertiaA'); hold(); key('LEFTCTRL',True); tap('RIGHT'); key('LEFTCTRL',False); release(); coast()
        after=state('InertiaA')
        print('odd resize centers:',center(before),center(after),before['geometry'],after['geometry'],flush=True)
        coast(.5); settled=state('InertiaA')
        check(near(center(before),center(after),1) and abs(after['geometry']['width']-before['geometry']['width']-91)<1 and after['geometry']==settled['geometry'],'odd-size resize keeps its center within L20 rounding and stays settled')
        ipc('wayfire/set-config-options',{'scottland/key_impulse':335.0})
        # An arrow is an explicit movement request; wait for fullscreen exit geometry first.
        ipc('wm-actions/set-fullscreen',{'view_id':a,'state':True}); coast(.4)
        hold(); tap('RIGHT'); release(); coast(1.2)
        check(not next(v for v in ipc('window-rules/list-views') if v['id']==a)['fullscreen'],'arrow explicitly leaves fullscreen before moving')
        ipc('wm-actions/set-fullscreen',{'view_id':a,'state':True}); coast(.4)
        hold(); tap('LEFT'); coast(.2); tap('ESC'); coast(.5); release()
        check(next(v for v in ipc('window-rules/list-views') if v['id']==a)['fullscreen'],'Esc restores a fullscreen Alt-down origin')
        ipc('wm-actions/set-fullscreen',{'view_id':a,'state':False}); coast(.4)

        b=launch('InertiaB'); focus(a); hold(); choose(b); before=center(state('InertiaB')); tap('RIGHT'); release(); coast()
        check(abs(center(state('InertiaB'))[0]-before[0]-distance)<1,'hint-selected window receives subsequent impulses')
        focus(a); hold(); focus(b); before=center(state('InertiaB')); tap('LEFT'); release(); coast()
        check(abs(before[0]-center(state('InertiaB'))[0]-distance)<1,'without a selection the currently focused window is the target')

        focus(b); hold(); choose(b); focus(a); before=center(state('InertiaB')); other=center(state('InertiaA'))
        tap('DOWN'); release(); coast()
        check(abs(center(state('InertiaB'))[1]-before[1]-distance)<1 and near(center(state('InertiaA')),other,.05),
            'a hint that skips redundant select still owns the arrow target after focus changes')
        drag('InertiaB',w/2,h/2)

        # Cancellation uses Alt-down, even if the first arrow follows a hint cycle.
        focus(b); before=center(state('InertiaB')); hold(); choose(b); choose(b); tap('DOWN'); coast(.2); tap('ESC'); coast(.6); release()
        check(near(center(state('InertiaB')),before,2),'Esc after a cycle and an arrow restores Alt-down rather than arrow-down')
        focus(b); before=state('InertiaB'); hold(); choose(b); key('LEFTCTRL',True); tap('RIGHT'); key('LEFTCTRL',False); coast(.65)
        dock(b); wait_for(lambda:state('InertiaB')['widgetized']); coast(.3); tap('DOWN'); coast(.2); tap('ESC'); coast(.8); release()
        after=state('InertiaB')
        check(not after['widgetized'] and near(center(after),center(before),2) and after['geometry']['width']==before['geometry']['width'],'Esc restores original window size after resize, dock cycle and widget motion')
        focus(b); hold(); dock(b); release(); wait_for(lambda:state('InertiaB')['widgetized']); coast(.8)
        def card():
            link=next(w for w in ipc('scottland/widgets')['widgets'] if int(w['id'])==b)
            return next(v for v in ipc('window-rules/list-views') if v['id']==link['widget_view'])
        def cc(): return center({'geometry':card()['geometry']})
        widget=card(); focus(widget['id']); before=cc(); hold(); tap('DOWN'); release(); coast()
        check(abs(cc()[1]-before[1]-distance)<2,'widget arrows coast along the rail')
        origin=cc(); hold(); key('LEFTCTRL',True); tap('RIGHT'); tap('UP'); key('LEFTCTRL',False); release(); coast()
        check(near(cc(),origin,.1) and state('InertiaB')['widgetized'],'Ctrl+arrows do nothing for widgets')
        rail=next(w['rail'] for w in ipc('scottland/widgets')['widgets'] if int(w['id'])==b)
        hold(); tap('RIGHT' if rail=='left' else 'LEFT'); coast(.4)
        newrail=next(w['rail'] for w in ipc('scottland/widgets')['widgets'] if int(w['id'])==b)
        check(newrail!=rail and abs(cc()[1]-origin[1])<2 and state('InertiaB')['widgetized'],'horizontal widget arrow changes rails and keeps height and form')
        tap('ESC'); coast(.6); release()
        check(next(w['rail'] for w in ipc('scottland/widgets')['widgets'] if int(w['id'])==b)==rail and near(cc(),origin,2),'Esc restores widget rail and position')
        origin=cc(); hold(); choose(b); tap('RIGHT'); coast(.15); tap('ESC'); coast(.8); release()
        wait_for(lambda:state('InertiaB')['widgetized']); coast(.5)
        check(state('InertiaB')['widgetized'] and near(cc(),origin,2),'Esc restores original widget form after hint-open and arrow movement')
        screenshot('widget-restored')
except Exception as e:
    check(False,'suite exception: '+repr(e))
finally:
    print(f'{passed} passed, {failed} failed',flush=True)
    for p in clients:
        p.terminate() # only PIDs returned by our own Popen
        try: p.wait(timeout=3)
        except subprocess.TimeoutExpired: p.kill(); p.wait()
sys.exit(bool(failed))
