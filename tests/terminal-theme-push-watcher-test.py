#!/usr/bin/env python3
"""Exercise the existing watcher with real inotifywait and a disposable signal target.

Run only in an isolated Linux workspace. This test creates temporary config/runtime trees
and a child process named ghostty; it never connects to a desktop or session.
"""
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import time


checks = 0


def check(name, okay):
    global checks
    if not okay:
        raise AssertionError(name)
    checks += 1
    print("PASS", name)


def wait_until(description, predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {description}")


def wait_for_line(stream, description, timeout=5):
    ready, _, _ = select.select([stream], [], [], timeout)
    if not ready:
        raise AssertionError(f"timed out waiting for {description}")
    return stream.readline().rstrip("\n")


def child_watches_path(pid, watched_path):
    children = Path(f"/proc/{pid}/task/{pid}/children")
    try:
        child_pids = children.read_text().split()
    except OSError:
        return False
    expected_inode = watched_path.stat().st_ino
    for child_pid in child_pids:
        try:
            argv = Path(f"/proc/{child_pid}/cmdline").read_bytes().split(b"\0")
            descriptors = Path(f"/proc/{child_pid}/fd").iterdir()
        except OSError:
            continue
        if not (argv and b"inotifywait" in os.path.basename(argv[0])
                and os.fsencode(str(watched_path)) in argv):
            continue
        for descriptor in descriptors:
            try:
                if os.readlink(descriptor) != "anon_inode:inotify":
                    continue
                details = Path(f"/proc/{child_pid}/fdinfo/{descriptor.name}").read_text().splitlines()
            except OSError:
                continue
            for line in details:
                if not line.startswith("inotify wd:"):
                    continue
                fields = dict(part.split(":", 1) for part in line.split()[2:])
                try:
                    if int(fields["ino"], 16) == expected_inode:
                        return True
                except (KeyError, ValueError):
                    continue
    return False


def signal_count(path):
    try:
        return len(path.read_text().splitlines())
    except FileNotFoundError:
        return 0


def stop_process_group(process):
    if process is None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    if process.poll() is not None:
        return
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=3)


print(f"Python: {sys.version.split()[0]}")
inotify = subprocess.run(["inotifywait", "--version"], capture_output=True, text=True, check=True)
inotify_version = (inotify.stdout + inotify.stderr).strip().splitlines()
print("inotifywait:", inotify_version[0] if inotify_version else "version output unavailable")

root = Path(__file__).resolve().parents[1]
watcher_source = root / "core/libexec/scottland-color-scheme"
worker_code = r"""
from importlib.machinery import SourceFileLoader
import os
import pathlib

core = SourceFileLoader("scottland_color_scheme", os.environ["SCOTTLAND_WATCHER_SOURCE"]).load_module()
core.current_accent = lambda: None
core.accent_watch_paths = lambda: []
real_sleep = core.time.sleep
def mark_no_watch_path(seconds):
    pathlib.Path(os.environ["SCOTTLAND_WATCH_SLEEP_MARKER"]).touch()
    real_sleep(seconds)
core.time.sleep = mark_no_watch_path
core.watch_accent()
"""
ghostty_code = r"""
import ctypes
import os
import signal

libc = ctypes.CDLL(None, use_errno=True)
if libc.prctl(15, ctypes.c_char_p(b"ghostty"), 0, 0, 0) != 0:
    raise OSError(ctypes.get_errno(), "prctl(PR_SET_NAME) failed")
def reloaded(_signum, _frame):
    with open(os.environ["SCOTTLAND_SIGNAL_LOG"], "a", encoding="utf-8") as log:
        log.write("SIGUSR2\n")
signal.signal(signal.SIGUSR2, reloaded)
print("ready", flush=True)
while True:
    signal.pause()
"""

with tempfile.TemporaryDirectory(prefix="scottland-terminal-theme-watcher-") as temporary:
    base = Path(temporary)
    config_home = base / "config"
    location = config_home / "gooarchy" / "current-theme"
    runtime_dir = base / "runtime"
    runtime_dir.mkdir()
    signal_log = base / "ghostty-signals.log"
    sleep_marker = base / "watcher-no-path-sleep"
    display = f"wayland-theme-push-test-{os.getpid()}"
    env = os.environ.copy()
    env.update({
        "XDG_CONFIG_HOME": str(config_home),
        "XDG_RUNTIME_DIR": str(runtime_dir),
        "WAYLAND_DISPLAY": display,
        "SCOTTLAND_HOOKS": str(base / "empty-hooks"),
        "SCOTTLAND_SIGNAL_LOG": str(signal_log),
        "SCOTTLAND_WATCHER_SOURCE": str(watcher_source),
        "SCOTTLAND_WATCH_SLEEP_MARKER": str(sleep_marker),
    })

    ghostty = watcher = None
    try:
        ghostty = subprocess.Popen([sys.executable, "-c", ghostty_code], env=env, text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        check("signal target starts with isolated session identity", wait_for_line(ghostty.stdout, "signal target readiness") == "ready")

        # Start with the whole Gooarchy path absent so the watcher must recover by polling
        # its exact configured location, rather than by observing a parent-directory event.
        watcher = subprocess.Popen([sys.executable, "-c", worker_code], env=env, text=True,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True)
        wait_until("watcher reaches its no-path wait", sleep_marker.exists)
        check("absent current-theme location sends no reload", signal_count(signal_log) == 0)

        location.parent.mkdir(parents=True)
        stage = location.parent / "current-theme.stage"
        stage.mkdir()
        light = base / "light.conf"
        dark = base / "dark.conf"
        light.write_text("theme = light\n", encoding="utf-8")
        dark.write_text("theme = dark\n", encoding="utf-8")
        os.symlink(light, stage / "light")
        os.symlink(dark, stage / "dark")
        os.replace(stage, location)

        wait_until("late location reload signal", lambda: signal_count(signal_log) >= 1)
        check("late current-theme directory appearance reaches the existing watcher once",
              signal_count(signal_log) == 1)
        wait_until("real inotifywait registers a kernel watch on current-theme",
                   lambda: child_watches_path(watcher.pid, location))

        replacement = base / "light-replacement.conf"
        replacement.write_text("theme = light-v2\n", encoding="utf-8")
        staged_link = location / "light.next"
        os.symlink(replacement, staged_link)
        original_links = (os.readlink(location / "light"), os.readlink(location / "dark"))
        os.replace(staged_link, location / "light")
        wait_until("atomic light-link replacement reload signal", lambda: signal_count(signal_log) >= 2)
        check("real inotify link replacement reloads the same-session Ghostty target once",
              signal_count(signal_log) == 2)
        check("watcher observes the replacement while preserving both theme links",
              os.readlink(location / "light") == str(replacement)
              and os.readlink(location / "dark") == original_links[1])
        check("watcher leaves theme target files unchanged",
              light.read_text(encoding="utf-8") == "theme = light\n"
              and dark.read_text(encoding="utf-8") == "theme = dark\n"
              and replacement.read_text(encoding="utf-8") == "theme = light-v2\n")
    finally:
        stop_process_group(watcher)
        if ghostty is not None and ghostty.poll() is None:
            ghostty.terminate()
            try:
                ghostty.wait(timeout=3)
            except subprocess.TimeoutExpired:
                ghostty.kill()
                ghostty.wait(timeout=3)

print(f"{checks} watcher behavior checks passed")
