#!/usr/bin/env python3
"""Real stipc rail transitions and Super+M; compositor geometry and captured pixels."""
import sys as _sys; _sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from session_reload import reload_session
import importlib.util
import ast
import math
import json
import os
import re
from pathlib import Path
import subprocess
import shutil
import sys
import time
import threading
import struct
import zlib

spec = importlib.util.spec_from_file_location("widget_input", Path(__file__).with_name("widget-input-test.py"))
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)
out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
samples = {}


def state():
    return t.ipc.call("scottland/layout-state")


def frames(s):
    return {v["id"]: v["frame"] for v in s["views"] if v["widget"] and not v["preview"]}


def sample(label, duration=.75):
    series = []
    start = time.monotonic()
    while time.monotonic() - start < duration:
        series.append((time.monotonic() - start, state()))
        time.sleep(.006)
    samples[label] = series
    return series


def body_left(frame):
    # GO16: the body can have transparent insets and its current crossfade can
    # change them. The frame publishes its measured alpha bounds separately.
    return frame.get("alpha_shape", {}).get("body_left", frame["x"])


def settle():
    t.wait_for(lambda: state()["widget_transition_count"] == 0)
    time.sleep(.1)


def screenshot(name):
    raw = subprocess.check_output(["grim", "-t", "ppm", "-"], stderr=subprocess.PIPE)
    header = re.match(rb"P6\s+(\d+)\s+(\d+)\s+255\s", raw)
    width = int(header[1])
    pixels = raw[header.end():]

    height = int(header[2])
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    rows = b"".join(b"\0" + pixels[y * width * 3:(y + 1) * width * 3] for y in range(height))
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    (out / (name + ".png")).write_bytes(png + chunk(b"IDAT", zlib.compress(rows, 1)) + chunk(b"IEND", b""))

    class RGB:
        def red_source_pixels(self):
            return sum(pixels[offset] > 220 and pixels[offset + 1] < 20 and pixels[offset + 2] < 20
                for offset in range(0, len(pixels) - 2, 3 * 13))
        def red_blend(self):
            # A single red fixture is on screen. Scan across the captured image so
            # a moving frame cannot outrun an IPC-derived sample coordinate.
            count = 0
            for offset in range(0, len(pixels) - 2, 3 * 13):
                r, g, b = pixels[offset:offset + 3]
                if r > g + 40 and r > b + 40 and abs(g - b) < 15 and 5 < g < 200:
                    count += 1
            return count >= 20
        def getpixel(self, at):
            x, y = at
            offset = (y * width + x) * 3
            return tuple(pixels[offset:offset+3])

    return RGB()


def fixture_pixels(title, label):
    """Sample a constant opaque patch and an icon throughout a real transition."""
    patch = []
    icons = []
    markers = []
    for i in range(9):
        f = t.card(title)["frame"]
        image = screenshot(f"{label}-{i}")
        # The rail edge and vertical center are constant even while the other edge moves.
        edge = round(f["x"] + f["width"])
        y = round(f["y"] + 48)
        patch.append(image.getpixel((edge - 5, y)))
        markers.append(image.getpixel((edge - 5, round(f["y"] + 10))))
        row = [image.getpixel((x, y)) for x in range(edge - 90, edge - 5)]
        green = [j for j, p in enumerate(row) if p[1] > 240 and p[0] < 10 and p[2] < 10]
        icons.append(len(green))
    t.check(label + ": opaque crossfade never exposes wallpaper", all(abs(r + b - 255) <= 3 and g < 3
        for r, g, b in patch), patch)
    t.check(label + ": content stays at natural scale", all(abs(n - 56) <= 1 for n in icons), icons)
    t.check(label + ": snapshot orientation stays upright", all(r > 250 and g > 250 and b < 3
        for r, g, b in markers), markers)


def verify(label, series, before, smaller, bounce=True):
    for wid, first in before.items():
        track = [(stamp, frames(s)[wid]) for stamp, s in series if wid in frames(s)]
        widths = [first["width"]] + [f["width"] for _, f in track]
        active = [f for _, f in track if "presentation" in f]
        t.check(f"{label} widget {wid}: sampled intermediate frames", len(active) >= 5 and
                len({round(w, 1) for w in widths}) >= 6, widths)
        sign = -1 if smaller else 1
        directed = [sign * w for w in widths]
        peak = max(range(len(directed)), key=directed.__getitem__)
        if bounce:
            t.check(f"{label} widget {wid}: overshoots once, then settles without wobble",
                directed[peak] > directed[-1] + .5 and
                all(b >= a - .05 for a, b in zip(directed[:peak], directed[1:peak+1])) and
                all(b <= a + .05 for a, b in zip(directed[peak:], directed[peak+1:])), widths)
        else:
            t.check(f"{label} widget {wid}: zero bounce is monotonic", all(
                b >= a - .05 for a, b in zip(directed, directed[1:])), widths)
        edge = first["x"] if first["x"] < 100 else first["x"] + first["width"]
        edges = [f["x"] if first["x"] < 100 else f["x"] + f["width"] for _, f in track]
        t.check(f"{label} widget {wid}: rail edge fixed", max(abs(e - edge) for e in edges) < .05, edges)
        end = widths[-1]
        last_active = active[-1]["width"] if active else 0
        t.check(f"{label} widget {wid}: no final snap", abs(last_active - end) < 3, (last_active, end))
        t.check(f"{label} widget {wid}: exact final size", abs(end - (96 if smaller else 320)) < .001, end)
        running = [stamp for stamp, f in track if "presentation" in f and not f["presentation"]["waiting"]]
        t.check(f"{label} widget {wid}: bounded animation duration", running and
                ((.30 < running[-1] - running[0] < .41) if bounce else (.16 < running[-1] - running[0] < .26)),
                running[-1] - running[0] if running else running)


def entry_paths():
    """Observe real rail requests and the rendered frames, including an early drop."""
    for path in ("center-cycle", "periphery-cycle", "widget-cycle", "double-tap", "periphery-double-tap",
                 "collapsed-double-tap", "double-tap-mode-switch",
                 "push-left", "push-right", "coast-left", "coast-right", "held-drag", "early-drop", "collapsed-drop", "halo-drop", "esc-return", "load-recovery"):
        if os.environ.get("SCOTTLAND_TEST_ENTRY") and path != os.environ["SCOTTLAND_TEST_ENTRY"]:
            continue
        title = "Entry " + path
        if path.startswith("collapsed") or t.ipc.call("scottland/desktop-model")["collapsed"]:
            t.launch("Entry mode seed", "left", 160)
        process = subprocess.Popen(["foot", "-o", "colors-dark.background=ff0000", "-T", title,
            "-W", "40x8", "sh", "-c", "exec sleep 600"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        t.owned.append((title, process)); t.wait_for(lambda: t.app(title)); time.sleep(.5)
        if t.ipc.call("scottland/desktop-model")["collapsed"] != path.startswith("collapsed"):
            t.toggle(); time.sleep(.5)
        if path.startswith("periphery"):
            t.drag_begin(t.app(title), t.screen["width"] * .2, 330); t.drag_end()
        if path == "widget-cycle":
            t.drag_begin(t.app(title), t.screen["width"] - 6, 330); t.drag_end()
        if path == "load-recovery":
            # Exercise the recovery entry with stock move input while Scottland
            # is unloaded in THIS private compositor, then load the same build.
            plugins = t.ipc.call("wayfire/get-config-option", {"option": "core/plugins"})["value"]
            without = " ".join(p for p in plugins.split() if p != "scottland" and "/libscottland" not in p)
            stock_move = t.ipc.call("wayfire/get-config-option", {"option": "move/activate"})["value"]
            t.ipc.call("wayfire/set-config-options", {"core/plugins": without, "move/activate": "<super> BTN_LEFT"})
            try:
                g = next(v["geometry"] for v in t.ipc.call("window-rules/list-views") if v["title"] == title)
                t.move(g["x"] + g["width"] / 2, g["y"] + g["height"] / 2)
                t.key("LEFTMETA", True)
                t.ipc.call("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "press"})
                t.move(t.screen["width"] - 6, 330)
                t.ipc.call("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "release"})
                t.key("LEFTMETA", False)
            finally:
                t.ipc.call("wayfire/set-config-options", {"core/plugins": plugins, "move/activate": stock_move})
            first = t.app(title).get("scene_frame", t.app(title).get("frame", g))
        elif path.startswith(("push-", "coast-")):
            sign = -1 if path.endswith("left") else 1
            x, y = t.screen["width"] / 2, 330
            t.drag_begin(t.app(title), x, y); time.sleep(.12); t.drag_end()
            time.sleep(.4)
            first = t.app(title).get("scene_frame", t.app(title)["frame"])
            if path.startswith("push-"):
                t.ipc.call("wayfire/set-config-options", {"scottland/key_impulse": 1400.0})
                t.key("LEFTALT", True)
                t.wait_for(lambda: t.ipc.call("scottland/hints")["active"])
                t.key("LEFT" if sign < 0 else "RIGHT", True)
                t.key("LEFT" if sign < 0 else "RIGHT", False)
                t.key("LEFTALT", False)
                t.ipc.call("wayfire/set-config-options", {"scottland/key_impulse": 335.0})
            else:
                t.move(x,y); t.key("LEFTMETA", True)
                t.ipc.call("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "press"})
                for n in range(1,7):
                    time.sleep(.015); t.move(x + sign * 130 * n / 6,y)
                t.ipc.call("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "release"})
                t.key("LEFTMETA", False)
            # Capture the contact image before startup hands it to the card.
            t.wait_for(lambda: t.app(title)["widgetized"])
            first = t.app(title).get("scene_frame", t.app(title)["frame"])
        elif path == "esc-return":
            t.drag_begin(t.app(title), t.screen["width"] - 6, 330); t.drag_end()
            time.sleep(2.6)
            t.drag_begin(t.card(title), t.screen["width"] / 2, 330); t.drag_end()
            first = t.app(title).get("scene_frame", t.app(title)["frame"])
            f = t.app(title)["frame"]
            t.move(f["x"] + f["width"] / 2, f["y"] + f["height"] / 2)
            t.key("LEFTMETA", True)
            t.ipc.call("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "press"})
            t.move(t.screen["width"] / 2 + 15, 330)
            t.key("ESC", True); t.key("ESC", False)
            t.ipc.call("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "release"})
            t.key("LEFTMETA", False)
        elif "cycle" in path or "double-tap" in path:
            t.key("LEFTALT", True)
            t.wait_for(lambda: t.ipc.call("scottland/hints")["active"])
            label = next(h["hint"] for h in t.ipc.call("scottland/hints")["hints"]
                         if h["window"] == t.app(title)["id"])
            def hint():
                for letter in label:
                    t.key(letter.upper(), True); t.key(letter.upper(), False)
            hint()
            time.sleep(.07 if "double-tap" in path else .65)
            if path == "widget-cycle":
                hint(); time.sleep(.65)
            first = t.app(title).get("scene_frame", t.app(title)["frame"])
            hint()
            if path == "double-tap-mode-switch":
                t.key("LEFTALT", False)
                t.toggle()
        else:
            f = t.app(title)["frame"]
            t.move(f["x"] - 5 if path == "halo-drop" else f["x"] + f["width"] / 2,
                   f["y"] + f["height"] / 2)
            if path != "halo-drop": t.key("LEFTMETA", True)
            t.ipc.call("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "press"})
            t.move(t.screen["width"] - 50, 330)
            time.sleep(.3)
            first = t.app(title).get("scene_frame", t.app(title)["frame"])
            t.move(t.screen["width"] - 6, 330)
            if path != "held-drag":
                t.ipc.call("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "release"})
                t.key("LEFTMETA", False)
        track = []; history = []; field_samples = []; blends = []
        start = time.monotonic()
        # PNG encoding and pixel inspection must not set the geometry sampling rate.
        # Use an independent IPC connection so short circle-eased transitions are
        # sampled throughout, even on a slower machine. Pixel assertions stay separate.
        stop_sampling = threading.Event()
        sampling_errors = []
        def sample_geometry():
            connection = t.Ipc()
            try:
                while not stop_sampling.is_set():
                    snapshot = connection.call("scottland/layout-state")
                    track.append((time.monotonic() - start, snapshot))
                    # Geometry and goo must be sampled independently of PNG encoding.
                    if goo_enabled:
                        for v in snapshot["views"]:
                            if v["hidden"] or not (v["title"] == title or v["title"].endswith(": " + title)): continue
                            f = v.get("scene_frame", v["frame"])
                            x = round(f["x"] + f["width"] / 2); y = round(f["y"] + f["height"] / 2)
                            if 0 <= x < t.screen["width"] and 0 <= y < t.screen["height"]:
                                field = connection.call("scottland/goo-state", {"x": x, "y": y})["screens"][0]
                                field_samples.append((time.monotonic() - start, f, x, y, field["window_distance"]))
                    stop_sampling.wait(.006)
            except Exception as error:
                sampling_errors.append(error)
            finally:
                connection.sock.close()
        goo_enabled = t.ipc.call("scottland/goo-state")["enabled"]
        sampler = threading.Thread(target=sample_geometry)
        sampler.start()
        try:
            i = 0
            while i < 42 or (path.startswith(("push-", "coast-")) and time.monotonic() - start < 8
                    and (not t.card(title) or state()["widget_transition_count"])):
                i += 1
                stamp_before = time.monotonic() - start
                before = state()
                history.append((stamp_before, before))
                image = screenshot(path + "-entry-" + str(i))
                blends.append(image.red_blend())
                after = state()
                history.append((time.monotonic() - start, after))
                for v in before["views"]:
                    if v["hidden"] or not (v["title"] == title or v["title"].endswith(": " + title)): continue
                    f = v.get("scene_frame", v["frame"])
                    # Probe the goo at the currently presented frame center.
                    x = round(f["x"] + f["width"] / 2); y = round(f["y"] + f["height"] / 2)
                    if 0 <= x < t.screen["width"] and 0 <= y < t.screen["height"]:
                        if t.ipc.call("scottland/goo-state")["enabled"]:
                            fresh = next(w for w in after["views"] if w["id"] == v["id"])
                            ff = fresh.get("scene_frame", fresh["frame"])
                            x = round(ff["x"] + ff["width"] / 2); y = round(ff["y"] + ff["height"] / 2)
                            field = t.ipc.call("scottland/goo-state", {"x": x, "y": y})["screens"][0]
                            field_samples.append((time.monotonic() - start, ff, x, y, field["window_distance"]))
        finally:
            stop_sampling.set()
            sampler.join()
        if sampling_errors: raise sampling_errors[0]
        samples[path] = track
        t.key("LEFTALT", False)
        if path == "held-drag": t.drag_end()
        time.sleep(.8)
        final = t.card(title)["frame"]
        settled_image = screenshot(path + "-settled")
        t.check(path + ": no app pixels remain outside the settled card",
                settled_image.red_source_pixels() == 0, settled_image.red_source_pixels())
        candidates = [v.get("scene_frame", v["frame"]) for _, s in track for v in s["views"]
            if not v["hidden"] and (v["title"] == title or v["title"].endswith(": " + title))]
        # Height differs even when a peripheral window and expanded card have similar widths.
        low, high = sorted((first["height"], final["height"]))
        middle = [f for f in candidates if low + 2 < f["height"] < high - 2]
        t.check(path + ": visible intermediate shape between window and card",
                len({round(f["height"], 1) for f in middle}) >= 3,
                {"first": first, "final": final, "heights": [f["height"] for f in candidates]})
        t.check(path + ": screenshots retain the app image in the crossfade",
                any(blends), blends)
        t.check(path + ": startup and handoff never leave the app invisible",
                all(any(not v["hidden"] and (v["title"] == title or v["title"].endswith(": " + title))
                        for v in s["views"]) for _, s in track))
        if field_samples:
            # Goo reflects the last output repaint; IPC can see a newer timer step.
            # Compare its distance with recent real frames, including the source
            # immediately before the transfer, rather than assuming a shared clock.
            errors = []
            for stamp, f, x, y, distance in field_samples:
                if not low + 2 < f["height"] < high - 2: continue
                recent = [v.get("scene_frame", v["frame"]) for at, s in track + history if stamp - .07 <= at <= stamp
                    for v in s["views"] if not v["hidden"] and
                    (v["title"] == title or v["title"].endswith(": " + title))]
                def signed(r):
                    dx = abs(x - r["x"] - r["width"] / 2) - r["width"] / 2
                    dy = abs(y - r["y"] - r["height"] / 2) - r["height"] / 2
                    return math.hypot(max(dx, 0), max(dy, 0)) + min(max(dx, dy), 0)
                distances = [signed(r) for r in recent]
                if distances: errors.append(max(min(distances) - distance, distance - max(distances), 0))
            t.check(path + ": goo follows the intermediate frame", errors and max(errors) < 12, errors)
        t.check(path + ": app alive behind final widget", t.app(title) and t.app(title)["hidden"])
        t.cleanup()


def entry_disappearance():
    title = "Entry disappearing card"
    t.launch(title, rail=None)
    t.key("LEFTALT", True)
    t.wait_for(lambda: t.ipc.call("scottland/hints")["active"])
    label = next(h["hint"] for h in t.ipc.call("scottland/hints")["hints"] if h["window"] == t.app(title)["id"])
    for _ in range(2):
        for letter in label: t.key(letter.upper(), True); t.key(letter.upper(), False)
    card = t.wait_for(lambda: t.card(title) and t.card(title)["frame"].get("presentation") and t.card(title))
    t.ipc.call("window-rules/close-view", {"id": card["id"]})
    t.key("LEFTALT", False)
    time.sleep(.5)
    t.check("WG5 card vanished mid-morph: app survives and is shown", t.app(title) and not t.app(title)["hidden"])
    t.check("WG5 card vanished mid-morph: snapshots released", state()["widget_transition_count"] == 0)
    t.cleanup()


def entry_lifecycle():
    """Multiple arrivals share a timer; reload hands off an entry still in flight."""
    titles = ["Concurrent entry left", "Concurrent entry right"]
    for title in titles: t.launch(title, rail=None)
    t.key("LEFTALT", True)
    t.wait_for(lambda: t.ipc.call("scottland/hints")["active"])
    labels = {h["window"]: h["hint"] for h in t.ipc.call("scottland/hints")["hints"]}
    for title in titles:
        for _ in range(2):
            for letter in labels[t.app(title)["id"]]:
                t.key(letter.upper(), True); t.key(letter.upper(), False)
    series = sample("concurrent-entry", 2)
    t.key("LEFTALT", False)
    for title in titles:
        active = [(stamp, v["frame"]) for stamp, s in series for v in s["views"]
            if v["widget"] and v["title"].endswith(": " + title) and
            "presentation" in v["frame"] and not v["frame"]["presentation"]["waiting"]]
        t.check(title + ": full animation even when another card arrives on the same tick",
            len(active) >= 5 and active[-1][0] - active[0][0] >= .16, active)
    plugins = t.ipc.call("wayfire/get-config-option", {"option": "core/plugins"})["value"]
    without = " ".join(p for p in plugins.split() if p != "scottland" and "/libscottland-" not in p)
    ids = [t.app(title)["id"] for title in titles]
    t.ipc.call("wayfire/set-config-options", {"core/plugins": without})
    # Client exit and Wayfire's close animation are asynchronous; WG5 allows
    # three seconds to close plus two seconds for process termination.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        plain = t.ipc.call("window-rules/list-views")
        if not any(v["title"].startswith("Scottland widget") for v in plain): break
        time.sleep(.05)
    plain = t.ipc.call("window-rules/list-views")
    t.check("entry unload: both apps survive", all(any(v["id"] == wid for v in plain) for wid in ids))
    t.check("entry unload: both widget windows close", not any(v["title"].startswith("Scottland widget") for v in plain), plain)
    t.ipc.call("wayfire/set-config-options", {"core/plugins": plugins})
    card = t.wait_for(lambda: t.card(titles[0]) and t.card(titles[0])["frame"].get("presentation") and t.card(titles[0]))
    # This reload is strictly inside the private test compositor: the real one (receipt, handover,
    # acknowledgment), since a plugin swapped in without a receipt carries nothing over.
    reload_session(timeout=20)
    time.sleep(1)
    t.check("entry reload: mapped card keeps its identity and hidden live app",
        t.card(titles[0]) and t.card(titles[0])["id"] == card["id"] and
        t.app(titles[0]) and t.app(titles[0])["hidden"])
    t.check("entry reload: no snapshots retained", state()["widget_transition_count"] == 0)
    deadline = time.monotonic() + 5
    while True:
        diagnostic = subprocess.check_output(["gdbus", "call", "--session", "--dest", "org.scottland.Widgets",
            "--object-path", "/org/scottland/Widgets", "--method", "org.scottland.Diagnostics.Snapshot"], text=True)
        audit = t.ipc.call("scottland/audit-model", {"service": json.loads(ast.literal_eval(diagnostic)[0])})
        if audit["ok"] or time.monotonic() >= deadline: break
        time.sleep(.1)
    t.check("entry reload: lifecycle and scene agree", audit["ok"], audit)
    t.cleanup()


try:
    t.ipc.call("wayfire/set-config-options", {"scottland/sounds": False})
    if "--lifecycle-only" in sys.argv:
        entry_lifecycle()
        sys.exit(bool(t.failures))
    entry_paths()
    if "--baseline" not in sys.argv: entry_disappearance()
    if "--entry-only" in sys.argv: sys.exit(bool(t.failures))
    entry_lifecycle()
    t.launch("Morph left with a title long enough for maximum width", "left", 230)
    t.launch("Morph right with a title long enough for maximum width", "right", 500)
    t.launch("Morph neighbor with a title long enough for maximum width", "right", 625)
    t.move(t.screen["width"] / 2, 60)
    time.sleep(1)
    before = frames(state())
    t.toggle()
    collapsed = sample("collapse")
    verify("collapse", collapsed, before, True)
    t.check("all widgets animate concurrently", any(s["widget_transition_count"] == 3 for _, s in collapsed))
    before = frames(state())
    t.toggle()
    expanded = sample("expand")
    verify("expand", expanded, before, False)

    # Live option through the settings-facing CLI: 0 preserves the monotonic transition.
    subprocess.check_call(["core/libexec/scottland-ctl", "set", "widget_bounce", "0"])
    for smaller in (True, False):
        before = frames(state())
        t.toggle()
        verify("bounce disabled", sample("zero-" + str(smaller)), before, smaller, bounce=False)
    subprocess.check_call(["core/libexec/scottland-ctl", "set", "widget_bounce", "0.04"])

    # Real pointer hover and leave exercise the same spring without changing collapse intent.
    t.toggle(); settle()
    title = "Morph left with a title long enough for maximum width"
    wid = t.card(title)["id"]
    before = {wid: frames(state())[wid]}
    f = before[wid]
    t.move(f["x"] + 40, f["y"] + 48)
    verify("hover peek", sample("hover-peek", 1.0), before, False)
    before = {wid: frames(state())[wid]}
    t.move(t.screen["width"] / 2, 60)
    verify("hover leave", sample("hover-leave", 1.0), before, True)
    focus_title = "Bounce attention focus"
    t.launch(focus_title, rail=None)
    before = {wid: frames(state())[wid]}
    t.ipc.call("scottland/attention", {"window": t.app(title)["id"],
        "attention": True, "source": "bounce-test"})
    verify("attention peek", sample("attention-peek"), before, False)
    # Attention expiry is a production timer; capture across its five-second deadline.
    time.sleep(3.8)
    before = {wid: frames(state())[wid]}
    verify("attention expiry", sample("attention-expiry", 1.2), before, True)
    t.ipc.call("scottland/attention", {"window": t.app(title)["id"],
        "attention": False, "source": "bounce-test"})
    t.ipc.call("window-rules/close-view", {"id": t.app(focus_title)["id"]})
    t.toggle(); settle()

    link = t.widgets()[0]
    wid = t.card(link["title"])["id"]
    for smaller in (True, False):
        before = {wid: frames(state())[wid]}
        t.ipc.call("scottland/widget-action", {"id": str(link["id"]), "action": "minimize"})
        verify("IPC/menu minimize" if smaller else "IPC/menu expand",
               sample("ipc-minimize-" + str(smaller)), before, smaller)

    # The fallback band follows each animated frame independently (A10/WG16).
    goo_on = t.ipc.call("scottland/goo-state")["enabled"]
    t.ipc.call("wayfire/set-config-options", {"scottland/goo": False})
    title = "Morph right with a title long enough for maximum width"
    history = [t.card(title)["frame"]]
    t.toggle()
    bands = []
    for i in range(9):
        before_shot = t.card(title)["frame"]
        image = screenshot("halo-band-" + str(i))
        after_shot = t.card(title)["frame"]
        # IPC and grim do not share an animation clock. Bracket the screenshot
        # with geometry, including the previous repaint, and locate the band's
        # outer edge instead of sampling a point it may already have passed.
        recent = history[-1:] + [before_shot, after_shot]
        left = min(body_left(f) - f["thickness"] for f in recent)
        right = max(body_left(f) - f["thickness"] for f in recent)
        y = round(before_shot["y"] + before_shot["height"] / 2)
        background = image.getpixel((round(left - 55), y))
        edge = next((x for x in range(round(left - 3), round(right + 4))
                     if image.getpixel((x, y)) != background), None)
        bands.append(edge is not None and left - 3 <= edge <= right + 3)
        history.extend([before_shot, after_shot])
    t.check("independent halo band follows the animated widget frame", all(bands) and
            max(f["width"] for f in history) - min(f["width"] for f in history) > 40, bands)
    settle()
    t.toggle()
    settle()
    t.ipc.call("wayfire/set-config-options", {"scottland/goo": goo_on})

    right = "Morph right with a title long enough for maximum width"
    old = t.card(right)["frame"]
    edge = round(old["x"] + old["width"])
    top = round(old["y"])
    background = screenshot("card-expanded").getpixel((edge - 110, top + 8))
    t.toggle()
    t.wait_for(lambda: 170 < t.card(right)["frame"]["width"] < 240)
    f = t.card(right)["frame"]
    image = screenshot("card-collapsing")
    strip = [image.getpixel((x, top + 8)) for x in range(edge - 104, edge - 85)]
    t.check("card's former inner corner stays opaque inside the changing frame", all(
        max(abs(a-b) for a,b in zip(pixel, background)) <= 3 for pixel in strip), (background, strip))
    f = t.card(right)["frame"]
    card_id = t.card(right)["id"]
    t.move(f["x"] + 15, f["y"] + 48)
    # stipc queues pointer motion through the compositor; let its hit-test
    # snapshot catch up before asserting ownership during the animated clip.
    deadline = time.monotonic() + .4
    cursor_view = state()["cursor_view"]
    while cursor_view != card_id and time.monotonic() < deadline:
        time.sleep(.02)
        cursor_view = state()["cursor_view"]
    t.check("snapshot-only visible area blocks click-through", cursor_view == card_id,
            {"cursor_view": cursor_view, "card_id": card_id, "frame": t.card(right)["frame"]})
    t.wait_for(lambda: t.card(right)["frame"]["width"] < 125)
    t.move(old["x"] + 5, old["y"] + 48)
    t.check("input excludes the part of the old frame already clipped away", state()["cursor_view"] != t.card(right)["id"])
    settle(); t.toggle(); settle()
    t.move(t.screen["width"] / 2, 60)

    t.toggle()
    t.wait_for(lambda: any(120 < f["width"] < 270 for f in frames(state()).values()))
    at_reverse = frames(state())
    t.toggle()
    reversal = sample("reversal")
    for wid, f in at_reverse.items():
        track = [frames(s)[wid]["width"] for _, s in reversal]
        t.check(f"reversal widget {wid}: starts at shown width", abs(track[0] - f["width"]) < 20, track)
        t.check(f"reversal widget {wid}: bounces and reaches expanded exactly", max(track) > 320 and abs(track[-1] - 320) < .001, track)

    # More than one reversal must flatten the previous composition, not recapture the surface.
    for _ in range(4):
        t.toggle(); time.sleep(.065)
    settle()
    t.check("four fast reversals converge", all(abs(f["width"] - 320) < .001 for f in frames(state()).values()))
    count = state()["widget_transition_steps"]
    time.sleep(.5)
    t.check("settled morph releases snapshots and stops ticking", state()["widget_transition_count"] == 0 and
            state()["widget_transition_steps"] == count)

    # Temporary presentation uses the same mechanism without changing either collapsed intent.
    t.toggle(); settle()
    link = t.widgets()[0]
    t.ipc.call("scottland/widget-action", {"id": str(link["id"]), "action": "test-peek", "peek": True})
    peek = sample("peek")
    current = next(w for w in t.widgets() if w["id"] == link["id"])
    t.check("peek expands without changing collapsed intent or mode", not current["minimized"] and current["collapsed"] and
            t.ipc.call("scottland/desktop-model")["collapsed"])
    t.ipc.call("scottland/widget-action", {"id": str(link["id"]), "action": "test-peek", "peek": False})
    settle()
    t.check("ending peek returns to collapsed intent", all(abs(f["width"] - 96) < .001 for f in frames(state()).values()))

    # A held widget drag and its drop glide retain their own lifecycle and position resources.
    title = "Morph right with a title long enough for maximum width"
    t.set_widget_mode("expanded")  # the real tap below goes expanded -> collapsed (WG16)
    settle()
    t.drag_begin(t.card(title), t.screen["width"] - 10, 350)
    t.key("M", True); t.key("M", False)
    time.sleep(.12)
    t.ipc.call("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "release"})
    t.key("LEFTMETA", False)
    time.sleep(.04)
    t.toggle()  # collapsed -> expanded during the drop glide
    sample("drag-glide")
    t.check("toggle during held drag and glide keeps widget docked", t.card(title) and not t.card(title)["hidden"] and
            not t.card(title)["preview"] and abs(t.card(title)["frame"]["width"] - 320) < .001)
    t.toggle(); settle()  # collapsed, as the sequence below expects

    # Focus mode is entered with real input; the toggles may finish while widgets are hidden.
    t.launch("Morph fullscreen focus", rail=None)
    t.key("LEFTMETA", True); t.key("F", True); t.key("F", False); t.key("LEFTMETA", False)
    time.sleep(.4)
    t.toggle(); sample("fullscreen")
    t.check("fullscreen keeps toggled widgets hidden", all(v["hidden"] for v in t.views() if v["widget"]))
    t.key("LEFTMETA", True); t.key("F", True); t.key("F", False); t.key("LEFTMETA", False)
    time.sleep(.5)
    t.check("leaving fullscreen restores final expanded presentation", all(not v["hidden"] and
        abs(v["frame"]["width"] - 320) < .001 for v in t.views() if v["widget"]))

    # Close in flight, then reload in flight: no callbacks retain a dead widget/plugin copy.
    t.toggle(); time.sleep(.08)
    t.ipc.call("window-rules/close-view", {"id": t.card(title)["id"]})
    settle()
    t.check("closing during a morph releases its resources", state()["widget_transition_count"] == 0 and not t.card(title))
    t.toggle(); time.sleep(.08)
    reload_session(timeout=20)
    time.sleep(1)
    t.check("headless reload during a morph survives and releases snapshots", state()["widget_transition_count"] == 0)

    t.cleanup(); t.owned.clear()
    for kind in ("delayed", "taller", "same-size", "unresponsive"):
        title = "Morph " + kind
        t.launch(title, "right", t.screen["height"] - 3 if kind == "taller" else 350, "scottland-morph-fixture")
        t.move(t.screen["width"] / 2, 60)
        time.sleep(.5)
        if kind == "taller" and t.minimized(title):
            t.toggle(); settle()
        start = t.card(title)["frame"]
        triggered = time.monotonic()
        t.toggle()
        time.sleep(.07)
        held = t.card(title)["frame"]
        t.check(kind + ": holds old frame until client applies buffer", held.get("presentation", {}).get("waiting") and
                abs(held["width"] - start["width"]) < .001)
        if kind == "taller":
            track = [frames(s)[t.card(title)["id"]] for _, s in sample("height-change")]
            tops = [start["y"]] + [f["y"] for f in track]
            heights = [f["height"] for f in track]
            t.check("height response preserves frame position without a configure jump", max(abs(b-a) for a,b in zip(tops, tops[1:])) < 15, tops)
            t.check("height response animates to exact size", len(set(round(h, 1) for h in heights)) > 6 and heights[-1] == 160, heights)
        elif kind != "unresponsive":
            t.wait_for(lambda: not t.card(title)["frame"].get("presentation", {"waiting": True})["waiting"])
            response = t.card(title)["frame"]["presentation"]
            t.check(kind + ": applied buffer starts morph before fallback", not response["fallback"], response)
            fixture_pixels(title, kind)
        settle()
        end = t.card(title)["frame"]
        t.check(kind + ": settles to actual client size with no retained snapshot", "presentation" not in end and
                abs(end["width"] - (96 if kind in ("delayed", "taller") else 320)) < .001)
        if kind == "unresponsive":
            t.check("unresponsive: bounded fallback completes", time.monotonic() - triggered < .85)
        if kind == "delayed":
            # A reversal must preserve the pixels of the in-flight premultiplied blend.
            t.toggle()
            t.wait_for(lambda: .25 < t.card(title)["frame"].get("presentation", {}).get("fade", -1) < .6)
            frame = t.card(title)["frame"]
            at = (round(frame["x"] + frame["width"] - 5), round(frame["y"] + 48))
            before_pixel = screenshot("reverse-before").getpixel(at)
            t.toggle()
            after_pixel = screenshot("reverse-after").getpixel(at)
            t.check("reversal preserves currently blended pixels", max(abs(a-b) for a,b in zip(before_pixel, after_pixel)) < 65,
                    (before_pixel, after_pixel))
            settle()
        t.cleanup(); t.owned.clear()
    if t.ipc.call("scottland/goo-state")["enabled"]:
        # Infer the actual field's inner edge from its signed union distance, away
        # from rounded corners. GO16 follows the card's current alpha bounds,
        # including WG10's transparent badge reservation. This catches sampling the client's final rect
        # instead of the compositor's animated presentation (including reversals).
        if t.ipc.call("scottland/desktop-model")["collapsed"]:
            t.toggle(); settle()
        title = "Goo morph with a title long enough for maximum width"
        t.launch(title, "right", 350)
        # A toggle with no widgets leaves mode unchanged. WG19 can make an icon-mode
        # widget look expanded on attention, so establish intent after the fixture exists.
        t.move(t.screen["width"] / 2, 60)
        if t.ipc.call("scottland/desktop-model")["collapsed"]:
            t.toggle(); settle()
        t.check("goo fixture starts with expanded intent", not t.widgets()[0]["collapsed"])
        # Asking a focused widget for attention is already answered (WG15).
        # Put keyboard focus on a separate window well away from the field probe.
        focus = t.launch("Goo morph focus fixture", rail=None)
        t.ipc.call("window-rules/configure-view", {"id": focus["id"],
            "geometry": {"x": 0, "y": 0, "width": 100, "height": 60}})
        time.sleep(.4)
        focus_frame = t.app("Goo morph focus fixture")["frame"]
        t.move(focus_frame["x"] + focus_frame["width"] / 2,
            focus_frame["y"] + focus_frame["height"] / 2)
        t.ipc.call("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "press"})
        t.ipc.call("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "release"})
        t.move(t.screen["width"] / 2, 60)
        t.ipc.call("wayfire/set-config-options", {"scottland/attention_color": "#20ff40ff"})
        link = t.widgets()[0]
        requested = t.ipc.call("scottland/attention", {"window": int(link["id"]),
            "attention": True, "source": "morph-test"})
        t.check("goo attention fixture is unfocused", not requested.get("in_front", False))
        time.sleep(1)
        for label in ("collapse", "expand", "reversal"):
            previous_frames = []
            previous_frames.append(t.card(title)["frame"])
            t.toggle()
            if label == "reversal":
                time.sleep(.09)
                previous_frames.append(t.card(title)["frame"])
                t.toggle()
            track = []
            captured = False
            start = time.monotonic()
            while time.monotonic() - start < .6:
                f = t.card(title)["frame"]
                x = t.screen["width"] / 2
                field = t.ipc.call("scottland/goo-state", {"x": x,
                    "y": f["y"] + f["height"] / 2})["screens"][0]
                # Timestamp the field read, not the two preceding IPC requests;
                # under compositor load that request time can exceed a render frame.
                stamp = time.monotonic()
                edge = x + field["window_distance"]
                track.append({"stamp": stamp, "frame": f, "field_edge": edge})
                if not captured and 140 < f["width"] < 270:
                    screenshot("goo-" + label + "-mid")
                    captured = True
                time.sleep(.006)
            active = [v for v in track if 110 < v["frame"]["width"] < 300]
            errors = [abs(v["field_edge"] - body_left(v["frame"])) for v in active]
            # IPC sees timer geometry before the next output repaint. Compare the
            # field with the recent presentation history, allowing up to 50 ms of
            # render scheduling, rather than treating IPC as a synchronized frame.
            history_errors = []
            for v in active:
                recent = [body_left(p["frame"]) for p in track
                    if v["stamp"] - .05 <= p["stamp"] <= v["stamp"]]
                if v["stamp"] - start <= .05:
                    recent.extend(body_left(f) for f in previous_frames)
                history_errors.append(max(min(recent) - v["field_edge"],
                    v["field_edge"] - max(recent), 0))
            t.check("goo " + label + ": field follows intermediate frame", len(active) >= 4 and
                max(errors, default=999) < 45 and max(history_errors, default=999) < 1,
                {"current_frame": errors, "recent_frames": history_errors})
            last = track[-1]
            t.check("goo " + label + ": field settles at exact final frame",
                abs(last["field_edge"] - body_left(last["frame"])) < .1, last)
            samples["goo-" + label] = track
        f = t.card(title)["frame"]
        dye = t.ipc.call("scottland/goo-state", {"x": f["x"] + f["width"] / 2,
            "y": f["y"] - 7})["screens"][0]
        t.check("goo morph retains the widget's attention dye", dye["green"] > dye["red"] + .15 and
            dye["green"] > dye["blue"] + .15, dye)
        t.ipc.call("wayfire/set-config-options", {"scottland/goo": False})
        t.check("goo switches off live after morph", not t.ipc.call("scottland/goo-state")["enabled"])
        t.ipc.call("wayfire/set-config-options", {"scottland/goo": True})
        t.check("goo switches on live after morph", t.ipc.call("scottland/goo-state")["enabled"])
finally:
    (out / "morph-samples.json").write_text(json.dumps(samples))
    t.cleanup()
    print(f"widget morph regressions: {t.passes} passed, {t.failures} failed", flush=True)
sys.exit(bool(t.failures))
