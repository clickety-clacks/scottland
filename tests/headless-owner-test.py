#!/usr/bin/env python3
"""Actual pinned harness + real private mounts; fake compositor/shim only.

All runtime endpoints and children are fixture-owned. No real Wayfire, Hyprland,
user service, login session, shared runtime, network or target-host access.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import select
import socket
import subprocess
import tempfile
import time
import unittest
import importlib.util
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent
spec = importlib.util.spec_from_file_location('headless_owner', HERE/'headless-owner.py')
OWN = importlib.util.module_from_spec(spec)
spec.loader.exec_module(OWN)
RESULTS = []
OWNER = '11111111-2222-4333-8444-555555555555'
FAKE = r'''#!/usr/bin/python3
import json,os,pathlib,socket,subprocess,sys
d=pathlib.Path(os.environ['SCOTTLAND_HEADLESS_DIR'])
r=pathlib.Path(os.environ['XDG_RUNTIME_DIR'])
sock=socket.socket(socket.AF_UNIX);sock.bind(str(r/'wayland-1'));sock.listen(32)
env=dict(os.environ,WAYLAND_DISPLAY='wayland-1',WAYFIRE_SOCKET=str(r/'wayland-1'),HYPRLAND_INSTANCE_SIGNATURE='scottland_fixture')
shim=subprocess.Popen([str(pathlib.Path(env['SCOTTLAND_HOOKS'])/'libexec/scottland-hyprshim')],env=env)
lock=r/'hypr/scottland_fixture';lock.mkdir(parents=True)
(lock/'hyprland.lock').write_text(str(shim.pid)+'\nwayland-1\n')
def proc(pid):
 raw=pathlib.Path('/proc/'+str(pid)+'/stat').read_text(); fields=raw.rsplit(')',1)[1].split()
 return {'pid':pid,'ppid':int(fields[1]),'starttime':int(fields[19]),'argv':pathlib.Path('/proc/'+str(pid)+'/cmdline').read_bytes().decode().split('\0')}
def inode(p):
 s=p.stat();return [s.st_dev,s.st_ino,s.st_uid]
e={'compositor':proc(os.getpid()),'shim':proc(shim.pid),'runtime':inode(r),'backing':inode(d/'runtime'),'endpoint':inode(r/'wayland-1'),'mount_ns':os.readlink('/proc/self/ns/mnt'),'mountinfo':pathlib.Path('/proc/self/mountinfo').read_text()}
(d.parent/'spawn-evidence.json').write_text(json.dumps(e))
print('Using socket name wayland-1',flush=True)
if not (d.parent/'omit-record').exists():
 subprocess.run([str(pathlib.Path(env['SCOTTLAND_HOOKS'])/'autostart.d/01-record-environment')],env=env,check=True)
while True:
 c,_=sock.accept();c.close()
'''


# Faults exercise the real controller concurrently with guardian finalization.
BOUNDARY_BOOTSTRAP = r'''#!/usr/bin/python3
import importlib.util,sys,os,signal,json,time
from pathlib import Path
spec=importlib.util.spec_from_file_location('exact_owner',SOURCE)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
d=Path(sys.argv[2]); injected=[]
if sys.argv[1]=='launch':
 old_fsync=m.os.fsync;old_unlink=m.os.unlink;old_close=m.os.close
 def fsync(fd):
  if CASE in ('fsync_and_invalidation_fail','fsync_after_receipt_read') and (d/'launch-terminal.json').exists() and os.path.realpath('/proc/self/fd/'+str(fd))==str(d):
   injected.append('directory_fsync')
   if CASE=='fsync_after_receipt_read':time.sleep(.3)
   raise OSError(5,'injected directory fsync failure')
  return old_fsync(fd)
 def unlink(name,*a,**kw):
  if CASE=='fsync_and_invalidation_fail' and name=='launch-terminal.json':
   injected.append('invalidation_unlink');raise OSError(5,'injected invalidation failure')
  return old_unlink(name,*a,**kw)
 def close(fd):
  target=CASE=='late_term' and (d/'launch-terminal.json').exists() and os.path.realpath('/proc/self/fd/'+str(fd))==str(d)
  result=old_close(fd)
  if target:
   injected.append('late_SIGTERM');os.kill(os.getpid(),signal.SIGTERM)
  return result
 m.os.fsync=fsync;m.os.unlink=unlink;m.os.close=close
rc=m.main()
if sys.argv[1]=='launch':(d.parent/'guardian-result.json').write_text(json.dumps({'return':rc,'injected':injected}))
raise SystemExit(rc)
'''

def identity(path):
    s = path.lstat()
    return [s.st_dev, s.st_ino, s.st_mode, s.st_uid]


class Lifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='p52-lf-', dir=SOURCE.parent)
        self.root = Path(self.tmp.name)
        self.repo = self.root/'s'
        self.runtime = self.root/'r'
        self.scratch = self.repo/'build/hl'
        for p in [self.repo/'tests', self.repo/'core/config', self.repo/'build/hooks/libexec',
                  self.repo/'build/hooks/autostart.d', self.repo/'build/hooks/session-env.d',
                  self.root/'bin', self.root/'home', self.runtime]:
            p.mkdir(parents=True, mode=0o700)
        for src,dst in [('tests/headless.sh','tests/headless.sh'),
                        ('tests/headless-owner.py','tests/headless-owner.py'),
                        ('core/config/scottland.ini','core/config/scottland.ini'),
                        ('core/libexec/scottland-exec','build/hooks/libexec/scottland-exec'),
                        ('core/autostart.d/01-record-environment','build/hooks/autostart.d/01-record-environment')]:
            original = (Path(os.environ['SCOTTLAND_OWNER_TEST_HEADLESS_BASELINE'])
                        if src == 'tests/headless.sh' and os.environ.get('SCOTTLAND_OWNER_TEST_HEADLESS_BASELINE')
                        else (Path(os.environ['SCOTTLAND_OWNER_TEST_HELPER_BASELINE'])
                              if src == 'tests/headless-owner.py' and os.environ.get('SCOTTLAND_OWNER_TEST_HELPER_BASELINE')
                              else SOURCE/src))
            q=self.repo/dst;q.write_bytes(original.read_bytes());q.chmod(0o755)
        self.script(self.repo/'build/hooks/libexec/scottland-build-config',
                    '#!/bin/sh\n: >"$SCOTTLAND_CONFIG_OUTPUT"\n')
        self.script(self.repo/'build/hooks/libexec/scottland-hyprshim',
                    '#!/usr/bin/python3\nimport time\nwhile True:time.sleep(1)\n')
        self.script(self.root/'bin/wayfire',FAKE)
        self.script(self.root/'bin/dbus-run-session','#!/bin/sh\n[ "$1" != -- ] || shift\nexec "$@"\n')
        self.shared = socket.socket(socket.AF_UNIX)
        self.shared.bind(str(self.runtime/'wayland-1'));self.shared.listen(1)
        (self.runtime/'wayland-1.lock').write_text('foreign fixture lock\n')
        (self.runtime/'scottland').mkdir()
        (self.runtime/'scottland/wayland-1.env').write_bytes(b'FOREIGN=fixture\0')
        self.before = self.sentinels()
        self.env = dict(os.environ,PATH=str(self.root/'bin')+':/usr/bin:/bin',
                        HOME=str(self.root/'home'),XDG_RUNTIME_DIR=str(self.runtime),
                        SCOTTLAND_TEST_PATH=str(self.root/'bin'),
                        SCOTTLAND_HEADLESS_DIR=str(self.scratch),SCOTTLAND_HEADLESS_OWNER=OWNER,
                        SCOTTLAND_HEADLESS_OWNER_DIR=str(self.scratch),SCOTTLAND_HEADLESS_ISOLATION='0')
        for k in ['SCOTTLAND_SESSION_DIR','SCOTTLAND_TEST_HOOKS','SCOTTLAND_TEST_SCRATCH']:
            self.env.pop(k,None)
        self.receipt={'test':self.id().split('.')[-1],'commands':[]}

    def script(self,p,text):
        p.write_text(text);p.chmod(0o755)

    def sentinels(self):
        return {n:{'identity':identity(self.runtime/n),
                   'sha256':hashlib.sha256((self.runtime/n).read_bytes()).hexdigest() if n!='wayland-1' else None}
                for n in ['wayland-1','wayland-1.lock','scottland/wayland-1.env']}

    def call(self,*args):
        p=subprocess.run([str(self.repo/'tests/headless.sh'),*args],env=self.env,
                         capture_output=True,text=True,timeout=25)
        self.receipt['commands'].append({'argv':list(args),'exit':p.returncode,'stdout':p.stdout,'stderr':p.stderr})
        return p

    def spawn_evidence(self):
        return json.loads((self.repo/'build/spawn-evidence.json').read_text())

    def tearDown(self):
        # Reap ONLY fresh fixture children whose full starttime matches the record;
        # pidfd binds cleanup to the measured process, never a reused PID/group.
        evidence=self.repo/'build/spawn-evidence.json'
        if evidence.exists():
            e=json.loads(evidence.read_text());self.receipt['spawn']=e
            for record in [e['shim'],e['compositor']]:
                pid=record['pid']
                try:
                    fd=os.pidfd_open(pid)
                    try:
                        raw=Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()
                        if int(raw[19])!=record['starttime']:raise AssertionError('fixture PID reused; preserve')
                        signal.pidfd_send_signal(fd,signal.SIGTERM)
                        poll=select.poll();poll.register(fd,select.POLLIN)
                        if not poll.poll(3000):raise AssertionError('fixture child did not exit; preserve')
                    finally:os.close(fd)
                except ProcessLookupError:pass
        self.receipt['shared_sentinels_unchanged']=self.sentinels()==self.before
        self.shared.close()
        RESULTS.append(self.receipt)
        self.assertTrue(self.receipt['shared_sentinels_unchanged'])
        self.tmp.cleanup()

    def test_normal_private_start_run_stop(self):
        p=self.call('start','--omarchy');self.assertEqual(p.returncode,0,p.stderr)
        e=self.spawn_evidence();self.assertEqual(e['runtime'],e['backing'])
        self.assertNotEqual(e['runtime'][:2],identity(self.runtime)[:2])
        self.assertNotEqual(e['endpoint'][:2],identity(self.runtime/'wayland-1')[:2])
        self.assertNotEqual(e['mount_ns'],os.readlink('/proc/self/ns/mnt'))
        self.assertEqual(e['shim']['ppid'],e['compositor']['pid'])
        p=self.call('run','python3','-c','import os,json; s=os.stat(os.environ["XDG_RUNTIME_DIR"]); print(json.dumps([s.st_dev,s.st_ino,s.st_uid]))')
        self.assertEqual(p.returncode,0,p.stderr);self.assertEqual(json.loads(p.stdout),e['backing'])
        p=self.call('stop');self.assertEqual(p.returncode,0,p.stderr);self.assertFalse(self.scratch.exists())

    def test_plain_private_launch_uses_same_owned_stop(self):
        self.assertEqual(self.call('start').returncode,0)
        self.assertEqual(self.call('run','true').returncode,0)
        self.assertEqual(self.call('stop').returncode,0)
        self.assertFalse(self.scratch.exists())

    def test_missing_record_stops_owned_subtree_and_preserves_failure(self):
        (self.repo/'build/omit-record').touch()
        p=self.call('start','--omarchy');self.assertEqual(p.returncode,1,p.stderr)
        self.assertIn('missing or redirected',p.stderr)
        self.assertNotIn('failed to stop partial headless session',p.stderr)
        self.assertEqual((self.scratch/'.scottland-headless-mode').read_text(),'shim-pending\n')
        self.assertFalse((self.scratch/'display').exists())
        e=self.spawn_evidence();self.assertEqual(e['runtime'],e['backing'])
        p=self.call('stop','--preserve-scratch');self.assertEqual(p.returncode,2,p.stderr)
        # Automatic cleanup already committed; cached receipts do not authorize a second stop.
        terminal=json.loads((self.scratch/'launch-terminal.json').read_text())
        self.assertEqual(terminal['exit'],0);self.assertEqual(terminal['children'],[])
        for child in [e['shim'],e['compositor']]:
            try:
                fd=os.pidfd_open(child['pid'])
            except ProcessLookupError:
                continue
            try:
                poll=select.poll();poll.register(fd,select.POLLIN);self.assertTrue(poll.poll(1000))
            finally:os.close(fd)

    def prepare_owner(self):
        self.scratch.mkdir(mode=0o700)
        for n in ['runtime','tmp']:(self.scratch/n).mkdir(mode=0o700)
        (self.scratch/'.scottland-headless-owner').write_text(f'{os.getuid()}\n{self.runtime}\n{OWNER}\n')

    def test_foreign_owner_refused(self):
        self.prepare_owner();(self.scratch/'.scottland-headless-owner').write_text('foreign\n')
        p=self.call('stop');self.assertEqual(p.returncode,2);self.assertTrue(self.scratch.exists())

    def test_redirected_runtime_refused(self):
        self.prepare_owner();(self.scratch/'runtime').rmdir();(self.scratch/'runtime').symlink_to(self.runtime)
        p=self.call('stop');self.assertEqual(p.returncode,2);self.assertTrue(self.scratch.exists())

    def test_incomplete_mode_never_successful_stop(self):
        self.prepare_owner();(self.scratch/'.scottland-headless-mode').write_text('shim-pending\n')
        p=self.call('stop');self.assertEqual(p.returncode,2);self.assertTrue(self.scratch.exists())

    def test_guardian_birth_mismatch_refuses_without_signalling(self):
        self.assertEqual(self.call('start','--omarchy').returncode,0)
        path=self.scratch/'launch-owner.json';original=path.read_bytes()
        record=json.loads(original);record['guardian']['starttime']+=1
        path.write_text(json.dumps(record))
        try:
            p=self.call('stop');self.assertEqual(p.returncode,2,p.stderr)
            self.assertIn('birth identity changed',p.stderr)
            self.assertEqual(self.call('run','true').returncode,0)
        finally:path.write_bytes(original)
        self.assertEqual(self.call('stop').returncode,0)

    def test_runtime_inode_replacement_refuses(self):
        self.assertEqual(self.call('start','--omarchy').returncode,0)
        runtime=self.scratch/'runtime';saved=self.scratch/'runtime-saved'
        runtime.rename(saved);runtime.mkdir(mode=0o700)
        try:
            p=self.call('stop');self.assertEqual(p.returncode,2,p.stderr)
            self.assertIn('owner/runtime changed',p.stderr)
        finally:runtime.rmdir();saved.rename(runtime)
        self.assertEqual(self.call('stop').returncode,0)

    def test_owner_marker_inode_replacement_refuses(self):
        self.assertEqual(self.call('start','--omarchy').returncode,0)
        marker=self.scratch/'.scottland-headless-owner';saved=self.scratch/'owner-saved'
        marker.rename(saved);marker.write_bytes(saved.read_bytes())
        try:
            p=self.call('stop');self.assertEqual(p.returncode,2,p.stderr)
            self.assertEqual(self.call('run','true').returncode,0)
        finally:marker.unlink();saved.rename(marker)
        self.assertEqual(self.call('stop').returncode,0)

    def test_guardian_interrupt_reaps_and_keeps_nonzero(self):
        self.assertEqual(self.call('start','--omarchy').returncode,0)
        owner=json.loads((self.scratch/'launch-owner.json').read_text())
        fd=os.pidfd_open(owner['guardian']['pid'])
        try:
            self.assertTrue(OWN.same_birth(OWN.birth(owner['guardian']['pid']),owner['guardian']))
            signal.pidfd_send_signal(fd,signal.SIGTERM)
            poll=select.poll();poll.register(fd,select.POLLIN);self.assertTrue(poll.poll(7000))
        finally:os.close(fd)
        terminal=json.loads((self.scratch/'launch-terminal.json').read_text())
        self.assertEqual(terminal['exit'],143);self.assertEqual(terminal['children'],[])
        self.assertEqual(self.call('stop').returncode,2)

    def test_caller_timeout_preserves_failure_and_reaps(self):
        (self.repo/'build/omit-record').touch()
        p=subprocess.run(['timeout','--signal=TERM','--kill-after=8s','1s',
                          str(self.repo/'tests/headless.sh'),'start','--omarchy'],
                         env=self.env,capture_output=True,text=True,timeout=15)
        self.receipt['commands'].append({'argv':['timeout','start','--omarchy'],'exit':p.returncode,
                                         'stdout':p.stdout,'stderr':p.stderr})
        self.assertEqual(p.returncode,124,p.stderr)
        path=self.scratch/'launch-terminal.json';deadline=time.monotonic()+7
        while not path.exists() and time.monotonic()<deadline:time.sleep(.02)
        terminal=json.loads(path.read_text())
        self.assertNotEqual(terminal['exit'],0);self.assertEqual(terminal['children'],[])


    def boundary_case(self, case):
        helper = self.repo/'tests/headless-owner.py'
        source = self.root/'exact-owner.py'
        source.write_bytes(helper.read_bytes())
        helper.write_text(BOUNDARY_BOOTSTRAP.replace('SOURCE', repr(str(source))).replace('CASE', repr(case)))
        self.assertEqual(self.call('start','--omarchy').returncode,0)
        stopped = self.call('stop','--preserve-scratch')
        result = self.repo/'build/guardian-result.json'
        deadline = time.monotonic()+3
        while not result.exists() and time.monotonic()<deadline:time.sleep(.01)
        actual = json.loads(result.read_text())
        self.receipt['boundary_result'] = actual
        expected = {'fsync_after_receipt_read': ['directory_fsync'],
                    'fsync_and_invalidation_fail': ['directory_fsync', 'invalidation_unlink'],
                    'late_term': ['late_SIGTERM']}
        self.assertEqual(actual['injected'], expected[case])
        self.assertNotEqual(actual['return'],0)
        self.assertEqual(stopped.returncode,2,stopped.stderr)
        self.assertTrue(self.scratch.exists())
        self.assertNotEqual(self.call('stop').returncode,0)
        self.assertTrue(self.scratch.exists())

    def test_concurrent_fsync_invalidation_refuses_cached_success(self):
        self.boundary_case('fsync_after_receipt_read')

    def test_fsync_and_invalidation_failure_refuses_stale_success(self):
        self.boundary_case('fsync_and_invalidation_fail')

    def test_late_term_after_custody_close_is_nonzero(self):
        self.boundary_case('late_term')


class FailureControls(unittest.TestCase):
    def test_unreadable_child_identity_never_signals(self):
        with patch.object(OWN,'reap'),patch.object(OWN,'children',return_value=[42]), \
             patch.object(OWN,'birth',side_effect=PermissionError('fixture denied')), \
             patch.object(OWN.signal,'pidfd_send_signal') as send:
            with self.assertRaises(PermissionError):OWN.stop_children(time.monotonic()+1)
            send.assert_not_called()

    def test_child_pid_reuse_never_signals(self):
        parent=os.getpid()
        with patch.object(OWN,'reap'),patch.object(OWN,'children',return_value=[42]), \
             patch.object(OWN,'birth',side_effect=[{'pid':42,'ppid':parent,'starttime':1},
                                                  {'pid':42,'ppid':parent,'starttime':2}]), \
             patch.object(OWN.os,'pidfd_open',return_value=99),patch.object(OWN.os,'close'), \
             patch.object(OWN.signal,'pidfd_send_signal') as send:
            with self.assertRaises(OWN.Refusal):OWN.stop_children(time.monotonic()+1)
            send.assert_not_called()

    def test_foreign_parent_never_signals(self):
        foreign={'pid':42,'ppid':os.getpid()+1,'starttime':1}
        with patch.object(OWN,'reap'),patch.object(OWN,'children',return_value=[42]), \
             patch.object(OWN,'birth',return_value=foreign), \
             patch.object(OWN.os,'pidfd_open',return_value=99),patch.object(OWN.os,'close'), \
             patch.object(OWN.signal,'pidfd_send_signal') as send:
            with self.assertRaises(OWN.Refusal):OWN.stop_children(time.monotonic()+1)
            send.assert_not_called()

    def test_cleanup_deadline_is_nonzero(self):
        with patch.object(OWN,'reap'),patch.object(OWN,'children',return_value=[42]):
            with self.assertRaises(OWN.Refusal):OWN.stop_children(time.monotonic()-1)

    def persistence(self,operation,effect):
        with tempfile.TemporaryDirectory() as root:
            state=OWN.State(root)
            try:
                with patch.object(OWN.os,operation,side_effect=effect):
                    with self.assertRaises(OSError):state.write('launch-terminal.json',{'exit':0})
                self.assertFalse((Path(root)/'launch-terminal.json').exists())
            finally:os.close(state.fd)

    def test_terminal_file_fsync_failure_has_no_success(self):
        self.persistence('fsync',OSError('file fsync denied'))

    def test_terminal_rename_failure_has_no_success(self):
        self.persistence('rename',OSError('rename denied'))

    def test_terminal_directory_fsync_failure_invalidates_success(self):
        self.persistence('fsync',[None,OSError('directory fsync denied')])


if __name__=='__main__':
    try:unittest.main(verbosity=2)
    finally:(Path(os.environ.get('SCOTTLAND_OWNER_TEST_EVIDENCE', str(HERE.parent/'build/headless-owner-observations.json')))).write_text(json.dumps(RESULTS,indent=2)+'\n')
