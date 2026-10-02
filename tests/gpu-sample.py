#!/usr/bin/env python3
"""gpusample.py PID SECONDS -> GPU render busy% (fdinfo drm-cycles-rcs) and CPU% for PID, plus whole-GPU rcs%."""
import os, sys, time, glob
def clients(pid):
    out = {}
    for f in glob.glob(f'/proc/{pid}/fdinfo/*'):
        try: txt = open(f).read()
        except OSError: continue
        if 'drm-client-id' not in txt: continue
        d = dict(l.split(':\t', 1) for l in txt.splitlines() if ':\t' in l)
        cid = (pid, d['drm-client-id'])
        if 'drm-cycles-rcs' in d:
            out[cid] = (int(d['drm-cycles-rcs']), int(d['drm-total-cycles-rcs']))
        elif 'drm-engine-gfx' in d:
            out[cid] = (int(d['drm-engine-gfx'].split()[0]), time.monotonic_ns())
    return out
def all_clients():
    out = {}
    for p in os.listdir('/proc'):
        if p.isdigit(): out.update(clients(p))
    return out
def cpu(pid):
    s = open(f'/proc/{pid}/stat').read().rsplit(')', 1)[1].split()
    return int(s[11]) + int(s[12])
pid, secs = sys.argv[1], float(sys.argv[2])
a, A, c0, t0 = clients(pid), all_clients(), cpu(pid), time.time()
time.sleep(secs)
b, B, c1, t1 = clients(pid), all_clients(), cpu(pid), time.time()
def busy(x, y):
    tot = max((y[k][1] - x[k][1] for k in y if k in x), default=0)
    used = sum(y[k][0] - x[k][0] for k in y if k in x)
    return 100 * used / tot if tot else float('nan')
print(f'compositor GPU {busy(a,b):5.1f}%  whole GPU {busy(A,B):5.1f}%  compositor CPU {100*(c1-c0)/os.sysconf("SC_CLK_TCK")/(t1-t0):5.1f}%')
