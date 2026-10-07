#!/usr/bin/env python3
"""WK42/S23: one absolute hint background opacity for window and widget hints, set by real
Settings input and by scottland-ctl, judged by independently captured pixels.
IPC arranges/focuses fixtures and observes geometry; it never edits the tested setting.
Run only in a caller-owned headless session; artifacts remain outside the runtime.
"""
import json
import math
import os
from pathlib import Path
import signal
import socket
import struct
import subprocess
import sys
import time

import gi
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf

assert os.environ.get('SCOTTLAND_TEST_MODEL') == '1', 'caller-owned headless session required'
repo = Path(__file__).resolve().parents[1]
art = Path(sys.argv[1]).resolve(); art.mkdir(parents=True, exist_ok=True)
log = (art / 'clients.log').open('w')
layout = art / 'settings/scottland/layout.ini'; layout.parent.mkdir(parents=True, exist_ok=True)
layout.unlink(missing_ok=True)
sock = socket.socket(socket.AF_UNIX); sock.settimeout(8); sock.connect(os.environ['WAYFIRE_SOCKET'])
clients, held, observations = [], set(), []
passed = failed = 0
panel = None


def ipc(method, data=None):
    body = json.dumps(dict(method=method, data=data or {})).encode()
    sock.sendall(struct.pack('<I', len(body)) + body)
    def read(n):
        result = b''
        while len(result) < n:
            chunk = sock.recv(n - len(result))
            if not chunk: raise RuntimeError('compositor disconnected')
            result += chunk
        return result
    result = json.loads(read(struct.unpack('<I', read(4))[0]))
    if isinstance(result, dict) and 'error' in result: raise RuntimeError(f'{method}: {result}')
    return result


def wait(predicate, description, timeout=12):
    end = time.monotonic() + timeout; last = None
    while time.monotonic() < end:
        last = predicate()
        if last: return last
        time.sleep(.04)
    raise RuntimeError(f'timed out waiting for {description}; last={last}')


def check(name, condition):
    global passed, failed
    print(('PASS ' if condition else 'FAIL ') + name, flush=True)
    passed += bool(condition); failed += not condition


def key(code, down):
    ipc('stipc/feed_key', dict(key='KEY_' + code, state=down))
    if down: held.add(code)
    else: held.discard(code)


def pointer(x, y):
    ipc('stipc/move_cursor', dict(x=round(x), y=round(y)))


def button(down):
    ipc('stipc/feed_button', dict(combo='BTN_LEFT', mode='press' if down else 'release'))
    if down: held.add('BUTTON')
    else: held.discard('BUTTON')


def click(x, y):
    pointer(x, y); button(True); button(False)


def drag(x, y, dx, dy):
    pointer(x, y); button(True)
    for i in range(1, 17):
        pointer(x + dx * i / 16, y + dy * i / 16)
        time.sleep(.025)  # pace a continuous drag, not a readiness wait
    button(False)


def option(field='value'):
    return float(ipc('wayfire/get-config-option', {'option':'scottland/hint_background_opacity'})[field])


def hints():
    return {h['window']: h for h in ipc('scottland/hints')['hints']}


def views():
    return {v['id']:v for v in ipc('scottland/layout-state')['views']}


def mapped(name):
    return next((v for v in views().values() if v.get('title') == name and v.get('frame')), None)


def stable():
    previous, count = None, 0
    def settled():
        nonlocal previous, count
        state = hints()
        if not all(i in state and state[i].get('circle', {}).get('size', 0) > 0 and
                   abs(state[i].get('pop', 0) - 1) < .001 and
                   abs(state[i]['dx'] - state[i]['target_dx']) + abs(state[i]['dy'] - state[i]['target_dy']) < .1
                   for i in (window, widget)):
            count = 0; return False
        now = {i:(state[i]['circle'], state[i]['dx'], state[i]['dy']) for i in (window, widget)}
        count = count + 1 if now == previous else 0; previous = now
        return state if count >= 4 else False
    return wait(settled, 'settled rendered hint geometry')


class Shot:
    def __init__(self, name):
        subprocess.run(['grim', str(art / (name + '.png'))], check=True, timeout=8)
        self.img = GdkPixbuf.Pixbuf.new_from_file(str(art / (name + '.png')))
        self.data = self.img.get_pixels(); self.stride = self.img.get_rowstride(); self.channels = self.img.get_n_channels()
    def pixel(self, x, y):
        offset = round(y) * self.stride + round(x) * self.channels
        return tuple(self.data[offset:offset + 3])


def distance(a, b): return max(abs(x-y) for x,y in zip(a,b))


THEME=(31,35,44)  # the session palette background (#1f232c) below
def backing(color):
    """WK42's one backing for every hint: the theme background under a 21% hint-color tint."""
    return tuple(.79*b+.21*d for b,d in zip(THEME,color))


def coverage(shot, points, under, color):
    """Least-squares opacity of the backing over `under` at the fill points, from pixels alone."""
    f=backing(color); num=den=0
    for p in points:
        u=under(p); q=shot.pixel(*p)
        for a,b,c in zip(q,u,f): num+=(a-b)*(c-b); den+=(c-b)**2
    return num/den if den else float('nan')


def quickshell_pid(pid):
    def search():
        pending, seen = [pid], set()
        while pending:
            n = pending.pop()
            if n in seen: continue
            seen.add(n); p = Path(f'/proc/{n}')
            try:
                if (p/'comm').read_text().strip() == 'quickshell' and str(repo/'core/settings') in (p/'cmdline').read_bytes().decode().split('\0'):
                    return n
                pending.extend(map(int,(p/'task'/str(n)/'children').read_text().split()))
            except (OSError, ValueError): pass
        return None
    return wait(search, 'owned Quickshell process')


def snapshot():
    try:
        return json.loads(subprocess.check_output(['qs','ipc','--pid',str(qs_pid),'call','settings-test','snapshot'], text=True, timeout=5))
    except (subprocess.CalledProcessError, json.JSONDecodeError): return {}


def open_panel():
    global panel, qs_pid, panel_x, panel_y
    panel = subprocess.Popen(['qs','-n','-p',str(repo/'core/settings')], stdout=log, stderr=log,
        env=dict(os.environ, QS_DISABLE_FILE_WATCHER='1', SCOTTLAND_CTL=str(repo/'core/libexec/scottland-ctl'),
            SCOTTLAND_LAYOUT_FILE=str(layout), SCOTTLAND_SOLAR_FILE=str(art/'settings/solar.ini'),
            SCOTTLAND_PALETTE=str(palette_path), SCOTTLAND_SETTINGS_TEST='1', SCOTTLAND_HINT_PROBE='0'))
    clients.append(panel); qs_pid = quickshell_pid(panel.pid)
    q = wait(lambda: snapshot() if snapshot().get('screen') else None, 'Settings map')
    panel_x = (1280 - q['panel']['width']) / 2
    panel_y = 720 - max(24, round(720*.04)) - q['panel']['height']
    click(panel_x + 36 + 2.5*(q['panel']['width']-72)/6, panel_y+100)
    wait(lambda: snapshot().get('tab') == 2, 'Window mode tab from one click')
    previous, count = None, 0
    def laid_out():
        nonlocal previous, count
        q=snapshot(); now=(q.get('contentHeight'),q.get('windowTintSettings'),q.get('viewport'))
        count=count+1 if now==previous else 0;previous=now
        return count>=4
    wait(laid_out,'settled Window mode panel layout')
    reveal()
    return snapshot()


def reveal():
    q=snapshot(); observations.append({'panel-before-reveal':q}); v=q['viewport']; row=q['windowTintSettings']; offset=row['rowHeight']+1
    target_y=row['y']+offset+row['rowHeight']/2
    if not v['y']+20 < target_y < v['y']+v['height']-20:
        target=max(0,min(q['contentHeight']-v['height'],q['scroll']+target_y-v['y']-v['height']/2))
        thumb=v['height']**2/q['contentHeight']
        start=panel_y+v['y']+q['scroll']/q['contentHeight']*v['height']+thumb/2
        end=panel_y+v['y']+target/q['contentHeight']*v['height']+thumb/2
        drag(panel_x+v['x']+v['width']-5,start,0,end-start)
        def revealed():
            q=snapshot();observations.append({'scroll':q['scroll'],'target':target,'viewport':q['viewport'],'row':q['windowTintSettings']})
            y=q['windowTintSettings']['y']+offset+row['rowHeight']/2;v=q['viewport']
            return v['y']+20<y<v['y']+v['height']-20
        wait(revealed, 'scrollbar reveals hint opacity row')
    q=snapshot(); row=q['windowTintSettings']
    return panel_x+row['x'],panel_y+row['y']+row['rowHeight']+1,row['width'],row['rowHeight']


def set_slider(value):
    x,y,w,h=reveal()
    click(x+max(1,min(w-1,w*value/100)),y+h/2)
    wait(lambda: abs(option()-value)<.1, f'one slider click reaches {value}%')
    pointer(640,5)


def close_panel(save=False):
    q=snapshot()
    click(panel_x+q['panel']['width']-(86 if save else 196),panel_y+q['panel']['height']-56)
    wait(lambda: panel.poll() is not None,'Settings closes after one action')
    panel.wait(timeout=5)


def interrupted(signum, frame): raise RuntimeError(f'interrupted by signal {signum}')
signal.signal(signal.SIGTERM, interrupted)
signal.signal(signal.SIGINT, interrupted)

try:
    palette_path=art/'palette.json'
    palette_path.write_text(json.dumps(dict(scheme='dark',background='#1f232c',foreground='#d8deea',accent='#81a1c1')))
    session_palette=Path(os.environ['XDG_RUNTIME_DIR'])/'scottland'/(os.environ['WAYLAND_DISPLAY']+'.palette.json')
    temp=session_palette.with_suffix('.opacity-test.tmp'); temp.write_text(palette_path.read_text()); temp.replace(session_palette)
    ipc('wayfire/set-config-options',{'scottland/color_scheme':'dark','scottland/accent_color':'#81a1c1ff','scottland/sounds':False})
    light_path=art/'light-client.json'
    light_path.write_text(json.dumps(dict(background='#d8deea')))  # fixture client surface, not the theme
    def app(name,g,surface=palette_path):
        p=subprocess.Popen(['python3',str(repo/'tests/hint-style-app.py'),name,str(g[2]),str(g[3]),str(surface)],stdout=log,stderr=log)
        clients.append(p); v=wait(lambda:mapped(name),name+' maps')
        v_id=v['id']
        ipc('window-rules/configure-view',dict(id=v_id,geometry=dict(zip(('x','y','width','height'),g))))
        wait(lambda: next(v['geometry'] for v in ipc('window-rules/list-views') if v['id']==v_id) == dict(zip(('x','y','width','height'),g)),name+' fixture geometry')
        return v['id']
    window=app('OpacityWindow',(-20,160,270,440),light_path)
    widget=app('OpacityWidget',(530,180,280,200))
    frame=views()[widget]['frame']; cx=frame['x']+frame['width']/2; cy=frame['y']+frame['height']/2
    key('LEFTMETA',True); drag(cx,cy,1272-cx,0); key('LEFTMETA',False); pointer(640,5)
    wait(lambda: any(int(w['id'])==widget and int(w.get('widget_view',-1))>0 and int(w['widget_view']) in views() for w in ipc('scottland/widgets')['widgets']), 'real drag produces widget')
    widget_view=next(w['widget_view'] for w in ipc('scottland/widgets')['widgets'] if int(w['id'])==widget)
    ipc('window-rules/configure-view',dict(id=int(widget_view),geometry={'x':1190,'y':20,'width':80,'height':90}))  # fixture card footprint keeps its hint clear of Settings
    ipc('window-rules/focus-view',{'id':window})  # fixture focus only
    default=option('default')
    check('one shipped default: 21%',default==21)
    key('LEFTALT',True); wait(lambda:ipc('scottland/hints')['active'],'real Alt hold enters Window mode')
    state=stable(); observations.append(state)
    def fill_points(i, shot):
        c=state[i]['circle']; r=c['size']/2; cx=c['x']+r; cy=c['y']+r
        color=tuple(round(v*255) for v in state[i]['color'])
        ring=[(round(cx+r*.76*math.cos(t)),round(cy+r*.76*math.sin(t))) for t in (n*math.pi/24 for n in range(48))]
        return [p for p in set(ring) if distance(shot.pixel(*p),color)>10], color
    def inside(p, i, margin=8):
        # Over the widget card (or its goo shore) rather than the wallpaper beside it.
        f=card; dx,dy=state[i]['dx'],state[i]['dy']
        return (f['x']+dx-margin<=p[0]<=f['x']+dx+f['width']+margin and
                f['y']+dy-margin<=p[1]<=f['y']+dy+f['height']+margin)
    images={}
    images['default']=Shot('opacity-default')
    # C3: the option alone, written by scottland-ctl while hints show, must redraw the backing.
    ctl=[str(repo/'core/libexec/scottland-ctl'),'set','hint_background_opacity']
    subprocess.run(ctl+['100'],check=True,timeout=10,stdout=log,stderr=log)
    points,color=fill_points(window,images['default'])
    def solid():
        shot=Shot('ctl-100')
        return shot if all(distance(shot.pixel(*p),backing(color))<=4 for p in points) else None
    try: live=wait(solid,'scottland-ctl change redraws the showing backing',timeout=4)
    except RuntimeError: live=None
    check('a lone scottland-ctl write redraws showing hints live',live is not None)
    subprocess.run(ctl+[str(default)],check=True,timeout=10,stdout=log,stderr=log)
    key('LEFTALT',False); wait(lambda:not ipc('scottland/hints')['active'],'Alt release before Settings')
    q=open_panel()
    check('Settings slider reads the 21% default',q['motion']['hint_background_opacity']==default and option()==default)
    check('opening Settings writes no layout file',not layout.exists())
    key('LEFTALT',True); wait(lambda:ipc('scottland/hints')['active'],'real Alt hold enters Window mode')
    state=stable(); observations.append(state)
    for i in (window,widget):
        c=state[i]['circle']
        assert c['x']+c['size']<panel_x or c['x']>panel_x+q['panel']['width'] or c['y']+c['size']<panel_y, 'fixture hint must be clear of changing Settings content'
    images['default']=Shot('opacity-default-held')
    for value in (0,100,50):
        set_slider(value); stable()
        # Captures poll the independent fill pixels after the single action, not merely option telemetry.
        circle=state[window]['circle']; cx=circle['x']+circle['size']/2; cy=circle['y']+circle['size']/2
        probe=(cx,cy+circle['size']*.37)
        def rendered():
            shot=Shot(f'opacity-{value}')
            return shot if distance(shot.pixel(*probe),images['default'].pixel(*probe))>2 else None
        images[value]=wait(rendered,'new backing pixels')
        observations.append(dict(value=value,hints=hints()))
    f=views()[window]['frame']; content=(round(f['x']+f['width']/2),round(f['y']+f['height']*.85))
    card=dict(views()[int(widget_view)]['frame'])
    key('LEFTALT',False);wait(lambda:not ipc('scottland/hints')['active'] and not any(h.get('visible') for h in hints().values()),'Alt release')
    # What lies beneath the hints once they are gone (wallpaper, Settings' zone labels).
    images['released']=wait(lambda:(lambda a,b:b if a.data==b.data else None)(Shot('released-a'),Shot('released')),'still frame after release')
    alphas={}
    for name,i in (('window',window),('widget',widget)):
        c=state[i]['circle']; r=c['size']/2; cx=c['x']+r; cy=c['y']+r
        fill,color=fill_points(i,images[0])
        observations.append(dict(kind=name,fill=fill,color=color,
            pixels={str(v):[images[v].pixel(*p) for p in fill] for v in images}))
        # 0%: no backing at all. Over the window the fill shows exactly the client's own (tinted)
        # surface sampled away from the hint; beside the widget, exactly what is there once the
        # hints are gone (Window mode tints windows and cards, not the wallpaper).
        if name=='window': bare=fill; beneath=lambda p: images[0].pixel(*content)
        else: bare=[p for p in fill if not inside(p,i)]; beneath=lambda p: images['released'].pixel(*p)
        check(name+': 0% leaves no backing (fill matches the surface beneath)',len(bare)>12 and
              all(distance(images[0].pixel(*p),beneath(p))<=3 for p in bare))
        check(name+': 100% is a solid themed backing',len(fill)>20 and
              all(distance(images[100].pixel(*p),backing(color))<=4 for p in fill))
        under=lambda p: images[0].pixel(*p)
        alphas[name]={v:coverage(images[v],fill,under,color) for v in ('default',50,100)}
        check(name+': 50% backing is half opaque',abs(alphas[name][50]-.5)<=.05)
        check(name+': default backing is 21% opaque',abs(alphas[name]['default']-.21)<=.05)
        # Fully covered glyph pixels only: over a light surface an edge pixel at 97% coverage is
        # within a few levels of the ink and would then show the backing change through it.
        ink=[(x,y) for x in range(round(cx-r*.55),round(cx+r*.55)) for y in range(round(cy-r*.65),round(cy+r*.65)) if distance(images[0].pixel(x,y),color)<=1]
        check(name+': opaque letter ink is unchanged at all slider values',len(ink)>=8 and all(distance(images[v].pixel(*p),images[0].pixel(*p))<=3 for v in (100,50,'default') for p in ink))
        # Partial rim/glyph coverage composites over the changing fill; only opaque ink
        # can prove unchanged foreground independently of that backing.
        rim=[(x,y) for x in range(round(cx-r-4),round(cx+r+4)) for y in range(round(cy-r-4),round(cy+r+4))
             if r+.75<=math.hypot(x-cx,y-cy)<=r+3 and distance(images[0].pixel(x,y),color)<=1]  # clear of the fill's own antialiased edge
        if str(ipc('wayfire/get-config-option',{'option':'scottland/goo'})['value']).lower() in ('false','0'):
            check(name+': opaque fallback rim ink remains unchanged',len(rim)>=8 and all(distance(images[100].pixel(*p),images[0].pixel(*p))<=3 for p in rim))
    print('measured backing opacity',json.dumps(alphas),flush=True); observations.append(dict(alphas=alphas))
    check('window and widget hints show the same opacity at every value',
          all(abs(alphas['window'][v]-alphas['widget'][v])<=.04 for v in ('default',50,100)))
    check('window content/opacity outside the hint is unchanged',all(distance(images[v].pixel(*content),images[100].pixel(*content))<=2 for v in (0,50,'default')))
    check('live previews still write no layout file',not layout.exists())
    set_slider(37); close_panel(save=True)
    check('Save persists hint backing separately',layout.exists() and 'hint_background_opacity = 37\n' in layout.read_text() and option()==37)
    saved=layout.read_bytes()
    consumed=subprocess.check_output([str(repo/'core/config.d/20-layout-settings')],env=dict(os.environ,XDG_CONFIG_HOME=str(art/'settings')),text=True,timeout=5)
    check('next config generation consumes the saved hint opacity', 'hint_background_opacity = 37\n' in consumed)
    q=open_panel()
    check('reopen reads saved/current 37%',q['motion']['hint_background_opacity']==37)
    set_slider(80); close_panel()
    wait(lambda:option()==37,'Cancel restores opening backing')
    check('Cancel restores 37% and leaves the saved file unchanged',option()==37 and layout.read_bytes()==saved)
    q=open_panel();click(panel_x+80,panel_y+q['panel']['height']-56)
    wait(lambda:option()==default,'Defaults restores the default backing')
    check('Defaults previews the plugin default without saving',option()==default and layout.read_bytes()==saved)
    close_panel();wait(lambda:option()==37,'Cancel reverses Defaults')
    check('Cancel after Defaults restores opening opacity',option()==37 and layout.read_bytes()==saved)
except Exception:
    import traceback;traceback.print_exc();failed+=1
finally:
    for code in list(held):
        try: button(False) if code=='BUTTON' else key(code,False)
        except Exception: pass
    for p in clients:
        if p.poll() is None: p.terminate()
    for p in clients:
        try:p.wait(timeout=5)
        except subprocess.TimeoutExpired:p.kill();p.wait(timeout=5)
    if 'session_palette' in globals():session_palette.unlink(missing_ok=True)
    (art/'observations.json').write_text(json.dumps(observations,indent=2))
    sock.close();log.close()
    print(f'{passed} passed, {failed} failed',flush=True)
    raise SystemExit(1 if failed else 0)
