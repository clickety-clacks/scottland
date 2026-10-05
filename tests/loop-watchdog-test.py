#!/usr/bin/env python3
"""The watchdog and diagnostic ring in a real headless session (docs/main-loop.md). Run on a test
host: tests/loop-watchdog-test.py. Uses scottland/test-loop (SCOTTLAND_TEST_MODEL) to occupy the
main loop inside a work scope, or outside every Scottland scope."""
import json, os, subprocess, sys, time
from pathlib import Path

repo = Path(__file__).resolve().parents[1]
work = repo / 'build/loop-watchdog'; work.mkdir(parents=True, exist_ok=True)
reader = repo / 'build/scottland-loop-read'
runtime = Path(os.environ.get('XDG_RUNTIME_DIR') or f'/run/user/{os.getuid()}') / 'scottland'
fails = 0
def check(what, ok, detail=''):
    global fails
    print(f"{'PASS' if ok else 'FAIL'}  {what}" + ('' if ok else f'  {detail}'), flush=True)
    fails += not ok

def session(name, faults=''):
    env = dict(os.environ, SCOTTLAND_HEADLESS_DIR=str(work / name), TMPDIR=str(work))
    if faults: env['SCOTTLAND_TEST_LOOP_FAULTS'] = faults
    subprocess.run([str(repo / 'tests/headless.sh'), 'stop'], env=env, capture_output=True)
    subprocess.run([str(repo / 'tests/headless.sh'), 'start'], env=env, check=True, capture_output=True)
    display = (work / name / 'display').read_text().strip()
    def ipc(method, data=None):
        out = subprocess.run([str(repo / 'tests/headless.sh'), 'ipc', method, json.dumps(data or {})], env=env,
                             capture_output=True, text=True).stdout
        return json.loads(out)
    def stop(): subprocess.run([str(repo / 'tests/headless.sh'), 'stop'], env=env, capture_output=True)
    return display, ipc, stop

def ring(display, follow=None):
    out = subprocess.run([str(reader), '--json', '--file', str(runtime / f'{display}.loop')],
                         capture_output=True, text=True, timeout=10).stdout
    return [json.loads(l) for l in out.splitlines() if l.strip()]

display, ipc, stop = session('main')
try:
    time.sleep(3)  # the session settles; only heartbeats remain
    stats = ipc('scottland/loop-stats')
    check('watchdog and ring are on', stats['watchdog']['on'] and stats['ring']['on'], stats.get('ring'))
    # Stuck, read by the shipped external reader while the scope is still running.
    ipc('scottland/test-loop', {'busy_ms': 600, 'scoped': True})
    time.sleep(.35)
    during = ring(display)
    header = during[0]['header']
    check('the external reader sees the running scope', header['current'] == 'test_loop' and header['current_ms'] > 100, header)
    check('... and its stuck record before it returns', any(r.get('kind') == 'stuck' and r.get('scope') == 'test_loop' for r in during))
    time.sleep(1)
    # Unresponsive: busy outside every Scottland scope.
    ipc('scottland/test-loop', {'busy_ms': 600, 'scoped': False})
    time.sleep(1.5)
    after = ring(display)
    late = [r for r in after if r.get('kind') == 'unresponsive']
    check('a busy loop outside every scope is unresponsive, not stuck', late and
          not any(r.get('kind') == 'stuck' and r['time_ns'] > late[-1]['time_ns'] - 10**9 for r in after), late)
    check('slow callbacks are recorded with their scope', any(r.get('kind') == 'slow' and r.get('scope') == 'test_loop' for r in after))
    check('no records were lost while reading', after[-1].get('lost') == 0, after[-1])
    # Idle: rounds 2 s apart, heartbeats answered.
    time.sleep(12)
    w = ipc('scottland/loop-stats')['watchdog']
    # Idle stretches have 2 s rounds (the goo's periodic wake is real work and runs fast rounds).
    slow = [x for x in w['round_intervals_ms'] if 1900 < x < 2300]
    check('idle with only heartbeats: rounds 2 s apart', len(slow) >= 3, w['round_intervals_ms'])
    check('heartbeats are answered', w['heartbeats_acked'] >= w['heartbeats'] - 1, w)
    # Across a reload: the new copy continues the ring; older records show as the previous build.
    env = dict(os.environ, SCOTTLAND_HEADLESS_DIR=str(work / 'main'), TMPDIR=str(work))
    done = subprocess.run([str(repo / 'tests/headless.sh'), 'run', 'env', f'SCOTTLAND_TEST_RELOAD_DIR={work / "main"}',
                           f'SCOTTLAND_RELOAD_SOURCE={repo / "build/libscottland.so"}', str(repo / 'core/session/scottland-reload')],
                          env=env, capture_output=True, text=True)
    check('reload succeeds', 'reloaded from' in done.stdout, done.stdout + done.stderr)
    records = ring(display)
    check('after a reload the ring continues with instance 2', records[0]['header']['instance'] == 2, records[0])
    check('records of the previous copy are marked as such', any('previous build' in str(r.get('scope', '')) for r in records))
    check('the new copy has names for its own records', any(r.get('kind') == 'loaded' and r.get('instance') == 2 for r in records))
finally:
    stop()
check('stopping the session removes its ring', not (runtime / f'{display}.loop').exists())

for faults, expect in (('ring', 'no ring'), ('mlock', 'unlocked'), ('eventfd', 'no watchdog'), ('thread', 'no watchdog')):
    display, ipc, stop = session('fault-' + faults, faults)
    try:
        time.sleep(1)
        stats = ipc('scottland/loop-stats')
        ok = {'no ring': not stats['ring']['on'], 'unlocked': stats['ring']['mlock_failed'] and stats['watchdog']['on'],
              'no watchdog': stats['ring']['on'] and not stats['watchdog']['on']}[expect]
        check(f'{faults} failure: {expect}, and Scottland still works', ok and 'views' in ipc('scottland/layout-state'), stats.get('ring'))
    finally:
        stop()
print('all watchdog checks passed' if not fails else f'{fails} check(s) failed')
sys.exit(1 if fails else 0)
