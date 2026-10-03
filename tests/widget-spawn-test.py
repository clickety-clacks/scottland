#!/usr/bin/env python3
"""Broker ownership: transferable pidfd, cancellation and disconnect queue draining.
No compositor needed. Run on the test host; temporary files stay under build/.
"""
import array
import json
import os
from pathlib import Path
import select
import shutil
import socket
import subprocess
import sys
import tempfile
import time

root=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='widget-spawn-',dir=root/'build') as directory:
    folder=Path(directory)
    shutil.copy(root/'core/libexec/scottland-widget-spawn',folder/'scottland-widget-spawn')
    launcher=folder/'scottland-widget-launch'
    launcher.write_text('#!/usr/bin/python3\nimport os,sys,time,json\nc=json.loads(sys.argv[1])\nopen(c["record"],"w").write(str(os.getpid()))\ntime.sleep(600)\n')
    launcher.chmod(0o755)
    children=[];rights=[];peers=[];brokers=[]
    def start():
        a,b=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
        child=subprocess.Popen([str(folder/'scottland-widget-spawn'),str(b.fileno())],pass_fds=[b.fileno()])
        b.close();a.settimeout(5);peers.append(a);brokers.append(child)
        return a,child
    def send(peer,body):peer.send(json.dumps(body).encode())
    def receive(peer):
        raw,ancillary,flags,_=peer.recvmsg(65536,socket.CMSG_SPACE(4))
        fd=array.array('i');fd.frombytes(ancillary[0][2]);rights.append(fd[0])
        reply=json.loads(raw);children.append(reply['pid']);return reply,fd[0]
    def stopped(fd):assert select.select([fd],[],[],5)[0], 'launch did not stop'
    try:
        peer,broker=start();record=folder/'normal.pid'
        send(peer,{'unit':'scottland-test-spawn-normal.scope','record':str(record)})
        reply,fd=receive(peer)
        assert reply['pid']>0 and not select.select([fd],[],[],.05)[0]
        send(peer,{'stop':reply['unit']});stopped(fd)
        print('PASS broker transfers a live pidfd and cancels that exact launch',flush=True)
        peer.close();broker.wait(timeout=5)
        peer,broker=start();record=folder/'disconnected.pid'
        # Queue launch and cancellation, then close before any reply can be read.
        send(peer,{'unit':'scottland-test-spawn-disconnected.scope','record':str(record)})
        send(peer,{'stop':'scottland-test-spawn-disconnected.scope'})
        peer.close();broker.wait(timeout=5)
        if record.exists():
            pid=int(record.read_text());children.append(pid)
            fd=os.pidfd_open(pid) if Path(f'/proc/{pid}').exists() else None
            if fd is not None:rights.append(fd);stopped(fd)
        print('PASS disconnect drains queued cancellation without leaving its launch alive',flush=True)
    finally:
        for peer in peers:peer.close()
        for fd in rights:
            if not select.select([fd],[],[],0)[0]:
                import signal
                signal.pidfd_send_signal(fd,signal.SIGKILL)
            os.close(fd)
        for broker in brokers:
            if broker.poll() is None:broker.terminate();broker.wait(timeout=5)
