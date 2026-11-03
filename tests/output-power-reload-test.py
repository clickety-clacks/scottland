#!/usr/bin/env python3
"""Compare output-power/reload behavior in an isolated stock Wayfire instance.

Caller supplies private HOME/XDG directories, a config with Scottland loaded, a
built plugin path. This driver
starts and reaps only its own Wayfire/client. Use --expect-bug only on the base
revision to establish the reported-ON/black regression before the repair.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import time


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class Wire:
    def __init__(self, path):
        self.s = socket.socket(socket.AF_UNIX); self.s.settimeout(3); self.s.connect(path)
        self.buf = b''; self.next_id = 2; self.globals = []; self.events = []; self.powers = {}
        self.registry = self.new(); self.send(1, 1, struct.pack('<I', self.registry)); self.roundtrip()
    def new(self):
        n = self.next_id; self.next_id += 1; return n
    def string(self, value):
        b = value.encode() + b'\0'; return struct.pack('<I', len(b)) + b + b'\0' * (-len(b) % 4)
    def send(self, obj, op, payload=b''):
        self.s.sendall(struct.pack('<II', obj, (8 + len(payload)) << 16 | op) + payload)
    def receive(self):
        data = self.s.recv(65536)
        if not data: raise RuntimeError('Wayland disconnected')
        self.buf += data
    def event(self):
        while len(self.buf) < 8: self.receive()
        obj, size_op = struct.unpack('<II', self.buf[:8]); size, op = size_op >> 16, size_op & 65535
        while len(self.buf) < size: self.receive()
        b = self.buf[8:size]; self.buf = self.buf[size:]
        if obj == 1 and op == 0: raise RuntimeError('Wayland error: ' + repr(b))
        if obj == self.registry and op == 0:
            name, length = struct.unpack('<II', b[:8]); iface = b[8:8+length-1].decode()
            off = 8 + ((length + 3) // 4 * 4); version = struct.unpack('<I', b[off:off+4])[0]
            self.globals.append({'name': name, 'interface': iface, 'version': version})
        if obj in self.powers:
            if op == 0: self.powers[obj]['mode'] = struct.unpack('<I', b)[0]
            elif op == 1: self.powers[obj]['failed'] = True
        self.events.append({'object': obj, 'opcode': op, 'bytes': b.hex(), 'utc': now()})
        return obj, op, b
    def roundtrip(self):
        cb = self.new(); self.send(1, 0, struct.pack('<I', cb))
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            obj, op, b = self.event()
            if obj == cb and op == 0: return
        raise RuntimeError('Wayland sync timeout')
    def bind(self, g, version):
        obj = self.new()
        self.send(self.registry, 0, struct.pack('<I', g['name']) + self.string(g['interface']) + struct.pack('<II', min(version, g['version']), obj))
        return obj
    def qualify_power(self):
        outputs = [g for g in self.globals if g['interface'] == 'wl_output']
        managers = [g for g in self.globals if g['interface'] == 'zwlr_output_power_manager_v1']
        if len(outputs) != 1 or len(managers) != 1:
            raise RuntimeError('Expected one private output and power manager: ' + repr(self.globals))
        self.output = self.bind(outputs[0], 4); self.manager = self.bind(managers[0], 1)
        self.power = self.new(); self.powers[self.power] = {}
        self.send(self.manager, 0, struct.pack('<II', self.power, self.output)); self.roundtrip()
        if 'mode' not in self.powers[self.power] or self.powers[self.power].get('failed'):
            raise RuntimeError('No valid output-power mode event')
        return self.powers[self.power]
    def power_state(self):
        self.roundtrip(); return dict(self.powers[self.power])
    def set_power(self, mode):
        self.send(self.power, 0, struct.pack('<I', mode)); self.roundtrip()
        state = self.power_state()
        if state.get('mode') != mode or state.get('failed'):
            raise RuntimeError('Output power request not established: ' + repr(state))
        return state


# A changing colored client covers the asserted region; its counter also changes text.
PIXEL_CLIENT = r'''
import sys, time
frame = 0
while True:
    color = 21 + (frame % 6) * 30
    sys.stdout.write('\x1b[2J\x1b[H\x1b[48;5;' + str(color) + 'm\x1b[97m' +
                     ('Power reload frame ' + str(frame) + ' ' * 50 + '\n') * 20 + '\x1b[0m')
    sys.stdout.flush()
    frame += 1
    time.sleep(.3)
'''


args = argparse.ArgumentParser(description=__doc__)
args.add_argument('root', type=Path)
args.add_argument('--expect-bug', action='store_true')
args = args.parse_args()
root = args.root.resolve()
result = {'start': now(), 'expected_bug': args.expect_bug, 'checks': []}
children = []
wire = None


def wait(read, accept, description, timeout=10):
    deadline = time.monotonic() + timeout
    value = None
    while time.monotonic() < deadline:
        value = read()
        if accept(value):
            return value
        time.sleep(.02)
    raise RuntimeError(f'{description}: deadline expired, last observation={value!r}')


def ipc(method):
    paths = [p for p in Path(os.environ['XDG_RUNTIME_DIR']).glob('wayfire-*.socket') if p.is_socket()]
    if len(paths) != 1:
        raise RuntimeError(f'Expected one private IPC socket, found {paths}')
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(4)
        sock.connect(str(paths[0]))
        def read(n):
            data = b''
            while len(data) < n:
                part = sock.recv(n - len(data))
                if not part:
                    raise RuntimeError('IPC disconnected')
                data += part
            return data
        body = json.dumps({'method': method, 'data': {}}).encode()
        sock.sendall(struct.pack('<I', len(body)) + body)
        return json.loads(read(struct.unpack('<I', read(4))[0]))


def reload(name):
    previous = ipc('scottland/session-state')['config-reloads']
    start = len(wire.events)
    with (root / 'config/wayfire.ini').open('a') as f:
        f.write(f'\n# output power regression {name}\n')
    observed = wait(lambda: ipc('scottland/session-state')['config-reloads'],
                    lambda count: count > previous, 'config reload')
    assert observed == previous + 1, (previous, observed)
    state = wire.power_state()
    modes = [e for e in wire.events[start:] if e['object'] == wire.power and e['opcode'] == 0]
    result['checks'].append({'reload': name, 'utc': now(), 'power': state, 'mode_events': modes})
    return state


def capture(name):
    path = root / (name + '.png')
    subprocess.run(['grim', '-o', 'HEADLESS-1', str(path)], check=True, timeout=5,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    pixels = subprocess.check_output(['magick', str(path), '-depth', '8', 'RGB:-'], timeout=5)
    # The fixed 640x480 fixture's software cursor is centered at (320,240).
    # Check the client's left-hand region; controls prove it actually changes.
    # Preserve the full screenshot for review, including the visible cursor.
    content = subprocess.check_output(['magick', str(path), '-crop', '300x480+0+0',
                                       '+repage', '-depth', '8', 'RGB:-'], timeout=5)
    return {'path': path.name, 'utc': now(), 'sha256': hashlib.sha256(pixels).hexdigest(),
            'min': min(pixels), 'max': max(pixels), 'nonuniform': min(pixels) != max(pixels),
            'content_sha256': hashlib.sha256(content).hexdigest(), 'content_max': max(content),
            'content_nonuniform': min(content) != max(content)}


def drawing(name):
    a = wait(lambda: capture(name + '-a'), lambda frame: frame['content_nonuniform'], name + ' visible pixels')
    b = wait(lambda: capture(name + '-b'),
             lambda frame: frame['content_nonuniform'] and frame['content_sha256'] != a['content_sha256'],
             name + ' changing pixels')
    result['checks'].append({'drawing': name, 'frames': [a, b], 'power': wire.power_state()})


try:
    assert not Path('/dev/dri').exists(), 'DRM must not be exposed'
    for key in ['HOME', 'XDG_RUNTIME_DIR', 'XDG_CONFIG_HOME', 'XDG_STATE_HOME', 'XDG_CACHE_HOME']:
        assert Path(os.environ[key]).resolve().is_relative_to(root), (key, os.environ[key])
    result['environment'] = dict(os.environ)
    with (root / 'wayfire.log').open('wb') as log:
        compositor = subprocess.Popen(['wayfire', '-d', '-c', str(root / 'config/wayfire.ini')],
                                      stdout=log, stderr=subprocess.STDOUT)
        children.append(compositor)
        def sockets():
            assert compositor.poll() is None, 'Wayfire exited before readiness'
            return [p for p in Path(os.environ['XDG_RUNTIME_DIR']).glob('wayland-*') if p.is_socket()]
        paths = wait(sockets, lambda paths: len(paths) == 1, 'private Wayland socket')
        os.environ['WAYLAND_DISPLAY'] = paths[0].name
        wire = Wire(str(paths[0]))
        result['initial_power'] = wire.qualify_power()
        assert result['initial_power']['mode'] == 1
        result['session'] = ipc('scottland/session-state')
        assert 'config-reloads' in result['session'], 'Scottland plugin IPC missing'
        result['plugin_maps'] = [line for line in Path(f'/proc/{compositor.pid}/maps').read_text().splitlines()
                                 if 'libscottland.so' in line]
        assert result['plugin_maps'], 'Scottland plugin not mapped'
        client = subprocess.Popen(['foot', '--config=' + str(root / 'config/foot.ini'),
                                   '--title=Power regression', '--app-id=power-regression',
                                   'python3', '-u', '-c', PIXEL_CLIENT], stdout=log, stderr=log)
        children.append(client)
        drawing('s1-baseline')
        wire.set_power(0)
        wire.set_power(1)
        drawing('s2-off-on')
        assert reload('s3-on')['mode'] == 1
        drawing('s3-reload')
        wire.set_power(0)
        state = reload('s4-off')
        result['scenario4_pass'] = state['mode'] == 0
        if args.expect_bug:
            assert state['mode'] == 1, 'Expected base regression did not reproduce'
            frame = capture('s4-reported-on-black')
            result['checks'].append({'base_regression_pixels': frame})
            assert frame['content_max'] == 0, 'Base failure is not black in the proven client region'
        else:
            assert state['mode'] == 0, 'OFF plus reload did not settle OFF'
            assert reload('s4-off-again')['mode'] == 0, 'Repeated reload lost requested OFF'
        wire.set_power(1)
        drawing('s4-recovery-on')
        assert reload('s4-after-on')['mode'] == 1, 'A later reload undid explicit ON'
        drawing('s4-on-reload')
        result['comparison_pass'] = True
except Exception as error:
    result['error'] = str(error)
    result['comparison_pass'] = False
finally:
    if wire:
        result['wire_events'] = wire.events
        wire.s.close()
    for child in reversed(children):
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=4)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=3)
    result['children_reaped'] = [{'pid': c.pid, 'returncode': c.poll()} for c in children]
    result['end'] = now()
    (root / 'result.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
raise SystemExit(0 if result.get('comparison_pass') else 1)
