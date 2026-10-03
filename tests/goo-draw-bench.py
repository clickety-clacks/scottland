#!/usr/bin/env python3
"""GO10 redraw fixture: run via headless.sh run with a private --widgets session.

Eighteen windows, six continuously drawing terminals, overlap film, wallpaper,
and one breathing rail widget. Samples the same scene with goo on/off/on.
"""
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time

root = Path(__file__).resolve().parents[1]
session = Path(sys.argv[1])
out = Path(sys.argv[2])
seconds = float(sys.argv[3]) if len(sys.argv) > 3 else 10
out.mkdir(parents=True, exist_ok=True)
sock = socket.socket(socket.AF_UNIX)
sock.connect(os.environ['WAYFIRE_SOCKET'])

def ipc(method, data=None):
    payload = json.dumps({'method': method, 'data': data or {}}).encode()
    sock.sendall(struct.pack('<I', len(payload)) + payload)
    def read(n):
        b = b''
        while len(b) < n:
            chunk = sock.recv(n-len(b))
            if not chunk: raise RuntimeError('compositor disconnected')
            b += chunk
        return b
    result = json.loads(read(struct.unpack('<I', read(4))[0]))
    if 'error' in result: raise RuntimeError(result)
    return result

def views(): return ipc('scottland/layout-state')['views']
def state():
    screens = ipc('scottland/goo-state')['screens']
    return screens[0] if screens else None
def pointer(x, y): ipc('stipc/move_cursor', {'x': round(x), 'y': round(y)})
def key(down): ipc('stipc/feed_key', {'key': 'KEY_LEFTMETA', 'state': down})
def button(mode): ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': mode})
def move(title, x, y):
    view = next(v for v in views() if v['title'] == title)
    frame = view['frame']; cx = frame['x']+frame['width']/2; cy = frame['y']+frame['height']/2
    pointer(cx, cy); key(True); button('press')
    for i in range(1, 31):
        pointer(cx+(x-cx)*i/30, cy+(y-cy)*i/30); time.sleep(.02)
    button('release'); key(False); time.sleep(1)

pid = int((session/'pid').read_text())
for child in Path(f'/proc/{pid}/task/{pid}/children').read_text().split():
    if Path(f'/proc/{child}/comm').read_text().strip() == 'wayfire': pid = int(child); break

clients = []
def measure(label):
    before = state()
    sample = subprocess.Popen([sys.executable, str(root/'tests/gpu-sample.py'), str(pid), str(seconds)],
                              stdout=subprocess.PIPE, text=True)
    draws = []
    end = time.monotonic()+seconds
    while time.monotonic() < end:
        current = state()
        if current: draws.append(current['draw_gpu_ms'])
        time.sleep(.1)
    gpu_output = sample.communicate()[0].strip()
    assert sample.returncode == 0
    after = state()
    result = {'case': label, 'gpu': gpu_output,
              'steps': after['steps']-before['steps'] if before and after else None,
              'sleeping': after['sleeping'] if after else None,
              'draw_gpu_ms_median': sorted(draws)[len(draws)//2] if draws else None,
              'breath_ticks': after['breath_ticks']-before['breath_ticks'] if before and after else None,
              'sources': after['sources'] if after else None}
    print(json.dumps(result), flush=True)
    with (out/'measurements.jsonl').open('a') as f: f.write(json.dumps(result)+'\n')
    subprocess.run(['grim', str(out/(label+'.png'))], check=True)

try:
    ipc('wayfire/set-config-options', {'output:HEADLESS-1/mode': '2560x1600@60000'})
    time.sleep(1)
    with (out/'wallpaper.log').open('w') as f:
        clients.append(subprocess.Popen(['quickshell', '-p', str(root/'tests/GooWallpaper.qml')],
                                        stdout=f, stderr=subprocess.STDOUT))
    for i in range(18):
        if 2 <= i < 8:
            cmd = ['python3', '-u', '-c',
                   'import time\ni=0\nwhile True:\n print(f"stream {i:08d} "*5,flush=True);i+=1;time.sleep(.03)']
        else:
            cmd = ['sleep', '600']
        clients.append(subprocess.Popen(['foot', '-c', '/dev/null', '-T', f'draw-{i}', *cmd],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        time.sleep(.16)
    time.sleep(2)
    geometry = []
    for i in range(18):
        col, row = i % 5, i // 5
        geometry.append((130+col*440+(row%2)*50, 150+row*345, 590, 440))
    for i, (x,y,w,h) in enumerate(geometry):
        view = next(v for v in views() if v['title'] == f'draw-{i}')
        ipc('window-rules/configure-view', {'id': view['id'],
            'geometry': {'x': x, 'y': y, 'width': w, 'height': h}})
        time.sleep(.08)
    move('draw-0', 15, 350)
    move('draw-1', 2540, 350)
    pointer(2500, 1500)
    widgets = ipc('scottland/widgets')['widgets']
    assert len(widgets) == 2, widgets
    ipc('scottland/attention', {'window': widgets[0]['window'], 'attention': True,
                               'source': 'draw-bench'})
    for _ in range(600):
        s = state()
        if s and s['sleeping']: break
        time.sleep(.1)
    else: raise AssertionError('goo failed to sleep with streaming windows')
    (out/'fixture.json').write_text(json.dumps({'views': views(), 'widgets': widgets,
                                                'state': state()}, indent=2))
    measure('on-1')
    ipc('wayfire/set-config-options', {'scottland/goo': False})
    time.sleep(3)
    measure('off')
    ipc('wayfire/set-config-options', {'scottland/goo': True})
    for _ in range(600):
        s = state()
        if s and s['sleeping']: break
        time.sleep(.1)
    else: raise AssertionError('goo failed to sleep after re-enable')
    measure('on-2')
finally:
    for client in clients:
        client.terminate()
    for client in clients:
        try: client.wait(timeout=3)
        except subprocess.TimeoutExpired: client.kill()
