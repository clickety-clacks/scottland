#!/usr/bin/env python3
"""Attention action errors must not replace state or stop later source updates."""
import importlib.machinery
import importlib.util
from pathlib import Path

loader = importlib.machinery.SourceFileLoader('attention', str(Path(__file__).resolve().parents[1] / 'core/libexec/scottland-attention-sources'))
module = importlib.util.module_from_spec(importlib.util.spec_from_loader('attention', loader))
loader.exec_module(module)

class Source:
    name = 'test-source'
    entries_by_window = {}
    answered = []
    listing = [{'window': 11}, {'window': 12}, {'window': 999}]
    def entries(self): return self.listing
    def window_of(self, entry, views): return entry['window']
    def run_answered(self, entry): self.answered.append(entry)

source = Source()
service = module.Service.__new__(module.Service)
service.sources = [source]
service.snapshot = {'session': 'test', 'version': 1, 'windows': [
    {'id': 11, 'attention': []}, {'id': 12, 'attention': []}]}

class Ipc:
    calls = []
    def call(self, method, data):
        self.calls.append(data)
        if data['window'] == 11:
            return {'error': 'no such window', 'in_front': True}
        return {'session': 'test', 'version': 2, 'windows': [{'id': 12, 'attention': ['test-source']}]}

service.ipc = Ipc()
service.sync(source)
assert [c['window'] for c in service.ipc.calls] == [11, 12]
print('PASS  stale listing is filtered and a close during marking does not stop the next window')
assert service.snapshot['windows'] == [{'id': 12, 'attention': ['test-source']}]
assert not source.answered
print('PASS  action error never replaces the snapshot or acknowledges attention')
# Closing an already-marked window while removing its source must also survive.
source.listing = []
service.ipc.call = lambda *_: {'error': 'no such window'}
service.sync(source)
assert service.snapshot['version'] == 2
print('PASS  vanished marked window does not crash source removal')
service.replace_snapshot({'session': 'test', 'version': 3, 'windows': []})
service.sync(source)
assert service.snapshot['windows'] == []
print('PASS  helper continues accepting subsequent snapshots after errors')

service.snapshot = {'session': 'test', 'version': 4, 'windows': [{'id': 12, 'attention': []}]}
source.listing = [{'window': 12}]
service.ipc.call = lambda *_: {**service.snapshot, 'in_front': True}
service.sync(source)
assert source.answered == [{'window': 12}]
print('PASS  unchanged valid action reply still acknowledges a window already in front')
