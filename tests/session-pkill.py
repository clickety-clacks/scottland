#!/usr/bin/env python3
"""pkill / pgrep for an isolated test session (tests/omarchy_fixture.py puts it on the fixture
PATH under both names): it sees only processes of the session it runs in, identified by the
XDG_CONFIG_HOME tests/headless.sh gives each session, so a stock command such as
`pkill hyprpicker` can't reach the machine's other sessions. Supports the forms tests and Omarchy
use: [-x] [-f] [-SIGNAL|-9] PATTERN.
"""
import os
import re
import signal
import sys
from pathlib import Path

marker = os.environ.get("XDG_CONFIG_HOME", "")
if os.environ.get("SCOTTLAND_TEST_MODEL") != "1" or not marker.endswith("/config"):
    sys.exit(f"{Path(sys.argv[0]).name}: not inside a test session; refusing")
marker = f"XDG_CONFIG_HOME={marker}".encode()
grep = Path(sys.argv[0]).name == "pgrep"
full = exact = False
sig, pattern = signal.SIGTERM, None
for arg in sys.argv[1:]:
    if arg == "-f":
        full = True
    elif arg == "-x":
        exact = True
    elif arg.startswith("-") and len(arg) > 1:
        name = arg[1:].upper()
        sig = signal.Signals(int(name)) if name.isdigit() else signal.Signals[
            name if name.startswith("SIG") else "SIG" + name]
    else:
        pattern = arg
if pattern is None:
    sys.exit(2)

found = []
for proc in Path("/proc").glob("[0-9]*"):
    pid = int(proc.name)
    if pid in (os.getpid(), os.getppid()):
        continue
    try:
        if marker not in (proc / "environ").read_bytes().split(b"\0"):
            continue
        text = ((proc / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
                if full else (proc / "comm").read_text().strip())
    except OSError:
        continue
    if re.fullmatch(pattern, text) if exact else re.search(pattern, text):
        found.append(pid)
for pid in found:
    if grep:
        print(pid)
    else:
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass
sys.exit(0 if found else 1)
