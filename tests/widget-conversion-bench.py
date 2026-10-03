#!/usr/bin/env python3
"""Real-input window/card conversion latency. Run inside an isolated --widgets session.
Records ~1 kHz IPC round trips from a separate process, input windows, and screenshots.
Frame intervals and callback cost can be joined from CONVERSION trace timestamps.
"""
import argparse
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import socket
import struct
import subprocess
import time

spec = importlib.util.spec_from_file_location('widget_input', Path(__file__).with_name('widget-input-test.py'))
t = importlib.util.module_from_spec(spec); spec.loader.exec_module(t)


def ping(path, stop):
    peer = t.Ipc()
    with open(path, 'w') as out:
        while not stop.is_set():
            start = time.monotonic()
            peer.call('stipc/ping')
            out.write(f'{start:.6f} {(time.monotonic()-start)*1000:.3f}\n')
            time.sleep(.001)


def press(name):
    t.key(name, True); t.key(name, False)


def begin(view):
    f = view.get('scene_frame', view['frame'])
    t.move(f['x']+f['width']/2, f['y']+f['height']/2)
    t.key('LEFTMETA', True)
    t.ipc.call('stipc/feed_button', {'combo':'BTN_LEFT', 'mode':'press'})


def end():
    t.ipc.call('stipc/feed_button', {'combo':'BTN_LEFT', 'mode':'release'})
    t.key('LEFTMETA', False)


def settle(title, widget):
    t.wait_for(lambda: bool(t.app(title)['widgetized']) == widget and
               (not widget or t.card(title)), timeout=8)
    t.wait_for(lambda: t.ipc.call('scottland/layout-state')['widget_transition_count']==0, timeout=8)
    time.sleep(.35)


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('out', type=Path); ap.add_argument('--trials',type=int,default=8)
    args=ap.parse_args(); args.out.mkdir(parents=True,exist_ok=True)
    t.ipc.call('wayfire/set-config-options', {'scottland/sounds':False})
    records=[]; stop=multiprocessing.Event()
    worker=multiprocessing.Process(target=ping,args=(args.out/'pings.txt',stop)); worker.start()
    try:
        for path in ('drag','fling','keyboard'):
            for trial in range(args.trials):
                title=f'conversion-{path}-{trial}'
                proc=subprocess.Popen(['foot','-T',title,'-W','100x30','sh','-c','exec sleep 600'],
                    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                t.owned.append((title,proc)); t.wait_for(lambda:t.app(title)); time.sleep(.4)
                x,y=t.screen['width']/2,t.screen['height']/2
                t.drag_begin(t.app(title),x,y); t.drag_end(); time.sleep(.4)
                hint=None
                if path=='keyboard':
                    t.key('LEFTALT',True);t.wait_for(lambda:t.ipc.call('scottland/hints')['active'])
                    hint=next(h['hint'] for h in t.ipc.call('scottland/hints')['hints'] if h['window']==t.app(title)['id'])
                    time.sleep(.2)
                start=time.monotonic()
                if path=='drag':
                    begin(t.app(title))
                    for n in range(1,21):
                        t.move(x+(t.screen['width']-6-x)*n/20,y);time.sleep(.015)
                    time.sleep(.45);end()
                elif path=='fling':
                    begin(t.app(title));time.sleep(.1)
                    for n in range(1,7):
                        time.sleep(.015);t.move(x+130*n/6,y)
                    end()
                else:
                    for c in hint: press(c.upper())
                    time.sleep(.04)
                    for c in hint: press(c.upper())
                    t.key('LEFTALT',False)
                settle(title,True)
                finish=time.monotonic()
                records.append({'path':path,'direction':'entry','trial':trial,'start':start,'end':finish})
                if trial==0: subprocess.run(['grim',str(args.out/f'{path}-widget.png')],check=True,stdout=subprocess.DEVNULL)
                # Same reverse drag fixture for all three entry paths.
                start=time.monotonic();begin(t.card(title));t.move(x,y);time.sleep(.45);end();settle(title,False)
                records.append({'path':path,'direction':'restore','trial':trial,'start':start,'end':time.monotonic()})
                if trial==0: subprocess.run(['grim',str(args.out/f'{path}-restored.png')],check=True,stdout=subprocess.DEVNULL)
                t.ipc.call('window-rules/close-view',{'id':t.app(title)['id']});proc.wait(timeout=5)
                t.owned.remove((title,proc));time.sleep(.3)
                (args.out/'windows.json').write_text(json.dumps(records,indent=2))
                print(f'{path} trial {trial+1} complete',flush=True)
    finally:
        stop.set();worker.join(timeout=5)
        if worker.is_alive(): worker.terminate();worker.join()
        t.cleanup()

if __name__=='__main__': main()
