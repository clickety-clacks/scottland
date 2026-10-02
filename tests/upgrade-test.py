#!/usr/bin/env python3
"""A real legacy main -> model upgrade preserves the original resolved launch identity."""
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import time

branch = Path(sys.argv[1])
# Reuse only the real-input fixture helpers, without running its other regressions.
loader = importlib.machinery.SourceFileLoader('helpers', str(branch / 'tests/state-regressions-test.py'))
helpers = importlib.util.module_from_spec(importlib.util.spec_from_loader('helpers', loader))
loader.exec_module(helpers)
ipc = helpers.ipc
client = None
bus = None
try:
    ipc.call('wayfire/set-config-options', {'scottland/sounds': False})
    window = helpers.open_app('foot')
    width = ipc.call('window-rules/list-outputs')[0]['geometry']['width']
    helpers.drag(window, width-6)
    time.sleep(1)
    before = ipc.call('scottland/widgets')['widgets'][0]
    unit = before['widget_unit']
    widget = next(v for v in ipc.call('scottland/layout-state')['views'] if v['widget'])
    env = dict(item.split('=', 1) for item in Path(f"/proc/{before['widget_pid']}/environ").read_bytes().decode().split('\0') if '=' in item)
    expected = {k: env['SCOTTLAND_WIDGET_'+k.upper()] for k in ('desktop', 'name', 'icon')}
    assert expected['desktop'] and expected['name'] and expected['icon'], expected
    # This compositor genuinely started on main; its legacy list has no resolved identity.
    assert 'desktop' not in before
    plugins = ipc.call('wayfire/get-config-option', {'option': 'core/plugins'})['value']
    mark = Path(os.environ['XDG_RUNTIME_DIR']) / 'scottland' / (os.environ['WAYLAND_DISPLAY']+'.reloading')
    mark.touch()
    try:
        ipc.call('wayfire/set-config-options', {'core/plugins': plugins.replace('scottland', str(branch/'build/libscottland.so'))})
    finally:
        mark.unlink(missing_ok=True)
    time.sleep(.7)
    after = ipc.call('scottland/desktop-model')['widgets'][0]
    assert all(after[k] == v for k, v in expected.items()), (expected, after)
    assert after['card'] and after['touch_drag']
    assert after['widget_view'] == widget['id'] and after['widget_unit'] == unit
    print('PASS  real main upgrade migrates desktop/name/icon/card traits for the surviving launch', flush=True)
    # Replace the old service with this branch's subscriber, as an actual code update does.
    pidfile = Path(os.environ['XDG_RUNTIME_DIR'])/'scottland'/(os.environ['WAYLAND_DISPLAY']+'.widget-bus.pid')
    oldpid = int(pidfile.read_text().splitlines()[0])
    os.kill(oldpid, 15)
    time.sleep(.5)
    bus = subprocess.Popen([str(branch/'core/libexec/scottland-widget-bus')])
    time.sleep(.8)
    subprocess.run(['busctl','--user','emit','/com/canonical/unity/launcherentry/1',
                    'com.canonical.Unity.LauncherEntry','Update','sa{sv}',
                    'application://'+expected['desktop']+'.desktop','2','count','x','23','count-visible','b','true'],check=True)
    time.sleep(.5)
    value = subprocess.check_output(['busctl','--user','get-property','org.scottland.Widgets',
                                     f'/org/scottland/widget/{window}','org.scottland.Widget','Badge'],text=True).strip()
    assert value == 'x 23', value
    print('PASS  future launcher badge matches the original desktop ID after upgrade', flush=True)
finally:
    if bus and bus.poll() is None: bus.terminate()
    for client in helpers.clients:
        if client.poll() is None: client.terminate()
