"""Isolated Omarchy-adapter test sessions: a fixture HOME and stand-in commands.

The fixture HOME holds a small ~/.config/hypr/hyprland.lua that loads the installed Omarchy
helpers and whichever installed, unchanged Omarchy config files a test names (for example
default/hypr/bindings/voxtype.lua), plus any extra Lua. Commands those files run (voxtype,
omarchy-system-lid-close, ...) are replaced by recorders on SCOTTLAND_TEST_PATH that append their
name and arguments to a log, so a test observes what actually ran without touching the machine's
desktop, microphone, lock screen or displays.

A Session wraps tests/headless.sh (start --omarchy) with that HOME, its own
SCOTTLAND_HEADLESS_DIR under this checkout's build/, and cleanup of exactly what it created.
"""
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OMARCHY = Path(os.environ.get("OMARCHY_PATH", "/usr/share/omarchy"))

RECORDER = """#!/bin/sh
# Test stand-in: record the call instead of doing it.
printf '%s\\t%s\\n' "$(basename "$0")" "$*" >>'{log}'
{extra}
"""


class Fixture:
    def __init__(self, root, modules=(), lua="", recorders=(), commands=None):
        """modules: installed Omarchy Lua modules to load unchanged, e.g. "default.hypr.bindings.voxtype".
        lua: extra config appended after them. recorders: command names to replace with recorders.
        commands: {name: shell body} for stand-ins that must do more than record."""
        self.root = Path(root).resolve()
        if self.root.exists():
            shutil.rmtree(self.root)
        self.home = self.root / "home"
        self.bin = self.root / "bin"
        self.log = self.root / "calls.log"
        hypr = self.home / ".config/hypr"
        hypr.mkdir(parents=True)
        self.bin.mkdir(parents=True)
        self.log.touch()
        requires = "\n".join(f'require("{m}")' for m in modules)
        (hypr / "hyprland.lua").write_text(f"""-- Test fixture: installed Omarchy files, unchanged.
local root = "{OMARCHY}"
package.path = root .. "/?.lua;" .. root .. "/?/init.lua;" .. package.path
require("default.hypr.helpers")
{requires}
{lua}
""")
        for name in recorders:
            self.command(name)
        for name, body in (commands or {}).items():
            self.command(name, body)

    def command(self, name, extra=""):
        path = self.bin / name
        path.write_text(RECORDER.format(log=self.log, extra=extra))
        path.chmod(0o755)
        return path

    def calls(self):
        return [tuple((line.split("\t", 1) + [""])[:2])
                for line in self.log.read_text().splitlines() if line]

    def wait_calls(self, predicate, timeout=10.0):
        """Poll for the call log to satisfy predicate(calls); returns (ok, last calls)."""
        deadline = time.monotonic() + timeout
        calls = self.calls()
        while time.monotonic() < deadline:
            calls = self.calls()
            if predicate(calls):
                return True, calls
            time.sleep(0.05)
        return predicate(calls), calls


class Session:
    def __init__(self, fixture, name, extra_args=(), env=None):
        self.fixture = fixture
        self.dir = REPO / "build" / name
        self.env = {**os.environ, "HOME": str(fixture.home),
                    "SCOTTLAND_HEADLESS_DIR": str(self.dir),
                    "SCOTTLAND_TEST_PATH": str(fixture.bin),
                    "TMPDIR": str(REPO / "build" / "tmp"), **(env or {})}
        (REPO / "build" / "tmp").mkdir(parents=True, exist_ok=True)
        self.extra_args = list(extra_args)
        self.started = False

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()

    def harness(self, *args, check=True, timeout=60, input=None):
        return subprocess.run([str(REPO / "tests/headless.sh"), *args], env=self.env, text=True,
                              capture_output=True, check=check, timeout=timeout, input=input)

    def start(self):
        self.harness("stop", check=False)
        result = self.harness("start", "--omarchy", *self.extra_args, timeout=120)
        self.started = True
        self.display = (self.dir / "display").read_text().strip()
        return result

    def stop(self):
        if self.started:
            self.harness("stop", check=False)
            self.started = False

    def run(self, *command, check=False, timeout=30, input=None):
        return self.harness("run", *command, check=check, timeout=timeout, input=input)

    def ipc(self, method, data=None):
        reply = self.harness("ipc", method, json.dumps(data or {}))
        text = reply.stdout.strip()
        return json.loads(text) if text.startswith(("{", "[")) else text

    def key(self, name, pressed):
        return self.ipc("stipc/feed_key", {"key": name, "state": pressed})

    def tap(self, name, hold=0.05):
        self.key(name, True)
        time.sleep(hold)  # paces the gesture; not a readiness wait
        self.key(name, False)

    def hyprctl(self, *args, timeout=15):
        return self.run("hyprctl", *args, timeout=timeout)

    def config(self):
        return (self.dir / "wayfire.ini").read_text()

    def wait(self, predicate, timeout=10.0, interval=0.05):
        """Poll predicate() until truthy; returns (ok, last value)."""
        deadline = time.monotonic() + timeout
        value = predicate()
        while not value and time.monotonic() < deadline:
            time.sleep(interval)
            value = predicate()
        return bool(value), value

    def wait_shim(self, timeout=10.0):
        return self.wait(lambda: self.hyprctl("-j", "monitors").returncode == 0, timeout)

    def wait_lua_host(self, timeout=10.0):
        runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
        fifo = runtime / "scottland" / f"{self.display}.lua.fifo"
        return self.wait(fifo.exists, timeout)


class Checks:
    """Named scenario checks, reported honestly: one line per check, failures with detail."""

    def __init__(self):
        self.passed, self.failed = [], []

    def __call__(self, name, ok, detail=""):
        (self.passed if ok else self.failed).append(name)
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f": {detail}"), flush=True)
        return ok

    def summary(self):
        print(f"\n{len(self.passed)} passed, {len(self.failed)} failed", flush=True)
        return 0 if not self.failed else 1


def read_ppm(path):
    """(width, height, rgb bytes) of a binary PPM, such as `grim -t ppm` writes."""
    data = Path(path).read_bytes()
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
    assert fields[0] == b"P6", fields
    width, height = int(fields[1]), int(fields[2])
    return width, height, data[index + 1:]


def pixel(image, x, y):
    width, _height, rgb = image
    offset = (y * width + x) * 3
    return tuple(rgb[offset:offset + 3])


def screenshot(session, name):
    """Capture the session's screen with grim (screencopy, as any recorder would)."""
    path = session.dir.parent / f"{session.dir.name}-{name}.ppm"
    path.unlink(missing_ok=True)
    # Bounded: on an output that is powered off, screencopy may wait for a frame that never comes.
    result = session.run("timeout", "5", "grim", "-t", "ppm", str(path))
    return read_ppm(path) if result.returncode == 0 and path.exists() else None
