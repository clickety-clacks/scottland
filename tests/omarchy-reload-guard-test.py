#!/usr/bin/env python3
"""Omarchy's package reload guard pauses only file-triggered Scottland config rebuilds."""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time


repo = Path(__file__).resolve().parents[1]
shim = repo / "omarchy/shim/scottland-hyprshim"
watcher = repo / "core/autostart.d/06-watch-config"


def wait_for(watcher_process, predicate, description, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        if watcher_process.poll() is not None:
            raise AssertionError(f"config watcher exited {watcher_process.returncode} before {description}")
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {description}")


def main():
    with tempfile.TemporaryDirectory(prefix="scottland-reload-guard-") as temp_name:
        temp = Path(temp_name)
        fake_bin = temp / "bin"
        hooks = temp / "hooks"
        session_dir = temp / "session"
        state_dir = temp / "state"
        runtime = temp / "runtime"
        for path in (fake_bin, hooks / "libexec", session_dir, runtime, state_dir):
            path.mkdir(parents=True, exist_ok=True)

        watch_path = temp / "watched-config"
        watch_path.touch()
        build_log = temp / "builds.log"
        ready_log = temp / "watcher-ready.log"
        event_file = temp / "next-event"
        watch_log = state_dir / "scottland/watch-config.log"
        build = hooks / "libexec/scottland-build-config"
        build.write_text(
            "#!/bin/sh\n"
            "if [ \"${1:-}\" = --watch-paths ]; then printf '%s\\n' \"$WATCH_PATH\"; exit 0; fi\n"
            "printf 'build\\n' >>\"$BUILD_LOG\"\n"
        )
        build.chmod(0o755)
        (hooks / "libexec/scottland_hyprland_monitor.py").symlink_to(
            repo / "omarchy/libexec/scottland_hyprland_monitor.py")
        inotify = fake_bin / "inotifywait"
        inotify.write_text(
            "#!/bin/sh\n"
            "printf 'ready\\n' >>\"$READY_LOG\"\n"
            "while [ ! -e \"$EVENT_FILE\" ]; do sleep 0.01; done\n"
            "rm -f \"$EVENT_FILE\"\n"
        )
        inotify.chmod(0o755)

        env_names = (
            "PATH", "SCOTTLAND_HOOKS", "SCOTTLAND_SESSION_DIR", "XDG_RUNTIME_DIR",
            "XDG_STATE_HOME", "HOME", "HYPRLAND_INSTANCE_SIGNATURE", "WAYLAND_DISPLAY",
            "WATCH_PATH", "BUILD_LOG", "READY_LOG", "EVENT_FILE",
        )
        old_env = {name: os.environ.get(name) for name in env_names}
        old_dont_write_bytecode = sys.dont_write_bytecode
        os.environ.update({
            "PATH": f"{fake_bin}:{os.environ.get('PATH', '/usr/bin:/bin')}",
            "SCOTTLAND_HOOKS": str(hooks),
            "SCOTTLAND_SESSION_DIR": str(session_dir),
            "XDG_RUNTIME_DIR": str(runtime),
            "XDG_STATE_HOME": str(state_dir),
            "HOME": str(temp / "home"),
            "HYPRLAND_INSTANCE_SIGNATURE": "scottland_test",
            "WAYLAND_DISPLAY": "wayland-test",
            "WATCH_PATH": str(watch_path),
            "BUILD_LOG": str(build_log),
            "READY_LOG": str(ready_log),
            "EVENT_FILE": str(event_file),
        })
        sys.dont_write_bytecode = True
        module = None
        watch_process = None
        real_run = subprocess.run
        try:
            loader = importlib.machinery.SourceFileLoader("scottland_reload_guard_test", str(shim))
            spec = importlib.util.spec_from_loader("scottland_reload_guard_test", loader)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            translator = module.Translator(None)

            def option(name):
                return json.loads(translator.handle(f"j/getoption {name}"))["bool"]

            assert not option("misc.disable_autoreload")
            assert not option("debug.suppress_errors")
            unsupported = translator.handle("eval hl.config({ cursor = { zoom_factor = 2 } })")
            assert unsupported.startswith("error: unsupported in Scottland: eval "), unsupported

            watch_process = subprocess.Popen([str(watcher)], env=os.environ.copy(),
                                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                             stderr=subprocess.PIPE, text=True, start_new_session=True)
            wait_for(watch_process, lambda: ready_log.exists() and len(ready_log.read_text().splitlines()) >= 1,
                     "watcher readiness")

            paused = translator.handle(
                "eval hl.config({ misc = { disable_autoreload = true }, "
                "debug = { suppress_errors = true } })")
            assert paused == "ok", paused
            assert option("misc.disable_autoreload") and option("debug.suppress_errors")

            event_file.touch()
            wait_for(watch_process,
                     lambda: watch_log.exists() and "config change consumed while automatic reload is paused" in watch_log.read_text(),
                     "the paused watcher to consume the config change")
            assert not build_log.exists() or not build_log.read_text().strip(), "paused auto-reload rebuilt an intermediate config"

            reload_calls = []

            def record_explicit_reload(args, *call_args, **call_kwargs):
                reload_calls.append(args)
                return subprocess.CompletedProcess(args, 0, "reset complete\n", "")

            subprocess.run = record_explicit_reload
            try:
                assert translator.handle("reload") == "ok", "explicit reload must work while auto-reload is paused"
            finally:
                subprocess.run = real_run
            assert len(reload_calls) == 1 and reload_calls[0][-1] == "reset", reload_calls

            assert translator.handle("eval hl.config({ debug = { suppress_errors = false } })") == "ok"
            assert translator.handle(
                "eval hl.config({ misc = { disable_autoreload = false }, "
                "debug = { suppress_errors = false } })") == "ok"
            assert not option("misc.disable_autoreload") and not option("debug.suppress_errors")

            wait_for(watch_process,
                     lambda: len(ready_log.read_text().splitlines()) >= 2,
                     "the watcher to resume waiting for file changes")
            event_file.touch()
            wait_for(watch_process,
                     lambda: build_log.exists() and len(build_log.read_text().splitlines()) == 1,
                     "automatic config rebuild to resume after guard completion")

            # The guard must restore a pre-existing true value rather than always enabling auto-reload.
            assert translator.handle("eval hl.config({ misc = { disable_autoreload = true } })") == "ok"
            assert option("misc.disable_autoreload")
            assert translator.handle(
                "eval hl.config({ misc = { disable_autoreload = true }, "
                "debug = { suppress_errors = true } })") == "ok"
            assert translator.handle("eval hl.config({ debug = { suppress_errors = false } })") == "ok"
            subprocess.run = record_explicit_reload
            try:
                assert translator.handle("reload") == "ok"
            finally:
                subprocess.run = real_run
            assert translator.handle(
                "eval hl.config({ misc = { disable_autoreload = true }, "
                "debug = { suppress_errors = false } })") == "ok"
            assert option("misc.disable_autoreload") and not option("debug.suppress_errors")

            print("PASS  Omarchy guard options round-trip through the shim")
            print("PASS  unrelated Lua eval remains unsupported")
            print("PASS  file-triggered config rebuild is skipped while auto-reload is paused")
            print("PASS  explicit hyprctl reload still rebuilds while auto-reload is paused")
            print("PASS  file-triggered config rebuild resumes after the guard restores false")
            print("PASS  guard restoration preserves a pre-existing paused state")
            return 0
        except (AssertionError, OSError, ValueError) as error:
            print(f"FAIL  reload guard pause/resume: {error}", file=sys.stderr)
            if ready_log.exists():
                print(f"  watcher waits: {len(ready_log.read_text().splitlines())}", file=sys.stderr)
            if build_log.exists():
                print(f"  automatic rebuilds: {len(build_log.read_text().splitlines())}", file=sys.stderr)
            if watch_log.exists():
                print(f"  watcher log: {watch_log.read_text().splitlines()[-4:]}", file=sys.stderr)
            if watch_process and watch_process.poll() is not None:
                print(f"  watcher exit: {watch_process.returncode}", file=sys.stderr)
                print(f"  watcher stderr: {watch_process.stderr.read()}", file=sys.stderr)
            return 1
        finally:
            subprocess.run = real_run
            if watch_process and watch_process.poll() is None:
                os.killpg(watch_process.pid, signal.SIGTERM)
                try:
                    watch_process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(watch_process.pid, signal.SIGKILL)
                    watch_process.wait(timeout=3)
            if module is not None:
                module.log.file.close()
            sys.dont_write_bytecode = old_dont_write_bytecode
            for name, value in old_env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


if __name__ == "__main__":
    raise SystemExit(main())
