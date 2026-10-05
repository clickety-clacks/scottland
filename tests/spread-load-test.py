#!/usr/bin/env python3
"""Measured main-loop slices of a solo solve in the real build, with many windows (P8; final.md
section 4: main-thread slices are acceptable only when measured). argv: ARTIFACTS"""
import json, random, sys, time
from pathlib import Path
source = Path(__file__).with_name('spread-test.py').read_text().split('\ntry:\n', 1)[0]
helpers = {}
exec(compile(source, 'spread-test.py', 'exec'), helpers)
h = type('h', (), helpers)
ipc, check, wait, geometry = h.ipc, h.check, h.wait, h.geometry
import subprocess
art = Path(sys.argv[1]).resolve()
records = []
try:
    ipc('wayfire/set-config-options', {'scottland/sounds': False, 'scottland/window_hold_delay': 500,
        'scottland/window_avoidance_always': False, 'scottland/hint_avoidance_always': False,
        'output:HEADLESS-1/mode': '2560x1440@60000'})
    time.sleep(1)
    rng = random.Random(4)
    ids = []
    for count in (12, 24, 40):
        while len(ids) < count:
            title = f'Load{len(ids)}'
            h.clients.append(subprocess.Popen(['foot', '--app-id=scottland-spread-load', '--title=' + title, 'sh', '-c', 'sleep 900'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            ids.append(wait(lambda: next((v['id'] for v in h.views() if v.get('title') == title), None), 10, title))
        S = ids[0]
        for i in ids[1:]:
            w, hh = rng.randrange(400, 900), rng.randrange(300, 700)
            arrival = rng.random() < 0.3
            cx = rng.uniform(900, 1660) if arrival else rng.choice([rng.uniform(150, 800), rng.uniform(1760, 2410)])
            cy = rng.uniform(250, 1190)
            ipc('window-rules/configure-view', {'id': i, 'geometry': {'x': int(cx - w / 2), 'y': int(cy - hh / 2), 'width': w, 'height': hh}})
        ipc('window-rules/configure-view', {'id': S, 'geometry': {'x': 830, 'y': 370, 'width': 900, 'height': 700}})
        time.sleep(1.5)
        ipc('window-rules/focus-view', {'id': S}); time.sleep(.3)
        n = ipc('scottland/spread-state')['solves']
        h.alt(True); h.press_hint(S, hold=.75); h.alt(False)
        r = h.wait_solve(n)
        records.append({k: r[k] for k in ('windows', 'status', 'checkpoint', 'complete', 'work', 'slices',
            'longest_slice_ms', 'solving_ms', 'waited_ms', 'longest_gap_ms', 'deliver_ms', 'deliver_cpu_ms', 'snapshot_ms', 'commit_ms', 'commit_cpu_ms')})
        print(json.dumps(records[-1]), flush=True)
        check(r['longest_slice_ms'] < 4, f'{r["windows"]} windows: every slice is short (longest {r["longest_slice_ms"]:.2f} ms; 2 ms allowance)')
        # Delivery happens at the first event-loop turn after the limit; the loop's own work between
        # slices (drawing every window in software here) is what can make that later.
        bound = r['wall_limit_ms'] + r['longest_gap_ms'] + 2.5
        check(r['waited_ms'] <= bound, f'{r["windows"]} windows: delivered in {r["waited_ms"]:.1f} ms, within the {r["wall_limit_ms"]:.0f} ms '
              f'limit plus one event-loop turn ({r["longest_gap_ms"]:.1f} ms) ({r["status"]} via {r["checkpoint"]})')
        check(r['deliver_cpu_ms'] < 25, f'{r["windows"]} windows: delivering and committing took {r["deliver_cpu_ms"]:.1f} ms of '
              f'compositor CPU ({r["deliver_ms"]:.1f} ms wall)')
        h.shot(f'load-{count}.png')
        time.sleep(1)
    (art / 'load.json').write_text(json.dumps(records, indent=2))
finally:
    for c in h.clients: c.terminate()
    print(f"{helpers['passed']} passed, {helpers['failed']} failed", flush=True)
sys.exit(1 if helpers['failed'] else 0)
