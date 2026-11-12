#!/usr/bin/env python3
"""A permitted headless Scottland session starts after a tested 4.5 patch check."""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from omarchy_fixture import REPO, Checks, Fixture, Session  # noqa: E402

scratch = Path(os.environ.get("SCOTTLAND_TEST_SCRATCH", REPO / "build"))
fixture = Fixture(scratch / "omarchy-compat-session-fixture", helpers=False,
                  commands={"omarchy-version": 'printf "4.5.8-2\\n"'},
                  lua='hl.bind("CTRL + ALT + F12", hl.dsp.global("omarchy:menu.theme"))')
checks = Checks()
guard = REPO / "build/hooks/libexec/scottland-omarchy-compat"
result = subprocess.run([str(guard), "--check"], capture_output=True, text=True,
                        env={**os.environ, "PATH": f"{fixture.bin}:/usr/bin:/bin"}, timeout=15)
checks("the installed 4.5 patch passes the packaged-series guard",
       result.returncode == 0, (result.returncode, result.stderr))
if result.returncode == 0:
    with Session(fixture, "compat-patch-headless") as session:
        ready, _ = session.wait_shim(timeout=20)
        if not ready:
            for name in ("wayfire.log", "state/scottland/hyprshim.log"):
                log = session.dir / name
                if log.exists():
                    print(f"{name}:\n{log.read_text(errors='replace')[-3000:]}", flush=True)
        checks("Scottland's Omarchy headless session starts and answers monitor IPC",
               ready and bool(session.owned(r"wayfire")),
               (ready, session.owned(r"wayfire")))

sys.exit(checks.summary())
