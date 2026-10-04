#!/usr/bin/env python3
"""Exercise Wayland data-device DnD through real pointer input in an isolated session."""
import json
import os
import re
import signal
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time

assert os.environ.get('SCOTTLAND_TEST_MODEL') == '1'
art = Path(sys.argv[1]).resolve()
art.mkdir(parents=True, exist_ok=True)
sock = socket.socket(socket.AF_UNIX)
sock.connect(os.environ['WAYFIRE_SOCKET'])
def ipc(method, data=None):
    body = json.dumps({'method': method, 'data': data or {}}).encode()
    sock.sendall(struct.pack('<I', len(body)) + body)
    def read(n):
        out = b''
        while len(out) < n:
            chunk = sock.recv(n - len(out))
            if not chunk: raise RuntimeError('compositor disconnected')
            out += chunk
        return out
    out = json.loads(read(struct.unpack('<I', read(4))[0]))
    if 'error' in out: raise RuntimeError(out)
    return out
stock = '--stock' in sys.argv
def views():
    if not stock: return ipc('scottland/layout-state')['views']
    return [v | {'frame':v['geometry'],'scale':1,'applied_scale':1,'app_id':v['app-id']}
            for v in ipc('window-rules/list-views') if v['mapped']]
def view(title): return next(v for v in views() if v['title'] == title)
def pointer(x, y): ipc('stipc/move_cursor', {'x': round(x), 'y': round(y)})
def button(mode): ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': mode})
def key(down): ipc('stipc/feed_key', {'key': 'KEY_LEFTMETA', 'state': down})
def move(title, x, y):
    f = view(title)['frame']; sx, sy = f['x'] + f['width']/2, f['y'] + f['height']/2
    pointer(sx, sy); key(True); button('press')
    for i in range(1, 31):
        pointer(sx+(x-sx)*i/30, sy+(y-sy)*i/30); time.sleep(.02)
    button('release'); key(False); time.sleep(1)
def point(title, x, y):
    v = view(title); f = v.get('scene_frame',v['frame'])
    scale = v['applied_scale']
    # Frame bounds are the transformed app surface, including its GTK headerbar.
    return f['x'] + x*scale, f['y'] + y*scale
def records(title):
    path = art/(title+'.jsonl')
    return [json.loads(s) for s in path.read_text().splitlines()] if path.exists() else []
def transfer(source, target):
    sx, sy = source; tx, ty = target
    pointer(sx, sy); button('press'); time.sleep(.15)
    for i in range(1, 61):
        pointer(sx+(tx-sx)*i/60, sy+(ty-sy)*i/60); time.sleep(.02)
    time.sleep(.4); button('release'); time.sleep(.8)

clients = []
launcher_pid = None
launcher_cgroup = None
try:
    ipc('wayfire/set-config-options', {'output:HEADLESS-1/mode': '1600x1000@60000'})
    for title in ('dnd-source', 'dnd-target'):
        clients.append(subprocess.Popen([sys.executable, str(Path(__file__).with_name('dnd-app.py')), title, str(art/(title+'.jsonl'))], env=os.environ|{'WAYLAND_DEBUG':'1'}, stdout=open(art/(title+'.log'), 'w'), stderr=subprocess.STDOUT))
    deadline = time.monotonic()+15
    while time.monotonic()<deadline and len([v for v in views() if v['title'].startswith('dnd-')])<2: time.sleep(.1)
    for title, x in (('dnd-source', 350), ('dnd-target', 800)):
        v = view(title)
        ipc('window-rules/configure-view', {'id': v['id'], 'geometry': {'x': x, 'y': 300, 'width': 400, 'height': 340}})
    time.sleep(2)
    (art/'fixture.json').write_text(json.dumps(views(), indent=2))
    def drag_case(label):
        before = len([r for r in records('dnd-target') if r['event']=='drop'])
        source_frame=view('dnd-source')['frame']
        transfer(point('dnd-source', 180, 85), point('dnd-target', 180, 240))
        subprocess.run(['grim', str(art/(label+'.png'))], check=True)
        drops = [r for r in records('dnd-target') if r['event']=='drop']
        result = {'case': label, 'source': records('dnd-source'), 'target': records('dnd-target'), 'views': views()}
        (art/(label+'.json')).write_text(json.dumps(result, indent=2))
        assert len(drops)==before+1 and drops[-1]['value']=='scottland-dnd-payload', result
        assert abs(drops[-1]['x']-180)<2 and abs(drops[-1]['y']-140)<2, drops[-1]
        assert all(abs(view('dnd-source')['frame'][k]-source_frame[k])<.1
                   for k in ('x','y','width','height')), 'content drag moved the source window'
        print('PASS '+label, flush=True)
    drag_case('center')
    if not stock:
        ipc('wayfire/set-config-options',{'scottland/goo':False}); time.sleep(1)
        drag_case('fallback')
        ipc('wayfire/set-config-options',{'scottland/goo':True}); time.sleep(1)
        ipc('stipc/feed_key',{'key':'KEY_LEFTALT','state':True}); time.sleep(.5)
        assert ipc('scottland/hints')['active'], 'Window mode did not enter'
        try: drag_case('window-mode')
        finally: ipc('stipc/feed_key',{'key':'KEY_LEFTALT','state':False})
        time.sleep(.5)
        move('dnd-target', 1250, 750)
        assert view('dnd-target')['scale'] < .95
        assert not view('dnd-target')['widgetized']
        drag_case('scaled')
        move('dnd-target', 1585, 750)
        deadline = time.monotonic()+15
        while time.monotonic()<deadline and not any(v['title']=='dnd-widget' for v in views()): time.sleep(.1)
        widget = view('dnd-widget')
        assert widget['widget'] and widget['applied_scale']==1, widget
        journal = Path(os.environ['SCOTTLAND_TEST_STATE'])/'dnd-widget.jsonl'
        before = journal.read_text().count('"drop"') if journal.exists() else 0
        transfer(point('dnd-source',180,85), point('dnd-widget',180,240))
        subprocess.run(['grim', str(art/'widget.png')], check=True)
        assert journal.read_text().count('"drop"')==before+1, journal.read_text()
        (art/'widget.jsonl').write_text(journal.read_text())
        print('PASS widget target', flush=True)
    clients[1].terminate(); clients[1].wait(timeout=5)
    browser = subprocess.Popen(['chromium', '--ozone-platform=wayland', '--no-first-run',
        '--user-data-dir='+str(art/'chromium-profile'), '--app='+Path(__file__).with_name('DndBrowser.html').resolve().as_uri()],
        env=os.environ|{'WAYLAND_DEBUG':'1'}, stdout=open(art/'chromium.log', 'w'), stderr=subprocess.STDOUT)
    clients.append(browser)
    deadline = time.monotonic()+20
    while time.monotonic()<deadline and not any(v['title']=='dnd-browser' for v in views()): time.sleep(.1)
    v = view('dnd-browser')
    ipc('window-rules/configure-view', {'id':v['id'], 'geometry':{'x':800,'y':300,'width':400,'height':340}})
    time.sleep(2)
    transfer(point('dnd-source',180,85), point('dnd-browser',180,240))
    subprocess.run(['grim', str(art/'gtk-to-chromium.png')], check=True)
    assert any(v['title']=='dnd-browser-drop-scottland-dnd-payload' for v in views()), views()
    print('PASS GTK to Chromium', flush=True)
    before = len([r for r in records('dnd-source') if r['event']=='drop'])
    transfer(point('dnd-browser-drop-scottland-dnd-payload',180,75), point('dnd-source',180,240))
    subprocess.run(['grim', str(art/'chromium-to-gtk.png')], check=True)
    drops = [r for r in records('dnd-source') if r['event']=='drop']
    assert len(drops)==before+1 and drops[-1]['value']=='scottland-dnd-payload', records('dnd-source')
    print('PASS Chromium to GTK', flush=True)
    if '--nautilus' in sys.argv:
        clients[0].terminate(); clients[0].wait(timeout=5)
        folder=art/'files'; folder.mkdir(exist_ok=True)
        (folder/'dnd-file.txt').write_text('scottland-dnd-payload\n')
        if '--nautilus-launcher' in sys.argv:
            # Instrument only Nautilus. The imported binding, installed Omarchy
            # launcher, uwsm-app and systemd-run scope all remain the real ones.
            # Scope launches inherit the recorded session environment; do not
            # import a headless display into the shared user manager.
            test_bin = Path(os.environ['XDG_STATE_HOME']).parent/'bin'
            wrapper = test_bin/'nautilus'
            snapshot = art/'nautilus-launch.json'
            snapshot.unlink(missing_ok=True)
            wrapper.write_text('#!/usr/bin/python3\n'
                'import json, os, pathlib, sys\n'
                f'pathlib.Path({str(snapshot)!r}).write_text(json.dumps({{"pid":os.getpid(),'
                '"argv":sys.argv,"cgroup":pathlib.Path("/proc/self/cgroup").read_text(),'
                '"environment":{k:os.environ.get(k) for k in '
                '["WAYLAND_DISPLAY","DISPLAY","GDK_BACKEND","XDG_CURRENT_DESKTOP",'
                '"DBUS_SESSION_BUS_ADDRESS","XDG_CONFIG_HOME"]}},indent=2))\n'
                f'fd=os.open({str(art/"nautilus.log")!r},os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)\n'
                'os.dup2(fd,1); os.dup2(fd,2)\n'
                'os.environ["WAYLAND_DEBUG"]="1"\n'
                'os.execv("/usr/bin/nautilus",["nautilus"]+sys.argv[1:])\n')
            wrapper.chmod(0o755)
            assert str(test_bin) in os.environ['PATH'].split(':'), os.environ['PATH']
            for k in ('KEY_LEFTMETA','KEY_LEFTSHIFT','KEY_F'):
                ipc('stipc/feed_key',{'key':k,'state':True})
            for k in ('KEY_F','KEY_LEFTSHIFT','KEY_LEFTMETA'):
                ipc('stipc/feed_key',{'key':k,'state':False})
            deadline=time.monotonic()+20
            while time.monotonic()<deadline and not snapshot.exists(): time.sleep(.1)
            launch=json.loads(snapshot.read_text())
            launcher_pid=launch['pid']
            launcher_cgroup=launch['cgroup']
            assert '.scope' in launch['cgroup'], launch
            assert launch['argv'][1:]==['--new-window'], launch
            assert launch['environment']['WAYLAND_DISPLAY']==os.environ['WAYLAND_DISPLAY'], launch
            assert launch['environment']['GDK_BACKEND']==os.environ['GDK_BACKEND'], launch
            assert launch['environment']['XDG_CURRENT_DESKTOP']==os.environ['XDG_CURRENT_DESKTOP'], launch
            deadline=time.monotonic()+20
            while time.monotonic()<deadline and not any(v['app_id']=='org.gnome.Nautilus' for v in views()): time.sleep(.1)
            v=next(v for v in views() if v['app_id']=='org.gnome.Nautilus')
            f=v['frame']; pointer(f['x']+f['width']/2,f['y']+f['height']/2)
            button('press'); button('release'); time.sleep(.3)
            subprocess.run(['wtype','-M','ctrl','-k','l','-m','ctrl','-s','100',str(folder),'-k','Return'],check=True)
        else:
            clients.append(subprocess.Popen(['nautilus','--new-window',str(folder)],
                env=os.environ|{'WAYLAND_DEBUG':'1'}, stdout=open(art/'nautilus.log','w'), stderr=subprocess.STDOUT))
        deadline=time.monotonic()+20
        while time.monotonic()<deadline and not any(v['title']=='files' for v in views()): time.sleep(.1)
        v=view('files')
        ipc('window-rules/configure-view',{'id':v['id'],'geometry':{'x':300,'y':250,'width':600,'height':500}})
        v=view('dnd-browser-drop-scottland-dnd-payload')
        ipc('window-rules/configure-view',{'id':v['id'],'geometry':{'x':950,'y':300,'width':400,'height':340}})
        time.sleep(2)
        subprocess.run(['grim',str(art/'nautilus-before.png')],check=True)
        icon_x=280 if view('files')['frame']['width']>800 else 110
        transfer(point('files',icon_x,120),point('dnd-browser-drop-scottland-dnd-payload',330,240))
        subprocess.run(['grim',str(art/'nautilus-after.png')],check=True)
        trace=(art/'nautilus.log').read_text()
        events=[line for line in trace.splitlines() if re.search(
            r'\.(start_drag|enter|leave|drop|cancelled|dnd_drop_performed|dnd_finished)\(',line)
            and ('wl_data_' in line)]
        (art/'nautilus-drag-events.log').write_text('\n'.join(events)+'\n')
        print('Nautilus drag protocol: '+json.dumps(events),flush=True)
        assert any(v['title']=='dnd-browser-drop-dnd-file.txt' for v in views()), views()
        print('PASS Nautilus file to Chromium',flush=True)
        if not stock:
            move('dnd-browser-drop-dnd-file.txt',1250,750)
            assert view('dnd-browser-drop-dnd-file.txt')['applied_scale']<.95
            # Reload the target with real input so a second successful drop is
            # observable independently of the first identical filename.
            pointer(*point('dnd-browser-drop-dnd-file.txt',330,240))
            button('press'); button('release')
            subprocess.run(['wtype','-M','ctrl','-k','r','-m','ctrl'],check=True)
            deadline=time.monotonic()+10
            while time.monotonic()<deadline and not any(v['title']=='dnd-browser' for v in views()): time.sleep(.1)
            transfer(point('files',icon_x,120),point('dnd-browser',330,240))
            assert any(v['title']=='dnd-browser-drop-dnd-file.txt' for v in views()), views()
            subprocess.run(['grim',str(art/'nautilus-scaled.png')],check=True)
            print('PASS Nautilus file to scaled Chromium',flush=True)
            clients.append(subprocess.Popen([sys.executable,str(Path(__file__).with_name('dnd-app.py')),
                'dnd-target',str(art/'dnd-target.jsonl')]))
            deadline=time.monotonic()+15
            while time.monotonic()<deadline and not any(v['title']=='dnd-target' for v in views()): time.sleep(.1)
            v=view('dnd-target')
            ipc('window-rules/configure-view',{'id':v['id'],'geometry':{'x':800,'y':200,'width':400,'height':340}})
            time.sleep(1)
            move('dnd-target',1585,600)
            deadline=time.monotonic()+15
            while time.monotonic()<deadline and not any(v['title']=='dnd-widget' for v in views()): time.sleep(.1)
            journal=Path(os.environ['SCOTTLAND_TEST_STATE'])/'dnd-widget.jsonl'
            before=journal.read_text().count('"file-drop"') if journal.exists() else 0
            transfer(point('files',icon_x,120),point('dnd-widget',180,240))
            events=[json.loads(s) for s in journal.read_text().splitlines()]
            drops=[e for e in events if e['event']=='file-drop']
            assert len(drops)==before+1 and drops[-1]['names']==['dnd-file.txt'], events
            assert abs(drops[-1]['x']-180)<2 and abs(drops[-1]['y']-140)<2, drops[-1]
            (art/'nautilus-widget.jsonl').write_text(journal.read_text())
            subprocess.run(['grim',str(art/'nautilus-widget.png')],check=True)
            print('PASS Nautilus file to widget',flush=True)
    protocol=[]
    for name in ('dnd-source','chromium','nautilus'):
        path=art/(name+'.log')
        if not path.exists(): continue
        text=path.read_text()
        presses=set(re.findall(r'wl_pointer#\d+\.button\((\d+), \d+, 272, 1\)',text))
        requests=list(re.finditer(r'wl_data_device#\d+\.start_drag\([^\n]*, (\d+)\)',text))
        assert requests, ('source sent no start_drag',name)
        for index, request in enumerate(requests):
            assert request[1] in presses, ('start_drag serial is not its left press',name,request[1])
            end=requests[index+1].start() if index+1<len(requests) else len(text)
            assert re.search(r'wl_data_device#\d+\.enter\(',text[request.end():end]), ('drag rejected',name,request[1])
        protocol.append({'source':name,'accepted_start_drag_serials':[r[1] for r in requests]})
    (art/'protocol.json').write_text(json.dumps(protocol,indent=2))
    print('PASS start_drag serials and compositor DnD enters',flush=True)
finally:
    if launcher_pid is not None:
        try:
            process=Path('/proc')/str(launcher_pid)
            if process.joinpath('cgroup').read_text()==launcher_cgroup:
                os.kill(launcher_pid, signal.SIGTERM)
        except (FileNotFoundError, ProcessLookupError): pass
    for client in clients:
        if client.poll() is None: client.terminate()
    for client in clients: client.wait(timeout=5)
