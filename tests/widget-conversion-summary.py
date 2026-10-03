#!/usr/bin/env python3
"""Join conversion benchmark input windows with IPC latency and optional compositor trace."""
import argparse
import json
from pathlib import Path
import statistics


def stats(values):
    values=sorted(values)
    if not values: return None
    return {'n':len(values),'p50':round(statistics.median(values),3),
            'p95':values[min(len(values)-1,int(len(values)*.95))],
            'p99':values[min(len(values)-1,int(len(values)*.99))],
            'max':max(values),'over8':sum(x>8 for x in values),'over20':sum(x>20 for x in values)}


def main():
    ap=argparse.ArgumentParser();ap.add_argument('results',type=Path);args=ap.parse_args()
    windows=json.loads((args.results/'windows.json').read_text())
    pings=[tuple(map(float,line.split())) for line in (args.results/'pings.txt').read_text().splitlines()]
    traces=[]
    for line in (args.results/'wayfire.log').read_text().splitlines():
        if line.startswith('CONVERSION '):
            _,stamp,label,ms=line.split();traces.append((float(stamp),label,float(ms)))
    result={}
    for path in ('drag','fling','keyboard'):
        for direction in ('entry','restore'):
            runs=[w for w in windows if w['path']==path and w['direction']==direction]
            inside=lambda stamp:any(w['start']<=stamp<=w['end'] for w in runs)
            frames=[]
            for run in runs:
                stamps=[t for t,label,ms in traces if label=='frame' and run['start']<=t<=run['end']]
                frames.extend(round((b-a)*1000,3) for a,b in zip(stamps,stamps[1:]))
            result[path+'-'+direction]={'ipc_ms':stats([ms for stamp,ms in pings if inside(stamp)]),
                'frame_interval_ms':stats(frames),
                'callbacks_ms':{label:stats([ms for stamp,k,ms in traces if k==label and inside(stamp)])
                    for label in sorted(set(k for stamp,k,ms in traces if k!='frame' and inside(stamp)))}}
    (args.results/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
