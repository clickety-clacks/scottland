#!/usr/bin/env python3
"""Stand-in screen recorder for tests/capture-test.sh, taking gpu-screen-recorder's arguments.

  capture-recorder.py HOOKS [gpu-screen-recorder arguments]

A virtual output has no screen the real recorder can capture, so this one records a moving test
pattern instead: an MP4 at 60 frames a second, in the size the arguments ask for (-s, else the
-w region's size, else the -w output's pixel size), with a tone when -a asks for audio. Like the
real recorder it writes the -o file while it runs and finishes it properly on SIGINT. Each run's
arguments go to HOOKS/recorder-args.jsonl, and a finished recording is copied to HOOKS/raw/ as it
was before any processing. Files in HOOKS/switch change what it does: recorder-refuse makes it
fail to start, recorder-stubborn makes it ignore SIGINT.
"""
import ctypes
import json
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

hooks = Path(sys.argv[1])
args = sys.argv[2:]
with (hooks / 'recorder-args.jsonl').open('a') as log:
    log.write(json.dumps(args) + '\n')


def value(flag):
    return args[args.index(flag) + 1] if flag in args else None


out = value('-o')
if (hooks / 'switch/recorder-refuse').exists():
    print('stand-in: the encoder refused to start', file=sys.stderr)
    sys.exit(1)
if (hooks / 'switch/recorder-stubborn').exists():
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    Path(out).write_bytes(b'\0' * 4096)
    while True:
        time.sleep(1)

window, size = value('-w'), value('-s')
if not size:
    if 'x' in window and '+' in window:
        size = window.split('+', 1)[0]
    else:
        header = subprocess.run(['/usr/bin/grim', '-t', 'ppm', '-o', window, '-'],
                                capture_output=True, check=True).stdout.split(maxsplit=3)
        size = f'{header[1].decode()}x{header[2].decode()}'
width, height = (int(n) // 2 * 2 for n in size.split('x'))
audio = value('-a') is not None
command = ['/usr/bin/ffmpeg', '-hide_banner', '-loglevel', 'error', '-re', '-f', 'lavfi',
           '-i', f'testsrc2=size={width}x{height}:rate=60']
if audio:
    command += ['-re', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000,volume=0.3']
command += ['-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-g', '30']
if audio:
    command += ['-c:a', 'aac']
command += ['-f', 'mp4', out]

libc = ctypes.CDLL(None, use_errno=True)
PR_SET_PDEATHSIG = 1
# The encoder goes with this process, even when it is killed outright.
encoder = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                           preexec_fn=lambda: libc.prctl(PR_SET_PDEATHSIG, signal.SIGKILL))
stopping = []
signal.signal(signal.SIGINT, lambda *_: stopping.append(True))
while not stopping and encoder.poll() is None:
    time.sleep(0.02)
if encoder.poll() is not None:
    sys.exit(f'stand-in: the encoder stopped by itself ({encoder.returncode})')
encoder.send_signal(signal.SIGINT)
encoder.wait()
shutil.copy(out, hooks / 'raw' / Path(out).name)
sys.exit(0)
