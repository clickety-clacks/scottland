#!/usr/bin/env python3
"""Large-window avoidance must not change real geometry or widgetize (WK13/WK31)."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

if os.environ.get('SCOTTLAND_TEST_MODEL') != '1':
    sys.exit('requires the isolated headless harness')

spec = importlib.util.spec_from_file_location('widget_input', Path(__file__).with_name('widget-input-test.py'))
t = importlib.util.module_from_spec(spec); spec.loader.exec_module(t)
out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)

try:
    t.ipc.call('wayfire/set-config-options', {'scottland/sounds': False})
    for attention in (False, True):
        for i in range(6):
            title = f'large-avoidance-{attention}-{i}'
            p = subprocess.Popen(['foot', '-c', '/dev/null', '-T', title, '-W', '120x38', 'sleep', '600'],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            t.owned.append((title, p))
            view = t.wait_for(lambda: t.app(title))
            if attention:
                for old_title, _ in t.owned[:-1]:
                    t.ipc.call('scottland/attention', {'window': t.app(old_title)['id'],
                               'attention': True, 'source': 'large-test'})
            time.sleep(.8)
            print(f'OPEN {title}', flush=True)
        before = t.ipc.call('window-rules/list-views')
        time.sleep(2)
        after = t.ipc.call('window-rules/list-views')
        assert {v['id']: v['geometry'] for v in before} == {v['id']: v['geometry'] for v in after}
        assert not t.widgets(), t.widgets()
        (out / f'large-{attention}.json').write_text(json.dumps({
            'layout': t.ipc.call('scottland/layout-state'), 'views': after,
            'hints': t.ipc.call('scottland/hints')}, indent=2))
        subprocess.run(['grim', str(out / f'large-{attention}.png')], check=True, timeout=5)
        print(f'PASS 6 large windows, attention={attention}: geometry stable, no widgets', flush=True)
        # Then hold real Alt to exercise the same avoidance with visible hints.
        t.key('LEFTALT', True)
        t.wait_for(lambda: t.ipc.call('scottland/hints')['active'])
        time.sleep(2)
        assert not t.widgets()
        t.key('LEFTALT', False)
        print(f'PASS 6 large windows, attention={attention}: Alt remains responsive, no widgets', flush=True)
        t.cleanup()
finally:
    for _, p in t.owned:
        if p.poll() is None: p.terminate()
