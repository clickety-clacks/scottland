#!/usr/bin/env python3
"""Window avoidance draws displaced windows on exactly the pixels an older build does.

Invoked by render-parity-test.sh inside a headless session started on OLD_CHECKOUT's build. Six
solid-colored terminals stack so that always-on avoidance displaces several by fractional
offsets (hint text scale 0.82 gives 19.68-pixel steps). For each output configuration (scale and
transform), a round captures the scene after reloads old -> old (the control), -> this checkout's
build, -> old again. A reload may let the solver choose another arrangement, so a round counts only
when all three have the same offsets; rounds repeat until one does. In it, the two old captures must
agree (otherwise the comparison proves nothing) and the new one must match them: no pixel differs by
more than two color levels. Goo is off so nothing animates.
"""
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import struct
import subprocess
import sys
import time

artifacts, old_plugin, new_plugin = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
CONFIGS = [(1, 'normal'), (1.5, 'normal'), (2, 'normal'), (1.25, '90')]
ROUNDS = 5
passed = failed = 0
clients = []


def check(ok, message, details=None):
    global passed, failed
    print(('PASS  ' if ok else 'FAIL  ') + message + ('' if ok or details is None else f': {details}'), flush=True)
    passed += bool(ok)
    failed += not ok


sock = socket.socket(socket.AF_UNIX)
sock.settimeout(10)
sock.connect(os.environ['WAYFIRE_SOCKET'])


def ipc(method, data=None):
    body = json.dumps({'method': method, 'data': data or {}}).encode()
    sock.sendall(struct.pack('<I', len(body)) + body)
    def exactly(n):
        b = b''
        while len(b) < n:
            chunk = sock.recv(n - len(b))
            if not chunk:
                raise ConnectionError('compositor disconnected')
            b += chunk
        return b
    reply = json.loads(exactly(struct.unpack('<I', exactly(4))[0]))
    if isinstance(reply, dict) and reply.get('error'):
        raise RuntimeError(f'{method}: {reply}')
    return reply


def wait_until(predicate, what, deadline=60):
    end = time.monotonic() + deadline
    last = None
    while time.monotonic() < end:
        last = predicate()
        if last:
            return last
        time.sleep(.1)
    raise AssertionError(f'timed out waiting for {what}; last: {last}')


def capture(name):
    path = artifacts / (name + '.ppm')
    subprocess.run(['grim', '-t', 'ppm', str(path)], check=True)
    return path.read_bytes()


def stable(name):
    """The screen once three consecutive captures agree."""
    state = {'prev': None, 'same': 0}
    def poll():
        data = capture(name)
        state['same'] = state['same'] + 1 if data == state['prev'] else 0
        state['prev'] = data
        return data if state['same'] >= 3 else None
    data = wait_until(poll, 'stable pixels for ' + name)
    (artifacts / (name + '.json')).write_text(json.dumps({'hints': ipc('scottland/hints'),
                                                          'layout': ipc('scottland/layout-state')}, indent=1))
    return data


def offsets():
    return sorted((h['window'], round(h['dx'], 4), round(h['dy'], 4)) for h in ipc('scottland/hints')['hints'])


def reload(path):
    before = ipc('scottland/desktop-model')['version']
    fresh = artifacts / f'libscottland-{time.monotonic_ns()}.so'
    shutil.copyfile(path, fresh)
    plugins = ipc('wayfire/get-config-option', {'option': 'core/plugins'})['value']
    mark = Path(os.environ['XDG_RUNTIME_DIR']) / 'scottland' / (os.environ['WAYLAND_DISPLAY'] + '.reloading')
    mark.touch()
    try:
        ipc('wayfire/set-config-options', {'core/plugins': ' '.join(
            str(fresh) if p == 'scottland' or '/libscottland' in p else p for p in plugins.split())})
        def loaded():
            try:
                return ipc('scottland/desktop-model')['version'] > before
            except RuntimeError as error:
                if 'No such method' in str(error):
                    return False
                raise
        wait_until(loaded, 'the reloaded plugin')
    finally:
        mark.unlink(missing_ok=True)
    fresh.unlink()


def differing(a, b):
    start = a.index(b'\n255\n') + 5
    if len(a) != len(b) or a[:start] != b[:start]:
        return {'pixels_gt2': -1, 'max_delta': 255}
    width = int(a[:start].split()[1])
    count = worst = 0
    for row in range(start, len(a), width * 3):
        ra, rb = a[row:row + width * 3], b[row:row + width * 3]
        if ra == rb:
            continue
        for i in range(0, len(ra), 3):
            d = max(abs(ra[i] - rb[i]), abs(ra[i + 1] - rb[i + 1]), abs(ra[i + 2] - rb[i + 2]))
            count += d > 2
            worst = max(worst, d)
    return {'pixels_gt2': count, 'max_delta': worst}


palette = Path(os.environ['XDG_RUNTIME_DIR']) / 'scottland' / (os.environ['WAYLAND_DISPLAY'] + '.palette.json')
old_palette = palette.read_bytes() if palette.exists() else None
try:
    ipc('wayfire/set-config-options', {'scottland/goo': False, 'scottland/window_avoidance_always': True,
                                       'scottland/center_opacity_unfocused': 1., 'scottland/side_opacity_unfocused': 1.,
                                       'scottland/sounds': False})
    for i, color in enumerate(['ff00ff', '00ffff', 'ffff00', '00ff00', 'ff8000', '0080ff']):
        code = 'import time; print("\\033[?25l", flush=True); time.sleep(3600)'
        clients.append(subprocess.Popen(['foot', '-c', '/dev/null', '-o', 'resize-by-cells=no', '-o',
                                         'colors.background=' + color, '-T', f'parity-{i}', 'python3', '-c', code],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
        wait_until(lambda: sum(v['title'].startswith('parity-') for v in ipc('scottland/layout-state')['views']) == i + 1,
                   'terminal ' + str(i))
    # The hint palette is read while hints update, which avoidance keeps doing once windows exist.
    palette.write_text(json.dumps({'scheme': 'dark', 'background': '#1f232c', 'foreground': '#d8deea',
                                   'accent': '#81a1c1', 'text_scale': .82}))
    wait_until(lambda: abs(ipc('scottland/hints').get('hint_text_scale', 0) - .82) < .0001, 'the hint text scale')
    results = []
    for scale, transform in CONFIGS:
        label = f'scale-{scale}-transform-{transform}'
        ipc('wayfire/set-config-options', {'output:HEADLESS-1/mode': '3840x2160@60000',
                                           'output:HEADLESS-1/scale': float(scale),
                                           'output:HEADLESS-1/transform': transform})
        expected = round((2160 if transform == '90' else 3840) / scale)
        wait_until(lambda: abs(ipc('window-rules/list-outputs')[0]['geometry']['width'] - expected) <= 1, 'output ' + label)
        width = ipc('window-rules/list-outputs')[0]['geometry']['width']
        height = ipc('window-rules/list-outputs')[0]['geometry']['height']
        rect = {'x': round(width / 2 - width * .24 - 20), 'y': round(height * .05),
                'width': round(width * .48), 'height': round(height * .9)}
        for v in sorted((v for v in ipc('scottland/layout-state')['views'] if v['title'].startswith('parity-')),
                        key=lambda v: v['title']):
            ipc('window-rules/configure-view', {'id': v['id'], 'geometry': rect})
        ipc('stipc/move_cursor', {'x': 5, 'y': height - 5})
        stable(label + '-settle')
        result = None
        for attempt in range(ROUNDS):
            reload(old_plugin)
            control, control_offsets = stable(f'{label}-{attempt}-old'), offsets()
            reload(new_plugin)
            new, new_offsets = stable(f'{label}-{attempt}-new'), offsets()
            reload(old_plugin)
            reverse, reverse_offsets = stable(f'{label}-{attempt}-old-again'), offsets()
            if control_offsets == new_offsets == reverse_offsets:
                result = {'config': label, 'round': attempt, 'offsets': control_offsets,
                          'control': differing(control, reverse), 'new': differing(control, new)}
                break
            print(json.dumps({'config': label, 'round': attempt, 'arrangement changed': [
                control_offsets, new_offsets, reverse_offsets]}), flush=True)
        check(result is not None, f'{label}: a round with the same arrangement in every build ({ROUNDS} tried)')
        if result is None:
            continue
        results.append(result)
        print(json.dumps(result), flush=True)
        fractional = [o for o in result['offsets'] if (o[1] % 1) or (o[2] % 1)]
        check(len(fractional) >= 2, f'{label}: windows are displaced by fractional offsets ({len(fractional)})',
              result['offsets'])
        check(result['control']['pixels_gt2'] == 0, f'{label}: the old build renders the same twice', result['control'])
        check(result['new']['pixels_gt2'] == 0, f'{label}: the new build renders the same pixels as the old', result['new'])
    (artifacts / 'results.json').write_text(json.dumps(results, indent=1))
finally:
    if old_palette is None:
        palette.unlink(missing_ok=True)
    else:
        palette.write_bytes(old_palette)
    for client in clients:
        try:
            os.killpg(client.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    for client in clients:
        client.wait()

print(f'{passed} passed, {failed} failed')
sys.exit(1 if failed else 0)
