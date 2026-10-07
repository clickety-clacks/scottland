#!/usr/bin/env python3
"""An unsupported GL context retains the real, draggable halo instead of blank decoration.
Run after a fresh start --widgets with SCOTTLAND_TEST_GOO_GLES=unsupported
or no-derivatives.

With `runtime SESSION_DIR`, after a fresh start --widgets on a supported context: goo's
own GL work fails at runtime (a test switch raises a GL error in its pass), and goo falls
back to the same halo with one log line and no repeating errors.
"""
import json
from pathlib import Path
import re
import subprocess
import sys
import time

repo=Path(__file__).resolve().parents[1]
runtime=sys.argv[1:2]==['runtime']
def ipc(method,data=None):
    return json.loads(subprocess.check_output(['python3',str(repo/'tests/wfipc.py'),method,json.dumps(data or {})]))
def view():
    return next(v for v in ipc('scottland/layout-state')['views'] if v['title']=='fallback-app')
def pointer(x,y):ipc('stipc/move_cursor',{'x':round(x),'y':round(y)})
passed=failed=0
def check(name,ok):
    global passed,failed
    print(('PASS ' if ok else 'FAIL ')+name,flush=True)
    passed+=bool(ok);failed+=not ok

p=subprocess.Popen(['foot','-c','/dev/null','-T','fallback-app','sleep','600'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
try:
    for _ in range(50):
        if any(v['title']=='fallback-app' for v in ipc('scottland/layout-state')['views']):break
        time.sleep(.1)
    # Confirm the request remains on even though this GPU cannot run it.
    check('goo is requested by default', str(ipc('wayfire/get-config-option', {'option':'scottland/goo'})['value']).lower() in ('true', '1'))
    time.sleep(.7)
    if runtime:
        log=Path(sys.argv[2])/'wayfire.log'
        lines=lambda pattern:[l for l in log.read_text(errors='replace').splitlines() if re.search(pattern,l)]
        check('goo runs before the failure',ipc('scottland/goo-state')['enabled'])
        ipc('scottland/goo-state',{'gl_fail':True});time.sleep(.7)
        check('a runtime GL error falls back to the halo',not ipc('scottland/goo-state')['enabled'])
        check('goo logs why once',len(lines(r"GL error 0x[0-9a-f]+ in goo's drawing; retaining halo"))==1)
        errors=len(lines(r'GL_INVALID|GL error'));time.sleep(2)
        check('no GL error repeats after the fallback',len(lines(r'GL_INVALID|GL error'))==errors)
    else:
        check('unsupported context falls back to the halo',not ipc('scottland/goo-state')['enabled'])
    check('unsupported goo leaves no available model screens', ipc('scottland/desktop-model')['goo'] == [])
    ipc('wayfire/set-config-options',{'scottland/center_width':90,'scottland/min_scale':1,'scottland/max_scale':1,'scottland/scale_curve':'0:1 1:1'})
    time.sleep(.4);f=view()['frame'];x=f['x']-6;y=f['y']+f['height']/2
    pointer(x,y);time.sleep(.1)
    ipc('stipc/feed_button',{'combo':'BTN_LEFT','mode':'press'})
    for i in range(1,11):pointer(x+i*4,y);time.sleep(.03)
    ipc('stipc/feed_button',{'combo':'BTN_LEFT','mode':'release'});pointer(20,20);time.sleep(.7)
    check('the fallback halo still moves its window',view()['frame']['x']>f['x']+25)
    subprocess.run(['grim',str(repo/('build/goo-runtime-fallback.png' if runtime else 'build/goo-fallback.png'))],check=True)
finally:
    ipc('window-rules/close-view',{'id':view()['id']})
    print(f'RESULT {passed} passed, {failed} failed',flush=True)
raise SystemExit(bool(failed))
