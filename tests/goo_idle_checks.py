"""Large-center attention screenshot/cadence checks, independent of GPU timing."""
import json
import math
import subprocess
import time


def verify(ipc, art, scale):
    import gi
    gi.require_version('GdkPixbuf', '2.0')
    from gi.repository import GdkPixbuf

    def state(): return ipc('scottland/goo-state')['screens'][0]
    before = state()
    damage = before['breath_damage']
    checks = []
    def check(name, ok):
        checks.append((name, bool(ok)))
        print(('PASS ' if ok else 'FAIL ')+name, flush=True)
    check('large source uses local bands, below 30% of output',
          0 < sum(b['width']*b['height'] for b in damage) < 2560*1600*.3)
    samples, shots = [], {}
    end = time.monotonic()+10.5
    while time.monotonic() < end:
        t, s = time.monotonic(), state()
        expected = (math.exp(-math.cos(t*2*math.pi/5))-math.exp(-1))/(math.e-math.exp(-1))
        samples.append({'time': t, 'expected': expected, **s})
        label = 'trough' if s['breath'] < .002 else 'peak' if s['breath'] > .998 else None
        if label and label not in shots:
            path = art/(label+'.png')
            subprocess.run(['grim', str(path)], check=True)
            shots[label] = GdkPixbuf.Pixbuf.new_from_file(str(path))
        time.sleep(.025)
    check('two periods advance no simulation',
          all(s['sleeping'] and s['steps'] == before['steps'] for s in samples))
    check('cached energy unchanged', all(s['energy'] == before['energy'] for s in samples))
    check('five-second light curve unchanged', max(abs(s['breath']-s['expected']) for s in samples) < .06)
    rate = (samples[-1]['breath_ticks']-samples[0]['breath_ticks'])/(samples[-1]['time']-samples[0]['time'])
    check('25 Hz cadence preserved', 22 < rate < 27)
    check('full peak and trough captured', len(shots) == 2)
    if len(shots) == 2:
        a, b = shots['trough'], shots['peak']
        check('requested output pixel size', a.get_width() == round(2560*scale) and a.get_height() == round(1600*scale))
        da, db = a.get_pixels(), b.get_pixels()
        stride, channels = a.get_rowstride(), a.get_n_channels()
        inside = outside = 0
        for y in range(a.get_height()):
            for x in range(a.get_width()):
                k = y*stride+x*channels
                if da[k:k+3] == db[k:k+3]:
                    continue
                local = any(r['x'] <= x/scale < r['x']+r['width'] and
                            r['y'] <= y/scale < r['y']+r['height'] for r in damage)
                if local: inside += 1
                else: outside += 1
        check('attention changes visible pixels', inside > 100)
        check('outside breathing bands is pixel-identical', outside == 0)
        (art/'pixels.json').write_text(json.dumps({'inside': inside, 'outside': outside, 'rate': rate}))
    (art/'breath-samples.json').write_text(json.dumps(samples, indent=2))
    (art/'checks.json').write_text(json.dumps(checks, indent=2))
    assert all(ok for _, ok in checks), checks


def verify_fallback(view, art):
    """Check the off comparator renders its changing halo, not only model swell."""
    import gi
    gi.require_version('GdkPixbuf', '2.0')
    from gi.repository import GdkPixbuf

    shots = {}
    end = time.monotonic()+6
    while time.monotonic() < end and len(shots) < 2:
        swell = view()['frame']['swell']
        label = 'off-trough' if swell < .001 else 'off-peak' if swell > .123 else None
        if label and label not in shots:
            path = art/(label+'.png')
            subprocess.run(['grim', str(path)], check=True)
            shots[label] = GdkPixbuf.Pixbuf.new_from_file(str(path))
        time.sleep(.025)
    assert len(shots) == 2, 'fallback did not reach both extrema'
    a, b = (shots[k].get_pixels() for k in ('off-trough', 'off-peak'))
    changed = sum(x != y for x, y in zip(a, b))
    (art/'fallback-pixels.json').write_text(json.dumps({'changed_channels': changed}))
    print(('PASS ' if changed > 100 else 'FAIL ')+'goo-off halo visibly breathes', flush=True)
    assert changed > 100, 'fallback model changes without visible breathing'
