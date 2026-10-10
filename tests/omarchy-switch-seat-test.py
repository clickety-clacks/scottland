#!/usr/bin/env python3
"""Real desktop switches on the test machine's seat: Hyprland -> Scottland -> Hyprland (W2-W6, W11).

MANUAL AND DISRUPTIVE: this closes every window on the seat's current desktop, twice. Run it
only on the test machine, only when nobody is using its screen, holding the shared test lock.
It must start in Hyprland. Not part of any default test run.

Each leg remembers the old "keep both desktops running" choice (switch.json closeCurrent=false),
opens this checkout's switch dialog inside the running desktop (as the Omarchy menu would: a
process the compositor starts), checks its warning on the captured screen, presses Return with
wtype (virtual-keyboard input; the shared seat session has no stipc plugin) and then judges the
result independently of the switch code:

- the old desktop's compositor process is gone and the new desktop is the only graphical
  session of the user on seat0 (logind);
- the user service manager points at the new desktop, and the graphical-session services are
  running again, started after the switch, with the new desktop's WAYLAND_DISPLAY and
  XDG_CURRENT_DESKTOP in their process environment (/proc; 1Password's reads back empty, so for
  it only the restart counts);
- no keep-mode leftovers: no handover watcher, no desktop started on its own VT, no switch.json;
- no core dumps from processes of either desktop session during the switch.

The dialog runs this checkout's scottland-switch (first on PATH); the root helper is the
installed one (pkexec only runs /usr/lib/scottland/scottland-session-helper). Evidence
(screenshots, webcam frames, logs) goes to a unique, owner-marked build/seat-<run>/.

  tests/omarchy-switch-seat-test.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from omarchy_fixture import REPO, Checks, read_ppm  # noqa: E402
from seat_test_scratch import create_after_preflight  # noqa: E402

RUNTIME = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
REMEMBERED = CONFIG / "scottland/switch.json"
USER = subprocess.run(["id", "-un"], text=True, capture_output=True).stdout.strip()
SCOTTLAND_EXEC = shutil.which("scottland-exec") or str(Path.home() / ".local/bin/scottland-exec")
KEY_SERVICES = ["xdg-desktop-portal.service", "app-com.onepassword.OnePassword@autostart.service",
                "voxtype.service", "omarchy-fcitx5.service"]
check = Checks()
log_lines = []
ppm_outputs = set()


def log(message):
    line = f"{time.strftime('%T')} {message}"
    log_lines.append(line)
    print(line, flush=True)


def sh(*args, env=None, timeout=30):
    return subprocess.run(args, text=True, capture_output=True, timeout=timeout,
                          env={**os.environ, **(env or {})})


def wait(predicate, timeout, interval=0.5):
    """Poll predicate() until truthy; returns (ok, last value)."""
    deadline = time.monotonic() + timeout
    value = predicate()
    while not value and time.monotonic() < deadline:
        time.sleep(interval)
        value = predicate()
    return bool(value), value


def prop(session, name):
    return sh("loginctl", "show-session", session, "-p", name, "--value").stdout.strip()


def graphical_sessions():
    """{id: desktop} of this user's graphical sessions on seat0 that are not closing."""
    found = {}
    for line in sh("loginctl", "list-sessions", "--no-legend").stdout.splitlines():
        fields = line.split()
        if len(fields) >= 4 and fields[2] == USER and fields[3] == "seat0":
            if prop(fields[0], "Type") == "wayland" and prop(fields[0], "State") != "closing":
                found[fields[0]] = prop(fields[0], "Desktop")
    return found


def active_session():
    return sh("loginctl", "show-seat", "seat0", "-p", "ActiveSession", "--value").stdout.strip()


def hyprland():
    """(signature, wayland display, pid) of the running Hyprland, from its lock file."""
    for lock in (RUNTIME / "hypr").glob("*/hyprland.lock"):
        if lock.parent.name.startswith("scottland_"):
            continue  # a Hyprland shim
        try:
            pid, display = (lock.read_text().split("\n") + [""])[:2]
            os.kill(int(pid), 0)
        except (OSError, ValueError):
            continue
        return lock.parent.name, display, int(pid)
    return None


def scottland(session):
    """(wayland display, wayfire pid) of the Scottland running in logind session `session`."""
    listing = sh(SCOTTLAND_EXEC, "--list").stdout.split()
    for display in [d for d in listing if d.startswith("wayland-")]:
        env = sh(SCOTTLAND_EXEC, "--display", display, "--", "sh", "-c",
                 'echo "$XDG_SESSION_ID $WAYFIRE_SOCKET"').stdout.split()
        if env and env[0] == session:
            for proc in Path("/proc").glob("[0-9]*"):
                try:
                    if (proc / "comm").read_text().strip() == "wayfire" and \
                            f"XDG_SESSION_ID={session}".encode() in (proc / "environ").read_bytes().split(b"\0"):
                        return display, int(proc.name)
                except OSError:
                    continue
    return None


def manager_env():
    return dict(line.split("=", 1) for line in sh("systemctl", "--user", "show-environment").stdout.splitlines()
                if "=" in line)


def service_state(name):
    out = sh("systemctl", "--user", "show", name, "-p", "ActiveState", "-p", "MainPID",
             "-p", "ExecMainStartTimestampMonotonic").stdout
    values = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    pid = int(values.get("MainPID") or 0)
    display = desktop = None
    if pid:
        try:
            environ = Path(f"/proc/{pid}/environ").read_bytes()
        except OSError:
            environ = b""
        if not environ.strip(b"\0"):
            display = "unreadable"  # 1Password clears what /proc shows of its environment
        for entry in environ.split(b"\0"):
            if entry.startswith(b"WAYLAND_DISPLAY="):
                display = entry.split(b"=", 1)[1].decode()
            if entry.startswith(b"XDG_CURRENT_DESKTOP="):
                desktop = entry.split(b"=", 1)[1].decode()
        if display == "unreadable":
            desktop = "unreadable"
    return {"active": values.get("ActiveState"), "pid": pid, "display": display, "desktop": desktop,
            "started_us": int(values.get("ExecMainStartTimestampMonotonic") or 0)}


def services_on(display, desktop, since_us):
    """Every key service runs, restarted after the switch, with the new desktop's display and
    name. Both desktops may use the same display name, so the name is what tells them apart."""
    states = {name: service_state(name) for name in KEY_SERVICES}
    ok = all(s["active"] == "active" and s["display"] in (display, "unreadable")
             and (s["desktop"] == "unreadable" or desktop in (s["desktop"] or ""))
             and s["started_us"] > since_us for s in states.values())
    return ok and states


def monotonic_us():
    return int(time.clock_gettime(time.CLOCK_MONOTONIC) * 1_000_000)


def urgent_color():
    """The Omarchy shell's urgent color for the current theme (its Color.qml rule: the last
    `red` or `color1` in colors.toml; default #a55555)."""
    color = "#a55555"
    path = Path.home() / ".local/state/omarchy/current/theme/colors.toml"
    try:
        for line in path.read_text().splitlines():
            match = re.match(r'\s*(red|color1)\s*=\s*["\']?(#[0-9A-Fa-f]{6})', line)
            if match:
                color = match.group(2)
    except OSError:
        pass
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


def urgent_pixels(ppm):
    """Pixels in the urgent color in the middle third of the screen, where the dialog's card is
    centered. The rest of the screen is ignored: the dialog's scrim dims what was there."""
    try:
        width, height, rgb = read_ppm(ppm)
    except (OSError, AssertionError, ValueError, IndexError):
        return 0
    target = urgent_color()
    count = 0
    for y in range(height // 3, 2 * height // 3):
        row = rgb[(y * width + width // 3) * 3:(y * width + 2 * width // 3) * 3]
        count += sum(1 for i in range(0, len(row) - 2, 3)
                     if all(abs(row[i + k] - target[k]) <= 24 for k in range(3)))
    return count


def grab(name, runner):
    """Screenshot through `runner` (a function running grim inside the desktop): (ppm, png)."""
    ppm = SCRATCH.output(f"{name}.ppm")
    png = SCRATCH.output(f"{name}.png")
    ppm_outputs.add(ppm)
    SCRATCH.remove(ppm)
    SCRATCH.remove(png)
    runner(["timeout", "5", "grim", "-t", "ppm", str(ppm)])
    runner(["timeout", "5", "grim", str(png)])
    return ppm, png


def camera(name):
    """A webcam frame of the physical screen (evidence when screenshots might lie)."""
    sh("timeout", "15", "ffmpeg", "-loglevel", "error", "-y", "-f", "v4l2", "-i", "/dev/video2",
       "-frames:v", "1", str(SCRATCH.output(f"cam-{name}.jpg")), timeout=20)


def core_dumps(since):
    """Core dumps since `since` (epoch seconds) from processes in a desktop session or the user's
    graphical services: (exe, cgroup) pairs."""
    listing = sh("coredumpctl", "list", f"--since=@{int(since)}", "--json=short", "--no-pager")
    found = []
    try:
        entries = json.loads(listing.stdout or "[]")
    except json.JSONDecodeError:
        entries = []
    for entry in entries:
        info = sh("coredumpctl", "info", str(entry["pid"]), "--no-pager").stdout
        cgroup = re.search(r"Control Group: (\S+)", info)
        cgroup = cgroup.group(1) if cgroup else ""
        if re.search(r"/session-\d+\.scope|app\.slice|session\.slice|wayland-wm", cgroup):
            found.append((entry.get("exe"), cgroup))
    return found


def leftovers():
    handover = sh("systemctl", "--user", "is-active", "scottland-handover.service").stdout.strip()
    vt_units = sh("systemctl", "list-units", "--all", "--no-legend", "scottland-session-tty*").stdout.strip()
    return {"handover": handover, "vt units": vt_units, "switch.json": REMEMBERED.exists()}


def quickshell_logs(pids):
    """Quickshell's per-instance run directories (it hardcodes them under XDG_RUNTIME_DIR)."""
    paths = []
    for pid in pids:
        link = RUNTIME / "quickshell/by-pid" / str(pid)
        if link.is_symlink():
            paths += [link, link.resolve()]
    return paths


def remember_keep():
    REMEMBERED.parent.mkdir(parents=True, exist_ok=True)
    REMEMBERED.write_text(json.dumps({"closeCurrent": False}) + "\n")


def leg(source, target, launch, runner, mapped=lambda: True):
    """Open the dialog in `source`, check its warning, press Return; returns the qs PIDs seen.
    Return is only sent once the warning is on screen (and `mapped()` holds), so it cannot land in
    another window."""
    log(f"{source} -> {target}: remembering keep and opening the dialog")
    remember_keep()
    baseline = urgent_pixels(grab(f"{source}-before", runner)[0])
    launch()
    ok, count = wait(lambda: mapped() and urgent_pixels(grab(f"{source}-dialog", runner)[0]) >= baseline + 150,
                     timeout=30)
    pids = [int(p) for p in sh("pgrep", "-u", USER, "-f", f"qs -p {REPO}/omarchy/switch-dialog").stdout.split()]
    check(f"{source}: the real dialog shows the 'will close all windows' warning", ok,
          f"urgent pixels: {count}, before the dialog: {baseline}")
    camera(f"{source}-dialog")
    if not ok:
        return pids, False
    runner(["wtype", "-k", "Return"])
    return pids, True


dialog = REPO / "omarchy/switch-dialog"
path = f"{REPO}/omarchy/bin:/usr/local/bin:/usr/bin:/bin"
qs_pids = []

before = hyprland()
sessions = graphical_sessions()
start = active_session()
log(f"start: seat0 active session {start}, graphical sessions {sessions}, Hyprland {before}")
SCRATCH = create_after_preflight(REPO / "build", before, sessions, start)
if SCRATCH is None:
    log("preflight refused: expected one active Hyprland graphical session; no evidence directory created")
    sys.exit(2)
had_remembered = REMEMBERED.exists()
sig, hdisplay, hpid = before
camera("hyprland-before")

# Leg 1: Hyprland -> Scottland
launcher = SCRATCH.output("dialog-to-scottland.sh")
launcher_log = SCRATCH.output("dialog-to-scottland.log")
launcher.write_text(f"#!/bin/sh\nexport PATH={path} OTHER=scottland CURRENT=hyprland\n"
                    f"exec qs -p {dialog} >{launcher_log} 2>&1\n")
launcher.chmod(0o755)
hypr_env = {"HYPRLAND_INSTANCE_SIGNATURE": sig, "WAYLAND_DISPLAY": hdisplay}
t0_us, t0 = monotonic_us(), time.time()
pids, pressed = leg("hyprland", "scottland",
                    lambda: sh("hyprctl", "dispatch", f'hl.dsp.exec_cmd("{launcher}")', env=hypr_env),
                    lambda cmd: sh(*cmd, env=hypr_env),
                    lambda: '"scottland-switch"' in sh("hyprctl", "layers", "-j", env=hypr_env).stdout)
qs_pids += pids
if not pressed:
    sys.exit(check.summary())

ok, _ = wait(lambda: not Path(f"/proc/{hpid}").exists(), timeout=90)
check("Hyprland -> Scottland: Hyprland's compositor exits", ok)
ok, new = wait(lambda: (lambda s: s if s != start and "scottland" in prop(s, "Desktop").lower() else None)(
    active_session()), timeout=180, interval=1)
check("Hyprland -> Scottland: a new Scottland session is active on seat0", ok, active_session())
s1 = new if ok else active_session()
ok, found = wait(lambda: scottland(s1), timeout=60, interval=1)
sdisplay, spid = found if ok else (None, None)
check("Hyprland -> Scottland: Scottland is the only graphical session",
      ok and list(graphical_sessions()) == [s1] and hyprland() is None, (graphical_sessions(), hyprland()))
ok, states = wait(lambda: sh("systemctl", "--user", "is-active", "scottland-session.target",
                             "graphical-session.target").returncode == 0 and services_on(sdisplay, "Scottland", t0_us),
                  timeout=120, interval=1)
check("Hyprland -> Scottland: session services restarted in Scottland (portal, 1Password, Voxtype, Fcitx5)",
      ok, {n: service_state(n) for n in KEY_SERVICES})
env = manager_env()
check("Hyprland -> Scottland: the service manager points at Scottland",
      env.get("WAYLAND_DISPLAY") == sdisplay and "Scottland" in env.get("XDG_CURRENT_DESKTOP", ""),
      {k: env.get(k) for k in ("WAYLAND_DISPLAY", "XDG_CURRENT_DESKTOP")})
left = leftovers()
check("Hyprland -> Scottland: no keep-mode leftovers (handover, VT desktop, remembered choice)",
      left["handover"] != "active" and not left["vt units"] and not left["switch.json"], left)
dumps = core_dumps(t0)
check("Hyprland -> Scottland: no crash reports from the desktops' processes", not dumps, dumps)


def in_scottland(cmd):
    return sh(SCOTTLAND_EXEC, "--display", sdisplay, "--", *cmd)


grab("scottland-after", in_scottland)
camera("scottland-after")

# Leg 2: Scottland -> Hyprland
if sdisplay:
    unit = f"rkm-switch-dialog-{int(time.time())}"
    t1_us, t1 = monotonic_us(), time.time()
    launch = lambda: in_scottland([  # noqa: E731
        "systemd-run", "--user", "--collect", "--quiet", f"--unit={unit}",
        "-E", f"PATH={path}", "-E", "WAYLAND_DISPLAY", "-E", "XDG_CURRENT_DESKTOP", "-E", "XDG_SESSION_ID",
        "-E", "XDG_RUNTIME_DIR", "-E", "OTHER=hyprland", "-E", "CURRENT=scottland",
        "qs", "-p", str(dialog)])
    pids, pressed = leg("scottland", "hyprland", launch, in_scottland)
    qs_pids += pids
    if pressed:
        ok, _ = wait(lambda: not Path(f"/proc/{spid}").exists(), timeout=90)
        check("Scottland -> Hyprland: Scottland's compositor exits", ok)
        ok, back = wait(hyprland, timeout=180, interval=1)
        s2 = active_session()
        check("Scottland -> Hyprland: Hyprland is running and the only graphical session",
              ok and list(graphical_sessions()) == [s2] and s2 not in (start, s1), (graphical_sessions(), back))
        hdisplay2 = back[1] if back else None
        ok, states = wait(lambda: sh("systemctl", "--user", "is-active", "graphical-session.target",
                                     "wayland-wm@hyprland.desktop.service").returncode == 0
                          and services_on(hdisplay2, "Hyprland", t1_us), timeout=120, interval=1)
        check("Scottland -> Hyprland: uwsm started Hyprland and the services came back attached to it",
              ok, {n: service_state(n) for n in KEY_SERVICES})
        env = manager_env()
        check("Scottland -> Hyprland: the service manager points at Hyprland",
              env.get("WAYLAND_DISPLAY") == hdisplay2 and "Hyprland" in env.get("XDG_CURRENT_DESKTOP", ""),
              {k: env.get(k) for k in ("WAYLAND_DISPLAY", "XDG_CURRENT_DESKTOP")})
        left = leftovers()
        check("Scottland -> Hyprland: no keep-mode leftovers (handover, VT desktop, remembered choice)",
              left["handover"] != "active" and not left["vt units"] and not left["switch.json"], left)
        dumps = core_dumps(t1)
        check("Scottland -> Hyprland: no crash reports from the desktops' processes", not dumps, dumps)
        if back:
            henv = {"HYPRLAND_INSTANCE_SIGNATURE": back[0], "WAYLAND_DISPLAY": back[1]}
            grab("hyprland-after", lambda cmd: sh(*cmd, env=henv))
        camera("hyprland-after")

# Clean up exactly what this test created.
if not had_remembered:
    REMEMBERED.unlink(missing_ok=True)
for path_ in quickshell_logs(qs_pids):
    if path_.is_symlink():
        path_.unlink()
    elif path_.is_dir() and path_.is_relative_to(RUNTIME / "quickshell"):
        shutil.rmtree(path_)
for ppm in sorted(ppm_outputs):
    SCRATCH.remove(ppm)
SCRATCH.output("seat.log").write_text("\n".join(log_lines) + "\n")
sys.exit(check.summary())
