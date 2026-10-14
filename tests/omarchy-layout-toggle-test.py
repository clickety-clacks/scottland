#!/usr/bin/env python3
"""The Scottland shell must not show Omarchy's false layout-reload notice."""
import os
from pathlib import Path
import subprocess
import tempfile


repo = Path(__file__).resolve().parents[1]
hook = repo / "omarchy/autostart.d/20-omarchy-shell"
override = repo / "omarchy/libexec/omarchy-hyprland-workspace-layout-toggle"


def main():
    with tempfile.TemporaryDirectory(prefix="scottland-layout-toggle-") as temp_name:
        temp = Path(temp_name)
        hooks = temp / "hooks"
        adapter_bin = hooks / "libexec"
        fake_bin = temp / "bin"
        runtime = temp / "runtime"
        adapter_bin.mkdir(parents=True)
        fake_bin.mkdir()
        adapter_bin.joinpath(override.name).symlink_to(override)

        # The launch hook's socket-ready poll is setup for a desktop session, not this command
        # behavior. Finish that bounded wait immediately in this isolated fixture.
        seq = fake_bin / "seq"
        seq.write_text("#!/bin/sh\nexit 0\n")
        seq.chmod(0o755)

        # This stands in for Omarchy's stock command: it emits the misleading notice
        # after the shim rejects workspace layout changes in Scottland.
        stock = fake_bin / override.name
        stock.write_text(
            "#!/bin/sh\n"
            "omarchy-notification-send 'Workspace layout needs a Hyprland reload'\n"
        )
        stock.chmod(0o755)
        notify_log = temp / "notifications.log"
        notifier = fake_bin / "omarchy-notification-send"
        notifier.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >>\"$NOTIFY_LOG\"\n")
        notifier.chmod(0o755)

        lookup = temp / "selected-command"
        stdout = temp / "shell.stdout"
        stderr = temp / "shell.stderr"
        launcher = fake_bin / "omarchy-launch-shell"
        launcher.write_text(
            "#!/bin/sh\n"
            f"command -v {override.name} >\"$SELECTED_COMMAND\"\n"
            f"{override.name} >\"$SHELL_STDOUT\" 2>\"$SHELL_STDERR\"\n"
        )
        launcher.chmod(0o755)

        env = os.environ.copy()
        env.update({
            "HOME": str(temp / "home"),
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "SCOTTLAND_HOOKS": str(hooks),
            "XDG_RUNTIME_DIR": str(runtime),
            "HYPRLAND_INSTANCE_SIGNATURE": "test-instance",
            "NOTIFY_LOG": str(notify_log),
            "SELECTED_COMMAND": str(lookup),
            "SHELL_STDOUT": str(stdout),
            "SHELL_STDERR": str(stderr),
        })
        result = subprocess.run([str(hook)], env=env, capture_output=True, text=True, timeout=5)

        selected = lookup.read_text().strip() if lookup.exists() else "<not found>"
        error = stderr.read_text() if stderr.exists() else ""
        notified = notify_log.read_text() if notify_log.exists() else ""
        expected = str(adapter_bin / override.name)
        passed = (
            result.returncode == 7
            and selected == expected
            and "workspace layout cycling is unsupported in Scottland" in error
            and notified.count("Workspace layout cycling is unavailable in Scottland") == 1
            and "Workspace layout needs a Hyprland reload" not in notified
        )
        if passed:
            print("PASS  Scottland shell reports layout cycling unavailable without Omarchy's reload notice")
            return 0

        print("FAIL  Scottland shell layout-toggle route")
        print(f"  launcher exit: {result.returncode}")
        print(f"  selected command: {selected}")
        print(f"  expected command: {expected}")
        print(f"  command stderr: {error.rstrip()}")
        print(f"  notification calls: {notified.rstrip() or '<none>'}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
