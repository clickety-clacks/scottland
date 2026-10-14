#!/usr/bin/env python3
"""TP6 internal CLI action: one same-session Ghostty reload or a truthful no-op/error."""
from contextlib import redirect_stderr
from importlib.machinery import SourceFileLoader
import io
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
from unittest.mock import patch

root = Path(__file__).resolve().parents[1]
helper = root / "core/libexec/scottland-color-scheme"
core = SourceFileLoader("scottland_color_scheme_tp6", str(helper)).load_module()
checks = 0


def check(name, okay):
    global checks
    assert okay, name
    checks += 1
    print("PASS", name)


with tempfile.TemporaryDirectory(prefix="scottland-theme-push-cli-") as temporary:
    config_home = Path(temporary) / "config"
    location = config_home / "gooarchy" / "current-theme"
    environment = {**os.environ, "XDG_CONFIG_HOME": str(config_home),
                   "XDG_RUNTIME_DIR": str(Path(temporary) / "runtime"),
                   "WAYLAND_DISPLAY": "wayland-tp6-test"}

    result = subprocess.run([sys.executable, str(helper), "terminal-theme-push"], env=environment,
                            capture_output=True, text=True, check=False)
    check("executable action returns success with no current-theme location",
          result.returncode == 0 and result.stdout == "" and result.stderr == "")

    with patch.dict(os.environ, environment, clear=False):
        with patch.object(core, "_ghostty_pids", side_effect=AssertionError("absent location scanned processes")):
            check("absent location skips process scan", core.main(["terminal-theme-push"]) == 0)

        location.mkdir(parents=True)
        with patch.object(core, "_ghostty_pids", side_effect=AssertionError("missing links scanned processes")):
            check("missing theme links skip process scan", core.main(["terminal-theme-push"]) == 0)

        theme = Path(temporary) / "theme.conf"
        theme.write_text("theme = test\n", encoding="utf-8")
        os.symlink(theme, location / "light")

        with patch.dict(os.environ, {"WAYLAND_DISPLAY": ""}):
            with patch.object(core, "_ghostty_pids", side_effect=AssertionError("absent display scanned processes")):
                check("absent display is a no-op", core.main(["terminal-theme-push"]) == 0)

        with patch.object(core, "_ghostty_pids", return_value=[]):
            check("no eligible Ghostty is a successful no-op", core.main(["terminal-theme-push"]) == 0)

        with patch.object(core, "_ghostty_pids", return_value=[107]), patch.object(core.os, "kill") as kill:
            status = core.main(["terminal-theme-push"])
            check("eligible Ghostty receives exactly one SIGUSR2", status == 0 and
                  kill.call_args_list == [((107, signal.SIGUSR2), {})])

        with patch.object(core, "_ghostty_pids", return_value=[106]), \
                patch.object(core.os, "kill", side_effect=PermissionError("permission denied")), \
                redirect_stderr(io.StringIO()) as stderr:
            status = core.main(["terminal-theme-push"])
            check("attempted signal failure reports stderr and nonzero status", status == 1 and
                  "could not reload Ghostty process 106: permission denied" in stderr.getvalue())

print(f"{checks} passed")
