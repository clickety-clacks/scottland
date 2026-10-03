#!/usr/bin/env python3
"""GO16, real rail/collapse input and badge commits, plus a round custom widget.
Run inside an isolated --widgets session with SCOTTLAND_WIDGET_PATH=tests/widgets.
--inset-breath-only checks GO17 attention on a deeply inset GO16 alpha body.
--baseline captures the same scenes without the new shape assertions.
"""
import argparse, importlib.util, json, math, subprocess, time
from pathlib import Path
import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf
repo = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('widget_input', repo/'tests/widget-input-test.py')
t = importlib.util.module_from_spec(spec); spec.loader.exec_module(t)
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('artifacts', type=Path)
parser.add_argument('--baseline', action='store_true')
parser.add_argument('--fallback-only', action='store_true')
parser.add_argument('--offset-only', action='store_true', help='check controls on a body outside the surface center')
parser.add_argument('--inset-breath-only', action='store_true', help='GO16+GO17: settled attention on a body deeply inset from its surface')
args=parser.parse_args();args.artifacts.mkdir(parents=True,exist_ok=True)
records=[]
def options(**values):
    t.ipc.call('wayfire/set-config-options',{'scottland/'+k:v for k,v in values.items()})
def sample(x,y):
    return t.ipc.call('scottland/goo-state',{'x':x,'y':y})['screens'][0]
def badge(count):
    subprocess.run(['busctl','--user','emit','/com/canonical/unity/launcherentry/1',
        'com.canonical.Unity.LauncherEntry','Update','sa{sv}','application://foot.desktop',
        '2','count','x',str(count),'count-visible','b','true'],check=True)
    time.sleep(.65)
def shot(label,title):
    f=t.card(title)['frame'];path=args.artifacts/(label+'.png')
    subprocess.run(['grim',str(path)],check=True)
    pixbuf=GdkPixbuf.Pixbuf.new_from_file(str(path));r=22
    data=pixbuf.get_pixels()
    def im(at):
        x,y=map(round,at);i=y*pixbuf.get_rowstride()+x*pixbuf.get_n_channels()
        return tuple(data[i:i+3])
    x1=max(0,round(f['x'])-r);y1=max(0,round(f['y'])-r)
    w=min(pixbuf.get_width()-x1,round(f['width'])+2*r)
    h=min(pixbuf.get_height()-y1,round(f['height'])+2*r)
    crop=pixbuf.new_subpixbuf(x1,y1,w,h)
    crop.scale_simple(w*3,h*3,GdkPixbuf.InterpType.NEAREST).savev(str(args.artifacts/(label+'-crop.png')),'png',[],[])
    records.append({'case':label,'frame':f})
    return f,im
def shape_assert(label,f,im,rail,count,goo):
    x=f['x']+f['width']/2;y=f['y']+6
    if goo:
        d=sample(x,y)['window_distance']
        t.check(label+': goo meets body at reserved top inset',abs(d)<1.1,d)
        d=sample(x,f['y']+2)['window_distance']
        t.check(label+': transparent band is outside body',d>2.5,d)
        bx=f['x']+f['width']-18 if rail=='left' else f['x']+18
        d=sample(bx,f['y']+4)['window_distance']
        t.check(label+': badge participates in body' if count else label+': empty badge reservation excluded',
                d<0 if count else d>0,d)
    # Sample visible liquid inside the old reserved gap, immediately above the straight body.
    px,py=round(x),round(y-2)
    bg=im((t.screen['width']//2,t.screen['height']-40))[:3]
    ink=im((px,py))[:3]
    t.check(label+': visible halo reaches body',sum(abs(a-b) for a,b in zip(ink,bg))>25,(ink,bg))
    t.move(x,y-2);time.sleep(.1)
    t.check(label+': gap pixels are a move handle',t.card('alpha-card')['frame']['hovered']=='halo',t.card('alpha-card')['frame'])
    t.move(t.screen['width']/2,t.screen['height']-40)
try:
    options(sounds=False,goo_noise=0.,goo_drift=0.,goo_wave_height=0.,goo_swell=0.,
            goo_hover_cloudiness=0.,goo_hover_emissivity=0.)
    if args.inset_breath_only:
        options(goo=True)
        title='inset-breath'
        t.launch(title,'left',240,app_id='scottland-alpha-shape')
        f=t.card(title)['frame'];cx=f['x']+f['width']/2;cy=f['y']+f['height']/2
        focus=t.launch('inset-breath-focus',rail=None)
        t.ipc.call('window-rules/configure-view',{'id':focus['id'],
            'geometry':{'x':0,'y':0,'width':100,'height':60}})
        time.sleep(.4)
        focus_frame=t.app('inset-breath-focus')['frame']
        t.move(focus_frame['x']+focus_frame['width']/2,focus_frame['y']+focus_frame['height']/2)
        t.ipc.call('stipc/feed_button',{'combo':'BTN_LEFT','mode':'full'})
        t.move(t.screen['width']/2,t.screen['height']-40)
        requested=t.ipc.call('scottland/attention',{'window':int(t.widgets()[0]['id']),
            'attention':True,'source':'shape-breath-test'})
        t.check('inset attention belongs to unfocused widget',not requested.get('in_front',False),requested)
        for _ in range(450):
            if sample(cx,cy)['sleeping']:break
            time.sleep(.1)
        before=sample(cx,cy)
        t.check('inset widget settles with attention',before['sleeping'] and before['breath_ticks']>0,before)
        t.check('inset body has an alpha shape',bool(f.get('alpha_shape')),f)
        # With the old rectangle-strip damage, the center of this 400px surface
        # is outside every breathing strip despite being the 96px body's shore.
        shore=(round(cx+50),round(cy))
        damaged=any(r['x']<=shore[0]<r['x']+r['width'] and r['y']<=shore[1]<r['y']+r['height']
                    for r in before['breath_damage'])
        t.check('deeply inset alpha shore receives breath damage',damaged,before['breath_damage'])
        shots={};deadline=time.monotonic()+12
        while time.monotonic()<deadline and len(shots)<2:
            s=sample(*shore)
            label='trough' if s['breath']<.003 else 'peak' if s['breath']>.997 else None
            if label and label not in shots:
                path=args.artifacts/('inset-'+label+'.png')
                subprocess.run(['grim',str(path)],check=True)
                shots[label]=GdkPixbuf.Pixbuf.new_from_file(str(path))
            time.sleep(.04)
        after=sample(cx,cy)
        t.check('inset attention breath runs while simulation sleeps',
                after['sleeping'] and after['steps']==before['steps'] and after['breath_ticks']>before['breath_ticks'],
                (before['steps'],after['steps'],before['breath_ticks'],after['breath_ticks']))
        t.check('inset breath reaches both extrema',len(shots)==2,sorted(shots))
        if len(shots)==2:
            a,b=shots['trough'],shots['peak'];da,db=a.get_pixels(),b.get_pixels()
            stride=a.get_rowstride();channels=a.get_n_channels();changed=0
            for y in range(max(0,round(cy-75)),min(a.get_height(),round(cy+76))):
                for x in range(max(0,round(cx-75)),min(a.get_width(),round(cx+76))):
                    radius=math.hypot(x-cx,y-cy)
                    if not 46<=radius<=75:continue
                    k=y*stride+x*channels
                    changed+=da[k:k+3]!=db[k:k+3]
            t.check('inset widget contour visibly breathes',changed>100,changed)
        t.ipc.call('scottland/attention',{'window':int(t.widgets()[0]['id']),
            'attention':False,'source':'shape-breath-test'})
        t.cleanup()
        raise SystemExit(bool(t.failures))
    if args.offset_only:
        for goo in ((False,) if args.fallback_only else (True,False)):
            options(goo=goo)
            title='offset-'+str(goo)
            t.launch(title,'left',240,app_id='scottland-alpha-shape')
            t.move(600,600);time.sleep(.7)
            f,im=shot('offset-'+str(goo),title)
            if goo:
                d=sample(f['x']+120,f['y']+60)['window_distance']
                t.check('off-center widget surface center is outside its body',d>10,d)
            x=f['x']+60;y=f['y']+108+f['thickness']/2
            t.move(x,y);time.sleep(.3);t.move(x+1,y);time.sleep(.1)
            t.check(('goo' if goo else 'fallback')+' close follows off-center body',t.card(title)['frame']['hovered']=='close',t.card(title)['frame'])
            t.ipc.call('stipc/feed_button',{'combo':'BTN_LEFT','mode':'full'})
            closed=t.wait_for(lambda:not t.app(title) and not t.card(title))
            t.check(('goo' if goo else 'fallback')+' off-center close closes both forms',closed)
            t.cleanup()
        raise SystemExit(bool(t.failures))
    t.launch('alpha-card','left',240)
    for goo in ((False,) if args.fallback_only else (True,False)):
        options(goo=goo)
        for collapsed in (False,True):
            if collapsed:t.toggle();time.sleep(.8)
            for rail in ('left','right'):
                t.drag_begin(t.card('alpha-card'),6 if rail=='left' else t.screen['width']-6,240)
                t.drag_end();t.move(t.screen['width']/2,t.screen['height']-40);time.sleep(1)
                for count in (0,7,123,0):
                    badge(count)
                    label=f"{'goo' if goo else 'halo'}-{rail}-{'collapsed' if collapsed else 'expanded'}-{count}"
                    f,im=shot(label,'alpha-card')
                    if not args.baseline:shape_assert(label,f,im,rail,count,goo)
            if collapsed:t.toggle();time.sleep(.8)
    if not args.baseline:
        f=t.card('alpha-card')['frame'];before=f['alpha_shape']['builds']
        badge(0);time.sleep(.3)
        t.check('same alpha does not rebuild SDF',t.card('alpha-card')['frame']['alpha_shape']['builds']==before)
        # Badge changes while settled must wake goo without pointer input.
        options(goo=not args.fallback_only)
        for _ in range(0 if args.fallback_only else 400):
            if sample(600,600)['sleeping']:break
            time.sleep(.05)
        if not args.fallback_only:t.check('shape settles with goo',sample(600,600)['sleeping'])
        badge(7)
        if not args.fallback_only:t.check('badge commit wakes sleeping field',not sample(600,600)['sleeping'])
        stats=t.card('alpha-card')['frame']['alpha_shape'];records.append({'mask_cost':stats})
        t.check('ordinary window has no alpha cache', 'alpha_shape' not in t.app('alpha-card')['frame'])
        old=t.card('alpha-card')['frame']['alpha_shape']['builds']
        t.key('LEFTALT',True);time.sleep(.5)
        t.check('window-mode tint preserves widget alpha',t.card('alpha-card')['frame']['alpha_shape']['builds']==old)
        t.key('LEFTALT',False);time.sleep(.25)
    t.cleanup()
    options(goo=not args.fallback_only)
    t.launch('round-widget','left',240,app_id='scottland-alpha-shape')
    t.move(600,600);time.sleep(1)
    f,im=shot('round-widget','round-widget');cx=f['x']+f['width']/2;cy=f['y']+f['height']/2
    if not args.baseline:
        for angle in (() if args.fallback_only else (0,math.pi/4,math.pi/2,3*math.pi/4,math.pi)):
            d=sample(cx+48*math.cos(angle),cy+48*math.sin(angle))['window_distance']
            t.check('round widget radial boundary '+str(angle),abs(d)<1.1,d)
        if not args.fallback_only:
            d=sample(f['x']+8,f['y']+8)['window_distance']
            t.check('round widget surface corners are empty',d>20,d)
        bg=im((t.screen['width']/2,t.screen['height']-40))
        corner=im((f['x']+4,f['y']+4))
        t.check('round halo leaves rectangle corners empty',sum(abs(a-b) for a,b in zip(bg,corner))<6,(bg,corner))
        arc=im((cx+52/math.sqrt(2),cy+52/math.sqrt(2)))
        t.check('round halo follows the circular arc',sum(abs(a-b) for a,b in zip(bg,arc))>15,(bg,arc))
        stats=t.card('round-widget')['frame']['alpha_shape'];time.sleep(1.1)
        later=t.card('round-widget')['frame']['alpha_shape']
        t.check('color-only buffer commits reuse the SDF',later['builds']==stats['builds'],(stats,later))
        t.check('widget alpha checks are throttled to five per second',later['checks']-stats['checks']<=6,(stats,later))
        # Real halo-only grab, in the transparent surface outside the circular body.
        x,y=cx,cy-51;t.move(x,y);time.sleep(.15)
        t.check('round widget halo is move only',t.card('round-widget')['frame']['hovered']=='halo')
        t.ipc.call('stipc/feed_button',{'combo':'BTN_LEFT','mode':'press'})
        t.move(x+5,y+30);time.sleep(.2)
        t.ipc.call('stipc/feed_button',{'combo':'BTN_LEFT','mode':'release'});time.sleep(.6)
        moved=t.card('round-widget')['frame']
        t.check('round halo real input moves widget',moved['y']>f['y']+15,(f,moved))
        options(goo=False);t.move(600,600);time.sleep(.6);shot('round-fallback','round-widget')
        t.cleanup()
        for goo in ((False,) if args.fallback_only else (True,False)):
            options(goo=goo)
            title='round-close-'+str(goo)
            t.launch(title,'left',240,app_id='scottland-alpha-shape')
            t.move(600,600);time.sleep(.7)
            f=t.card(title)['frame'];x=f['x']+f['width']/2;y=f['y']+108+f['thickness']/2
            t.move(x,y);time.sleep(.3)
            # Motion refreshes the hover once the proximity dot has appeared.
            t.move(x+1,y);time.sleep(.1)
            t.check(('goo' if goo else 'fallback')+' close control follows round bottom shore',t.card(title)['frame']['hovered']=='close',t.card(title)['frame'])
            t.ipc.call('stipc/feed_button',{'combo':'BTN_LEFT','mode':'full'})
            closed=t.wait_for(lambda:not t.app(title) and not t.card(title))
            t.check(('goo' if goo else 'fallback')+' round close input closes both forms',closed)
            t.cleanup()
finally:
    t.cleanup()
    (args.artifacts/'results.json').write_text(json.dumps(records,indent=2))
    print(f'RESULT {t.passes} passed, {t.failures} failed',flush=True)
raise SystemExit(bool(t.failures))
