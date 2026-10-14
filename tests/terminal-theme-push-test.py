#!/usr/bin/env python3
"""TP1 watcher reload and TP6 terminal-theme-push CLI behavior."""
from contextlib import redirect_stderr
from importlib.machinery import SourceFileLoader
import io
import os
from pathlib import Path
import subprocess
import sys
import signal
import tempfile
from unittest.mock import patch

root = Path(__file__).resolve().parents[1]
core = SourceFileLoader("scottland_color_scheme", str(root / "core/libexec/scottland-color-scheme")).load_module()
checks = 0


def check(name, okay):
    global checks
    if not okay:
        raise AssertionError(name)
    checks += 1
    print("PASS", name)


def process(proc_root, pid, name, display, runtime_dir="/run/user/test"):
    folder = proc_root / str(pid)
    folder.mkdir()
    (folder / "comm").write_text(name + "\n")
    if display is not None:
        environment = b"PATH=/usr/bin\0WAYLAND_DISPLAY=" + display.encode() + b"\0"
        if runtime_dir is not None:
            environment += b"XDG_RUNTIME_DIR=" + runtime_dir.encode() + b"\0"
        (folder / "environ").write_bytes(environment)


with tempfile.TemporaryDirectory(prefix="scottland-theme-push-") as temporary:
    base = Path(temporary)
    config_home = base / "config"
    location = config_home / "gooarchy" / "current-theme"
    proc_root = base / "proc"
    proc_root.mkdir()

    with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config_home), "WAYLAND_DISPLAY": "wayland-test",
                                 "XDG_RUNTIME_DIR": "/run/user/test"}):
        with patch.object(core, "_ghostty_pids", side_effect=AssertionError("missing location must short-circuit")):
            check("an absent current-theme location does not scan or report a push",
                  core.push_terminal_theme(location=str(location), display="wayland-test", proc_root=str(proc_root)) == 0
                  and not location.exists())

        check("unrelated directory events do not push", core._terminal_theme_watch_event(
            "notes", str(location), "wayland-test", str(proc_root)) == 0)

        location.mkdir(parents=True)
        light_target = base / "light-theme.conf"
        dark_target = base / "dark-theme.conf"
        light_target.write_text("theme = light\n")
        dark_target.write_text("theme = dark\n")
        os.symlink(light_target, location / "light")
        os.symlink(dark_target, location / "dark")
        fixed_config = base / "fixed-ghostty.conf"
        fixed_config.write_text("theme = fixed\n")

        process(proc_root, 101, "ghostty", "wayland-test")
        process(proc_root, 102, "ghostty", "wayland-other")
        process(proc_root, 103, "foot", "wayland-test")
        process(proc_root, 104, "ghostty", None)
        process(proc_root, 105, "ghostty", "wayland-test", "/run/user/other")

        with patch.object(core.os, "kill") as kill:
            present, count = core._terminal_theme_location_transition(
                str(location), False, "wayland-test", str(proc_root), "/run/user/test")
            check("a location created after startup is pushed without changing its files",
                  present and count == 1 and kill.call_args_list == [((101, signal.SIGUSR2), {})])

            replacement = base / "light-theme-replacement.conf"
            replacement.write_text("theme = light-v2\n")
            pending_link = location / "light.next"
            os.symlink(replacement, pending_link)
            os.replace(pending_link, location / "light")
            before_push = (os.readlink(location / "light"), os.readlink(location / "dark"),
                           fixed_config.read_bytes(), replacement.read_bytes(), dark_target.read_bytes())
            count = core._terminal_theme_watch_event("light", str(location),
                                                     "wayland-test", str(proc_root), "/run/user/test")
            check("a v6 link event reloads Ghostty in this session only",
                  count == 1 and kill.call_args_list[-1] == ((101, signal.SIGUSR2), {}))
            after_push = (os.readlink(location / "light"), os.readlink(location / "dark"),
                          fixed_config.read_bytes(), replacement.read_bytes(), dark_target.read_bytes())
            check("push leaves the changed links and terminal configs untouched", after_push == before_push)

    with tempfile.TemporaryDirectory(prefix="scottland-theme-push-cli-") as temporary:
        config_home = Path(temporary) / "config"
        location = config_home / "gooarchy" / "current-theme"
        environment = {"XDG_CONFIG_HOME": str(config_home), "XDG_RUNTIME_DIR": "/run/user/test",
                       "WAYLAND_DISPLAY": "wayland-cli-test"}
        with patch.dict(os.environ, environment, clear=False):
            executable = subprocess.run([sys.executable, str(root / "core/libexec/scottland-color-scheme"),
                                         "terminal-theme-push"], env=os.environ.copy(),
                                        capture_output=True, text=True, check=False)
            check("executable one-shot CLI exits successfully when current-theme is absent",
                  executable.returncode == 0 and not executable.stdout and not executable.stderr)

            with patch.object(core, "_ghostty_pids", side_effect=AssertionError("absent location must not scan")):
                check("one-shot CLI exits successfully when current-theme is absent",
                      core.main(["terminal-theme-push"]) == 0)

            location.mkdir(parents=True)
            with patch.object(core, "_ghostty_pids", side_effect=AssertionError("missing links must not scan")):
                check("one-shot CLI exits successfully when neither theme link exists",
                      core.main(["terminal-theme-push"]) == 0)

            target = Path(temporary) / "theme.conf"
            target.write_text("theme = test\n")
            os.symlink(target, location / "light")

            with patch.dict(os.environ, {"WAYLAND_DISPLAY": ""}):
                with patch.object(core, "_ghostty_pids", side_effect=AssertionError("missing display must not scan")):
                    check("one-shot CLI exits successfully without a display",
                          core.main(["terminal-theme-push"]) == 0)

            with patch.object(core, "_ghostty_pids", return_value=[]):
                check("one-shot CLI exits successfully when this display has no eligible Ghostty",
                      core.main(["terminal-theme-push"]) == 0)

            with patch.object(core, "_ghostty_pids", return_value=[107]), patch.object(core.os, "kill") as kill:
                status = core.main(["terminal-theme-push"])
                check("one-shot CLI reloads an eligible Ghostty once and exits successfully",
                      status == 0 and kill.call_args_list == [((107, signal.SIGUSR2), {})])

            with patch.object(core, "_ghostty_pids", return_value=[106]), \
                    patch.object(core.os, "kill", side_effect=PermissionError("permission denied")), \
                    redirect_stderr(io.StringIO()) as stderr:
                status = core.main(["terminal-theme-push"])
                check("one-shot CLI reports a failed attempted reload with a nonzero status",
                      status == 1 and "could not reload Ghostty process 106: permission denied" in stderr.getvalue())

print(f"{checks} passed")
