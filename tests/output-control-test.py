#!/usr/bin/env python3
"""E14 display control for integrations: scottland-output list/set/reset, and scale auto.

Runs inside a headless session with two virtual outputs (tests/output-control-test.sh). Each change
is made once with scottland-output, the way an integration calls it, and judged by what the code
under test does not report about itself: Wayfire's own output list (stock ipc-rules: which outputs
are on and their layout geometry, where scale and transform show as logical size) and a screencopy
of the output (grim -o: its pixel size, or no frame while it is off).

Scale auto is checked through both ways of asking for it, set --scale auto and an integration's
`scale = auto` (config.d/50-test-display), on a HiDPI panel, an ordinary monitor and an output with
no physical size. The physical sizes are stand-ins the shell wrapper gives the compositor's report.

  tests/output-control-test.py ARTIFACTS
"""
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time

out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
hooks = Path(os.environ['SCOTTLAND_HOOKS'])
tool = str(hooks / 'libexec/scottland-output')
overrides = Path(os.environ['XDG_CONFIG_HOME']) / 'scottland/overrides.ini'
config = Path(os.environ['SCOTTLAND_CONFIG_OUTPUT'])
display = hooks / 'display.ini'
sys.dont_write_bytecode = True
sys.path.insert(0, str(hooks / 'libexec'))
from scottland_output_scale import WAITING
sock = socket.socket(socket.AF_UNIX); sock.connect(os.environ['WAYFIRE_SOCKET']); sock.settimeout(5)

def read(n):
    b = b''
    while len(b) < n:
        c = sock.recv(n - len(b))
        if not c: raise RuntimeError('compositor disconnected')
        b += c
    return b

def ipc(method, data=None):
    b = json.dumps({'method': method, 'data': data or {}}).encode(); sock.sendall(struct.pack('<I', len(b)) + b)
    return json.loads(read(struct.unpack('<I', read(4))[0]))

def outputs():
    reply = ipc('window-rules/list-outputs')
    return {o['name']: {k: int(v) for k, v in o['geometry'].items()} for o in reply} if isinstance(reply, list) else {}

def reloads():
    return ipc('scottland/session-state').get('config-reloads', -1)

def wait(condition, timeout=10):
    """Poll a condition the compositor reports until it holds or the time is up."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition(): return True
        time.sleep(0.05)
    return condition()

def capture(output, name):
    """The output's screencopy frame size, or None if it gives no frame."""
    path = out / f'{name}.ppm'
    path.unlink(missing_ok=True)
    result = subprocess.run(['timeout', '5', 'grim', '-t', 'ppm', '-o', output, str(path)], capture_output=True)
    if result.returncode != 0 or not path.exists(): return None
    fields = path.read_bytes().split(maxsplit=3)
    return int(fields[1]), int(fields[2])

def output(*args):
    result = subprocess.run([tool, *args], capture_output=True, text=True, timeout=60)
    try:
        reported = json.loads(result.stdout) if result.stdout.strip() else None
    except ValueError:
        reported = result.stdout
    (out / 'commands.log').open('a').write(f'$ scottland-output {" ".join(args)}\n-> {result.returncode}\n{result.stdout}{result.stderr}\n')
    return result.returncode, reported, result.stderr.strip()

def rebuild():
    """Rebuild the config and wait for Wayfire to load it."""
    before = reloads()
    result = subprocess.run([str(hooks / 'libexec/scottland-build-config')], capture_output=True, text=True, timeout=60)
    return result.returncode == 0 and wait(lambda: reloads() > before), result.stderr.strip()

def width(name='HEADLESS-2'):
    return outputs().get(name, {}).get('width')

# Stand-in physical sizes (mm) for HEADLESS-2; at 1024x768 a HiDPI panel has about 217 pixels per
# inch, an ordinary monitor about 79.
HIDPI, ORDINARY = [120, 90], [360, 203]
def size(mm):
    (hooks / 'physical-sizes.json').write_text(json.dumps({'HEADLESS-2': mm} if mm else {}))

failed = 0
def check(label, ok, detail=None):
    global failed
    print(('PASS ' if ok else 'FAIL ') + label + ('' if ok or detail is None else f': {detail}'), flush=True)
    failed += not ok

# Session start: the integration's scale = auto for HEADLESS-1, a HiDPI panel at 1280x720, was in
# the config before the compositor ran (tests/output-control-test.sh).
check('session start: scale auto waits at 1 for the compositor, and the config says so',
      width('HEADLESS-1') == 1280 and WAITING in config.read_text(), outputs())
started = subprocess.run([str(hooks / 'autostart.d/04-output-scale-auto')], capture_output=True, text=True, timeout=60)
check('then one rebuild gives the HiDPI panel its natural scale 2',
      started.returncode == 0 and wait(lambda: width('HEADLESS-1') == 640) and WAITING not in config.read_text(),
      (started.stderr, outputs()))
built = config.stat().st_mtime_ns
again = subprocess.run([str(hooks / 'autostart.d/04-output-scale-auto')], capture_output=True, text=True, timeout=60)
check('with no output waiting, the start hook builds nothing',
      again.returncode == 0 and config.stat().st_mtime_ns == built, again.stderr)
display.unlink()
ok, error = rebuild()
check('setup: without the display settings HEADLESS-1 is back at scale 1', ok and wait(lambda: width('HEADLESS-1') == 1280),
      (error, outputs()))

listed = output('list')[1] or []
geometry = outputs()
check('list reports every output with its mode, position and scale, as Wayfire lays them out',
      len(geometry) == 2 and sorted(h['name'] for h in listed) == sorted(geometry) and all(
          h['enabled'] and (h['mode']['width'], h['mode']['height']) == (1280, 720) and h['scale'] == 1
          and (h['x'], h['y']) == (geometry[h['name']]['x'], geometry[h['name']]['y']) for h in listed),
      (listed, geometry))

status, reported, error = output('set', 'HEADLESS-2', '--scale', '2', '--position', '1280,0')
check('scale and position: applied when the command returns',
      status == 0 and outputs().get('HEADLESS-2') == {'x': 1280, 'y': 0, 'width': 640, 'height': 360},
      (status, error, outputs()))
check('the output still renders its full mode (screencopy 1280x720)', capture('HEADLESS-2', 'scale2') == (1280, 720))

status, reported, error = output('set', 'HEADLESS-2', '--transform', '90')
check('transform 90: the output is laid out on its side',
      status == 0 and outputs().get('HEADLESS-2') == {'x': 1280, 'y': 0, 'width': 360, 'height': 640},
      (status, error, outputs()))
status, reported, error = output('set', 'HEADLESS-2', '--transform', 'normal')
check('transform normal: upright again',
      status == 0 and outputs().get('HEADLESS-2') == {'x': 1280, 'y': 0, 'width': 640, 'height': 360},
      (status, error, outputs()))

status, reported, error = output('set', 'HEADLESS-2', '--mode', '1024x768')
check('mode 1024x768: applied (layout 512x384 at scale 2, screencopy 1024x768)',
      status == 0 and outputs().get('HEADLESS-2', {}).get('width') == 512
      and capture('HEADLESS-2', 'mode') == (1024, 768), (status, error, outputs()))

status, reported, error = output('set', 'HEADLESS-2', '--off')
check('off: the output is gone from the layout and renders nothing',
      status == 0 and 'HEADLESS-2' not in outputs() and capture('HEADLESS-2', 'off') is None,
      (status, error, outputs()))
check('list still reports it, as disabled',
      any(h['name'] == 'HEADLESS-2' and h['enabled'] is False for h in output('list')[1] or []))

ok, error = rebuild()
check('a config rebuild keeps it off', ok and 'HEADLESS-2' not in outputs(), (error, outputs()))

status, reported, error = output('set', 'HEADLESS-2', '--on')
check('on again: back with the mode, position and scale it had',
      status == 0 and outputs().get('HEADLESS-2') == {'x': 1280, 'y': 0, 'width': 512, 'height': 384}
      and capture('HEADLESS-2', 'on') == (1024, 768), (status, error, outputs()))

# The user's config keeps it off: "on" can't override that, and says so.
overrides.parent.mkdir(parents=True, exist_ok=True)
overrides.write_text('[output:HEADLESS-2]\nmode = off\n')
output('reset')
check('setup: overrides.ini turns HEADLESS-2 off', wait(lambda: 'HEADLESS-2' not in outputs()), outputs())
status, reported, error = output('set', 'HEADLESS-2', '--on')
check('on when the config keeps it off: exit 1, saying why, and it stays off',
      status == 1 and 'keeps it off' in error and 'HEADLESS-2' not in outputs(), (status, error))
overrides.unlink()

# A virtual output has no preferred mode: Wayfire's default (auto) keeps its current size,
# 1024x768 here. Back at scale 1 (layout as wide as the mode) shows the settings dropped.
status, reported, error = output('reset')
check("reset: the session's settings are dropped; the configured default scale 1 again",
      status == 0 and wait(lambda: outputs().get('HEADLESS-2', {}).get('width') == 1024)
      and capture('HEADLESS-2', 'reset') == (1024, 768), (status, error, outputs()))

# Auto through scottland-output (HEADLESS-2 runs 1024x768 here). Each case starts from the other
# scale, so the change shows.
cases = [('an ordinary monitor', ORDINARY, 1), ('a HiDPI panel', HIDPI, 2), ('no physical size', None, 1)]
for label, mm, scale in cases:
    output('set', 'HEADLESS-2', '--scale', str(3 - scale))
    size(mm)
    status, reported, error = output('set', 'HEADLESS-2', '--scale', 'auto')
    check(f'set --scale auto, {label}: {scale}, applied when the command returns, list reports the number',
          status == 0 and width() == 1024 // scale and isinstance(reported, dict) and reported.get('scale') == scale,
          (status, error, reported, outputs()))
ok, error = rebuild()
check('a config rebuild keeps scale auto', ok and width() == 1024, (error, outputs()))
overrides.write_text('[output:HEADLESS-2]\nscale = 2\n')
output('set', 'HEADLESS-2', '--scale', 'auto')
check("a number in the user's config wins over auto", wait(lambda: width() == 512), outputs())
overrides.unlink()
output('reset')

# Auto in an integration's display settings: worked out again at every rebuild.
display.write_text('[output:HEADLESS-2]\nscale = auto\n')
for label, mm, scale in [('a HiDPI panel', HIDPI, 2), ('an ordinary monitor', ORDINARY, 1),
                         ('no physical size', None, 1), ('a HiDPI panel again', HIDPI, 2)]:
    size(mm)
    ok, error = rebuild()
    check(f"an integration's scale = auto, {label}: {scale}", ok and wait(lambda: width() == 1024 // scale),
          (error, outputs()))
overrides.write_text('[output:HEADLESS-2]\nscale = 1\n')
ok, error = rebuild()
check("a number in the user's config wins over an integration's auto", ok and wait(lambda: width() == 1024),
      (error, outputs()))
overrides.unlink()
ok, error = rebuild()
check("without it, the integration's auto applies again", ok and wait(lambda: width() == 512), (error, outputs()))
display.unlink()
size(None)
ok, error = rebuild()
check('setup: back at scale 1', ok and wait(lambda: width() == 1024), (error, outputs()))

status, _, error = output('set', 'HEADLESS-2', '--scale', 'fast')
check('an invalid value is refused before anything changes (exit 64)',
      status == 64 and outputs().get('HEADLESS-2', {}).get('width') == 1024, (status, error))

print(f'{"FAIL" if failed else "PASS"}: {failed} failed', flush=True)
sys.exit(1 if failed else 0)
