#!/usr/bin/env python3
"""WK15/16: physical hint presses with human dwell and repeat timing.

A double-tap is two final-key releases within the interval (Mike, 2026-10-07). A repeat pressed
inside the interval but released after it is an ordinary press, never the rail."""
import json
from itertools import product
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time

assert os.environ.get('SCOTTLAND_TEST_MODEL') == '1'
art = Path(sys.argv[1]).resolve()
art.mkdir(parents=True, exist_ok=True)
sock = socket.socket(socket.AF_UNIX)
sock.settimeout(15)
sock.connect(os.environ['WAYFIRE_SOCKET'])

def ipc(method, data=None):
    body = json.dumps({'method': method, 'data': data or {}}).encode()
    sock.sendall(struct.pack('<I',len(body))+body)
    def read(n):
        out=b''
        while len(out)<n:
            chunk=sock.recv(n-len(out))
            if not chunk: raise RuntimeError('compositor disconnected')
            out+=chunk
        return out
    reply=json.loads(read(struct.unpack('<I',read(4))[0]))
    if 'error' in reply: raise RuntimeError(reply)
    return reply

def wait(predicate, timeout=10):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        result=predicate()
        if result: return result
        time.sleep(.02)
    raise RuntimeError('state timeout')

def views(): return ipc('scottland/layout-state')['views']
def view(id): return next(v for v in views() if v['id']==id)
def hints(): return ipc('scottland/hints')
def label(id): return next(h['hint'] for h in hints()['hints'] if h['window']==id)
def geometry(id): return next(v['geometry'] for v in ipc('window-rules/list-views') if v['id']==id)
def key(code, down): return ipc('stipc/feed_key',{'key':'KEY_'+code,'state':down})
def sleep_until(at): time.sleep(max(0,at-time.monotonic()))

events=[]
def sequence(text, dwell):
    for index, letter in enumerate(text):
        if index: time.sleep(.02)
        start=time.monotonic()
        key(letter.upper(),True)
        events.append({'letter':letter,'down':True,'time':start})
        sleep_until(start+dwell)
        key(letter.upper(),False)
        events.append({'letter':letter,'down':False,'time':time.monotonic()})

clients=[]
results=[]
try:
    ipc('wayfire/set-config-options',{'output:HEADLESS-1/mode':'1600x1000@60000'})
    def launch(index):
        title='DoubleTap'+str(index)
        clients.append(subprocess.Popen(['foot','--app-id=scottland-double-tap','--title='+title,
            'sh','-c','sleep 600'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL))
        v=wait(lambda: next((v for v in views() if v['title']==title),None))
        # Setup only; the hint behavior is exclusively real key input.
        ipc('window-rules/configure-view',{'id':v['id'],'geometry':
            {'x':500+index%4*30,'y':350+index%5*25,'width':360,'height':240}})
        return v['id']
    primary=launch(0)
    other=launch(1)
    for width in (1,2):
        if width==2:
            for index in range(2,27): overflow=launch(index)
        targets=(primary,) if width==1 or '--quick' in sys.argv else (primary,overflow)
        # (timing, interval, dwell, double-tap expected): 'press' spaces the complete hints'
        # final-key presses, 'release' and 'late' their final-key releases.
        cases=list(product(targets,(False,True),(True,False),
            [('press',.18,.06,True),('press',.25,.10,True),('release',.20,.08,True),
             ('release',.26,.10,True),('late',.37,.12,False)]))
        # Focused, the first press already steps to periphery and a slow second press reaches
        # the widget anyway (WK7), so only an unfocused target tells a late repeat apart.
        cases=[c for c in cases if c[3][0]!='late' or not c[2]]
        if '--quick' in sys.argv:
            cases=[c for c in cases if not c[2] and c[3][0]=='release']
        if '--guards-only' in sys.argv: cases=[]
        for target, always, focused, timing_case in cases:
            timing,interval,dwell,expect=timing_case
            ipc('wayfire/set-config-options',{'scottland/window_avoidance_always':always,
                'scottland/window_double_tap_delay':300})
            ipc('scottland/present',{'window':target})
            wait(lambda: not view(target)['widgetized'])
            time.sleep(.5)
            ipc('scottland/present',{'window':target if focused else other})
            key('LEFTALT',True)
            wait(lambda: hints()['active'])
            assert hints()['selected']==(target if focused else other), 'fixture focus'
            text=label(target)
            assert len(text)==width, text
            events.clear()
            before=geometry(target)
            sequence(text,dwell)
            first=hints()
            first_geometry=geometry(target)
            assert (first_geometry==before)==(not focused), 'WK6 first press'
            if timing=='press': sleep_until(events[-2]['time']+interval-(width-1)*(dwell+.02))
            else: sleep_until(events[-1]['time']+interval-(width*dwell+(width-1)*.02))
            sequence(text,dwell)
            time.sleep(.6)
            result={'width':width,'always':always,'focused':focused,'timing':timing,'target':target,
                'expect':expect,
                'interval_ms':interval*1000,'dwell_ms':dwell*1000,'label':text,
                'events':events.copy(),'first_selected':first['selected'],
                'widgetized':view(target)['widgetized'],'hints_active':hints()['active'],
                'release_gap_ms':(events[width*2]['time']-events[width*2-1]['time'])*1000,
                'press_delta_ms':(events[(width*2)+(width-1)*2]['time']-events[(width-1)*2]['time'])*1000,
                'release_delta_ms':(events[-1]['time']-events[width*2-1]['time'])*1000}
            results.append(result)
            print(('PASS' if result['widgetized']==expect else 'FAIL')+
                f' {text} focused={focused} avoidance={always} {timing}={interval*1000:.0f}ms dwell={dwell*1000:.0f}ms'
                f' {"rail" if expect else "no rail"} complete-press delta={result["press_delta_ms"]:.1f}ms'
                f' release delta={result["release_delta_ms"]:.1f}ms',flush=True)
            key('LEFTALT',False)
            time.sleep(.1)
        if '--quick' not in sys.argv:
            def reset():
                key('LEFTALT',False)
                ipc('scottland/present',{'window':primary})
                wait(lambda: not view(primary)['widgetized'])
                time.sleep(.5)
                ipc('scottland/present',{'window':other})
                key('LEFTALT',True)
                wait(lambda: hints()['active'])
                return label(primary)
            text=reset()
            sequence(text,.08); time.sleep(.35); sequence(text,.08)
            assert not view(primary)['widgetized'], 'slow hints incorrectly double-tapped'
            print('PASS '+text+' slow hints cycle instead of double-tapping',flush=True)
            text=reset()
            sequence(text[:-1],.08)
            key(text[-1].upper(),True)
            for _ in range(4):
                time.sleep(.08); key(text[-1].upper(),True)
            assert not view(primary)['widgetized'], 'held-key repeats double-tapped'
            key(text[-1].upper(),False)
            print('PASS '+text+' held-key repeat cannot double-tap',flush=True)
            text=reset()
            sequence(text,.08)
            key('TAB',True); time.sleep(.06); key('TAB',False)
            sequence(text,.08)
            assert not view(primary)['widgetized'], 'Tab did not clear repeat recognition'
            print('PASS '+text+' Tab clears repeat recognition',flush=True)
            if width==2:
                text=reset()
                sequence(text,.06); time.sleep(.06)
                sequence(text[:1],.06)
                assert not view(primary)['widgetized'], 'prefix triggered a rail action'
                sequence(text[1:],.06)
                wait(lambda: view(primary)['widgetized'])
                print('PASS '+text+' repeated prefix waits for the complete hint',flush=True)
            text=reset()
            sequence(text,.08); time.sleep(.06); sequence(text,.08)
            wait(lambda: view(primary)['widgetized'])
            widget=wait(lambda: next((w['widget_view'] for w in ipc('scottland/widgets')['widgets']
                if int(w['id'])==primary and w['widget_view']>0),None))
            key('LEFTALT',False)
            ipc('window-rules/focus-view',{'id':widget})
            key('LEFTALT',True); wait(lambda: hints()['active'])
            assert hints()['selected']==primary, 'widget focus fixture'
            text=label(primary)
            sequence(text,.08); time.sleep(.06); sequence(text,.08)
            wait(lambda: view(primary)['widgetized'])
            print('PASS '+text+' focused-widget human double-tap returns to rail',flush=True)
            key('LEFTALT',False)
        subprocess.run(['grim',str(art/('width-'+str(width)+'.png'))],check=True)
    (art/'results.json').write_text(json.dumps(results,indent=2))
    assert all(r['widgetized']==r['expect'] and r['hints_active'] and r['first_selected']==r['target'] for r in results)
finally:
    key('LEFTALT',False)
    for client in clients:
        if client.poll() is None: client.terminate()
    for client in clients: client.wait(timeout=5)
