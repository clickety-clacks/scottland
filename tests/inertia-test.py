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

def redock(id):
    if state('InertiaB')['widgetized']:
        hold()
        if ipc('scottland/hints')['selected'] == id:
            choose(id)
        else:
            choose(id); choose(id)
        release()
        wait_for(lambda: not state('InertiaB')['widgetized'])
    hold(); dock(id); release()
    wait_for(lambda:state('InertiaB')['widgetized']); coast(.4)

def choose(id):
    h=next(h for h in ipc('scottland/hints')['hints'] if h['window']==id)
    for c in h['hint']: tap(c.upper())
    time.sleep(.4)
def drag(name,x,y):
    v=state(name)
    if v['widgetized'] or not 0 < center(v)[1] < ipc('window-rules/list-outputs')[0]['geometry']['height']:
        ipc('scottland/present', {'window':v['id']}); coast(.7); v=state(name)
    f=v['frame']; cx=f['x']+f['width']/2; cy=f['y']+f['height']/2
    ipc('stipc/move_cursor',{'x':round(cx),'y':round(cy)}); key('LEFTMETA',True)
    ipc('stipc/feed_button',{'combo':'BTN_LEFT','mode':'press'})
    for n in range(1,11):
        ipc('stipc/move_cursor',{'x':round(cx+(x-cx)*n/10),'y':round(cy+(y-cy)*n/10)}); time.sleep(.025)
    time.sleep(.12)  # fixture placement is a deliberate stop, not a flick
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
        'scottland/key_friction':608.0,'scottland/alt_hold_delay':100})
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
    # An explicit keyboard crossing remains committed when Esc stops any remaining coast.
    hold(); tap('RIGHT'); wait_for(lambda:output_info(a)['output-id']==right['id']); tap('ESC'); coast(.8); release()
    print('crossing cancel:',original,global_center(name),output_info(a)['output-id'],home,flush=True)
    check(output_info(a)['output-id']==right['id'] and math.dist(global_center(name),original)>20,
          'Esc keeps an explicit cross-output move at its current position')
    screenshot('two-output-crossing-kept')

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
        ipc('wayfire/set-config-options',{'scottland/key_impulse':1000.0})
        for code,sign in [('UP',-1),('DOWN',1)]:
            drag('InertiaA',w/2,h/2); hold(); tap(code); release()
            samples=[]; start=time.monotonic()
            while time.monotonic()-start<2.2:
                samples.append(state('InertiaA')); time.sleep(.015)
            positions=[center(v)[1] for v in samples]
            check(all((b-a)*sign >= -.1 for a,b in zip(positions,positions[1:])),code+' stops without reversing')
            f=samples[-1]['frame']
            visible=min(h,f['y']+f['height'])-max(0,f['y'])
            check(abs(visible-100)<1.1,code+' leaves 100 logical pt of scaled footprint visible')
            end=center(samples[-1]); coast(.3)
            check(near(end,center(state('InertiaA')),.05),code+' remains stopped')
            screenshot(code.lower()+'-100pt')
            (out/(code.lower()+'-samples.json')).write_text(json.dumps(samples))
        # The remaining strip is measured after scaling, and only y stops.
        for code in ('UP','DOWN'):
            drag('InertiaA',w*.2,h/2); hold(); tap(code); release(); coast(2)
            v=state('InertiaA'); f=v['frame']
            visible=min(h,f['y']+f['height'])-max(0,f['y'])
            check(v['applied_scale']<.9 and abs(visible-min(100,f['height']))<1.1,code+' measures visible strip in scaled logical points')
        drag('InertiaA',w/2,h/2); hold(); tap('UP'); release(); coast(2)
        origin=center(state('InertiaA'))
        ipc('wayfire/set-config-options',{'scottland/key_impulse':335.0})
        hold(); tap('RIGHT'); tap('UP'); release(); coast()
        check(abs(center(state('InertiaA'))[0]-origin[0]-distance)<1 and abs(center(state('InertiaA'))[1]-origin[1])<1,'vertical contact zeros only y; horizontal coast continues')
        ipc('wayfire/set-config-options',{'scottland/key_impulse':1000.0})
        for code,rail in [('LEFT','left'),('RIGHT','right')]:
            drag('InertiaA',w/2,h/2); hold(); tap(code); release()
            samples=[]; start=time.monotonic()
            while time.monotonic()-start<8:
                sample=ipc('scottland/layout-state'); samples.append(sample)
                if time.monotonic()-start>=2.2 and sample['widget_transition_count']==0 and any(v['widget'] for v in sample['views']): break
                time.sleep(.008)
            contact=next(v for sample in samples for v in sample['views'] if v['id']==a and v['widgetized'])
            f=contact.get('scene_frame',contact['frame'])
            edge=f['x'] if rail=='left' else f['x']+f['width']
            check(abs(edge-(w*.02 if rail=='left' else w*.98))<2,code+' begins morph at scaled footprint contact with the rail')
            links=ipc('scottland/widgets')['widgets']
            check(len([v for v in links if int(v['id'])==a and v['rail']==rail])==1,code+' widgetizes once onto matching rail')
            cards=[v for sample in samples for v in sample['views'] if v['widget'] and v['frame'].get('presentation')]
            check(len(cards)>=3 and len({round(v['frame']['width'],1) for v in cards})>=3,code+' has intermediate WG22 morph frames')
            check(all(any(not v['hidden'] and (v['id']==a or v['widget']) for v in sample['views']) for sample in samples),code+' retains a visible representation throughout handoff')
            (out/(code.lower()+'-morph.json')).write_text(json.dumps(samples))
            screenshot(code.lower()+'-rail')
        drag('InertiaA',w*.05,h/2); origin=center(state('InertiaA'))
        check(not state('InertiaA')['widgetized'],'precise pointer drop may already overlap the rail')
        hold(); tap('LEFT'); wait_for(lambda:state('InertiaA')['widgetized'])
        contact=state('InertiaA'); f=contact.get('scene_frame',contact['frame'])
        check(abs(f['x']+f['width']/2-origin[0])<2,'outward push from an overlapping drop morphs without snapping inward')
        tap('ESC'); coast(.8); release()
        # Esc stops the coast, while the explicit push's widget landing remains.
        drag('InertiaA',w/2,h/2); origin=center(state('InertiaA')); hold(); tap('LEFT'); coast(1.5); tap('ESC'); coast(.8); release()
        check(state('InertiaA')['widgetized'] and not near(center(state('InertiaA')),origin,2),
              'Esc keeps an explicit push that widgetized the window')
        drag('InertiaA',w/2,h/2); origin=center(state('InertiaA')); hold(); tap('RIGHT')
        wait_for(lambda:state('InertiaA')['widgetized']); tap('ESC'); coast(.04)
        returning=state('InertiaA'); f=returning.get('scene_frame',returning['frame'])
        check(returning['widgetized'],'Esc during widget startup keeps the explicit rail arrival')
        coast(.8); release()
        check(state('InertiaA')['widgetized'] and not near(center(state('InertiaA')),origin,2),
              'Esc does not undo an explicit widget landing')
        ipc('wayfire/set-config-options',{'scottland/key_impulse':335.0})

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

        drag('InertiaA',w*.37,h/2); initial=state('InertiaA')['applied_scale']
        hold(); key('LEFTSHIFT',True); tap('LEFT'); key('LEFTSHIFT',False); release(); coast(.8)
        shifted=state('InertiaA')
        check(shifted['zone']=='continuous' and abs(shifted['applied_scale']-initial)<.015,
              'Shift+arrow keeps scale through periphery coast')
        hold(); tap('LEFT'); release(); coast(.8)
        check(abs(state('InertiaA')['applied_scale']-state('InertiaA')['scale'])<.015,
              'plain arrow clears Shift scale pin and follows zone')

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
        shift_before=state('InertiaA')
        hold(); key('LEFTSHIFT',True); key('LEFTCTRL',True); tap('RIGHT')
        key('LEFTCTRL',False); key('LEFTSHIFT',False); release(); coast()
        check(state('InertiaA')['geometry']['width']>shift_before['geometry']['width']+50,
              'Ctrl+Shift+Right still resizes instead of scale-locking movement')

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
        stopped=center(state('InertiaA'))
        check(math.dist(stopped,center(before))>5,'Esc keeps the arrow movement and stops its remaining coast')
        tap('LEFT'); coast(.6); check(near(center(state('InertiaA')),stopped),'cancelled hold consumes arrows without another action'); release()
        before=state('InertiaA'); hold(); key('LEFTCTRL',True); tap('RIGHT'); tap('UP'); time.sleep(.1); tap('ESC'); key('LEFTCTRL',False); coast(.7); release()
        after=state('InertiaA')
        print('Esc resize:',before['geometry'],after['geometry'],flush=True)
        check(after['geometry']['width']>before['geometry']['width'] and after['geometry']['height']>before['geometry']['height'] and near(center(after),center(before),3),
              'Esc keeps explicit keyboard resize results at the same center')

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
        ipc('wayfire/set-config-options',{'scottland/resize_impulse':(2*608*91)**.5})
        before=state('InertiaA'); hold(); key('LEFTCTRL',True); tap('RIGHT'); key('LEFTCTRL',False); release(); coast()
        after=state('InertiaA')
        print('odd resize centers:',center(before),center(after),before['geometry'],after['geometry'],flush=True)
        coast(.5); settled=state('InertiaA')
        check(near(center(before),center(after),1) and abs(after['geometry']['width']-before['geometry']['width']-91)<1 and after['geometry']==settled['geometry'],'odd-size resize keeps its center within L20 rounding and stays settled')
        ipc('wayfire/set-config-options',{'scottland/resize_impulse':335.0})
        # An arrow is an explicit movement request; wait for fullscreen exit geometry first.
        ipc('wm-actions/set-fullscreen',{'view_id':a,'state':True}); coast(.4)
        hold(); tap('RIGHT'); release(); coast(1.2)
        check(not next(v for v in ipc('window-rules/list-views') if v['id']==a)['fullscreen'],'arrow explicitly leaves fullscreen before moving')
        ipc('wm-actions/set-fullscreen',{'view_id':a,'state':True}); coast(.4)
        hold(); tap('LEFT'); coast(.2); tap('ESC'); coast(.5); release()
        check(not next(v for v in ipc('window-rules/list-views') if v['id']==a)['fullscreen'],
              'Esc keeps the explicit move after its fullscreen exit')
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

        # Hint cycle placement and arrow movement both remain explicit user moves.
        focus(b); before=center(state('InertiaB')); hold(); choose(b); choose(b); tap('DOWN'); coast(.2); tap('ESC'); coast(.6); release()
        print('cycle cancel:',before,center(state('InertiaB')),flush=True)
        check(not near(center(state('InertiaB')),before,2),'Esc keeps the zone cycle and arrow movement')
        focus(b); before=state('InertiaB'); hold(); choose(b); key('LEFTCTRL',True); tap('RIGHT'); key('LEFTCTRL',False); coast(.65)
        dock(b); wait_for(lambda:state('InertiaB')['widgetized']); coast(.3); tap('DOWN'); coast(.2); tap('ESC'); coast(.8); release()
        after=state('InertiaB')
        print('resize dock cancel:',before['geometry'],after['geometry'],after['widgetized'],flush=True)
        check(after['widgetized'] and after['geometry']['width']>before['geometry']['width'],
              'Esc keeps the explicit resize, rail cycle and widget movement')
        focus(b); redock(b); coast(.8)
        def card():
            link=next(w for w in ipc('scottland/widgets')['widgets'] if int(w['id'])==b)
            return next(v for v in ipc('window-rules/list-views') if v['id']==link['widget_view'])
        def cc(): return center({'geometry':card()['geometry']})
        widget=card(); focus(widget['id']); before=cc()
        vertical='DOWN' if before[1] < h/2 else 'UP'; direction=1 if vertical=='DOWN' else -1
        hold(); tap(vertical); release(); coast()
        check(abs(cc()[1]-before[1]-direction*distance)<2,'widget arrows coast along the rail away from its nearest end')
        origin=cc(); hold(); key('LEFTCTRL',True); tap('RIGHT'); tap('UP'); key('LEFTCTRL',False); release(); coast()
        check(near(cc(),origin,.1) and state('InertiaB')['widgetized'],'Ctrl+arrows do nothing for widgets')
        rail=next(w['rail'] for w in ipc('scottland/widgets')['widgets'] if int(w['id'])==b)
        hold(); tap('RIGHT' if rail=='left' else 'LEFT'); coast(.6)
        undocked=state('InertiaB')
        check(not undocked['widgetized'] and undocked['zone']=='continuous' and
              (center(undocked)[0]<w/2 if rail=='left' else center(undocked)[0]>w/2),
              'away arrow undocks and coasts into the same-side periphery')
        tap('ESC'); coast(.6); release()
        stopped=state('InertiaB')
        check(not stopped['widgetized'] and stopped['zone']=='continuous' and
              (center(stopped)[0]<w/2 if rail=='left' else center(stopped)[0]>w/2),
              'Esc keeps the explicit widget undock in its same-side periphery')
        ipc('wayfire/set-config-options',{'scottland/key_impulse':1500.0})
        redock(b)
        hold(); tap('RIGHT' if rail=='left' else 'LEFT'); coast(1.2)
        fast=state('InertiaB')
        check(not fast['widgetized'] and fast['zone']=='continuous' and
              (center(fast)[0]<w/2 if rail=='left' else center(fast)[0]>w/2),
              'strong away impulse stops within its starting periphery, never on opposite rail')
        tap('ESC'); coast(.8); release()
        check(not state('InertiaB')['widgetized'] and state('InertiaB')['zone']=='continuous',
              'Esc keeps a strong explicit push in the periphery')
        ipc('wayfire/set-config-options',{'scottland/key_impulse':335.0})
        redock(b)
        drag('InertiaB',6,h*.56)
        wait_for(lambda:state('InertiaB')['widgetized'] and any(v['widget'] for v in ipc('scottland/layout-state')['views']))
        coast(.3); left_origin=cc()
        check(next(w['rail'] for w in ipc('scottland/widgets')['widgets'] if int(w['id'])==b)=='left',
              'real drag establishes the left-rail widget fixture')
        hold(); tap('RIGHT'); coast(.6)
        from_left=state('InertiaB')
        check(not from_left['widgetized'] and from_left['zone']=='continuous' and center(from_left)[0]<w/2,
              'right arrow undocks a left-rail widget into the left periphery')
        tap('ESC'); coast(.8); release()
        after_left=state('InertiaB')
        check(not after_left['widgetized'] and after_left['zone']=='continuous' and center(after_left)[0]<w/2,
              'Esc keeps a left-rail widget arrow move in the left periphery')
        redock(b)
        focus(a); hold(); choose(b); tap('RIGHT'); coast(.15); tap('ESC'); coast(.8); release()
        after_hint_move=state('InertiaB')
        check(not after_hint_move['widgetized'] and after_hint_move['zone']=='continuous' and center(after_hint_move)[0]<w/2,
              'Esc keeps a widget hint selection followed by an explicit arrow move')
        screenshot('widget-move-kept')
except Exception as e:
    check(False,'suite exception: '+repr(e))
finally:
    print(f'{passed} passed, {failed} failed',flush=True)
    for p in clients:
        p.terminate() # only PIDs returned by our own Popen
        try: p.wait(timeout=3)
        except subprocess.TimeoutExpired: p.kill(); p.wait()
sys.exit(bool(failed))
