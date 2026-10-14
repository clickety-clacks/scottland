#!/usr/bin/env python3
"""The Scottland shell must not show Omarchy's false layout-reload notice."""
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile


repo = Path(__file__).resolve().parents[1]
hook = repo / "omarchy/autostart.d/20-omarchy-shell"
override = repo / "omarchy/libexec/omarchy-hyprland-workspace-layout-toggle"
shim = repo / "omarchy/shim/scottland-hyprshim"


def restart_shell_uses_adapter(temp):
    fake_bin = temp / "bin"
    fake_bin.mkdir(parents=True)
    hooks = repo / "omarchy"
    state = temp / "state"
    selected_path = temp / "selected-command"
    command_output = temp / "command.output"
    command_error = temp / "command.error"
    notification_log = temp / "notifications.log"

    launcher = fake_bin / "omarchy-launch-shell"
    launcher.write_text(
        "#!/bin/sh\n"
        f"command -v {override.name} >\"$SELECTED_COMMAND\"\n"
        f"exec {override.name} >\"$COMMAND_OUTPUT\" 2>\"$COMMAND_ERROR\"\n"
    )
    launcher.chmod(0o755)

    # This stand-in is what wins if the shim restarts the shell without the adapter PATH.
    stock = fake_bin / override.name
    stock.write_text(
        "#!/bin/sh\n"
        "omarchy-notification-send 'Workspace layout needs a Hyprland reload'\n"
    )
    stock.chmod(0o755)
    notifier = fake_bin / "omarchy-notification-send"
    notifier.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >>\"$NOTIFICATION_LOG\"\n")
    notifier.chmod(0o755)

    names = (
        "PATH", "SCOTTLAND_HOOKS", "SCOTTLAND_TEST_STATE", "XDG_STATE_HOME",
        "SELECTED_COMMAND", "COMMAND_OUTPUT", "COMMAND_ERROR", "NOTIFICATION_LOG",
    )
    previous = {name: os.environ.get(name) for name in names}
    previous_dont_write_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    os.environ.update({
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "SCOTTLAND_HOOKS": str(hooks),
        "SCOTTLAND_TEST_STATE": str(state),
        "XDG_STATE_HOME": str(state),
        "SELECTED_COMMAND": str(selected_path),
        "COMMAND_OUTPUT": str(command_output),
        "COMMAND_ERROR": str(command_error),
        "NOTIFICATION_LOG": str(notification_log),
    })

    spawned = []
    real_popen = subprocess.Popen
    module = None
    reply = None
    try:
        loader = importlib.machinery.SourceFileLoader("scottland_hyprshim_layout_toggle_test", str(shim))
        spec = importlib.util.spec_from_loader("scottland_hyprshim_layout_toggle_test", loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        def capture_popen(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            spawned.append(process)
            return process

        subprocess.Popen = capture_popen
        reply = module.Translator(None).dispatch('hl.dsp.exec_cmd("omarchy-launch-shell")')
    finally:
        subprocess.Popen = real_popen
        if module is not None:
            module.log.file.close()
        sys.dont_write_bytecode = previous_dont_write_bytecode
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        libexec = str(hooks / "libexec")
        if libexec in sys.path:
            sys.path.remove(libexec)

    process_status = spawned[0].wait(timeout=5) if spawned else None
    selected = selected_path.read_text().strip() if selected_path.exists() else "<not found>"
    stderr = command_error.read_text() if command_error.exists() else ""
    notifications = notification_log.read_text() if notification_log.exists() else ""
    expected = str(hooks / "libexec" / override.name)
    passed = (
        reply == "ok"
        and process_status == 7
        and selected == expected
        and "workspace layout cycling is unsupported in Scottland" in stderr
        and notifications.count("Workspace layout cycling is unavailable in Scottland") == 1
        and "Workspace layout needs a Hyprland reload" not in notifications
    )
    if passed:
        print("PASS  shell restarted through the shim keeps the adapter layout command")
    else:
        print("FAIL  shell restart through the shim loses the adapter layout command")
        print(f"  shim reply/process exit: {reply}/{process_status}")
        print(f"  selected command: {selected}")
        print(f"  expected command: {expected}")
        print(f"  command stderr: {stderr.rstrip()}")
        print(f"  notifications: {notifications.rstrip() or '<none>'}")
    return passed


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
        if not passed:
            print("FAIL  initial Scottland shell layout-toggle route")
            print(f"  launcher exit: {result.returncode}")
            print(f"  selected command: {selected}")
            print(f"  expected command: {expected}")
            print(f"  command stderr: {error.rstrip()}")
            print(f"  notification calls: {notified.rstrip() or '<none>'}")
            return 1

        print("PASS  initial Scottland shell reports layout cycling unavailable without Omarchy's reload notice")
        return 0 if restart_shell_uses_adapter(temp / "restart") else 1


if __name__ == "__main__":
    raise SystemExit(main())
