#!/usr/bin/env python3
"""Frame samples come from real Super+M input, never configure-view or a test animation clock."""
import importlib.util
import json
import os
import re
from pathlib import Path
import subprocess
import shutil
import sys
import time

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


def settle():
    t.wait_for(lambda: state()["widget_transition_count"] == 0)
    time.sleep(.1)


def screenshot(name):
    path = out / (name + ".ppm")
    subprocess.run(["grim", "-t", "ppm", str(path)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    raw = path.read_bytes()
    header = re.match(rb"P6\s+(\d+)\s+(\d+)\s+255\s", raw)
    width = int(header[1])
    pixels = raw[header.end():]

    class RGB:
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


def verify(label, series, before, smaller):
    for wid, first in before.items():
        track = [(stamp, frames(s)[wid]) for stamp, s in series if wid in frames(s)]
        widths = [first["width"]] + [f["width"] for _, f in track]
        active = [f for _, f in track if "presentation" in f]
        t.check(f"{label} widget {wid}: sampled intermediate frames", len(active) >= 5 and
                len({round(w, 1) for w in widths}) >= 6, widths)
        sign = -1 if smaller else 1
        t.check(f"{label} widget {wid}: width is monotonic", all(
            sign * (b - a) >= -.05 for a, b in zip(widths, widths[1:])), widths)
        edge = first["x"] if first["x"] < 100 else first["x"] + first["width"]
        edges = [f["x"] if first["x"] < 100 else f["x"] + f["width"] for _, f in track]
        t.check(f"{label} widget {wid}: rail edge fixed", max(abs(e - edge) for e in edges) < .05, edges)
        end = widths[-1]
        last_active = active[-1]["width"] if active else 0
        t.check(f"{label} widget {wid}: no final snap", abs(last_active - end) < 3, (last_active, end))
        t.check(f"{label} widget {wid}: exact final size", abs(end - (96 if smaller else 320)) < .001, end)
        running = [stamp for stamp, f in track if "presentation" in f and not f["presentation"]["waiting"]]
        t.check(f"{label} widget {wid}: roughly 200 ms of animation", running and .16 < running[-1] - running[0] < .26,
                running[-1] - running[0] if running else running)


try:
    t.ipc.call("wayfire/set-config-options", {"scottland/sounds": False})
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

    # The fallback band follows each animated frame independently (A10/WG16).
    goo_on = t.ipc.call("scottland/goo-state")["enabled"]
    t.ipc.call("wayfire/set-config-options", {"scottland/goo": False})
    t.toggle()
    bands = []
    for i in range(9):
        f = t.card("Morph right with a title long enough for maximum width")["frame"]
        image = screenshot("halo-band-" + str(i))
        y = round(f["y"] + f["height"] / 2)
        # The band at the moving inner edge, and wallpaper safely beyond its full swell.
        bands.append(image.getpixel((round(f["x"] - 5), y)) !=
                     image.getpixel((round(f["x"] - 55), y)))
    t.check("independent halo band follows the animated widget frame", all(bands), bands)
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
    t.move(f["x"] + 15, f["y"] + 48)
    t.check("snapshot-only visible area blocks click-through", state()["cursor_view"] == t.card(right)["id"])
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
        t.check(f"reversal widget {wid}: smoothly reaches expanded", all(b >= a - .05 for a, b in zip(track, track[1:])) and abs(track[-1] - 320) < .001, track)

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
    t.drag_begin(t.card(title), t.screen["width"] - 10, 350)
    t.key("M", True); t.key("M", False)
    time.sleep(.12)
    t.ipc.call("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "release"})
    t.key("LEFTMETA", False)
    time.sleep(.04)
    t.toggle()
    sample("drag-glide")
    t.check("toggle during held drag and glide keeps widget docked", t.card(title) and not t.card(title)["hidden"] and
            not t.card(title)["preview"] and abs(t.card(title)["frame"]["width"] - 96) < .001)

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
    fresh = out / "libscottland-morph-reload.so"
    shutil.copyfile("build/libscottland.so", fresh)
    plugins = t.ipc.call("wayfire/get-config-option", {"option": "core/plugins"})["value"]
    changed = " ".join(str(fresh) if p == "scottland" or "/libscottland-" in p else p for p in plugins.split())
    mark = Path(os.environ["XDG_RUNTIME_DIR"]) / "scottland" / (os.environ["WAYLAND_DISPLAY"] + ".reloading")
    mark.touch()
    try:
        t.ipc.call("wayfire/set-config-options", {"core/plugins": changed})
        time.sleep(1)
    finally:
        mark.unlink(missing_ok=True)
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
        # from rounded corners. This catches sampling the client's final rect
        # instead of the compositor's animated presentation (including reversals).
        if t.ipc.call("scottland/desktop-model")["collapsed"]:
            t.toggle(); settle()
        title = "Goo morph with a title long enough for maximum width"
        t.launch(title, "right", 350)
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
            t.toggle()
            if label == "reversal":
                time.sleep(.09)
                t.toggle()
            track = []
            captured = False
            start = time.monotonic()
            while time.monotonic() - start < .6:
                stamp = time.monotonic()
                f = t.card(title)["frame"]
                x = t.screen["width"] / 2
                field = t.ipc.call("scottland/goo-state", {"x": x,
                    "y": f["y"] + f["height"] / 2})["screens"][0]
                edge = x + field["window_distance"]
                track.append({"stamp": stamp, "frame": f, "field_edge": edge})
                if not captured and 140 < f["width"] < 270:
                    screenshot("goo-" + label + "-mid")
                    captured = True
                time.sleep(.006)
            active = [v for v in track if 110 < v["frame"]["width"] < 300]
            errors = [abs(v["field_edge"] - v["frame"]["x"]) for v in active]
            # IPC sees timer geometry before the next output repaint. Compare the
            # field with the recent presentation history, allowing up to 50 ms of
            # render scheduling, rather than treating IPC as a synchronized frame.
            history_errors = []
            for v in active:
                recent = [p["frame"]["x"] for p in track
                    if v["stamp"] - .05 <= p["stamp"] <= v["stamp"]]
                history_errors.append(max(min(recent) - v["field_edge"],
                    v["field_edge"] - max(recent), 0))
            t.check("goo " + label + ": field follows intermediate frame", len(active) >= 4 and
                max(errors, default=999) < 45 and max(history_errors, default=999) < 1,
                {"current_frame": errors, "recent_frames": history_errors})
            last = track[-1]
            t.check("goo " + label + ": field settles at exact final frame",
                abs(last["field_edge"] - last["frame"]["x"]) < .1, last)
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
