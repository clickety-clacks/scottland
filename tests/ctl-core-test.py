#!/usr/bin/env python3
"""Exercise the Rust session-control binary in an isolated, real Wayfire session.

The login-manager fixture starts a real ext-session-lock surface in the headless session. It proves
that lock requests reach the selected session without locking the test machine.
"""
import json
import os
import shutil
import shlex
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RUN_ID = uuid.uuid4().hex
OWNER_ID = str(uuid.UUID(RUN_ID))
ROOT = REPO / "build" / f"ctl-core-fixture-{RUN_ID}"
SCRATCH = ROOT / "headless-scratch"
HEADLESS = SCRATCH / "session"
HOME = ROOT / "home"
BIN = ROOT / "bin"
CALLS = ROOT / "calls.log"
LOCK_PIDS = ROOT / "lock-pids"
LOCK_QML = REPO / "tests/session-lock-fixture.qml"
HOOKS = REPO / "build/hooks"
FIXTURE_HOOKS = ROOT / "hooks"
CTL = REPO / "build/hooks/libexec/scottland-ctl"
PASSED = []
FAILED = []
BLOCKED = []
INCOMPLETE = []


class FixtureUnavailable(RuntimeError):
    """The headless fixture could not provide the session needed to test ctl behavior."""


def check(name, ok, detail=""):
    (PASSED if ok else FAILED).append(name)
    print(("PASS" if ok else "FAIL") + "  " + name + ("" if ok else f": {detail}"), flush=True)
    return ok


def json_value(output):
    try:
        return json.loads(output)
    except (json.JSONDecodeError, TypeError):
        return None


def number_value(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def listed_posix_login_shell():
    try:
        shells = Path("/etc/shells").read_text().splitlines()
    except OSError:
        return None
    for entry in shells:
        candidate = Path(entry.strip())
        if candidate.name in {"sh", "bash"} and candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


class Session:
    def __init__(self):
        ROOT.mkdir(mode=0o700, parents=True)
        HOME.mkdir(parents=True)
        BIN.mkdir(parents=True)
        SCRATCH.mkdir(mode=0o700)
        FIXTURE_HOOKS.mkdir(mode=0o700)
        # This suite exercises core ctl behavior in a plain headless session. Keep
        # adapter-only session environment hooks out of it; those belong to --omarchy
        # fixtures and can activate unrelated host tooling such as mise.
        for source in HOOKS.iterdir():
            destination = FIXTURE_HOOKS / source.name
            if source.name == "session-env.d":
                destination.symlink_to(REPO / "core" / "session-env.d", target_is_directory=True)
            else:
                destination.symlink_to(source, target_is_directory=source.is_dir())
        tmp = ROOT / "tmp"
        tmp.mkdir(mode=0o700)
        self.env = {
            **os.environ,
            "HOME": str(HOME),
            "SCOTTLAND_TEST_HOOKS": str(FIXTURE_HOOKS),
            "SCOTTLAND_HEADLESS_DIR": str(HEADLESS),
            "SCOTTLAND_HEADLESS_ISOLATION": "1",
            "SCOTTLAND_HEADLESS_OWNER": OWNER_ID,
            "SCOTTLAND_HEADLESS_OWNER_DIR": str(HEADLESS),
            "SCOTTLAND_SESSION_DIR": str(HEADLESS / "session"),
            "SCOTTLAND_TEST_SCRATCH": str(SCRATCH),
            "SCOTTLAND_TEST_PATH": str(BIN),
            "TMPDIR": str(tmp),
        }
        self.display = None
        self.pids = []
        calls = shlex.quote(str(CALLS))
        lock_pids = shlex.quote(str(LOCK_PIDS))
        qml = shlex.quote(str(LOCK_QML))
        loginctl = BIN / "loginctl"
        loginctl.write_text(
            "#!/bin/sh\n"
            f"printf 'loginctl\\t%s\\n' \"$*\" >> {calls}\n"
            f"qs -p {qml} >/dev/null 2>&1 </dev/null &\n"
            f"printf '%s\\n' \"$!\" >> {lock_pids}\n"
        )
        loginctl.chmod(0o755)
        inhibitor = BIN / "systemd-inhibit"
        inhibitor.write_text(
            "#!/bin/sh\n"
            f"printf 'systemd-inhibit\\t%s\\n' \"$*\" >> {calls}\n"
            "while [ \"$#\" -gt 0 ]; do\n"
            "  case \"$1\" in --*) shift ;; *) break ;; esac\n"
            "done\n"
            "[ \"$#\" -gt 0 ] || exit 2\n"
            "exec \"$@\"\n"
        )
        inhibitor.chmod(0o755)
        monitor = BIN / "dbus-monitor"
        monitor.write_text(
            "#!/bin/sh\n"
            f"printf 'dbus-monitor\\t%s\\n' \"$*\" >> {calls}\n"
            "printf 'signal member=PrepareForSleep\\n   boolean false\\n"
            "signal member=PrepareForSleep\\n   boolean true\\n'\n"
        )
        monitor.chmod(0o755)
        busctl = BIN / "busctl"
        busctl.write_text(
            "#!/bin/sh\n"
            f"printf 'busctl\\t%s\\n' \"$*\" >> {calls}\n"
            "printf 'uint64 5000000\\n'\n"
        )
        busctl.chmod(0o755)
        CALLS.touch()

    def harness(self, *args, input=None, timeout=60):
        return subprocess.run(
            [str(REPO / "tests/headless.sh"), *args],
            env=self.env,
            text=True,
            capture_output=True,
            input=input,
            timeout=timeout,
        )

    def start(self):
        result = self.harness("start", timeout=120)
        if result.returncode:
            detail = f"{result.stdout}\n{result.stderr}"
            log = HEADLESS / "wayfire.log"
            if log.is_file():
                detail += f"\n--- headless compositor startup log ---\n{log.read_text(errors='replace')}"
            raise RuntimeError(f"headless start failed: {detail}")
        self.display = (HEADLESS / "display").read_text().strip()

    def run(self, *args, input=None, timeout=30):
        return self.harness("run", *map(str, args), input=input, timeout=timeout)

    def ipc(self, method, data=None):
        result = self.harness("ipc", method, json.dumps(data or {}))
        if result.returncode:
            raise RuntimeError(f"Wayfire IPC {method} failed: {result.stdout}\n{result.stderr}")
        return json.loads(result.stdout)

    def spawn(self, command):
        result = self.run("sh", "-c", f"{command} >/dev/null 2>&1 </dev/null & echo $!")
        if result.returncode:
            raise RuntimeError(f"cannot start fixture window: {result.stderr}")
        pid = int(result.stdout.strip().splitlines()[-1])
        self.pids.append(pid)
        return pid

    def terminate(self, pid):
        owned = [current for current in (pid, *self.descendants(pid)) if self.owns(current)]
        for current in owned:
            try:
                os.kill(current, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and any(self.owns(current) for current in owned):
            time.sleep(0.05)
        for current in owned:
            if self.owns(current):
                try:
                    os.kill(current, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def owns(self, pid):
        try:
            marker = f"XDG_CONFIG_HOME={HEADLESS / 'config'}".encode()
            return marker in Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
        except OSError:
            return False

    def descendants(self, pid):
        children = {}
        for proc in Path("/proc").glob("[0-9]*"):
            try:
                parent = int((proc / "stat").read_text().rsplit(")", 1)[1].split()[1])
            except (OSError, ValueError, IndexError):
                continue
            children.setdefault(parent, []).append(int(proc.name))
        found, queue = [], list(children.get(pid, []))
        while queue:
            child = queue.pop()
            if self.owns(child):
                found.append(child)
                queue.extend(children.get(child, []))
        return found

    def wait(self, predicate, timeout=10, interval=0.05):
        deadline = time.monotonic() + timeout
        value = predicate()
        while not value and time.monotonic() < deadline:
            time.sleep(interval)
            value = predicate()
        return bool(value), value

    def tap(self, key):
        self.ipc("stipc/feed_key", {"key": key, "state": True})
        time.sleep(0.05)  # pace the key press; readiness is checked separately
        self.ipc("stipc/feed_key", {"key": key, "state": False})

    def state(self):
        return self.ipc("scottland/layout-state").get("views", [])

    def locked(self):
        return self.run(CTL, "session-locked").returncode

    def calls(self):
        return [tuple(line.split("\t", 1)) for line in CALLS.read_text().splitlines() if line]

    def screenshot(self, name):
        path = ROOT / f"{name}.ppm"
        path.unlink(missing_ok=True)
        result = self.run("timeout", "5", "grim", "-t", "ppm", path)
        if result.returncode or not path.exists():
            return None
        data = path.read_bytes()
        fields, index = [], 0
        while len(fields) < 4:
            while data[index:index + 1].isspace():
                index += 1
            if data[index:index + 1] == b"#":
                index = data.index(b"\n", index) + 1
                continue
            end = index
            while not data[end:end + 1].isspace():
                end += 1
            fields.append(data[index:end])
            index = end
        if fields[0] != b"P6":
            raise RuntimeError(f"unexpected screenshot format: {fields[0]!r}")
        return int(fields[1]), int(fields[2]), data[index + 1:]

    def pixel(self, name, point):
        image = self.screenshot(name)
        if image is None:
            return None
        width, _height, rgb = image
        offset = (int(point[1]) * width + int(point[0])) * 3
        return tuple(rgb[offset:offset + 3])

    def stop(self):
        for pid in self.pids:
            self.terminate(pid)
        if LOCK_PIDS.exists():
            for pid in LOCK_PIDS.read_text().splitlines():
                if pid.isdigit():
                    self.terminate(int(pid))
        stopped = False
        try:
            result = self.harness("stop", timeout=90)
            stopped = result.returncode == 0
            if not stopped:
                print(f"headless stop failed: {result.stdout}\n{result.stderr}", file=sys.stderr)
        except Exception as error:
            print(f"headless stop failed: {error}", file=sys.stderr)
        if not stopped:
            BLOCKED.append("headless session cleanup")
        for pid in self.pids:
            self.terminate(pid)
        if stopped and ROOT.exists():
            shutil.rmtree(ROOT)


def focus_state(session):
    return session.ipc("window-rules/get-focused-view").get("info", {}).get("id")


def run_checks(session):
    initial_lock = session.locked()
    check("session starts unlocked", initial_lock == 1, initial_lock)

    first = session.spawn(
        f"python3 {shlex.quote(str(REPO / 'tests/solid-color-app.py'))} ctl-first '#3366CC'"
    )
    target = session.spawn(
        f"python3 {shlex.quote(str(REPO / 'tests/solid-color-app.py'))} ctl-target '#CC6633'"
    )
    ok, views = session.wait(
        lambda: (lambda items: items if len(items) == 2 else None)(session.state()), timeout=20
    )
    if not ok:
        raise FixtureUnavailable(f"two fixture windows did not map: {views}")

    ids = {
        view["app_id"].rsplit(".", 1)[-1]: view["id"]
        for view in views
        if isinstance(view.get("app_id"), str) and "id" in view
    }
    if not {"ctl-first", "ctl-target"}.issubset(ids):
        raise FixtureUnavailable(f"fixture windows did not expose expected app ids: {views}")
    focus = session.run(CTL, "focus-app", "ctl-target")
    focused = session.wait(lambda: focus_state(session) == ids["ctl-target"])[0]
    check("focus-app selects the matching app id", focus.returncode == 0 and focused,
          (focus.returncode, ids["ctl-target"]))

    cwd = session.run(CTL, "terminal-cwd")
    check("non-terminal focus falls back to session HOME",
          cwd.returncode == 0 and cwd.stdout.strip() == str(HOME), cwd.stdout.strip())
    terminal_workdir = ROOT / "terminal-workdir"
    terminal_workdir.mkdir()
    terminal_ready = ROOT / "terminal-ready"
    shell = listed_posix_login_shell()
    if shell is None:
        raise FixtureUnavailable("no executable POSIX login shell is listed in /etc/shells")
    terminal_parent = ROOT / "terminal-parent.py"
    terminal_parent.write_text(
        "#!/usr/bin/python3\n"
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.argv[2], '-c', 'cd \"$1\"; : > \"$2\"; while :; do sleep 60; done', '_', sys.argv[1], sys.argv[3]])\n"
        "time.sleep(60)\n"
    )
    terminal_parent.chmod(0o755)
    terminal_pid = session.spawn(
        f"{shlex.quote(str(terminal_parent))} {shlex.quote(str(terminal_workdir))} "
        f"{shlex.quote(str(shell))} {shlex.quote(str(terminal_ready))}"
    )
    terminal_is_ready, _ = session.wait(lambda: terminal_ready.is_file(), timeout=5)
    if not terminal_is_ready:
        raise FixtureUnavailable("the terminal shell did not enter its working directory")
    cwd = session.run(CTL, "terminal-cwd", str(terminal_pid))
    check("terminal-cwd reads a listed login shell's live working directory",
          cwd.returncode == 0 and cwd.stdout.strip() == str(terminal_workdir), cwd.stdout.strip())

    listing = session.run(CTL, "windows")
    check("windows lists both live views", listing.returncode == 0
          and "ctl-first" in listing.stdout and "ctl-target" in listing.stdout, listing.stdout)
    present = session.run(CTL, "present", str(ids["ctl-first"]))
    present_value = json_value(present.stdout)
    check("present keeps the existing Scottland presentation behavior",
          present.returncode == 0
          and isinstance(present_value, dict)
          and present_value.get("presented") in {"window", "moved"}, present.stdout)

    get = session.run(CTL, "get")
    values = json_value(get.stdout) if get.returncode == 0 else None
    check("get returns live values and the unsupported list",
          isinstance(values, dict) and "cycle_overshoot" in values
          and isinstance(values.get("unsupported"), list), get.stdout)
    expected_outputs = session.ipc("scottland/layout-state").get("outputs", [])
    check("get includes the current layout outputs",
          isinstance(values, dict) and values.get("outputs") == expected_outputs,
          values.get("outputs") if isinstance(values, dict) else None)

    original_zones = session.ipc(
        "wayfire/get-config-option", {"option": "scottland/screen_zones"}
    ).get("value", "[]")
    if not isinstance(original_zones, str):
        original_zones = json.dumps(original_zones)
    test_zones = [{
        "make": "Ctl test",
        "model": "Screen zones",
        "serial": "ctl-core-fixture",
        "center_width": 44.0,
        "rail_width": 3.0,
        "blend_width": 60.0,
        "min_scale": 0.25,
        "max_scale": 1.0,
        "scale_curve": "0:1 0.5:0.65 1:0.25",
    }]
    encoded_zones = json.dumps(test_zones, separators=(",", ":"))
    set_zones = session.run(CTL, "set", "screen_zones", encoded_zones)
    zones_get = session.run(CTL, "get")
    zones_values = json_value(zones_get.stdout) if zones_get.returncode == 0 else None
    check("get parses screen_zones as a JSON array",
          set_zones.returncode == 0 and isinstance(zones_values, dict)
          and zones_values.get("screen_zones") == test_zones,
          zones_get.stdout)
    malformed_zones = session.run(CTL, "set", "screen_zones", "not-json")
    malformed_get = session.run(CTL, "get")
    malformed_values = json_value(malformed_get.stdout) if malformed_get.returncode == 0 else None
    check("get falls back to an empty screen_zones array for malformed JSON",
          malformed_zones.returncode == 0 and isinstance(malformed_values, dict)
          and malformed_values.get("screen_zones") == [],
          malformed_get.stdout)
    session.run(CTL, "set", "screen_zones", str(original_zones))

    original_speed = session.ipc(
        "wayfire/get-config-option", {"option": "input/touchpad_scroll_speed"}
    ).get("value")
    session.ipc("wayfire/set-config-options", {"input/touchpad_scroll_speed": 0.4})
    speed_get = session.run(CTL, "get")
    speed_values = json_value(speed_get.stdout) if speed_get.returncode == 0 else None
    observed_speed = number_value(
        speed_values.get("touchpad_scroll_speed") if isinstance(speed_values, dict) else None
    )
    check("get includes the live touchpad scroll speed",
          observed_speed is not None and abs(observed_speed - 0.4) < 0.001,
          (speed_get.returncode,
           speed_values.get("touchpad_scroll_speed") if isinstance(speed_values, dict) else None))
    if original_speed is not None:
        session.ipc(
            "wayfire/set-config-options", {"input/touchpad_scroll_speed": original_speed}
        )
    set_value = session.run(CTL, "set", "cycle_overshoot", "4")
    option = session.run(CTL, "option", "scottland/cycle_overshoot")
    observed = json_value(option.stdout) if option.returncode == 0 else None
    check("set and option preserve the live settings contract",
          set_value.returncode == 0 and number_value(observed) == 4,
          (set_value.returncode, option.stdout))
    stream = session.run(
        CTL, "stdin", input='cycle_overshoot 3\nflush\nbatch {"cycle_overshoot":"5"}\n'
    )
    option = session.run(CTL, "option", "scottland/cycle_overshoot")
    observed = json_value(option.stdout) if option.returncode == 0 else None
    check("stdin applies line and batch updates and flushes", stream.returncode == 0
          and "flushed" in stream.stdout and number_value(observed) == 5,
          (stream.returncode, stream.stdout, option.stdout))

    # Give only the isolated session a synthetic logind id. The fixture command starts the real
    # session-lock surface; it never calls the host login manager.
    env_file = HEADLESS / "session" / f"{session.display}.env"
    env_file.write_bytes(env_file.read_bytes() + b"XDG_SESSION_ID=ctl-test\0")
    first_window = next(view for view in session.ipc("window-rules/list-views")
                        if view["id"] == ids["ctl-first"])
    geometry = first_window["geometry"]
    center = (geometry["x"] + geometry["width"] / 2,
              geometry["y"] + geometry["height"] / 2)
    locked_pixel = lambda name: session.pixel(name, center) == (255, 0, 255)

    lock = session.run(CTL, "lock")
    lock_shown, _ = session.wait(lambda: locked_pixel("lock"), timeout=15, interval=0.3)
    locker_called = ("loginctl", "lock-session ctl-test") in session.calls()
    check("lock asks the selected session's locker and locks it",
          lock.returncode == 0 and locker_called and lock_shown and session.locked() == 0,
          session.calls())
    session.tap("KEY_ENTER")
    unlocked, _ = session.wait(
        lambda: session.pixel("unlock", center) == (51, 102, 204), timeout=15, interval=0.3
    )
    check("session-lock status returns to unlocked", unlocked and session.locked() == 1,
          session.locked())

    lid = session.run(CTL, "lid-close")
    lid_shown, _ = session.wait(lambda: locked_pixel("lid-close"), timeout=15, interval=0.3)
    check("lid-close uses the same session locker", lid.returncode == 0 and lid_shown
          and session.locked() == 0, session.calls())
    session.tap("KEY_ENTER")
    session.wait(lambda: session.locked() == 1, timeout=15)

    sleep_lock = session.run(CTL, "sleep-lock", "4000", timeout=10)
    secure, _ = session.wait(lambda: locked_pixel("sleep-lock"), timeout=15, interval=0.3)
    check("sleep-lock waits for the real lock surface",
          sleep_lock.returncode == 0 and secure and session.locked() == 0,
          (sleep_lock.returncode, sleep_lock.stderr))
    session.tap("KEY_ENTER")
    session.wait(lambda: session.locked() == 1, timeout=15)

    monitor = session.run(CTL, "sleep-monitor", timeout=15)
    secure, _ = session.wait(lambda: locked_pixel("sleep-monitor"), timeout=15, interval=0.3)
    calls = session.calls()
    inhibitor_args = next((args for name, args in calls if name == "systemd-inhibit"), "")
    monitor_args = next((args for name, args in calls if name == "dbus-monitor"), "")
    locker_called = ("loginctl", "lock-session ctl-test") in calls
    check("sleep-monitor runs under a delay inhibitor and locks on PrepareForSleep true",
          monitor.returncode == 0 and secure and session.locked() == 0 and locker_called
          and "--what=sleep" in inhibitor_args and "--mode=delay" in inhibitor_args
          and "sleep-monitor --inhibited" in inhibitor_args
          and "PrepareForSleep" in monitor_args,
          (monitor.returncode, monitor.stderr, calls))
    session.tap("KEY_ENTER")
    session.wait(lambda: session.locked() == 1, timeout=15)

    close_all = session.run(CTL, "window-close-all")
    closed, remaining = session.wait(lambda: not session.state())
    check("window-close-all closes every Scottland window", close_all.returncode == 0 and closed,
          (close_all.returncode, remaining))


def main():
    session = None
    try:
        session = Session()
        session.start()
    except Exception as error:
        print(f"BLOCKED  headless fixture startup: {error}", file=sys.stderr)
        BLOCKED.append("headless fixture startup")
    else:
        try:
            run_checks(session)
        except FixtureUnavailable as error:
            print(f"BLOCKED  headless fixture setup: {error}", file=sys.stderr)
            BLOCKED.append("headless fixture setup")
        except Exception as error:
            print(f"INCOMPLETE  ctl product suite stopped before a verdict: {error}", file=sys.stderr)
            INCOMPLETE.append("ctl product suite interrupted")
    finally:
        if session is not None:
            try:
                session.stop()
            except Exception as error:
                print(f"BLOCKED  headless session cleanup: {error}", file=sys.stderr)
                BLOCKED.append("headless session cleanup")
    print(
        f"\nProduct assertions: {len(PASSED)} passed, {len(FAILED)} failed; "
        f"operational blocks: {len(BLOCKED)}; incomplete runs: {len(INCOMPLETE)}",
        flush=True,
    )
    if FAILED and (BLOCKED or INCOMPLETE):
        return 3
    if FAILED:
        return 1
    if BLOCKED or INCOMPLETE:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
