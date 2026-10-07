#!/usr/bin/env python3
"""Rebuild tests/mainloop-exceptions.json from latency runs (docs/main-loop.md "Exceptions").

  tests/mainloop-exceptions-update.py MACHINE BUILD OUTDIR_10 OUTDIR_30

MACHINE labels the measurements by architecture (aarch64, x86), never a host name: the table is
public. Each OUTDIR is a tests/mainloop-latency-test.sh output (results.json) at 10 and 30 windows. A scope
whose measured maximum exceeds the 2 ms budget in either run is an open exception, with its owner
and description kept from the table when it already had one; its ceiling is 1.5x the larger
measurement plus 1 ms. Every earlier entry no longer over budget becomes closed history (no
allowance) with its last numbers. Nothing is excused that wasn't measured."""
import json, sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
table_path = root / 'tests/mainloop-exceptions.json'
machine, build, runs = sys.argv[1], sys.argv[2], {'10': Path(sys.argv[3]), '30': Path(sys.argv[4])}
table = json.loads(table_path.read_text())
budget = table['budget_ms']

measured = {}
for windows, outdir in runs.items():
    for result in json.loads((outdir / 'results.json').read_text()):
        for name, scope in ((result.get('stats') or {}).get('scopes') or {}).items():
            key = f'{machine}_{windows}'
            measured.setdefault(name, {})
            measured[name][key] = round(max(measured[name].get(key, 0), scope['max_ms']), 2)

old = table['scopes']
scopes = {}
for name, numbers in sorted(measured.items(), key=lambda kv: -max(kv[1].values())):
    worst = max(numbers.values())
    if worst <= budget:
        continue
    before = old.get(name, {})
    scopes[name] = {
        'status': 'open',
        'ceiling_ms': round(1.5 * worst + 1),
        'measured_ms': numbers,
        'owner': before.get('owner', 'open: attribution'),
        'what': before.get('what', ''),
    }
for name, before in old.items():
    if name in scopes:
        continue
    numbers = measured.get(name)
    scopes[name] = {
        'status': 'closed',
        'closed_by': 'measured under 2 ms' if numbers else 'not reached in these runs (scope gone or not exercised)',
        'last_open_measured_ms': before.get('measured_ms', before.get('last_open_measured_ms')),
        'measured_ms': numbers or {},
        'owner': before.get('owner', ''),
        'what': before.get('what', ''),
    }
# Open but never measured here: no allowance (over 2 ms they fail the gate), kept visible (Astra 7).
for name, what in (('core_run', 'helper launches (D3); not exercised by these scenarios'),
                   ('widget_capture', 'the uncached widget capture fallback (R6); only the retained-pixel path was exercised')):
    if scopes.get(name, {}).get('status') != 'open':
        scopes[name] = {'status': 'open-unmeasured', 'measured_ms': measured.get(name, {}), 'owner': 'open', 'what': what}
table['scopes'] = scopes
table['measured'] = (f'{machine}, {build}, optimized headless with real stipc input, 10 and 30 windows; '
                     'open ceilings are 1.5x the larger measurement + 1 ms; closed entries carry no allowance')
table['comment'] = ('Named exceptions to ML1 (docs/main-loop.md). An open entry is a measured residual and an open '
                    'delivery risk, not a met target; its ceiling is what it may not exceed in '
                    'tests/mainloop-latency-test.sh --gate. Closed entries are history. Every other scope\'s ceiling '
                    'is budget_ms.')
table_path.write_text(json.dumps(table, indent=1) + '\n')
print(f"{sum(1 for s in scopes.values() if s['status'] == 'open')} open, "
      f"{sum(1 for s in scopes.values() if s['status'] == 'closed')} closed")
