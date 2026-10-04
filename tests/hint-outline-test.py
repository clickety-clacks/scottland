#!/usr/bin/env python3
"""WK37 occluded-window outline and WK38 overlay strength, with real stipc input.
Run inside a caller-owned headless session (tests/hint-outline-test.sh). Screenshots and
state land in build/hint-outline-evidence; no live config, session or service is used.
"""
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import time

import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf

repo = Path(__file__).resolve().parents[1]
art = repo / "build/hint-outline-evidence" / f"run-{os.getpid()}-{time.time_ns()}"
art.mkdir(parents=True)
layout = art / "settings-home/scottland/layout.ini"
layout.parent.mkdir(parents=True, exist_ok=True)
log = (art / "clients.log").open("w")
sock = socket.socket(socket.AF_UNIX)
sock.connect(os.environ["WAYFIRE_SOCKET"])
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


def option_reaches(name, expected, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if abs(option(name) - expected) < .3:
            return True
        time.sleep(.03)
    return False


def views():
    return {v["id"]: v for v in ipc("scottland/layout-state")["views"]}


def hints():
    return {h["window"]: h for h in ipc("scottland/hints")["hints"]}


class Shot:
    def __init__(self, name):
        self.path = art / (name + ".png")
        subprocess.run(["grim", str(self.path)], check=True)
        self.img = GdkPixbuf.Pixbuf.new_from_file(str(self.path))
        self.data, self.stride, self.channels = self.img.get_pixels(), self.img.get_rowstride(), self.img.get_n_channels()

    def pixel(self, x, y):
        offset = round(y) * self.stride + round(x) * self.channels
        return tuple(self.data[offset:offset + 3])


def near(a, b, tolerance=4):
    return all(abs(x - y) <= tolerance for x, y in zip(a, b))


def drawn(identifier):
    v, h = views()[identifier], hints()[identifier]
    f = v["frame"]
    return (f["x"] + h["dx"], f["y"] + h["dy"], f["width"], f["height"])


def union_visible(rect, front, screen=(0, 0, 1280, 720)):
    x1, y1 = max(rect[0], screen[0]), max(rect[1], screen[1])
    x2, y2 = min(rect[0] + rect[2], screen[0] + screen[2]), min(rect[1] + rect[3], screen[1] + screen[3])
    covered = 0
    for x in range(int(x1), int(x2), 2):
        for y in range(int(y1), int(y2), 2):
            covered += any(f[0] <= x < f[0] + f[2] and f[1] <= y < f[1] + f[3] for f in front)
    total = len(range(int(x1), int(x2), 2)) * len(range(int(y1), int(y2), 2))
    return 1 - covered / total


def open_window(name, geometry):
    p = subprocess.Popen(["python3", str(repo / "tests/hint-style-app.py"), name, str(geometry[2]),
                          str(geometry[3]), str(palette_path)], stdout=log, stderr=log)
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


try:
    assert os.environ["WAYLAND_DISPLAY"] != "wayland-1", "isolated headless session required"
    palette_path = art / "palette.json"
    palette_path.write_text(json.dumps(dict(scheme="dark", background=BACKGROUND,
                                            foreground="#d8deea", accent="#81a1c1")))
    session_palette = Path(os.environ["XDG_RUNTIME_DIR"]) / "scottland" / (os.environ["WAYLAND_DISPLAY"] + ".palette.json")
    staged = session_palette.with_suffix(".hint-outline-test.tmp")
    staged.write_text(palette_path.read_text())
    staged.replace(session_palette)
    ipc("wayfire/set-config-options", {"scottland/color_scheme": "dark", "scottland/accent_color": "#81a1c1ff",
                                       "scottland/sounds": False})
    pointer(640, 10)

    # --- WK37: a rear window almost wholly covered by a front one -------------------------
    back = open_window("Back", (450, 200, 380, 260))
    front = open_window("Front", (330, 110, 620, 470))
    side = open_window("Side", (20, 60, 380, 420))
    ipc("window-rules/focus-view", dict(id=front))
    time.sleep(.6)
    before = Shot("01-before-window-mode")
    check("no outline outside Window mode", not any(h["outline"] for h in hints().values()))
    hold()
    state = hints()
    shot = Shot("02-window-mode-outline")
    (art / "02-state.json").write_text(json.dumps({"hints": state, "views": views()}, indent=2))
    b, f, s = drawn(back), drawn(front), drawn(side)
    measured = union_visible(b, [f])
    print("back visible", state[back]["visible_fraction"], "measured", round(measured, 3), flush=True)
    check("solver occlusion matches the displayed layout", abs(state[back]["visible_fraction"] - measured) < .03)
    check("rear window is less than half visible", state[back]["visible_fraction"] < .5)
    check("mostly occluded rear window has an outline", state[back]["outline"])
    check("front window has no outline", not state[front]["outline"] and state[front]["visible_fraction"] == 1)
    check("uncovered window has no outline", not state[side]["outline"])
    color = tuple(round(c * 255) for c in state[back]["color"])
    circles = [h["badge"] for h in state.values() if h.get("badge")]
    def on_badge(x, y):
        return any((x - c["x"] - c["size"] / 2) ** 2 + (y - c["y"] - c["size"] / 2) ** 2 <
                   (c["size"] / 2 + 6) ** 2 for c in circles)
    # Sample the rear window's edges where the front window covers them, away from corners.
    samples = []
    for i in range(1, 20):
        x = b[0] + 24 + (b[2] - 48) * i / 20
        for y in (b[1], b[1] + b[3] - 1):
            samples.append((x, y))
        y = b[1] + 24 + (b[3] - 48) * i / 20
        for x in (b[0], b[0] + b[2] - 1):
            samples.append((x, y))
    covered = [(x, y) for x, y in samples if f[0] + 4 < x < f[0] + f[2] - 4 and
               f[1] + 4 < y < f[1] + f[3] - 4 and not on_badge(x, y)]
    def edge_hit(image, x, y):
        return any(near(image.pixel(x + dx, y + dy), color) for dx in (-1, 0, 1) for dy in (-1, 0, 1))
    hits = sum(edge_hit(shot, x, y) for x, y in covered)
    print("outline samples above front window", hits, "/", len(covered), "color", color, flush=True)
    check("opaque outline in the rear hint color is drawn above the front window",
          len(covered) >= 20 and hits >= .9 * len(covered))
    check("the edge was front-window content before Window mode",
          sum(edge_hit(before, x, y) for x, y in covered) == 0)
    inner = [(b[0] + 8, b[1] + b[3] / 2), (b[0] + b[2] / 2, b[1] + 8)]
    inner = [(x, y) for x, y in inner if not on_badge(x, y)]
    check("outline stays thin (front content shows inside it)",
          inner and not any(near(shot.pixel(x, y), color, 12) for x, y in inner))
    release()
    after = Shot("03-after-release")
    check("outline clears with the hints", not any(h["outline"] for h in hints().values()))
    check("released screen shows no outline pixels",
          sum(edge_hit(after, x, y) for x, y in covered) == 0)

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
    results = {}
    for value in (0, 7, 20):
        x, y, width = reveal_tint()
        click(x + width * value / 30, y + 34)
        check(f"slider sets {value}% live, before Save", option_reaches("window_mode_tint", value))
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
          all(abs(d20 - d7 * 20 / 7) <= 4 for d7, d20 in zip(results[7], results[20])))
    # Live inside one hold: moving the slider while hints show changes the drawn tint at once.
    hold()
    state = hints()
    sample = sample_point(state)
    first_image = Shot("05-live-before")
    first = first_image.pixel(*sample)
    # WK37: the outline is never occluded, not even by an overlay surface such as this panel.
    b = drawn(back)
    color = tuple(round(c * 255) for c in state[back]["color"])
    circles = [h["badge"] for h in state.values() if h.get("badge")]
    pw, ph = snapshot()["panel"]["width"], snapshot()["panel"]["height"]
    under_panel = [(b[0] + b[2] * i / 20, y) for i in range(1, 20) for y in (b[1], b[1] + b[3] - 1)]
    under_panel += [(x, b[1] + b[3] * i / 20) for i in range(1, 20) for x in (b[0], b[0] + b[2] - 1)]
    under_panel = [(x, y) for x, y in under_panel if panel_x + 16 < x < panel_x + pw - 16 and
                   panel_y + 16 < y < panel_y + ph - 16 and not on_badge(x, y)]
    hits = sum(edge_hit(first_image, x, y) for x, y in under_panel)
    print("outline samples above the settings panel", hits, "/", len(under_panel), flush=True)
    check("mostly occluded window still outlined with the panel open", state[back]["outline"])
    check("outline draws above an overlay surface (the settings panel)",
          len(under_panel) >= 8 and hits >= .9 * len(under_panel))
    x, y, width = reveal_tint()
    click(x + width * 30 / 30 - 2, y + 34)
    check("slider reaches its 30% maximum", option_reaches("window_mode_tint", 30))
    time.sleep(.3)
    second = Shot("05-live-after").pixel(*sample)
    print("live within one hold", first, "->", second, flush=True)
    check("strength changes live while Window mode is showing",
          sum(abs(a - b) for a, b in zip(second, bg)) > sum(abs(a - b) for a, b in zip(first, bg)) + 10)
    release()
    panel.terminate(); panel.wait(timeout=5)
except Exception:
    import traceback
    traceback.print_exc()
    failed += 1
finally:
    for p in clients:
        if p.poll() is None:
            p.terminate()
    try:
        session_palette.unlink(missing_ok=True)
    except NameError:
        pass
    print(f"{passed} passed, {failed} failed", flush=True)
    raise SystemExit(1 if failed else 0)
