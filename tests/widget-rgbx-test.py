#!/usr/bin/env python3
"""An opaque client's zero X bits stay opaque in the direct-texture startup morph."""
import importlib.util
from pathlib import Path
import re
import os
import signal
import subprocess
import sys
import time

spec=importlib.util.spec_from_file_location('conversion_bench',Path(__file__).with_name('widget-conversion-bench.py'))
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
t=b.t
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
title='startup-delayed-rgbx'
try:
    proc=subprocess.Popen([sys.argv[2]])
    t.owned.append((title,proc));t.wait_for(lambda:t.app(title));time.sleep(.4)
    x,y=t.screen['width']/2,330
    t.drag_begin(t.app(title),x,y);t.drag_end();time.sleep(.4)
    t.key('LEFTALT',True);t.wait_for(lambda:t.ipc.call('scottland/hints')['active'])
    hint=next(h['hint'] for h in t.ipc.call('scottland/hints')['hints'] if h['window']==t.app(title)['id'])
    for c in hint:b.press(c.upper())
    time.sleep(.04)
    for c in hint:b.press(c.upper())
    t.key('LEFTALT',False);time.sleep(.12)
    assert not t.card(title), 'fixture must still be starting'
    raw=subprocess.check_output(['grim','-t','ppm','-'])
    match=re.match(rb'P6\s+(\d+)\s+(\d+)\s+255\s',raw)
    pixels=raw[match.end():]
    # Wallpaper is dark gray: incorrectly blending zero-alpha red adds its green
    # channel to the source. Correct opaque sampling covers it completely.
    red=sum(pixels[n]>240 and pixels[n+1]<10 and pixels[n+2]<10 for n in range(0,len(pixels),3))
    t.check('RGBX source remains opaque during retained startup',red>1000,red)
    subprocess.run(['grim',str(out/'rgbx-startup.png')],check=True)
    # A real client-side resize during startup must leave the provisional rail
    # destination anchored. The source's logical geometry is deliberately changed.
    os.kill(proc.pid, signal.SIGUSR1)
    t.wait_for(lambda:any(v["title"]==title and v["geometry"]["width"]==600
                         for v in t.ipc.call("window-rules/list-views")))
    time.sleep(.32)
    frame=t.app(title)["frame"]
    provisional_right=frame['x']+frame['width']
    b.settle(title,True)
    card_frame=t.card(title)['frame']
    t.check('client resize preserves the provisional rail destination',
            abs(provisional_right-card_frame['x']-card_frame['width'])<1,
            (frame,card_frame))
    t.check('RGBX source hands off to its real card',t.app(title)['hidden'])
    sys.exit(bool(t.failures))
finally:t.cleanup()
