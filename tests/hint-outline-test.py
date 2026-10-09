#!/usr/bin/env python3
"""WK14/WK37 Window-mode tint layer and WK38 strength, with real stipc input.
Run inside a caller-owned headless session (tests/hint-outline-test.sh). Screenshots and
state land in build/hint-outline-evidence; no live config, session or service is used.
"""
import json
import os
from pathlib import Path
import stat
import socket
import struct
import subprocess
import sys
import time

import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf

repo = Path(__file__).resolve().parents[1]


def require_isolated_headless_session():
    """Fail closed unless this process is inside this run's private headless session."""
    env = os.environ

    def refuse(reason):
        raise RuntimeError(f"isolated headless session required: {reason}")

    def env_path(name):
        raw = env.get(name)
        if not raw:
            refuse(f"{name} is missing")
        path = Path(raw)
        if not path.is_absolute():
            refuse(f"{name} is not absolute")
        try:
            if path.resolve(strict=True) != path:
                refuse(f"{name} is not canonical")
        except OSError as error:
            refuse(f"{name} cannot be resolved: {error}")
        return path

    def owned(path, kind, mode=None):
        try:
            info = path.lstat()
        except OSError as error:
            refuse(f"{path} is unavailable: {error}")
        is_expected = {
            "directory": stat.S_ISDIR,
            "file": stat.S_ISREG,
            "socket": stat.S_ISSOCK,
        }[kind](info.st_mode)
        if not is_expected or stat.S_ISLNK(info.st_mode) or info.st_uid != os.getuid():
            refuse(f"{path} is not an owned, non-symlink {kind}")
        if mode is not None and stat.S_IMODE(info.st_mode) != mode:
            refuse(f"{path} does not have mode {mode:04o}")
        return info

    def same_inode(left, right, label):
        left_info = left.stat()
        right_info = right.stat()
        if (left_info.st_dev, left_info.st_ino) != (right_info.st_dev, right_info.st_ino):
            refuse(f"{label} is not the private bind-mounted path")

    if env.get("SCOTTLAND_HEADLESS_ISOLATION") != "1":
        refuse("runner isolation flag is absent")

    build = (repo / "build").resolve(strict=True)
    scratch = env_path("SCOTTLAND_TEST_SCRATCH")
    headless = env_path("SCOTTLAND_HEADLESS_DIR")
    session = env_path("SCOTTLAND_SESSION_DIR")
    runtime = env_path("XDG_RUNTIME_DIR")
    tmpdir = env_path("TMPDIR")
    prefix = next(
        (name for name in ("item2-tint.", "item2-acceptance.") if scratch.name.startswith(name)),
        None,
    )
    suffix = scratch.name[len(prefix):] if prefix else ""
    if (scratch.parent != build or len(suffix) != 8 or not suffix.isalnum()):
        refuse("test scratch is not this checkout's unique item2 run directory")
    if headless != scratch / "headless-wk37-wk38" or session != headless / "session":
        refuse("headless/session paths are outside the expected item2 run")
    private_runtime = headless / "runtime"
    private_tmp = headless / "tmp"
    if tmpdir != private_tmp:
        refuse("TMPDIR is not the headless run's private scratch")

    for path in (scratch, headless, private_runtime, private_tmp, session, runtime, tmpdir):
        owned(path, "directory", 0o700)
    marker = headless / ".scottland-headless-owner"
    owned(marker, "file", 0o600)
    try:
        owner_lines = marker.read_text().splitlines()
    except OSError as error:
        refuse(f"headless owner record cannot be read: {error}")
    owner_token = env.get("SCOTTLAND_HEADLESS_OWNER", "")
    if owner_lines != [str(os.getuid()), str(runtime), owner_token]:
        refuse("headless owner record does not match this uid, canonical runtime, and owner token")
    # wayland-1 is normal when a fresh private runtime starts empty; prove the bind mount instead.
    same_inode(runtime, private_runtime, "XDG_RUNTIME_DIR")
    same_inode(Path("/tmp"), private_tmp, "/tmp")

    display_file = headless / "display"
    compositor_file = headless / "compositor.pid"
    owned(display_file, "file", 0o600)
    owned(compositor_file, "file", 0o600)
    display = display_file.read_text().strip()
    if not display.startswith("wayland-") or not display.removeprefix("wayland-").isdigit():
        refuse("display record is not a Wayland socket name")
    owned(runtime / display, "socket")
    same_inode(runtime / display, private_runtime / display, "Wayland display socket")

    try:
        compositor_pid = int(compositor_file.read_text().strip())
    except ValueError as error:
        refuse(f"headless process identity record is invalid: {error}")
    if compositor_pid <= 1:
        refuse("headless process identity record is not a valid PID")

    env_file = session / f"{display}.env"
    owned(env_file, "file", 0o600)
    try:
        recorded = {}
        for entry in env_file.read_bytes().split(b"\0"):
            key, separator, value = entry.partition(b"=")
            if separator:
                recorded[os.fsdecode(key)] = os.fsdecode(value)
    except OSError as error:
        refuse(f"session environment record cannot be read: {error}")

    expected = {
        "SCOTTLAND_HEADLESS_ISOLATION": "1",
        "SCOTTLAND_TEST_SCRATCH": str(scratch),
        "SCOTTLAND_HEADLESS_DIR": str(headless),
        "SCOTTLAND_SESSION_DIR": str(session),
        "XDG_RUNTIME_DIR": str(runtime),
        "TMPDIR": str(private_tmp),
        "WAYLAND_DISPLAY": display,
    }
    for key, value in expected.items():
        if recorded.get(key) != value or env.get(key) != value:
            refuse(f"{key} does not match this run's saved session environment")
    if env.get("SCOTTLAND_EXEC") != "1":
        refuse("test command was not launched through the selected headless session")

    ipc_path = Path(env.get("WAYFIRE_SOCKET", ""))
    expected_ipc = runtime / f"wayfire-{display}-.socket"
    if ipc_path != expected_ipc or recorded.get("WAYFIRE_SOCKET") != str(expected_ipc):
        refuse("Wayfire IPC endpoint is not the recorded socket under the private runtime")
    owned(ipc_path, "socket")


require_isolated_headless_session()
evidence = Path(os.environ.get("SCOTTLAND_TEST_EVIDENCE_DIR") or repo / "build/hint-outline-evidence")
art = evidence / f"run-{os.getpid()}-{time.time_ns()}"
art.mkdir(parents=True)
layout = art / "settings-home/scottland/layout.ini"
layout.parent.mkdir(parents=True, exist_ok=True)
log = (art / "clients.log").open("w")
sock = socket.socket(socket.AF_UNIX)
sock.connect(os.environ["WAYFIRE_SOCKET"])
peer_pid, peer_uid, _ = struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
compositor_pid = int((Path(os.environ["SCOTTLAND_HEADLESS_DIR"]) / "compositor.pid").read_text().strip())
if peer_pid != compositor_pid or peer_uid != os.getuid():
    sock.close()
    raise RuntimeError("isolated headless session required: IPC peer is not this run's owned Wayfire")
clients = []
passed = failed = 0
BACKGROUND = "#1f232c"


def ipc(method, data=None):
    body = json.dumps(dict(method=method, data=data or {})).encode()
    sock.sendall(struct.pack("<I", len(body)) + body)
    def read(n):
        result = b""
        while len(result) < n:
            chunk = sock.recv(n - len(result))
            if not chunk:
                raise RuntimeError("compositor disconnected")
            result += chunk
        return result
    result = json.loads(read(struct.unpack("<I", read(4))[0]))
    if isinstance(result, dict) and "error" in result:
        raise RuntimeError(f"{method}: {result}")
    return result


def check(name, ok):
    global passed, failed
    print(("PASS " if ok else "FAIL ") + name, flush=True)
    passed += bool(ok)
    failed += not ok


def wait(predicate, timeout=8):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        result = predicate()
        if result:
            return result
        time.sleep(.05)
    raise RuntimeError("timed out waiting for test state")


def key(code, state):
    ipc("stipc/feed_key", dict(key="KEY_" + code, state=state))


def pointer(x, y):
    ipc("stipc/move_cursor", dict(x=round(x), y=round(y)))


def click(x, y):
    pointer(x, y)
    time.sleep(.08)
    ipc("stipc/feed_button", dict(combo="BTN_LEFT", mode="press"))
    ipc("stipc/feed_button", dict(combo="BTN_LEFT", mode="release"))
    time.sleep(.2)


def option(name):
    return float(ipc("wayfire/get-config-option", {"option": "scottland/" + name})["value"])


def option_reaches(name, expected, timeout=3, tolerance=.3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if abs(option(name) - expected) < tolerance:
            return True
        time.sleep(.03)
    return False


def views():
    return {v["id"]: v for v in ipc("scottland/layout-state")["views"]}


def hints():
    return {h["window"]: h for h in ipc("scottland/hints")["hints"]}


class Shot:
    def __init__(self, name, scale=1):
        self.path = art / (name + ".png")
        self.scale = scale
        subprocess.run(["grim", str(self.path)], check=True)
        self.img = GdkPixbuf.Pixbuf.new_from_file(str(self.path))
        self.data, self.stride, self.channels = self.img.get_pixels(), self.img.get_rowstride(), self.img.get_n_channels()

    def device(self, x, y):
        offset = round(y) * self.stride + round(x) * self.channels
        return tuple(self.data[offset:offset + 3])

    def pixel(self, x, y):
        return self.device(x * self.scale, y * self.scale)


def near(a, b, tolerance=4):
    return all(abs(x - y) <= tolerance for x, y in zip(a, b))


def drawn(identifier, state=None):
    links = {int(w["id"]): int(w["widget_view"]) for w in ipc("scottland/widgets")["widgets"]
             if int(w.get("widget_view", -1)) > 0}
    v, h = views()[links.get(identifier, identifier)], (state or hints())[identifier]
    f = v["frame"]
    return (f["x"] + h["dx"], f["y"] + h["dy"], f["width"], f["height"])


def open_window(name, geometry, palette=None):
    p = subprocess.Popen(["python3", str(repo / "tests/hint-style-app.py"), name, str(geometry[2]),
                          str(geometry[3]), str(palette or palette_path)], stdout=log, stderr=log)
    clients.append(p)
    v = wait(lambda: next((v for v in views().values() if v.get("title") == name and "frame" in v), None))
    ipc("window-rules/configure-view", dict(id=v["id"], geometry=dict(
        x=geometry[0], y=geometry[1], width=geometry[2], height=geometry[3])))
    time.sleep(.5)
    return v["id"]


def hold():
    key("LEFTALT", True)
    wait(lambda: ipc("scottland/hints")["active"])
    # Let avoidance offsets and badges settle (no assumed latency for the checked state).
    wait(lambda: all(abs(h["dx"] - h["target_dx"]) + abs(h["dy"] - h["target_dy"]) < .2
                     for h in hints().values()))
    time.sleep(.6)


def release():
    key("LEFTALT", False)
    wait(lambda: not ipc("scottland/hints")["active"])
    time.sleep(.8)


def tap(code):
    key(code, True)
    key(code, False)


def press_hint(identifier):
    for letter in hints()[identifier]["hint"]:
        tap(letter.upper())


def badges(state):
    return [h["badge"] for h in state.values() if h.get("badge")]


def off_badges(state, x, y, margin=6):
    return all((x - c["x"] - c["size"] / 2) ** 2 + (y - c["y"] - c["size"] / 2) ** 2 >
               (c["size"] / 2 + margin) ** 2 for c in badges(state))


def contains(rect, point):
    x, y = point
    return rect[0] <= x < rect[0] + rect[2] and rect[1] <= y < rect[1] + rect[3]


def color_bytes(color):
    return tuple(round(float(channel) * 255) for channel in color)


def blend_pixel(base, color, strength):
    return tuple(round((1 - strength) * old + strength * new)
                 for old, new in zip(base, color_bytes(color)))


def frame_map(state):
    result = {}
    for identifier in state:
        try:
            result[identifier] = drawn(identifier, state)
        except (KeyError, TypeError):
            continue
    return result


def set_tint(value):
    ipc("wayfire/set-config-options", {"scottland/window_mode_tint": float(value)})
    if not option_reaches("window_mode_tint", float(value), timeout=3, tolerance=.1):
        raise RuntimeError(f"window_mode_tint did not reach {value}%")
    time.sleep(.12)


def find_point(target_id, require_ids=(), avoid_ids=()):
    state = hints()
    frame_by_id = frame_map(state)
    target = frame_by_id[target_id]
    for fy in (.18, .28, .38, .48, .58, .68, .78):
        for fx in (.18, .28, .38, .48, .58, .68, .78):
            point = (target[0] + target[2] * fx, target[1] + target[3] * fy)
            if not off_badges(state, *point, margin=10):
                continue
            if any(identifier not in frame_by_id or not contains(frame_by_id[identifier], point)
                   for identifier in require_ids):
                continue
            if any(identifier in frame_by_id and contains(frame_by_id[identifier], point)
                   for identifier in avoid_ids):
                continue
            return point
    raise RuntimeError(f"no usable tint sample for window {target_id}; required={require_ids}")


def outside_point():
    state = hints()
    frames = frame_map(state)
    for identifier, frame in frames.items():
        candidates = ((frame[0] + frame[2] * t, frame[1] - 8) for t in (.2, .35, .5, .65, .8))
        candidates = list(candidates) + [(frame[0] - 8, frame[1] + frame[3] * t) for t in (.2, .35, .5, .65, .8)]
        for point in candidates:
            if off_badges(state, *point) and not any(contains(other, point) for other in frames.values()):
                return point
    return None


def tint_stack_check(name, point, order, strength=7, scale=1, origin=(0, 0), no_stroke=True):
    state = hints()
    frames = frame_map(state)
    old = option("window_mode_tint")
    set_tint(0)
    before = Shot(name + "-base", scale)
    set_tint(strength)
    after = Shot(name + "-tint", scale)
    screen_point = (point[0] + origin[0], point[1] + origin[1])
    expected = before.pixel(*screen_point)
    for identifier in order:
        if identifier in frames and contains(frames[identifier], point):
            expected = blend_pixel(expected, state[identifier]["color"], strength / 100)
    actual = after.pixel(*screen_point)
    check(name + f": {strength}% tint follows rear-first extent order",
          near(actual, expected, 5))
    if no_stroke:
        point_out = outside_point()
        if point_out is not None:
            set_tint(0)
            outside_base = Shot(name + "-outside-base", scale)
            set_tint(100)
            outside_full = Shot(name + "-outside-full", scale)
            outside_screen = (point_out[0] + origin[0], point_out[1] + origin[1])
            check(name + ": no tint stroke outside drawn extents",
                  near(outside_base.pixel(*outside_screen), outside_full.pixel(*outside_screen), 3))
    set_tint(old)
    return point, before, after


def no_tint_when_inactive(name, point, scale=1, origin=(0, 0)):
    old = option("window_mode_tint")
    set_tint(0)
    zero = Shot(name + "-zero", scale)
    set_tint(100)
    full = Shot(name + "-full", scale)
    screen_point = (point[0] + origin[0], point[1] + origin[1])
    check(name + ": inactive Window mode draws no tint at any strength",
          near(zero.pixel(*screen_point), full.pixel(*screen_point), 3))
    set_tint(old)


def hidpi():
    ipc("wayfire/set-config-options", {"output:HEADLESS-1/mode": "2560x1440@60000", "output:HEADLESS-1/scale": 2})
    time.sleep(1.5)
    back2 = open_window("Back", (450, 200, 380, 260))
    front2 = open_window("Front", (330, 110, 620, 470))
    ipc("window-rules/focus-view", dict(id=front2))
    time.sleep(.6)
    hold()
    image = Shot("11-hidpi", 2)
    check("HiDPI: screenshot is at device resolution", image.img.get_width() == 2560)
    point = find_point(back2, require_ids=(front2,))
    tint_stack_check("HiDPI", point, (back2, front2), scale=2)
    release()


def settings_quickshell_pid(wrapper_pid):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        pending, seen = [wrapper_pid], set()
        while pending:
            pid = pending.pop()
            if pid in seen:
                continue
            seen.add(pid)
            process = Path(f"/proc/{pid}")
            try:
                comm = (process / "comm").read_text().strip()
                argv = (process / "cmdline").read_bytes().decode(errors="replace").split("\0")
                if comm == "quickshell" and str(repo / "core/settings") in argv:
                    return pid
                pending.extend(int(c) for c in (process / "task" / str(pid) / "children").read_text().split())
            except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError):
                pass
        time.sleep(.03)
    raise RuntimeError("settings QuickShell not found")


def section(function):
    try:
        function()
    except Exception:
        import traceback
        traceback.print_exc()
        check(function.__name__ + " ran", False)


def reset_layout():
    for identifier, g in ((back, (450, 200, 380, 260)), (front, (330, 110, 620, 470)), (side, (20, 60, 380, 420))):
        ipc("window-rules/configure-view", dict(id=identifier, geometry=dict(x=g[0], y=g[1], width=g[2], height=g[3])))
    time.sleep(.8)
    ipc("window-rules/focus-view", dict(id=front))
    time.sleep(.5)


def restack_during_hold():
    reset_layout()
    hold()
    point = find_point(back, require_ids=(front,))
    tint_stack_check("restack before raise", point, (back, front))
    press_hint(back)  # selects, focuses and raises the rear window
    time.sleep(.4)
    tint_stack_check("restack after rear raise", point, (front, back))
    press_hint(front)
    time.sleep(.4)
    tint_stack_check("restack after front raise", point, (back, front))
    release()


def cover_moves_away():
    reset_layout()
    hold()
    before = drawn(front)
    for _ in range(4):
        tap("DOWN")
        time.sleep(.15)
    after = drawn(front)
    check("arrow pushes keep the cover an ordinary window", not any(v.get("widget") for v in views().values()))
    check("arrow pushes changed the covered layout", abs(before[0] - after[0]) + abs(before[1] - after[1]) > 1)
    point = find_point(back)
    tint_stack_check("arrow-pushed tint", point, (back, front))
    release()


def escape_clears():
    reset_layout()
    hold()
    point = find_point(back, require_ids=(front,))
    tint_stack_check("before Esc", point, (back, front))
    tap("ESC")
    wait(lambda: not ipc("scottland/hints")["active"], 3)
    key("LEFTALT", False)
    time.sleep(.4)
    no_tint_when_inactive("Esc with Alt held", point)


def scaled_window():
    for identifier, g in ((back, (40, 200, 380, 260)), (front, (-60, 110, 620, 470)), (side, (700, 60, 380, 420))):
        ipc("window-rules/configure-view", dict(id=identifier, geometry=dict(x=g[0], y=g[1], width=g[2], height=g[3])))
    time.sleep(1)
    ipc("window-rules/focus-view", dict(id=front))
    time.sleep(.5)
    hold()
    state, v = hints(), views()
    check("scaled: covered window is scaled down", v[back]["applied_scale"] < .9)
    point = find_point(back, require_ids=(front,))
    tint_stack_check("scaled drawn extent", point, (back, front))
    release()


def fullscreen_in_front():
    reset_layout()
    ipc("wm-actions/set-fullscreen", dict(view_id=front, state=True))
    time.sleep(1.2)
    ipc("window-rules/focus-view", dict(id=front))
    time.sleep(.5)
    hold()
    point = find_point(back, require_ids=(front,))
    tint_stack_check("fullscreen covered window", point, (back, front))
    # The fullscreen bounds have a square edge and receive the same tint as their interior,
    # without an extra inset rim.
    fs = views()[front]["frame"]
    edge_point = (fs["x"] + fs["width"] * .5, fs["y"] + 1)
    tint_stack_check("fullscreen edge has tint only", edge_point, (front,))
    release()
    ipc("wm-actions/set-fullscreen", dict(view_id=front, state=False))
    time.sleep(1.2)


def always_avoidance():
    reset_layout()
    ipc("wayfire/set-config-options", {"scottland/window_avoidance_always": True})
    time.sleep(1)
    ipc("window-rules/configure-view", dict(id=front, geometry=dict(x=340, y=110, width=620, height=470)))
    time.sleep(1)
    check("always-on avoidance leaves Window mode inactive", not ipc("scottland/hints")["active"])
    point = (drawn(back)[0] + drawn(back)[2] / 2, drawn(back)[1] + drawn(back)[3] / 2)
    no_tint_when_inactive("always-on avoidance", point)
    ipc("wayfire/set-config-options", {"scottland/window_avoidance_always": False})
    time.sleep(.8)


def widget_cover():
    reset_layout()
    card = open_window("Card", (800, 300, 320, 180))
    f = views()[card]["frame"]
    cx, cy = f["x"] + f["width"] / 2, f["y"] + f["height"] / 2
    pointer(cx, cy); time.sleep(.1)
    key("LEFTMETA", True)
    ipc("stipc/feed_button", dict(combo="BTN_LEFT", mode="press"))
    for i in range(1, 13):
        pointer(cx + (1272 - cx) * i / 12, cy)
        time.sleep(.03)
    ipc("stipc/feed_button", dict(combo="BTN_LEFT", mode="release"))
    key("LEFTMETA", False)
    pointer(640, 5)
    wait(lambda: next((w for w in views().values() if w.get("widget")), None), 10)
    time.sleep(1)
    widget = next(w for w in views().values() if w.get("widget"))
    wf_ = widget["frame"]
    client_color = (208, 80, 144)
    under_palette = art / "under-palette.json"
    under_palette.write_text(json.dumps(dict(scheme="dark", background="#d05090",
                                             foreground="#ffffff", accent="#81a1c1")))
    set_tint(0)
    small = open_window("Under", (950, round(wf_["y"]) - 110, 300, 220), under_palette)
    shown = views()[small]["frame"]
    ipc("window-rules/configure-view", dict(id=small, geometry=dict(
        x=950, y=round(wf_["y"] - 100 + shown["height"] / 2 - 110), width=300, height=220)))
    wait(lambda: abs(views()[small]["frame"]["y"] - (wf_["y"] - 100)) < 2)
    ipc("window-rules/focus-view", dict(id=front))
    time.sleep(.6)

    def measure(phase):
        hold()
        state = hints()
        links = {int(w["id"]): int(w["widget_view"]) for w in ipc("scottland/widgets")["widgets"]
                 if int(w.get("widget_view", -1)) > 0}
        widget_id = next(identifier for identifier in state
                         if links.get(identifier, -1) == widget["id"])
        card_rect = drawn(widget_id)
        under_rect = drawn(small)
        image = Shot("09-widget-cover-" + phase)
        ux, uy, uw, uh = under_rect
        points = [(x, y) for y in range(int(uy + 12), int(uy + uh - 12), 3)
                  for x in range(int(ux + 12), int(ux + uw - 12), 3) if off_badges(state, x, y, 20)]
        under_card = lambda x, y: contains(card_rect, (x, y))
        references = [p for p in points if not under_card(*p)]
        shown_points = sum(near(image.pixel(x, y), client_color, 8) for x, y in points)
        uncovered_hits = sum(near(image.pixel(x, y), client_color, 8) for x, y in references)
        covered_points = [p for p in points if under_card(*p)]
        covered_hits = sum(near(image.pixel(x, y), client_color, 8) for x, y in covered_points)
        pixel_fraction = shown_points / max(1, len(points))
        check("widget as cover " + phase + ": card hides client pixels while exposed pixels remain",
              len(references) > 30 and len(covered_points) > 30 and uncovered_hits >= 8 and
              covered_hits / len(covered_points) < .05)
        check("widget as cover " + phase + ": fixture moved across the card extent",
              (pixel_fraction < .5) == (phase == "mostly"))
        point = find_point(small, require_ids=(widget_id,))
        tint_stack_check("widget card tint " + phase, point, (small, widget_id))
        release()

    measure("partial")
    shown = views()[small]["frame"]
    ipc("window-rules/configure-view", dict(id=small, geometry=dict(
        x=950, y=round(wf_["y"] - 25 + shown["height"] / 2 - 110), width=300, height=220)))
    ipc("window-rules/focus-view", dict(id=small))
    time.sleep(.7)
    measure("mostly")
    set_tint(7)


def second_output():
    outputs = ipc("window-rules/list-outputs")
    target = next(o for o in outputs if o["geometry"]["x"] > 0)
    origin = (target["geometry"]["x"], target["geometry"]["y"])
    back2 = open_window("Back", (450, 200, 380, 260))
    front2 = open_window("Front", (330, 110, 620, 470))
    for identifier, g in ((back2, (450, 200, 380, 260)), (front2, (330, 110, 620, 470))):
        ipc("window-rules/configure-view", dict(id=identifier, output_id=target["id"],
            geometry=dict(x=g[0], y=g[1], width=g[2], height=g[3])))
        time.sleep(.6)
    ipc("window-rules/focus-view", dict(id=front2))
    time.sleep(.6)
    placed = {v["id"]: v["output-id"] for v in ipc("window-rules/list-views")}
    check("second output: both windows are on the offset output",
          placed.get(back2) == target["id"] and placed.get(front2) == target["id"])
    hold()
    point = find_point(back2, require_ids=(front2,))
    tint_stack_check("second output at offset", point, (back2, front2), origin=origin)
    release()


session_palette = None
session_palette_identity = None
try:
    palette_path = art / "palette.json"
    palette_path.write_text(json.dumps(dict(scheme="dark", background=BACKGROUND,
                                            foreground="#d8deea", accent="#81a1c1")))
    session_palette = Path(os.environ["SCOTTLAND_SESSION_DIR"]) / (os.environ["WAYLAND_DISPLAY"] + ".palette.json")
    if not session_palette.parent.is_dir():
        raise RuntimeError(f"session runtime directory is missing: {session_palette.parent}")
    palette_fd = os.open(session_palette, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(palette_fd, "wb") as palette_file:
        palette_stat = os.fstat(palette_file.fileno())
        session_palette_identity = (palette_stat.st_dev, palette_stat.st_ino)
        palette_file.write(palette_path.read_bytes())
    ipc("wayfire/set-config-options", {"scottland/color_scheme": "dark", "scottland/accent_color": "#81a1c1ff",
                                       "scottland/sounds": False})
    pointer(640, 10)
    if "--second-output" in sys.argv:
        section(second_output)
        raise SystemExit
    if "--hidpi" in sys.argv:
        section(hidpi)
        raise SystemExit

    # --- WK14/WK37: the tint layer replaces the outline and covers the rear window ------------------
    back = open_window("Back", (450, 200, 380, 260))
    front = open_window("Front", (330, 110, 620, 470))
    side = open_window("Side", (20, 60, 380, 420))
    ipc("window-rules/focus-view", dict(id=front))
    time.sleep(.6)
    base = views()[back]["frame"]
    inactive_point = (base["x"] + base["width"] * .7, base["y"] + base["height"] * .72)
    no_tint_when_inactive("before Window mode", inactive_point)
    hold()
    state = hints()
    shot = Shot("02-window-mode-tint")
    (art / "02-state.json").write_text(json.dumps({"hints": state, "views": views()}, indent=2))
    overlap = find_point(back, require_ids=(front,))
    tint_stack_check("rear tint over front window", overlap, (back, front))
    side_point = find_point(side, avoid_ids=(back, front))
    tint_stack_check("uncovered side window tint", side_point, (side,))
    release()
    no_tint_when_inactive("after Alt release", overlap)
    for function in (restack_during_hold, cover_moves_away, escape_clears, scaled_window,
                     fullscreen_in_front, always_avoidance, widget_cover):
        section(function)

    # --- WK38: overlay strength slider, live ------------------------------------------------
    probe = "0"
    panel = subprocess.Popen(["qs", "-n", "-p", str(repo / "core/settings")],
        env=dict(os.environ, QS_DISABLE_FILE_WATCHER="1", SCOTTLAND_CTL=str(repo / "core/libexec/scottland-ctl"),
                 SCOTTLAND_LAYOUT_FILE=str(layout), SCOTTLAND_PALETTE=str(palette_path),
                 SCOTTLAND_SOLAR_FILE=str(art / "solar.ini"), SCOTTLAND_HINT_PROBE=probe,
                 SCOTTLAND_SETTINGS_TEST="1"), stdout=log, stderr=log)
    clients.append(panel)
    qs_pid = settings_quickshell_pid(panel.pid)
    def snapshot():
        return json.loads(subprocess.check_output(["qs", "ipc", "--pid", str(qs_pid), "call", "settings-test",
                                                   "snapshot"], text=True, timeout=5))
    def ready():
        try:
            return snapshot().get("screen")
        except (json.JSONDecodeError, subprocess.CalledProcessError):
            return None
    wait(ready, 10)
    time.sleep(.4)
    q = snapshot()
    panel_x = (1280 - q["panel"]["width"]) / 2
    panel_y = 720 - max(24, round(720 * .04)) - q["panel"]["height"]
    check("default strength is 7%", abs(q["motion"]["window_mode_tint"] - 7) < .01 and option("window_mode_tint") == 7)
    for _ in range(3):
        click(panel_x + 36 + 2.5 * (q["panel"]["width"] - 72) / 6, panel_y + 100)
        if snapshot()["tab"] == 2:
            break
    check("Window mode tab opens", snapshot()["tab"] == 2)
    def reveal_tint():
        q = snapshot(); v = q["viewport"]; row = q["windowTintSettings"]
        if row["y"] + 68 > v["y"] + v["height"] - 10 or row["y"] < v["y"] + 10:
            # Drag the real scrollbar thumb so the row is in view.
            thumb = v["height"] * v["height"] / q["contentHeight"]
            target = max(0, min(q["contentHeight"] - v["height"], q["scroll"] + row["y"] - v["y"] - 40))
            sx = panel_x + v["x"] + v["width"] - 5
            start = panel_y + v["y"] + q["scroll"] / q["contentHeight"] * v["height"] + thumb / 2
            end = panel_y + v["y"] + target / q["contentHeight"] * v["height"] + thumb / 2
            pointer(sx, start); time.sleep(.1)
            ipc("stipc/feed_button", dict(combo="BTN_LEFT", mode="press"))
            for i in range(1, 13):
                pointer(sx, start + (end - start) * i / 12); time.sleep(.03)
            ipc("stipc/feed_button", dict(combo="BTN_LEFT", mode="release")); time.sleep(.3)
            q = snapshot(); row = q["windowTintSettings"]
        return panel_x + row["x"], panel_y + row["y"], row["width"]
    def sample_point(state):
        # The point inside the side window farthest from its badge, left of the panel.
        w = drawn(side); badge = state[side]["badge"]
        bx, by = badge["x"] + badge["size"] / 2, badge["y"] + badge["size"] / 2
        candidates = [(w[0] + w[2] * i / 10, w[1] + w[3] * j / 10) for i in range(1, 10) for j in range(1, 10)]
        candidates = [(x, y) for x, y in candidates if x < panel_x - 4 and
                      min(x - w[0], w[0] + w[2] - x, y - w[1], w[1] + w[3] - y) >= 10]
        x, y = max(candidates, key=lambda p: (p[0] - bx) ** 2 + (p[1] - by) ** 2)
        if (x - bx) ** 2 + (y - by) ** 2 <= (badge["size"] / 2 + 4) ** 2:
            raise RuntimeError("no sample point clear of the badge")
        return round(x), round(y)
    bg = tuple(int(BACKGROUND[i:i + 2], 16) for i in (1, 3, 5))
    # The panel's zone overlay is drawn over the windows, so compare each Window mode frame with
    # the same pixel just before the hold: the overlay adds (1 - its alpha) * strength * (hint - window),
    # zero at 0% and linear in the strength.
    results, strengths = {}, {}
    # 100% comes before 20% so the live check below still has room to raise the strength.
    for value in (0, 7, 100, 20):
        x, y, width = reveal_tint()
        # The slider steps by 0.5% over 0-100%, so a click lands within half a step of the value.
        click(x + max(1, min(width - 1, width * value / 100)), y + 34)
        check(f"slider sets {value}% live, before Save", option_reaches("window_mode_tint", value, tolerance=.6))
        strengths[value] = option("window_mode_tint")
        pointer(640, 5); time.sleep(.3)
        base_image = Shot(f"04-base-{value}")
        hold()
        image = Shot(f"04-tint-{value}")
        sample = sample_point(hints())
        base, got = base_image.pixel(*sample), image.pixel(*sample)
        results[value] = tuple(g - b for g, b in zip(got, base))
        print(f"tint {value}% at {sample}: {base} -> {got}", flush=True)
        release()
    check("0% turns the overlay off", all(abs(d) <= 2 for d in results[0]))
    check("7% overlay tints visibly", sum(abs(d) for d in results[7]) >= 6)
    check("20% overlay is the 7% tint scaled by strength",
          all(abs(d20 - d7 * strengths[20] / strengths[7]) <= 4 for d7, d20 in zip(results[7], results[20])))
    # 100% and 0% are exact: the slider's ends clamp to its range.
    check("slider's ends are exactly 0% and 100%", strengths[0] == 0 and strengths[100] == 100)
    # Each difference carries up to one unit of rounding, which scaling 20% to 100% multiplies by five.
    check("100% overlay is the 20% tint scaled by strength",
          all(abs(d100 - d20 * strengths[100] / strengths[20]) <= 6 for d20, d100 in zip(results[20], results[100])))
    # Live inside one hold: moving the slider while hints show changes the drawn tint at once.
    hold()
    sample = sample_point(hints())
    first_image = Shot("05-live-before")
    first = first_image.pixel(*sample)
    # Settings is above the tint layer. Its own pixels must not change when tint strength changes.
    panel_snapshot = snapshot()["panel"]
    overlay_sample = (panel_x + panel_snapshot["width"] / 2,
                      panel_y + panel_snapshot["height"] / 2)
    set_tint(0)
    overlay_zero = Shot("05-settings-untinted-zero")
    set_tint(100)
    overlay_full = Shot("05-settings-untinted-full")
    check("Window-mode Settings stays above and untinted by the layer",
          near(overlay_zero.pixel(*overlay_sample), overlay_full.pixel(*overlay_sample), 3))
    x, y, width = reveal_tint()
    click(x + width - 1, y + 34)
    check("slider reaches its 100% maximum", option_reaches("window_mode_tint", 100))
    time.sleep(.3)
    second = Shot("05-live-after").pixel(*sample)
    print("live within one hold", first, "->", second, flush=True)
    check("strength changes live while Window mode is showing",
          sum(abs(a - b) for a, b in zip(second, bg)) > sum(abs(a - b) for a, b in zip(first, bg)) + 10)
    release()
    panel.terminate(); panel.wait(timeout=5)
    # scottland-ctl takes both separate settings across 0-100%; Wayfire bounds a stored value by
    # the option's metadata range, so a tint above the old 30% maximum must read back unclamped.
    ctl = [str(repo / "core/libexec/scottland-ctl"), "set"]
    for name, values, default in (("window_mode_tint", (0, 45, 100), 7), ("hint_background_opacity", (0, 100), 21)):
        for value in values:
            subprocess.run(ctl + [name, str(value)], check=True, timeout=10, stdout=log, stderr=log)
            check(f"scottland-ctl sets {name} to {value}%", option_reaches(name, value, tolerance=.01))
        subprocess.run(ctl + [name, str(default)], check=True, timeout=10, stdout=log, stderr=log)
except Exception:
    import traceback
    traceback.print_exc()
    failed += 1
finally:
    for p in clients:
        if p.poll() is None:
            p.terminate()
    if session_palette is not None and session_palette_identity is not None:
        try:
            palette_stat = session_palette.lstat()
            if stat.S_ISREG(palette_stat.st_mode) and \
               (palette_stat.st_dev, palette_stat.st_ino) == session_palette_identity:
                session_palette.unlink()
        except FileNotFoundError:
            pass
    print(f"{passed} passed, {failed} failed", flush=True)
    raise SystemExit(1 if failed else 0)
