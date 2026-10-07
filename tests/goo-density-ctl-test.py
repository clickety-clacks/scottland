#!/usr/bin/env python3
"""Actual CLI IPC against an owned compatibility fixture, independent of compositor state.
This fixture supplies an older plugin's unsupported-new-option response, not an older binary.
"""
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import threading

repo = Path(__file__).resolve().parents[1]
failures = 0

def check(name, condition):
    global failures
    print(('PASS ' if condition else 'FAIL ') + name)
    failures += not condition

def scenario(directory, supported, saved_value):
    path = directory / 'fixture.socket'
    server = socket.socket(socket.AF_UNIX); server.bind(str(path)); server.listen(1); server.settimeout(5)
    calls = []
    errors = []
    def fixture():
        try:
            connection, _ = server.accept(); connection.settimeout(5)
            with connection:
                def read(count):
                    data = b''
                    while len(data) < count:
                        chunk = connection.recv(count-len(data))
                        if not chunk: return None
                        data += chunk
                    return data
                while True:
                    header = read(4)
                    if header is None: break
                    request = json.loads(read(struct.unpack('<I', header)[0]))
                    method, values = request['method'], request['data']; calls.append(request)
                    if method == 'wayfire/get-config-option':
                        key = values['option'].removeprefix('scottland/')
                        reply = {'value': saved_value} if key == supported else {'error': 'unsupported fixture option'}
                    else:
                        reply = {'result': 'ok'} if set(values) == {'scottland/' + supported} else {'error': 'unexpected option'}
                    body = json.dumps(reply).encode(); connection.sendall(struct.pack('<I', len(body)) + body)
        except Exception as error: errors.append(str(error))
    worker = threading.Thread(target=fixture); worker.start()
    try:
        # This is a newly created IPC fixture socket, never a compositor session/socket.
        result = subprocess.run([str(repo / 'core/libexec/scottland-ctl'), 'get'], env=dict(os.environ, WAYFIRE_SOCKET=str(path)),
                                capture_output=True, text=True, timeout=10)
    finally:
        server.close(); worker.join(timeout=10); path.unlink(missing_ok=True)
    if worker.is_alive() or errors: raise RuntimeError(errors or 'fixture did not finish')
    values = json.loads(result.stdout)
    check(supported + ': live value is returned under the canonical density name', result.returncode == 0 and values['goo_dye_density'] == saved_value)
    check(supported + ': a supported legacy density is not discarded as unsupported', 'goo_dye_density' not in values['unsupported'])
    # A second connection for the actual write path.
    server = socket.socket(socket.AF_UNIX); server.bind(str(path)); server.listen(1); server.settimeout(5)
    calls.clear(); worker = threading.Thread(target=fixture); worker.start()
    try:
        result = subprocess.run([str(repo / 'core/libexec/scottland-ctl'), 'set', 'goo_dye_density', '0.7'],
                                env=dict(os.environ, WAYFIRE_SOCKET=str(path)), capture_output=True, text=True, timeout=10)
    finally:
        server.close(); worker.join(timeout=10); path.unlink(missing_ok=True)
    if worker.is_alive() or errors: raise RuntimeError(errors or 'fixture did not finish')
    writes = [c['data'] for c in calls if c['method'] == 'wayfire/set-config-options']
    check(supported + ': live preview writes only the option supported by this plugin', result.returncode == 0 and writes == [{'scottland/' + supported: '0.7'}] and 'error' not in json.loads(result.stdout))

with tempfile.TemporaryDirectory(prefix='density-ctl-', dir=repo / 'build') as temporary:
    scenario(Path(temporary), 'goo_dye_strength', 1.5)
    scenario(Path(temporary), 'goo_dye_density', .4)
raise SystemExit(1 if failures else 0)
