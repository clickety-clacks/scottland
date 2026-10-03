#!/usr/bin/env python3
"""Read-only: a running session's compositor GPU beside what the goo did in the same seconds.

  scottland-exec --display wayland-N -- python3 tests/goo-live-sample.py [SAMPLES] [SECONDS]

Changes nothing in the session. Each line is one window of SECONDS (default 4): process
GPU busy, then goo-state deltas. `steps` above zero means the simulation was awake in that
window (the expensive state); `wakes` names what woke it. A sleeping breath shows ticks near
25 a second with zero steps.
"""
import json, os, socket, struct, subprocess, sys
from pathlib import Path

samples = int(sys.argv[1]) if len(sys.argv) > 1 else 10
seconds = sys.argv[2] if len(sys.argv) > 2 else '4'
path = os.environ['WAYFIRE_SOCKET']

def state():
    sock = socket.socket(socket.AF_UNIX); sock.connect(path)
    body = json.dumps({'method': 'scottland/goo-state', 'data': {}}).encode()
    sock.sendall(struct.pack('<I', len(body)) + body)
    def read(n):
        b = b''
        while len(b) < n: b += sock.recv(n-len(b))
        return b
    return json.loads(read(struct.unpack('<I', read(4))[0]))['screens'][0]

# The compositor is whoever owns the session's IPC socket.
peer = socket.socket(socket.AF_UNIX); peer.connect(path)
pid = str(struct.unpack('3i', peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[0])
peer.close()
sampler = str(Path(__file__).with_name('gpu-sample.py'))
for _ in range(samples):
    a = state()
    gpu = subprocess.run([sys.executable, sampler, pid, seconds], capture_output=True, text=True).stdout.split('\n')[0]
    b = state()
    d = lambda k: b.get(k, 0)-a.get(k, 0)
    wakes = {k: v-(a.get('wakes') or {}).get(k, 0) for k, v in (b.get('wakes') or {}).items()
             if v-(a.get('wakes') or {}).get(k, 0)}
    print(gpu, '| asleep', a['sleeping'], b['sleeping'], 'steps', d('steps'), 'wakes', wakes or '-',
          'ticks', d('breath_ticks'), 'draws', d('draws'), 'reused', d('backdrop_reuses'),
          'refreshes', d('breath_refreshes'), 'strip px', int(sum(r['width']*r['height'] for r in b['breath_damage'])),
          'draw ms', round(b['draw_gpu_ms'], 3), flush=True)
