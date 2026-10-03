#!/usr/bin/env python3
"""WG27: all entry paths animate before a deliberately late card's first commit.
Run inside an isolated --widgets session with SCOTTLAND_WIDGET_PATH=tests/widgets.
"""
import importlib.util
from pathlib import Path
import subprocess
import sys
import time

spec=importlib.util.spec_from_file_location('conversion_bench',Path(__file__).with_name('widget-conversion-bench.py'))
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
t=b.t
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
try:
    t.ipc.call('wayfire/set-config-options',{'scottland/sounds':False})
    for path in ('drag','fling','keyboard'):
        title='startup-delayed-'+path
        t.launch(title,rail=None,app_id='scottland-morph-fixture')
        x,y=t.screen['width']/2,330
        t.drag_begin(t.app(title),x,y);t.drag_end();time.sleep(.4)
        if path=='keyboard':
            t.key('LEFTALT',True)
            t.wait_for(lambda:t.ipc.call('scottland/hints')['active'])
            hint=next(h['hint'] for h in t.ipc.call('scottland/hints')['hints'] if h['window']==t.app(title)['id'])
            for c in hint:b.press(c.upper())
            time.sleep(.04)
            for c in hint:b.press(c.upper())
            t.key('LEFTALT',False)
        else:
            b.begin(t.app(title));time.sleep(.1)
            if path=='drag':
                t.move(t.screen['width']-6,y)
            else:
                for n in range(1,7):
                    time.sleep(.015);t.move(x+130*n/6,y)
            b.end()
        t.wait_for(lambda:t.app(title)['widgetized'])
        series=[];start=time.monotonic()
        while time.monotonic()-start<.24:
            v=t.app(title);f=v.get('scene_frame',v['frame'])
            series.append((time.monotonic()-start,f['width'],f['height'],v['hidden'],bool(t.card(title))))
            time.sleep(.008)
        t.check(path+' animates during card startup',len({round(row[1],1) for row in series})>4,series)
        t.check(path+' startup stays visible before any card exists',all(not row[3] and not row[4] for row in series),series)
        subprocess.run(['grim',str(out/(path+'-startup.png'))],check=True)
        b.settle(title,True)
        t.check(path+' hands off and releases presentation',t.app(title)['hidden'] and
                t.ipc.call('scottland/layout-state')['widget_transition_count']==0)
        b.begin(t.card(title));t.move(x,y);time.sleep(.4);b.end();b.settle(title,False)
        t.check(path+' real reverse drag restores',not t.app(title)['hidden'])
        t.cleanup()
    sys.exit(bool(t.failures))
finally:t.cleanup()
