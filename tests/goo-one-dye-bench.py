#!/usr/bin/env python3
"""GO28 cost (GO10/GO20): what pickup of the backdrop costs, against a build without it.

  SCOTTLAND_HEADLESS_DIR=... tests/headless.sh start --widgets
  tests/headless.sh run python3 tests/goo-one-dye-bench.py SESSION_DIR ARTIFACTS [--seconds 30] [--rounds 4]
  tests/headless.sh stop

Mike's goo settings (full pickup, dye density 1.5, swirl 3) at 2560x1600 on a colorful
wallpaper. A focused window lies in front of a window that redraws under its overlap film
(a terminal flipping its background at 10 Hz, like video), beside a third window.

Cases: at rest (the back window still), then the back window redrawing, sampled in
consecutive rounds so a growing pickup cool-down shows. Each sample reports the goo's own
work counters over the interval (simulation steps, dye passes, draws, composited and
pickup pixels, backdrop checks, pickup coasts), GPU query totals and completed sample counts
for dye-only flow, backdrop refresh/reduction and baseline copies, callback and renderer wall
time including readback waits, maximum callback duration, and compositor CPU. Draw/step GPU
medians describe those older scopes separately. Unsupported GPU timing is null, never zero. Older builds lack some counters;
they read as None. This is a benchmark, not a gate: it records machine, renderer and load.
"""
import argparse, json, os, platform, signal, socket, struct, subprocess, sys, time
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument('session', type=Path); ap.add_argument('artifacts', type=Path)
ap.add_argument('--build-ref', help='renderer build revision when benchmarking another checkout')
ap.add_argument('--width', type=int, default=2560); ap.add_argument('--height', type=int, default=1600)
ap.add_argument('--seconds', type=float, default=30); ap.add_argument('--rounds', type=int, default=4)
args = ap.parse_args()
repo = Path(__file__).resolve().parents[1]
out = args.artifacts.resolve(); out.mkdir(parents=True, exist_ok=True)
compositor = int((args.session / 'compositor.pid').read_text())
sock = socket.socket(socket.AF_UNIX); sock.connect(os.environ['WAYFIRE_SOCKET'])
clients = []

def ipc(method, data=None):
    b = json.dumps({'method': method, 'data': data or {}}).encode(); sock.sendall(struct.pack('<I', len(b)) + b)
    def read(n):
        r = b''
        while len(r) < n:
            c = sock.recv(n - len(r))
            if not c: raise RuntimeError('compositor disconnected')
            r += c
        return r
    return json.loads(read(struct.unpack('<I', read(4))[0]))
def state(): return ipc('scottland/goo-state')['screens'][0]
def views(): return ipc('scottland/layout-state')['views']
def view(t): return next(v for v in views() if v['title'] == t)
def cpu_ticks():
    s = open(f'/proc/{compositor}/stat').read().rsplit(')', 1)[1].split(); return int(s[11]) + int(s[12])
def rest(deadline=240):
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        s = state()
        if s['sleeping'] and not s.get('water_running') and not s.get('pickup_pending') and not s.get('breath_loose'): return
        time.sleep(.2)
    print(json.dumps({'warning': 'did not come to rest'}), flush=True)

COUNTERS = ('steps', 'dye_flows', 'draws', 'composite_pixels', 'surface_pixels', 'capture_pixels', 'under_pixels',
            'backdrop_checks', 'backdrop_changes', 'pickup_coasts', 'water_ticks', 'flow_gpu_samples', 'check_gpu_samples', 'seen_gpu_samples', 'seen_calls', 'pickup_callbacks')
def measure(label):
    a = state(); c0, t0 = cpu_ticks(), time.monotonic(); draw_ms, step_ms = [], []
    last_draws = a['draws']; last_steps = a['steps']
    while time.monotonic() - t0 < args.seconds:
        time.sleep(.1); s = state()
        if s['draws'] != last_draws: draw_ms.append(s['draw_gpu_ms']); last_draws = s['draws']
        if s['steps'] != last_steps: step_ms.append(s['gpu_ms']); last_steps = s['steps']
    b = state(); c1, t1 = cpu_ticks(), time.monotonic()
    med = lambda v: sorted(v)[len(v)//2] if v else None
    r = {'case': label, 'seconds': round(t1 - t0, 2),
         'counters': {k: (b[k] - a[k]) if k in a and k in b else None for k in COUNTERS},
         'draw_gpu_ms_median': med(draw_ms), 'step_gpu_ms_median': med(step_ms),
         'gpu_timing_available': b.get('gpu_timing'),
         'sampled_added_gpu_ms': {k: b[k] - a[k] if k in a and k in b and b.get('gpu_timing') else None
                          for k in ('flow_gpu_ms', 'check_gpu_ms', 'seen_gpu_ms')},
         'gpu_scope_coverage': {scope: {'completed_samples': b[samples]-a[samples] if samples in a and samples in b else None,
                                          'calls': b[calls]-a[calls] if calls in a and calls in b else None}
                                for scope, samples, calls in [('flow', 'flow_gpu_samples', 'dye_flows'),
                                                              ('check', 'check_gpu_samples', 'backdrop_checks'),
                                                              ('seen', 'seen_gpu_samples', 'seen_calls')]},
         'added_wall_ms': {k: b[k] - a[k] if k in a and k in b else None
                          for k in ('flow_wall_ms', 'check_wall_ms', 'seen_wall_ms', 'pickup_callback_ms')},
         'lifetime_wall_max_ms': {k: b.get(k) for k in ('flow_wall_max_ms', 'check_wall_max_ms', 'seen_wall_max_ms', 'pickup_callback_max_ms')},
         'compositor_cpu_percent': round(100*(c1 - c0)/os.sysconf('SC_CLK_TCK')/(t1 - t0), 2),
         'coasting_at_end': b.get('water_running'), 'pickup_gap': b.get('pickup_gap'),
         'load': os.getloadavg()}
    print(json.dumps(r), flush=True)
    with (out / 'measurements.jsonl').open('a') as f: f.write(json.dumps(r) + '\n')
    return r

try:
    renderer = ''
    for line in (args.session / 'wayfire.log').read_text(errors='replace').splitlines():
        if 'scottland goo:' in line and ('OpenGL' in line or 'simulation targets' in line): renderer += line.split('scottland goo:')[1].strip() + '; '
    meta = {'machine': platform.machine(), 'kernel': platform.release(), 'renderer': renderer, 'cpus': os.cpu_count(), 'output': [args.width, args.height],
            'sampling': 'GPU completed query counts; wall totals include waits; maxima are lifetime values',
            'build': args.build_ref or subprocess.run(['git', '-C', str(repo), 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True).stdout.strip()}
    print(json.dumps(meta), flush=True); (out / 'meta.json').write_text(json.dumps(meta, indent=1))
    ipc('wayfire/set-config-options', {'output:HEADLESS-1/mode': f'{args.width}x{args.height}@60000'})
    preset = {'goo_thickness': 22., 'goo_thinning': .27, 'goo_noise': .38, 'goo_lump': 315., 'goo_drift': .39,
              'goo_wave_speed': .16, 'goo_wave_damp': .958, 'goo_wave_height': .76, 'goo_spread': .83, 'goo_swirl': 3.,
              'goo_release': .3, 'goo_shine': .89, 'goo_relief': 5.6, 'goo_depth': 4., 'goo_profile': .45, 'goo_soak': 1.,
              'goo_overlap_film': 10., 'goo_hover_cloudiness': .2, 'goo_hover_emissivity': .31}
    ipc('wayfire/set-config-options', {'scottland/' + k: v for k, v in preset.items()})
    for key in ('goo_dye_density', 'goo_dye_strength'):   # whichever this build has
        ipc('wayfire/set-config-options', {'scottland/' + key: 1.5})
    code = ("import os,time\nstill=%r\nwhile True:\n"
            " if os.path.exists(still): time.sleep(.1); continue\n"
            " print('\\033]11;#20c040\\007',end='',flush=True); time.sleep(.05)\n"
            " print('\\033]11;#d02090\\007',end='',flush=True); time.sleep(.05)" % str(out / 'still'))
    (out / 'still').touch()
    for title, geom, cmd in (('bench-back', (1500, 700, 800, 600), ['python3', '-u', '-c', code]),
                             ('bench-front', (700, 300, 1100, 800), ['sleep', '3600']),
                             ('bench-side', (200, 250, 400, 300), ['sleep', '3600'])):
        clients.append(subprocess.Popen(['foot', '-c', '/dev/null', '-o', 'resize-by-cells=no', '-T', title, *cmd],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
        for _ in range(100):
            if any(v['title'] == title for v in views()): break
            time.sleep(.05)
        ipc('window-rules/configure-view', {'id': view(title)['id'], 'geometry': dict(zip(('x', 'y', 'width', 'height'), tuple(round(v * (args.width/2560 if i % 2 == 0 else args.height/1600)) for i, v in enumerate(geom))))})
        time.sleep(.3)
    clients.append(subprocess.Popen(['quickshell', '-p', str(repo / 'tests/GooWallpaper.qml')], env=dict(os.environ, GOO_WALLPAPER_PATCHES='dark'),
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
    time.sleep(2)
    f = view('bench-front')['frame']
    ipc('stipc/move_cursor', {'x': f['x'] + 50, 'y': f['y'] + 50})
    for m in ('press', 'release'): ipc('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': m})
    ipc('stipc/move_cursor', {'x': 5, 'y': 5})
    rest(); time.sleep(2)
    measure('rest')
    (out / 'still').unlink()
    for i in range(args.rounds): measure(f'redrawing-under-film-{i + 1}')
    (out / 'still').touch()
    rest(); measure('rest-after')
    subprocess.run(['grim', str(out / 'scene.png')])
finally:
    for c in clients:
        try: os.killpg(c.pid, signal.SIGTERM)
        except ProcessLookupError: pass
        try: c.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try: os.killpg(c.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            c.wait()
    sock.close()
