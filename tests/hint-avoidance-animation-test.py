#!/usr/bin/env python3
"""WK13: sample real scene transforms to ensure window avoidance eases, not jumps."""
import importlib.util
import json
import math
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import threading
import time

if len(sys.argv) != 2:
    raise SystemExit("usage: hint-avoidance-animation-test.py ARTIFACT_DIR")
artifacts = Path(sys.argv[1])
artifacts.mkdir(parents=True, exist_ok=True)
spec = importlib.util.spec_from_file_location(
    "widget_input", Path(__file__).with_name("widget-input-test.py"))
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)
passes = failures = 0
children = []
palette_path = Path(os.environ["XDG_RUNTIME_DIR"]) / "scottland" / \
    (os.environ["WAYLAND_DISPLAY"] + ".palette.json")
old_palette = palette_path.read_bytes() if palette_path.exists() else None


def check(name, okay, details=""):
    global passes, failures
    print(("PASS " if okay else "FAIL ") + name +
          (f": {details}" if details and not okay else ""), flush=True)
    passes += bool(okay)
    failures += not okay


def socket_call(sock, method, data=None):
    body = json.dumps({"method": method, "data": data or {}}).encode()
    sock.sendall(struct.pack("<I", len(body)) + body)
    def read(count):
        result = b""
        while len(result) < count:
            part = sock.recv(count - len(result))
            if not part:
                raise ConnectionError("Wayfire IPC disconnected")
            result += part
        return result
    return json.loads(read(struct.unpack("<I", read(4))[0]))


def offsets():
    return {str(row["window"]): (float(row["dx"]), float(row["dy"]))
            for row in t.ipc.call("scottland/hints")["hints"]}


def view_geometry(view_id):
    return next((row["geometry"] for row in t.ipc.call("window-rules/list-views")
                 if int(row["id"]) == int(view_id)), None)


def total_magnitude(values):
    return sum(math.hypot(*value) for value in values.values())


def difference(values, baseline):
    keys = values.keys() | baseline.keys()
    return sum(math.hypot(values.get(key, (0, 0))[0] - baseline.get(key, (0, 0))[0],
                          values.get(key, (0, 0))[1] - baseline.get(key, (0, 0))[1])
               for key in keys)


class Sampler:
    """Poll the compositor's published scene transforms across consecutive frames."""
    def __init__(self):
        self.samples = []
        self.stop = threading.Event()
        self.started = time.monotonic()
        self.thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self):
        sock = socket.socket(socket.AF_UNIX)
        sock.connect(os.environ["WAYFIRE_SOCKET"])
        sock.settimeout(2)
        try:
            while not self.stop.is_set():
                state = socket_call(sock, "scottland/hints")
                self.samples.append({
                    "ms": round((time.monotonic() - self.started) * 1000, 2),
                    "active": bool(state["active"]),
                    "offsets": {str(row["window"]): [row["dx"], row["dy"]]
                                for row in state["hints"]},
                    "targets": {str(row["window"]): [row["target_dx"], row["target_dy"]]
                                for row in state["hints"]},
                })
                self.stop.wait(.006)
        finally:
            sock.close()

    def start(self):
        self.thread.start()

    def finish(self):
        self.stop.set()
        self.thread.join(timeout=3)
        return self.samples


def values_in(samples):
    return [{key: (float(value[0]), float(value[1]))
             for key, value in sample["offsets"].items()} for sample in samples]


def eased_ramp(samples, start, label):
    """Require multiple observed interpolation states and no single-frame jump."""
    trajectory = [difference(value, start) for value in values_in(samples)]
    moved = []
    for value in trajectory:
        if value > .25 and (not moved or abs(value - moved[-1]) > .15):
            moved.append(value)
    final = trajectory[-1] if trajectory else 0
    distinct = len(moved)
    max_step = max((abs(b - a) for a, b in zip(trajectory, trajectory[1:])), default=0)
    smooth = (final > 5 and distinct >= 4 and moved[0] < final * .85 and
              max_step < max(final * .60, 2))
    check(label, smooth,
          f"{len(samples)} samples, {distinct} distinct moved states, first={moved[0] if moved else 0:.1f}, "
          f"final={final:.1f}, largest sample delta={max_step:.1f}")
    return trajectory


def cli(*args):
    return subprocess.check_output(
        [str(Path(__file__).resolve().parents[1] / "core/libexec/scottland-ctl"), *args],
        text=True, timeout=8)


def enter_window_mode():
    t.key("LEFTALT", True)
    t.wait_for(lambda: t.ipc.call("scottland/hints")["active"], timeout=4)


def exit_window_mode():
    t.key("LEFTALT", False)
    t.wait_for(lambda: not t.ipc.call("scottland/hints")["active"], timeout=4)


def open_foot(title, columns=100, rows=32):
    proc = subprocess.Popen(["foot", "-c", "/dev/null", "-T", title,
                             "-W", f"{columns}x{rows}", "sh", "-c", "exec sleep 600"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    children.append((title, proc))
    t.owned.append((title, proc))
    return proc


def settle(timeout=6):
    deadline = time.monotonic() + timeout
    prior = None
    stable = 0
    while time.monotonic() < deadline:
        current = offsets()
        if prior is not None and difference(current, prior) < .05:
            stable += 1
            if stable >= 5:
                return current
        else:
            stable = 0
        prior = current
        time.sleep(.035)
    raise AssertionError("avoidance transform did not settle")


try:
    cli("set", "window_avoidance_always", "false")
    t.ipc.call("wayfire/set-config-options", {
        "scottland/sounds": False, "scottland/alt_hold_delay": 100,
    })
    output = t.ipc.call("window-rules/list-outputs")[0]["geometry"]
    width, height = round(output["width"] * .72), round(output["height"] * .76)
    rect = {"x": output["x"] + (output["width"] - width) // 2,
            "y": output["y"] + (output["height"] - height) // 2,
            "width": width, "height": height}
    titles = [f"avoidance-animation-{i}" for i in range(4)]
    ids = {}
    for title in titles:
        open_foot(title)
        t.wait_for(lambda title=title: t.app(title))
    for title in titles:
        window = t.app(title)
        ids[title] = int(window["id"])
        t.ipc.call("window-rules/configure-view", {"id": window["id"], "geometry": rect})
    t.ipc.call("window-rules/focus-view", {"id": ids[titles[-1]]})
    time.sleep(.3)
    zero = settle()
    check("always-avoid off starts with zero outside-mode offsets",
          total_magnitude(zero) < .15)

    entering = Sampler(); entering.start()
    enter_window_mode()
    time.sleep(.65)
    enter_samples = entering.finish()
    (artifacts / "enter-window-mode.json").write_text(json.dumps(enter_samples, indent=2) + "\n")
    entered = settle()
    check("window-mode entry produces displaced hints", total_magnitude(entered) > 30)
    eased_ramp([s for s in enter_samples if s["active"]], zero,
               "window-mode entry eases offsets over multiple samples")
    subprocess.run(["grim", str(artifacts / "window-mode-avoiding.png")],
                   check=True, timeout=8)

    leaving = Sampler(); leaving.start()
    exit_window_mode()
    time.sleep(.65)
    exit_samples = leaving.finish()
    (artifacts / "leave-window-mode.json").write_text(json.dumps(exit_samples, indent=2) + "\n")
    returned = settle()
    check("window-mode exit returns every transform to zero", total_magnitude(returned) < .15)
    eased_ramp([s for s in exit_samples if not s["active"]], entered,
               "window-mode exit eases offsets home over multiple samples")

    cli("set", "window_avoidance_always", "true")
    t.wait_for(lambda: total_magnitude(offsets()) > 30, timeout=8)
    always_base = settle()
    check("always-on setting keeps offsets outside window mode",
          not t.ipc.call("scottland/hints")["active"] and total_magnitude(always_base) > 30)

    # A new, large real client changes the exposure solve while always-on avoidance is settled.
    adding = Sampler(); adding.start()
    new_title = "avoidance-animation-new-large"
    open_foot(new_title, 130, 48)
    t.wait_for(lambda: t.app(new_title))
    new_view = t.app(new_title)
    large = {"x": output["x"] + round(output["width"] * .125),
             "y": output["y"] + round(output["height"] * .10),
             "width": round(output["width"] * .75),
             "height": round(output["height"] * .80)}
    t.ipc.call("window-rules/configure-view", {"id": new_view["id"], "geometry": large})
    t.wait_for(lambda: (v := t.app(new_title)) and
               (geometry := view_geometry(v["id"])) and
               geometry["width"] >= output["width"] * .70)
    time.sleep(.75)
    added_samples = adding.finish()
    (artifacts / "always-on-new-large-window.json").write_text(
        json.dumps(added_samples, indent=2) + "\n")
    added = settle()
    new_geometry = view_geometry(new_view["id"])
    check("new test client is a large window",
          new_geometry is not None and
          new_geometry["width"] >= output["width"] * .70 and
          new_geometry["height"] >= output["height"] * .75)
    check("always-on avoidance retains a visible displacement after the large window arrives",
          total_magnitude(added) > 30 and
          json.loads(cli("get")).get("window_avoidance_always") is True,
          f"offset magnitude={total_magnitude(added):.1f}")
    eased_ramp(added_samples, always_base,
               "always-on solve eases when a new large window arrives")
    subprocess.run(["grim", str(artifacts / "always-on-new-large-window.png")],
                   check=True, timeout=8)

    # Changing hint visibility while always-on remains active must not detach or reattach a
    # settled nonzero offset. Sample across both mode transitions.
    stable_always = settle()
    check("always-on baseline stays displaced before mode roundtrip", total_magnitude(stable_always) > 30)
    mode_samples = Sampler(); mode_samples.start()
    enter_window_mode()
    time.sleep(.18)
    exit_window_mode()
    time.sleep(.35)
    mode_trace = mode_samples.finish()
    mode_values = values_in(mode_trace)
    mode_steps = [difference(after, before)
                  for before, after in zip(mode_values, mode_values[1:])]
    mode_switch_steps = []
    for before, after in zip(mode_trace, mode_trace[1:]):
        if before["active"] != after["active"]:
            mode_switch_steps.append(difference(
                {key: tuple(value) for key, value in after["offsets"].items()},
                {key: tuple(value) for key, value in before["offsets"].items()}))
    mode_change_samples = sum(step > .25 for step in mode_steps)
    # Per window: entering Window mode moves windows to full hint room (peek-strip decisions 2
    # and 5), so several windows ease at once; each is capped at 1000 px/s (16.7 px a frame).
    def largest_window_step(values, baseline):
        return max((math.hypot(values.get(k, (0, 0))[0]-baseline.get(k, (0, 0))[0],
                               values.get(k, (0, 0))[1]-baseline.get(k, (0, 0))[1])
                    for k in values.keys() | baseline.keys()), default=0)
    mode_window_steps = [largest_window_step(after, before) for before, after in zip(mode_values, mode_values[1:])]
    (artifacts / "always-on-window-mode-roundtrip.json").write_text(
        json.dumps(mode_trace, indent=2) + "\n")
    # The compositor measures its own easing speed per tick (IPC sample times only approximate
    # frame times, so a late sample sees more than one frame's step).
    eased = t.ipc.call("scottland/hints")["avoidance_max_easing_speed_px_s"]
    check("always-on offsets ease across both Window mode toggles without a snap",
          len(mode_switch_steps) == 2 and mode_change_samples >= 4 and eased <= 1000.5 and
          max(mode_window_steps, default=0) < 40,
          f"{mode_change_samples} intermediate transform changes; compositor max {eased:.0f} px/s; "
          f"largest one-window sample step {max(mode_window_steps, default=0):.2f}px "
          f"(all windows {max(mode_steps, default=0):.2f}px); "
          f"toggle-frame steps {[round(step, 2) for step in mode_switch_steps]}")

    # Turning the setting off is the other inactive-target path: it must ease back to zero.
    turning_off = Sampler(); turning_off.start()
    cli("set", "window_avoidance_always", "false")
    t.wait_for(lambda: total_magnitude(offsets()) < total_magnitude(stable_always), timeout=3)
    time.sleep(.65)
    off_samples = turning_off.finish()
    (artifacts / "always-off-ease-home.json").write_text(json.dumps(off_samples, indent=2) + "\n")
    off_result = settle()
    check("turning always-on off restores zero offsets", total_magnitude(off_result) < .15)
    eased_ramp(off_samples, stable_always,
               "always-on disable eases offsets home over multiple samples")

    # The selected palette's reduced_motion bit keeps its explicit immediate-snap behavior.
    palette_path.parent.mkdir(parents=True, exist_ok=True)
    palette_path.write_text(json.dumps({
        "scheme": "dark", "background": "#1f232c", "foreground": "#d8deea",
        "accent": "#81a1c1", "reduced_motion": True,
    }) + "\n")
    reduced = Sampler(); reduced.start()
    enter_window_mode()
    time.sleep(.18)
    reduced_samples = reduced.finish()
    active_reduced_samples = [s for s in reduced_samples if s["active"]]
    reduced_values = values_in(active_reduced_samples)
    reduced_final = offsets()
    reduced_jump = difference(reduced_values[0], zero) if reduced_values else 0
    reduced_total = difference(reduced_final, zero)
    snap_error = max((math.hypot(sample["offsets"][key][0] - target[0],
                                 sample["offsets"][key][1] - target[1])
                      for sample in active_reduced_samples
                      for key, target in sample["targets"].items()), default=0)
    check("reduced motion snaps avoidance to its destination",
          reduced_total > 30 and snap_error < .15,
          f"{len(active_reduced_samples)} samples, max transform-to-target gap "
          f"{snap_error:.3f}px; first active displacement {reduced_jump:.1f} of "
          f"{reduced_total:.1f} after bounded solve slices")
    exit_window_mode()
    t.wait_for(lambda: total_magnitude(offsets()) < .15, timeout=3)
    check("reduced motion snaps temporary avoidance home", total_magnitude(offsets()) < .15)
    (artifacts / "reduced-motion-entry.json").write_text(json.dumps(reduced_samples, indent=2) + "\n")
    print(f"avoidance animation: {passes} passed, {failures} failed", flush=True)
finally:
    # Restore protocol state before IPC cleanup: a compositor failure must not leave a
    # malformed palette for the next isolated session that reuses this Wayland name.
    if old_palette is None:
        palette_path.unlink(missing_ok=True)
    else:
        palette_path.write_bytes(old_palette)
    try:
        t.key("LEFTALT", False)
        cli("set", "window_avoidance_always", "false")
    except Exception:
        pass
    for _, proc in children:
        if proc.poll() is None:
            proc.terminate()
    for _, proc in children:
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)

sys.exit(bool(failures))
