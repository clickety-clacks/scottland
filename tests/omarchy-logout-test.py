#!/usr/bin/env python3
"""Exercise the adapter logout against isolated stand-in compositor processes."""

import ctypes
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
LOGOUT = ROOT / "omarchy/bin/scottland-omarchy-logout"
FAKE_WAYFIRE = (
    "import ctypes,time; "
    "ctypes.CDLL(None).prctl(15,b'wayfire',0,0,0); "
    "time.sleep(60)"
)


def stub(path, text):
    path.write_text("#!/bin/sh\n" + text)
    path.chmod(0o755)


with tempfile.TemporaryDirectory(prefix="scottland-logout-") as directory:
    root = Path(directory)
    bin_dir = root / "bin"
    bin_dir.mkdir()
    log = root / "calls"
    stub(bin_dir / "omarchy-osd", 'printf "osd\\n" >>"$LOGOUT_TEST_LOG"\n')
    stub(bin_dir / "omarchy-hyprland-window-close-all", 'printf "close\\n" >>"$LOGOUT_TEST_LOG"\n')
    stub(bin_dir / "omarchy-system-logout", 'printf "stock\\n" >>"$LOGOUT_TEST_LOG"\n')
    stub(bin_dir / "scottland-exec", 'test "$1" = -- || exit 2\nshift\nSCOTTLAND_SESSION_PID=$LOGOUT_TEST_LAUNCHER exec "$@"\n')
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", LOGOUT_TEST_LOG=str(log))

    # The menu extension is shared with Hyprland. Its action must retain stock logout there.
    result = subprocess.run([str(LOGOUT)], env=dict(env, XDG_CURRENT_DESKTOP="Hyprland"),
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert log.read_text() == "stock\n"
    log.unlink()

    # Newer Omarchy exposes the same stock action as `omarchy-system logout`.
    (bin_dir / "omarchy-system-logout").unlink()
    stub(bin_dir / "omarchy-system", 'test "$1" = logout || exit 2\nprintf "stock-new\\n" >>"$LOGOUT_TEST_LOG"\n')
    result = subprocess.run([str(LOGOUT)],
                            env=dict(env, PATH=str(bin_dir), XDG_CURRENT_DESKTOP="Hyprland"),
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert log.read_text() == "stock-new\n"
    log.unlink()

    # A missing recorded session must refuse before closing any user windows.
    result = subprocess.run([str(LOGOUT), "--inside-scottland"],
                            env=dict(env, XDG_CURRENT_DESKTOP="Scottland:Wayfire",
                                     WAYFIRE_SOCKET=str(root / "socket"), SCOTTLAND_SESSION_PID="bad"),
                            capture_output=True, text=True, check=False)
    assert result.returncode != 0 and not log.exists(), result.stderr

    # A different Wayfire process may coexist. Logout must stop only the child of this
    # Scottland launcher, after requesting the normal Omarchy app-close sequence.
    unrelated = subprocess.Popen([sys.executable, "-c", FAKE_WAYFIRE])
    try:
        time.sleep(0.1)
        fixture = r'''
set -e
python3 -c "$1" &
own=$!
trap 'kill "$own" 2>/dev/null || true; wait "$own" 2>/dev/null || true' EXIT
for i in {1..40}; do
  pgrep -P "$$" -x wayfire >/dev/null && break
  sleep 0.05
done
LOGOUT_TEST_LAUNCHER=$$ XDG_CURRENT_DESKTOP=Scottland:Wayfire WAYFIRE_SOCKET=/tmp/fixture-wayfire "$2"
if wait "$own" 2>/dev/null; then
  own_status=0
else
  own_status=$?
fi
if [[ $own_status -ne 143 ]]; then exit 42; fi
'''
        own_wayfire = FAKE_WAYFIRE.replace("time.sleep(60)", "time.sleep(3)")
        result = subprocess.run(["bash", "-c", fixture, "fixture", own_wayfire, str(LOGOUT)],
                                env=env, capture_output=True, text=True, check=False)
        assert result.returncode == 0, (result.returncode, result.stdout, result.stderr,
                                        log.read_text() if log.exists() else "no calls")
        assert log.read_text() == "osd\nclose\n"
        assert unrelated.poll() is None, "logout stopped a different Wayfire process"
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=5)

print("Omarchy logout: stock Hyprland path, safe refusal, own Wayfire stop passed")
