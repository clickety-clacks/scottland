#!/usr/bin/env python3
"""Deterministic behavior assertions, independent of the model/scene audit."""
import json
import os
import socket
import struct
import subprocess
import time

class Ipc:
    def __init__(self):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.settimeout(10)
        self.sock.connect(os.environ['WAYFIRE_SOCKET'])
    def call(self, method, data=None):
        body = json.dumps({'method': method, 'data': data or {}}).encode()
        self.sock.sendall(struct.pack('<I', len(body)) + body)
        def exactly(n):
            result = b''
            while len(result) < n:
                chunk = self.sock.recv(n-len(result))
                if not chunk: raise ConnectionError('compositor disconnected')
                result += chunk
            return result
        result = json.loads(exactly(struct.unpack('<I', exactly(4))[0]))
        assert not isinstance(result, dict) or not result.get('error'), result
        return result

ipc = Ipc()
def key(code, state): ipc.call('stipc/feed_key', {'key': code, 'state': state})
def drag(window, x):
    view = next(v for v in ipc.call('scottland/layout-state')['views'] if v['id'] == window)
    f = view['frame']; sx = f['x'] + f['width']/2; sy = f['y'] + f['height']/2
    ipc.call('stipc/move_cursor', {'x': round(sx), 'y': round(sy)})
    time.sleep(.1)
    key('KEY_LEFTMETA', True)
    ipc.call('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'press'})
    for i in range(1, 13):
        ipc.call('stipc/move_cursor', {'x': round(sx+(x-sx)*i/12), 'y': round(sy)})
        time.sleep(.025)
    time.sleep(.4)
    ipc.call('stipc/feed_button', {'combo': 'BTN_LEFT', 'mode': 'release'})
    key('KEY_LEFTMETA', False)
    time.sleep(.5)

clients = []
def open_app(app_id):
    clients.append(subprocess.Popen(['foot', '--app-id', app_id, '-T', app_id, '-W', '35x8', 'sleep', '600'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    time.sleep(.7)
    return next(v['id'] for v in ipc.call('window-rules/list-views') if v['app-id'] == app_id)

try:
    ipc.call('wayfire/set-config-options', {'scottland/sounds': False})
    width = ipc.call('window-rules/list-outputs')[0]['geometry']['width']
    window = open_app('scottland-regression-daemon')
    drag(window, width-6)
    time.sleep(.3)
    assert len(ipc.call('scottland/widgets')['widgets']) == 1
    subprocess.run(['python3', 'tests/model-process-test.py', '--reload'], check=True)
    print('PASS  unscoped launcher exit after marked reload clears PID while forked widget survives', flush=True)
finally:
    for client in clients:
        if client.poll() is None: client.terminate()
