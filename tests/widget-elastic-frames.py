#!/usr/bin/env python3
"""Capture real-input WG23 frames inside a caller-owned isolated headless session."""
import importlib.util
import json
import re
from pathlib import Path
import struct
import subprocess
import sys
import time
import zlib

spec = importlib.util.spec_from_file_location('widget_input', Path(__file__).with_name('widget-input-test.py'))
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)
out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)

def png(path, width, height, pixels):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    rows = b''.join(b'\0' + pixels[y*width*3:(y+1)*width*3] for y in range(height))
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width,height,8,2,0,0,0)) +
        chunk(b'IDAT', zlib.compress(rows, 1)) + chunk(b'IEND', b''))

def capture_frame():
    raw = subprocess.check_output(['grim', '-t', 'ppm', '-'], stderr=subprocess.DEVNULL)
    match = re.match(rb'P6\s+(\d+)\s+(\d+)\s+255\s', raw)
    if not match: raise RuntimeError('grim did not return RGB pixels')
    width, height = map(int, match.groups()); source = memoryview(raw)[match.end():]
    frame = t.card(title)['frame']
    top = max(0, min(height-164, round(baseline['y'])-32)); left = 0; crop_w = 375; crop_h = 164
    pixels = bytearray(crop_w*crop_h*3)
    for y in range(crop_h):
        start = ((top+y)*width+left)*3
        pixels[y*crop_w*3:(y+1)*crop_w*3] = source[start:start+crop_w*3]
    return bytes(pixels), frame

title = 'Elastic widget — a recognizable card on the rail'
records = {}; strips=[]
try:
    t.ipc.call('wayfire/set-config-options', {'scottland/sounds': False})
    t.launch(title, 'left', 330); t.move(640, 60); time.sleep(.8)
    baseline = t.card(title)['frame']
    for action in ('Contract', 'Expand'):
        pictures=[]; start=None
        before=t.card(title)['frame']; pixels,after=capture_frame(); pictures.append((0.0,pixels,before,after))
        start=time.monotonic(); t.toggle()
        while time.monotonic()-start < .65:
            before=t.card(title)['frame']; pixels,after=capture_frame()
            pictures.append((time.monotonic()-start,pixels,before,after))
        before=t.card(title)['frame']; pixels,after=capture_frame()
        pictures.append((time.monotonic()-start,pixels,before,after))
        records[action]=[dict(time=at,before=a,after=b) for at,_,a,b in pictures]
        widths=[picture[index]['width'] for picture in pictures for index in (2,3)]
        final=t.card(title)['frame']['width']
        peak=(min if action=='Contract' else max)(range(1,len(pictures)),key=lambda i:pictures[i][2]['width'])
        indices=sorted(set([0,max(1,peak//3),max(1,2*peak//3),peak,min(len(pictures)-1,peak+2),len(pictures)-1]))
        row=bytearray(375*len(indices)*164*3)
        for col,i in enumerate(indices):
            _,crop,_,_=pictures[i]
            for y in range(164):
                src=y*375*3; dst=(y*375*len(indices)+col*375)*3
                row[dst:dst+375*3]=crop[src:src+375*3]
            png(out/(action.lower()+f'-{i:02}.png'),375,164,crop)
        strips.append(bytes(row))
        t.check(action+' captured visible overshoot',min(widths)<final-1 if action=='Contract' else max(widths)>final+1,widths)
        t.check(action+' exact settle',final==(96 if action=='Contract' else 320),final)
        time.sleep(.2)
    # Stack contraction and expansion rows; frame order is left to right, with exact capture
    # timestamps and measured geometry alongside the PNG in frames.json.
    strip=b''.join(strips)
    png(out/'widget-bounce-frame-strip.png',375*6,164*2,strip)
    (out/'frames.json').write_text(json.dumps(records,indent=2))
    sys.exit(bool(t.failures))
finally: t.cleanup()
