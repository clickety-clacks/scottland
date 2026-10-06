"""Isolated Omarchy-adapter test sessions: a fixture HOME and stand-in commands.

The fixture HOME holds a small ~/.config/hypr/hyprland.lua that loads the installed Omarchy
helpers and whichever installed, unchanged Omarchy config files a test names (for example
default/hypr/bindings/voxtype.lua), plus any extra Lua. Commands those files run (voxtype,
omarchy-system-lid-close, ...) are replaced by recorders on SCOTTLAND_TEST_PATH that append their
name and arguments to a log, so a test observes what actually ran without touching the machine's
desktop, microphone, lock screen or displays.

A Session wraps tests/headless.sh (start --omarchy) with that HOME, its own
SCOTTLAND_HEADLESS_DIR under this checkout's build/, and cleanup of exactly what it created.
Processes a test starts in the session are recorded by PID (Session.spawn) and stopped by PID.
pkill and pgrep on the fixture PATH (stock Omarchy commands call them, e.g. the color picker's
`pkill hyprpicker || hyprpicker -a`) see only this session's processes, never the machine's.
"""
import json
import os
import re
import shutil
import signal
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
        for name in ("pkill", "pgrep"):
            (self.bin / name).symlink_to(Path(__file__).resolve().parent / "session-pkill.py")

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
    def __init__(self, fixture, name, extra_args=(), env=None, repo=REPO, omarchy=True):
        """repo: the checkout whose tests/headless.sh (and build) runs the session; an older
        checkout for reload rehearsals."""
        self.fixture = fixture
        self.repo = Path(repo)
        self.dir = self.repo / "build" / name
        self.env = {**os.environ, "HOME": str(fixture.home),
                    "SCOTTLAND_HEADLESS_DIR": str(self.dir),
                    "SCOTTLAND_TEST_PATH": str(fixture.bin),
                    "TMPDIR": str(REPO / "build" / "tmp"), **(env or {})}
        (REPO / "build" / "tmp").mkdir(parents=True, exist_ok=True)
        self.extra_args = list(extra_args)
        self.omarchy = omarchy  # False: core alone, no adapter hooks
        self.started = False

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()

    def harness(self, *args, check=True, timeout=60, input=None):
        return subprocess.run([str(self.repo / "tests/headless.sh"), *args], env=self.env, text=True,
                              capture_output=True, check=check, timeout=timeout, input=input)

    def start(self):
        self.harness("stop", check=False)
        result = self.harness("start", *(["--omarchy"] if self.omarchy else []), *self.extra_args,
                              timeout=120)
        self.started = True
        self.display = (self.dir / "display").read_text().strip()
        return result

    def stop(self):
        if self.started:
            log = self.dir / "wayfire.log"
            if log.exists():  # evidence outlives the session directory
                (self.dir.parent / f"{self.dir.name}-wayfire.log").write_bytes(log.read_bytes())
            self.harness("stop", check=False)
            self.started = False

    def run(self, *command, check=False, timeout=30, input=None):
        return self.harness("run", *command, check=check, timeout=timeout, input=input)

    def spawn(self, command, log="/dev/null"):
        """Start a shell command in the session in the background; returns its PID."""
        result = self.run("sh", "-c", f"{command} >{log} 2>&1 </dev/null & echo $!")
        return int(result.stdout.strip().splitlines()[-1])

    def marker(self):
        """An environment entry every process of this session (and only this session) carries:
        tests/headless.sh gives each session its own XDG_CONFIG_HOME (older harnesses too)."""
        return f"XDG_CONFIG_HOME={self.dir / 'config'}".encode()

    def owns(self, pid):
        try:
            return self.marker() in Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
        except OSError:
            return False

    def owned(self, pattern):
        """PIDs of this session's processes whose command line matches the regex pattern."""
        pids = []
        for proc in Path("/proc").glob("[0-9]*"):
            try:
                cmdline = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
            except OSError:
                continue
            if int(proc.name) != os.getpid() and re.search(pattern, cmdline) and self.owns(proc.name):
                pids.append(int(proc.name))
        return pids

    def descendants(self, pid):
        """This session's processes started under pid (e.g. the quickshell a sandbox wrapper runs)."""
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

    def terminate(self, *pids, sig=signal.SIGTERM, timeout=5.0):
        """Stop processes this session owns, by PID, with the processes they started; returns the
        PIDs still alive afterwards."""
        pids = [p for pid in pids if self.owns(pid) for p in (pid, *self.descendants(pid))]
        for pid in pids:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass
        self.wait(lambda: not [p for p in pids if self.owns(p)], timeout)
        alive = [p for p in pids if self.owns(p)]
        for pid in alive:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        return alive

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


def read_png(path):
    """(width, height, rgb bytes) of an 8-bit RGB/RGBA, non-interlaced PNG (grim's output)."""
    import struct
    import zlib
    data = Path(path).read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    pos, chunks, header = 8, [], None
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            chunks.append(body)
        pos += 12 + length
    width, height, depth, color, _c, _f, interlace = header
    assert depth == 8 and color in (2, 6) and not interlace, header
    channels = 3 if color == 2 else 4
    raw, stride = zlib.decompress(b"".join(chunks)), width * channels
    rows, previous = [], bytearray(stride)
    for y in range(height):
        kind, line = raw[y * (stride + 1)], bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - channels] if i >= channels else 0
            b = previous[i]
            c = previous[i - channels] if i >= channels else 0
            if kind == 1:
                line[i] = (line[i] + a) & 255
            elif kind == 2:
                line[i] = (line[i] + b) & 255
            elif kind == 3:
                line[i] = (line[i] + (a + b) // 2) & 255
            elif kind == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append(bytes(line))
        previous = line
    rgb = b"".join(bytes(v for i, v in enumerate(row) if i % channels < 3) for row in rows) \
        if channels == 4 else b"".join(rows)
    return width, height, rgb


def axis_recorder():
    """Build tests/axis-recorder.c into build/ (once per source change); returns its path."""
    source = REPO / "tests/axis-recorder.c"
    out = REPO / "build/axis-recorder"
    if out.exists() and out.stat().st_mtime >= source.stat().st_mtime:
        return out
    gen = REPO / "build/axis-recorder-gen"
    gen.mkdir(parents=True, exist_ok=True)
    protocols = {"xdg-shell": "/usr/share/wayland-protocols/stable/xdg-shell/xdg-shell.xml",
                 "wlr-layer-shell-unstable-v1":
                     str(REPO / "core/plugin/protocols/wlr-layer-shell-unstable-v1.xml")}
    sources = [str(source)]
    for name, xml in protocols.items():
        subprocess.run(["wayland-scanner", "client-header", xml, str(gen / f"{name}-client-protocol.h")],
                       check=True)
        subprocess.run(["wayland-scanner", "private-code", xml, str(gen / f"{name}-protocol.c")], check=True)
        sources.append(str(gen / f"{name}-protocol.c"))
    subprocess.run(["cc", "-O1", "-o", str(out), *sources, f"-I{gen}", "-lwayland-client"], check=True)
    return out


class Recorder:
    """An axis-recorder client in a session: what scroll its surface received."""

    def __init__(self, session, name, color, layer=False, new_app_id=None):
        """new_app_id: a pointer button press on it makes the client change its app-id to this."""
        self.session, self.name = session, name
        self.log = session.dir.parent / f"{session.dir.name}-axis-{name}.log"
        self.log.unlink(missing_ok=True)
        args = ["--layer", name, color] if layer else [name, color] + ([new_app_id] if new_app_id else [])
        self.pid = session.spawn(f"exec {axis_recorder()} {' '.join(args)}", self.log)

    def lines(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def mark(self):
        return len(self.lines())

    def vertical(self, since=0):
        """Sum of vertical axis values received after line `since`."""
        return sum(float(line.split()[2]) for line in self.lines()[since:] if line.startswith("axis 0 "))
