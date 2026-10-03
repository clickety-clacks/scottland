"""GO17 checks on the live-like benchmark's settled attention fixture."""
import json
import math
import subprocess
import time


def verify(ipc, art):
    import gi
    gi.require_version('GdkPixbuf', '2.0')
    from gi.repository import GdkPixbuf

    def state():
        return ipc('scottland/goo-state')['screens'][0]

    checks = []
    def check(name, result):
        checks.append((name, bool(result)))
        print(('PASS ' if result else 'FAIL ') + name, flush=True)

    before = state()
    check('breathing simulation sleeps', before['sleeping'])
    damage = before['breath_damage']
    check('breathing damages less than 2% of the 2560x1600 output',
          0 < sum(b['width']*b['height'] for b in damage) < 2560*1600*.02)
    check('distant strips receive no breathing damage',
          all(b['x']+b['width'] < 400 for b in damage))
    samples = []
    shots = {}
    start = time.monotonic()
    while time.monotonic()-start < 11:
        t = time.monotonic()
        s = state()
        expected = (math.exp(-math.cos((t % 5)*2*math.pi/5))-math.exp(-1))/(math.e-math.exp(-1))
        samples.append({'time':t, 'expected':expected, **s})
        label = 'trough' if s['breath'] < .002 else 'peak' if s['breath'] > .998 else None
        if label and label not in shots:
            path = art/(label+'.png')
            subprocess.run(['grim',str(path)],check=True)
            shots[label] = GdkPixbuf.Pixbuf.new_from_file(str(path))
        time.sleep(.025)
    check('two breathing periods advance no simulation steps',
          all(s['sleeping'] and s['steps']==before['steps'] for s in samples))
    check('cached dye/wave energy does not change',
          all(s['energy']==before['energy'] for s in samples))
    check('five-second exponential sine curve (40ms sampling allowance)',
          max(abs(s['breath']-s['expected']) for s in samples) < .06)
    elapsed = samples[-1]['time']-samples[0]['time']
    rate = (samples[-1]['breath_ticks']-samples[0]['breath_ticks'])/elapsed
    check('breathing cadence is 25Hz, independent of 60Hz output', 22 < rate < 27)
    check('curve reaches a full visible peak and trough', len(shots)==2)
    if len(shots)==2:
        a,b = shots['trough'],shots['peak']
        da,db = a.get_pixels(),b.get_pixels()
        stride,channels = a.get_rowstride(),a.get_n_channels()
        inside=outside=0
        for y in range(a.get_height()):
            for x in range(a.get_width()):
                k=y*stride+x*channels
                if da[k:k+3] == db[k:k+3]: continue
                local=any(r['x']<=x<r['x']+r['width'] and r['y']<=y<r['y']+r['height'] for r in damage)
                if local: inside+=1
                else: outside+=1
        check('screenshots visibly pulse within the attention band', inside>100)
        check('screenshots outside the breathing strips stay pixel-identical', outside==0)
        print(json.dumps({'changed_inside':inside,'changed_outside':outside,'rate':rate}),flush=True)
    (art/'breath-samples.json').write_text(json.dumps(samples,indent=2))
    # Both directions of a live switch must preserve an outstanding request.
    ipc('wayfire/set-config-options', {'scottland/goo':False})
    swells=[]
    start=time.monotonic()
    while time.monotonic()-start < 5.5:
        for v in ipc('scottland/layout-state')['views']:
            if v.get('frame',{}).get('attention'):
                swells.append(v['frame']['swell'])
        time.sleep(.1)
    check('switching goo off resumes local fallback breathing',
          len(swells)>20 and max(swells)-min(swells)>.08)
    ipc('wayfire/set-config-options', {'scottland/goo':True})
    time.sleep(1)
    deadline=time.monotonic()+40
    while time.monotonic()<deadline and not state()['sleeping']:
        time.sleep(.1)
    restored=state()
    time.sleep(1)
    check('switching back to goo sleeps with attention still breathing',
          restored['sleeping'] and state()['steps']==restored['steps']
          and state()['breath_ticks']>restored['breath_ticks'])
    (art/'checks.json').write_text(json.dumps(checks,indent=2))
    assert all(ok for _,ok in checks), checks
