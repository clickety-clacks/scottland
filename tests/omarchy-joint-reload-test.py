#!/usr/bin/env python3
"""Check the adapter's ordered, session-owned shim and Omarchy shell restart hooks."""
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time


REPO = Path(__file__).resolve().parents[1]
SIGNATURE = "scottland_joint_reload_test"
DISPLAY = "wayland-77"


def check(results, name, passed, detail=""):
    results.append((name, bool(passed)))
    print(f"{'PASS' if passed else 'FAIL'}  {name}", flush=True)
    if detail and not passed:
        print(f"  {detail}", flush=True)


def read_lock(lock):
    try:
        pid, display = (lock.read_text().splitlines() + ["", ""])[:2]
        return (int(pid), display) if pid.isdecimal() else (None, display)
    except OSError:
        return None, ""


def process_is_session_shim(pid, shim, env):
    if not pid:
        return False
    proc = Path(f"/proc/{pid}")
    try:
        command = proc.joinpath("cmdline").read_bytes()
        entries = proc.joinpath("environ").read_bytes().split(b"\0")
        return (
            proc.stat().st_uid == os.getuid()
            and str(shim).encode() in command
            and f"HYPRLAND_INSTANCE_SIGNATURE={env['HYPRLAND_INSTANCE_SIGNATURE']}".encode() in entries
            and f"WAYLAND_DISPLAY={env['WAYLAND_DISPLAY']}".encode() in entries
        )
    except OSError:
        return False


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    return predicate()


def stop_owned(pid, shim, env):
    if process_is_session_shim(pid, shim, env):
        os.kill(pid, signal.SIGTERM)
        wait_for(lambda: not process_is_session_shim(pid, shim, env))


def main():
    results = []
    with tempfile.TemporaryDirectory(prefix="scottland-joint-reload-") as temp_name:
        temp = Path(temp_name)
        runtime = temp / "runtime"
        directory = runtime / "hypr" / SIGNATURE
        hooks = temp / "hooks"
        libexec = hooks / "libexec"
        bin_dir = temp / "bin"
        state = temp / "state"
        directory.mkdir(parents=True)
        libexec.mkdir(parents=True)
        bin_dir.mkdir()
        state.mkdir()
        lock = directory / "hyprland.lock"

        shim = libexec / "scottland-hyprshim"
        shim.write_text(
            "#!/usr/bin/env python3\n"
            "import os, time\n"
            "from pathlib import Path\n"
            "signature = os.environ['HYPRLAND_INSTANCE_SIGNATURE']\n"
            "lock = Path(os.environ['XDG_RUNTIME_DIR']) / 'hypr' / signature / 'hyprland.lock'\n"
            "lock.write_text(f\"{os.getpid()}\\n{os.environ['WAYLAND_DISPLAY']}\\n\")\n"
            "while True: time.sleep(1)\n"
        )
        shim.chmod(0o755)
        adapter_command = libexec / "omarchy-hyprland-workspace-layout-toggle"
        adapter_command.write_text("#!/bin/sh\nexit 0\n")
        adapter_command.chmod(0o755)

        restart_log = temp / "shell-restart.log"
        restart_path = temp / "shell-restart.path"
        selected_command = temp / "shell-restart.command"
        restart = bin_dir / "omarchy-restart-shell"
        restart.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            "lock=$XDG_RUNTIME_DIR/hypr/$HYPRLAND_INSTANCE_SIGNATURE/hyprland.lock\n"
            "pid=$(sed -n '1p' \"$lock\")\n"
            "[ \"$pid\" = \"$EXPECTED_SHIM_PID\" ] || exit 31\n"
            "grep -aq scottland-hyprshim \"/proc/$pid/cmdline\" || exit 32\n"
            "printf '%s\\n' \"$pid\" >\"$SHELL_RESTART_LOG\"\n"
            "printf '%s\\n' \"$PATH\" >\"$SHELL_RESTART_PATH\"\n"
            "command -v omarchy-hyprland-workspace-layout-toggle >\"$SHELL_RESTART_COMMAND\"\n"
        )
        restart.chmod(0o755)
        lock_query = bin_dir / "omarchy-hyprland-session-locked"
        lock_query.write_text(
            "#!/bin/sh\n"
            "[ \"${SESSION_LOCKED:-0}\" = 1 ] && exit 0\n"
            "exit 1\n"
        )
        lock_query.chmod(0o755)

        env = os.environ.copy()
        env.update({
            "HOME": str(temp / "home"),
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "XDG_RUNTIME_DIR": str(runtime),
            "XDG_STATE_HOME": str(state),
            "SCOTTLAND_HOOKS": str(hooks),
            "HYPRLAND_INSTANCE_SIGNATURE": SIGNATURE,
            "WAYLAND_DISPLAY": DISPLAY,
        })
        (temp / "home").mkdir()
        old = subprocess.Popen([str(shim)], env=env, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, start_new_session=True)
        replacement = None
        unrelated = None
        try:
            before = wait_for(lambda: read_lock(lock) if read_lock(lock) == (old.pid, DISPLAY) else None)
            check(results, "the initial shim owns this session lock", before == (old.pid, DISPLAY))

            hook = REPO / "omarchy/reload.d/10-hyprshim"
            locked_env = env.copy()
            locked_env["SESSION_LOCKED"] = "1"
            locked = subprocess.run([str(hook)], env=locked_env, capture_output=True, text=True, timeout=10)
            check(results, "a secured lock refuses shim restart without disconnecting the old shell",
                  locked.returncode != 0 and read_lock(lock) == (old.pid, DISPLAY) and
                  process_is_session_shim(old.pid, shim, env),
                  f"hook exit={locked.returncode}; stderr={locked.stderr.strip()}")

            stopped = subprocess.run([str(hook)], env=env, capture_output=True, text=True, timeout=15)
            old_stopped = wait_for(lambda: old.poll() is not None)
            lock_after_restart = wait_for(
                lambda: (current if (current := read_lock(lock))[0] not in (None, old.pid) else None))
            replacement, replacement_display = lock_after_restart or (None, "")
            check(results, "reload stops the prior session shim", old_stopped,
                  f"hook exit={stopped.returncode}; stderr={stopped.stderr.strip()}")
            check(results, "reload starts a different shim for the same session",
                  stopped.returncode == 0 and replacement not in (None, old.pid) and
                  replacement_display == DISPLAY and process_is_session_shim(replacement, shim, env),
                  f"hook exit={stopped.returncode}; lock={(replacement, replacement_display)}")
            stop_owned(replacement, shim, env)

            unrelated = subprocess.Popen(["python3", "-c", "import time; time.sleep(30)"], env=env,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            lock.write_text(f"{unrelated.pid}\n{DISPLAY}\n")
            started = subprocess.run([str(hook)], env=env, capture_output=True, text=True, timeout=15)
            lock_after_stale = wait_for(
                lambda: (current if (current := read_lock(lock))[0] not in (None, unrelated.pid) else None))
            replacement, replacement_display = lock_after_stale or (None, "")
            check(results, "a stale lock PID is not signaled unless it is this session's shim",
                  unrelated.poll() is None,
                  f"hook exit={started.returncode}; unrelated exit={unrelated.poll()}")
            check(results, "a missing shim is restored after a stale lock",
                  started.returncode == 0 and replacement not in (None, unrelated.pid) and
                  replacement_display == DISPLAY and process_is_session_shim(replacement, shim, env),
                  f"hook exit={started.returncode}; lock={(replacement, replacement_display)}")

            shell_env = env.copy()
            shell_env.update({
                "EXPECTED_SHIM_PID": str(replacement),
                "SHELL_RESTART_LOG": str(restart_log),
                "SHELL_RESTART_PATH": str(restart_path),
                "SHELL_RESTART_COMMAND": str(selected_command),
            })
            shell = subprocess.run([str(REPO / "omarchy/reload.d/40-omarchy-shell")],
                                   env=shell_env, capture_output=True, text=True, timeout=10)
            actual_path = restart_path.read_text().strip().split(":") if restart_path.exists() else []
            actual_command = selected_command.read_text().strip() if selected_command.exists() else ""
            observed_pid = restart_log.read_text().strip() if restart_log.exists() else ""
            check(results, "adapter invokes the shell restart helper after shim replacement",
                  shell.returncode == 0 and observed_pid == str(replacement),
                  f"hook exit={shell.returncode}; stderr={shell.stderr.strip()}")
            check(results, "shell restart resolves the adapter command from libexec first",
                  bool(actual_path) and actual_path[0] == str(libexec) and
                  actual_command == str(adapter_command),
                  f"PATH={actual_path}; selected={actual_command}")
        finally:
            if old.poll() is None:
                old.terminate()
                old.wait(timeout=5)
            if replacement:
                stop_owned(replacement, shim, env)
            if unrelated and unrelated.poll() is None:
                unrelated.terminate()
                unrelated.wait(timeout=5)

    failed = [name for name, passed in results if not passed]
    print(f"RESULT {len(results) - len(failed)}/{len(results)} passed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
