#!/usr/bin/env python3
"""A footprint soak for the terminal workload (core/INVARIANTS.md E9): over a fixed amount of
retitling and redrawing, the compositor's anonymous memory stays within an allowance.

Invoked by heap-growth-test.sh inside its own headless session. A soak, not a default-run test: it
publishes several hundred thousand model versions, a few minutes of work.

Terminals retitle themselves and redraw as fast as they can (agent sessions in tmux/mosh do this all
day), with window avoidance always on. The verdict is the compositor's anonymous memory read from
/proc (resident plus swapped), which the code under test does not report; the model version is a
diagnostic used only to size the work.

What it does not show: that any one path is leak-free. Leaked allocations can first fill free heap
space without growing the footprint, and the allowance is not a bound on retained allocations. It
does not drag, morph, take snapshots or reload either. tests/offscreen-leak-test.sh checks each
path's allocations directly. Unfixed (2026-10-05) this workload grew the compositor by about 11 MB.
"""
import json
import os
from pathlib import Path
import signal
import socket
import struct
import subprocess
import sys
import time

if os.environ.get('SCOTTLAND_TEST_MODEL') != '1':
    sys.exit('run through tests/heap-growth-test.sh (an isolated headless session)')

artifacts = Path(sys.argv[1])
compositor = int(Path(sys.argv[2]).read_text())
WARM = int(os.environ.get('HEAP_GROWTH_WARM_VERSIONS', '100000'))
VERSIONS = int(os.environ.get('HEAP_GROWTH_VERSIONS', '300000'))
BOUND_KB = 2048      # unfixed, this much work grew it by tens of megabytes
HANG = 1800          # a deadline for a stuck session, not a performance budget
TERMINALS = 8
passed = failed = 0


def check(ok, message):
    global passed, failed
    print(('PASS  ' if ok else 'FAIL  ') + message, flush=True)
    passed += bool(ok)
    failed += not ok


sock = socket.socket(socket.AF_UNIX)
sock.settimeout(10)
sock.connect(os.environ['WAYFIRE_SOCKET'])


def ipc(method, data=None):
    body = json.dumps({'method': method, 'data': data or {}}).encode()
    sock.sendall(struct.pack('<I', len(body)) + body)
    def exactly(n):
        b = b''
        while len(b) < n:
            chunk = sock.recv(n - len(b))
            if not chunk:
                raise ConnectionError('compositor disconnected')
            b += chunk
        return b
    reply = json.loads(exactly(struct.unpack('<I', exactly(4))[0]))
    if isinstance(reply, dict) and reply.get('error'):
        raise RuntimeError(f'{method}: {reply}')
    return reply


def anon_kb():
    fields = {}
    for line in Path(f'/proc/{compositor}/status').read_text().splitlines():
        name, _, value = line.partition(':')
        fields[name] = value
    return int(fields['RssAnon'].split()[0]) + int(fields['VmSwap'].split()[0])


def version():
    return ipc('scottland/desktop-model')['version']


def titles():
    return {v['title'] for v in ipc('scottland/layout-state')['views']}


def wait_until(predicate, what, deadline=HANG):
    end = time.monotonic() + deadline
    last = None
    while time.monotonic() < end:
        last = predicate()
        if last:
            return last
        time.sleep(.5)
    raise AssertionError(f'timed out waiting for {what}; last: {last}')


clients = []
code = ('import sys,time\nn=0\nwhile True:\n'
        ' sys.stdout.write(f"\\033]0;heap-{0} {{n}}\\007line {{n}}\\n"); sys.stdout.flush(); n+=1; time.sleep(.002)\n')
try:
    ipc('wayfire/set-config-options', {'scottland/window_avoidance_always': True})
    for i in range(TERMINALS):
        clients.append(subprocess.Popen(
            ['foot', '-c', '/dev/null', '-T', f'heap-{i}', 'python3', '-u', '-c', code.format(i)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
    # Setup: the clients' retitles reach the compositor's windows, not just the clients' own state.
    first = wait_until(lambda: (t := titles()) and sum(x.startswith('heap-') and ' ' in x for x in t) == TERMINALS
                       and t, 'every terminal to retitle', 60)
    wait_until(lambda: not (titles() & first), 'the titles to change again', 60)

    # Warm-up: caches, glyph atlases, serialization buffers and the heap's free space settle.
    start = version()
    wait_until(lambda: version() - start >= WARM, 'warm-up versions')
    base_version, base_kb, t0 = version(), anon_kb(), time.monotonic()
    samples = []
    def enough():
        v = version()
        samples.append({'t': round(time.monotonic() - t0, 1), 'versions': v - base_version, 'anon_kb': anon_kb()})
        return v - base_version >= VERSIONS
    wait_until(enough, f'{VERSIONS} model versions')
    grown = samples[-1]['anon_kb'] - base_kb
    published = samples[-1]['versions']
    (artifacts / 'samples.json').write_text(json.dumps(samples, indent=1))
    print(json.dumps({'versions': published, 'seconds': samples[-1]['t'],
                      'versions_per_second': round(published / max(samples[-1]['t'], .1)),
                      'anon_growth_kb': grown}), flush=True)
    check(grown < BOUND_KB,
          f'compositor footprint grew {grown} KB over {published} model versions of redrawing windows (allowance {BOUND_KB} KB)')
finally:
    for client in clients:
        try:
            os.killpg(client.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    for client in clients:
        client.wait()

print(f'{passed} passed, {failed} failed')
sys.exit(1 if failed else 0)
