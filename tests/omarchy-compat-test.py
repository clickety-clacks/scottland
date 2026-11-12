#!/usr/bin/env python3
"""Exercise session admission and return to Omarchy with disposable command stand-ins."""
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix="scottland-compat-") as scratch:
    base = Path(scratch)
    hooks = base / "data/scottland/dev"
    (hooks / "libexec").mkdir(parents=True)
    (hooks / "libexec/scottland-omarchy-compat").symlink_to(
        ROOT / "omarchy/libexec/scottland-omarchy-compat")
    (hooks / "supported-series").symlink_to(ROOT / "omarchy/supported-series")
    bin_dir = base / "bin"
    bin_dir.mkdir()
    calls = base / "calls"

    def command(name, body):
        path = bin_dir / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)

    command("omarchy-version", 'cat "$VERSION_FILE"')
    command("pkexec", 'printf "pkexec %s\\n" "$*" >>"$CALLS"')
    command("wayfire", 'printf "wayfire %s\\n" "$*" >>"$CALLS"')
    command("notify-send", 'printf "notify %s\\n" "$*" >>"$CALLS"')
    command("omarchy-osd", 'printf "osd %s\\n" "$*" >>"$CALLS"')
    command("systemctl", "exit 1")
    command("scottland-build-config", 'printf "%s\\n" "$HOME/config.ini"')
    (hooks / "libexec/scottland-build-config").symlink_to(bin_dir / "scottland-build-config")
    version_file = base / "version"
    env = dict(os.environ, HOME=str(base), XDG_DATA_HOME=str(base / "data"),
               XDG_CONFIG_HOME=str(base / "config"), XDG_STATE_HOME=str(base / "state"),
               XDG_RUNTIME_DIR=str(base / "runtime"), VERSION_FILE=str(version_file),
               CALLS=str(calls), PATH=f"{bin_dir}:/usr/bin:/bin", XDG_SESSION_ID="42")
    (base / "runtime").mkdir()

    def run(path, version, **extra):
        version_file.write_text(version + "\n")
        calls.write_text("")
        result = subprocess.run([str(path)], env={**env, **extra}, text=True,
                                capture_output=True, timeout=15)
        return result, calls.read_text()

    launcher = ROOT / "core/session/start-scottland"
    result, invoked = run(launcher, "4.5.8-2")
    assert result.returncode == 0 and "wayfire " in invoked and "pkexec " not in invoked, (result, invoked)

    result, invoked = run(launcher, "4.6.0-1")
    assert result.returncode == 0 and "switch hyprland close 42" in invoked and "wayfire " not in invoked, (result, invoked)
    hook = base / "config/omarchy/hooks/post-boot.d/scottland-compatibility"
    assert hook.exists(), "fallback notice was not queued for Omarchy post-boot"
    calls.write_text("")
    notice = subprocess.run(["bash", str(hook)], env=env, text=True, capture_output=True, timeout=15)
    assert notice.returncode == 0 and "notify -u critical" in calls.read_text(), notice
    assert not hook.exists(), "fallback notice would repeat on every login"

    switch = ROOT / "omarchy/bin/scottland-switch"
    # The switch command accepts --to; run it explicitly to observe that it
    # leaves Omarchy's windows and login session untouched on a new series.
    version_file.write_text("5.0.0-1\n")
    calls.write_text("")
    result = subprocess.run([str(switch), "--to", "scottland"], env={**env,
                            "XDG_CURRENT_DESKTOP": "Hyprland", "SCOTTLAND_HOOKS": str(hooks)},
                            text=True, capture_output=True, timeout=15)
    assert result.returncode == 1 and "notify -u critical" in calls.read_text(), result
    assert "pkexec " not in calls.read_text(), "unsupported switch altered the session"

    (hooks / "supported-series").unlink()
    (hooks / "supported-series").write_text("4.5\n4.6\n")
    result, invoked = run(launcher, "4.6.3-1")
    assert result.returncode == 0 and "wayfire " in invoked and "pkexec " not in invoked, (result, invoked)

print("Omarchy 4.5 patch starts Scottland; unsupported login and switch return to working Omarchy")
