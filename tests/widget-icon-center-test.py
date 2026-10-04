#!/usr/bin/env python3
"""WG10/WG16 pixels: the default card's icon is centered on the card's visible body.

Collapsed, the icon sits at the center of the card (the body, not the client surface, which
also reserves room for the count badge at its upper inward corner); expanded, it is vertically
centered on the body. Measured from screenshots with real Super+M input on both rails, in a
light and a dark palette, at desktop text scale 1 and 1.64. Run inside tests/headless.sh run;
the argument is an artifacts directory under build/.
"""
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

spec = importlib.util.spec_from_file_location("widget_input", Path(__file__).with_name("widget-input-test.py"))
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)
out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
palette = Path(os.environ["XDG_RUNTIME_DIR"]) / "scottland" / (os.environ["WAYLAND_DISPLAY"] + ".palette.json")
SCHEMES = {
    "dark": {"background": "#2e3440", "foreground": "#d8dee9", "muted": "#97a3ab", "accent": "#81a1c1", "alert": "#bf616a"},
    "light": {"background": "#eceff4", "foreground": "#2e3440", "muted": "#4c566a", "accent": "#5e81ac", "alert": "#bf616a"},
}
TOLERANCE = 1.0  # px


def screenshot(name):
    raw = subprocess.check_output(["grim", "-t", "ppm", "-"])
    header = re.match(rb"P6\s+(\d+)\s+(\d+)\s+255\s", raw)
    width, height = int(header[1]), int(header[2])
    pixels = raw[header.end():]
    (out / (name + ".ppm")).write_bytes(raw)
    return lambda x, y: tuple(pixels[(y * width + x) * 3:(y * width + x) * 3 + 3])


def rgb(color):
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


def measure(pixel, frame, background):
    """Bounding boxes of the card body (its background color) and of the icon inside it."""
    x0, y0 = int(frame["x"]), int(frame["y"])
    x1, y1 = int(frame["x"] + frame["width"]), int(frame["y"] + frame["height"])
    near = lambda p, c, d: sum(abs(a - b) for a, b in zip(p, c)) <= d
    body = [(x, y) for y in range(y0, y1) for x in range(x0, x1) if near(pixel(x, y), background, 6)]
    if not body:
        return None
    bx0, bx1 = min(x for x, _ in body), max(x for x, _ in body) + 1
    by0, by1 = min(y for _, y in body), max(y for _, y in body) + 1
    # Leave the body's antialiased edges and its rounded corners (radius 16, where the halo
    # shows through) out of the icon search; the icon's straight sides lie between them.
    corner = lambda x, y: (x < bx0 + 18 or x >= bx1 - 18) and (y < by0 + 18 or y >= by1 - 18)
    icon = [(x, y) for y in range(by0 + 3, by1 - 3) for x in range(bx0 + 3, bx1 - 3)
            if not corner(x, y) and not near(pixel(x, y), background, 30)]
    if not icon:
        return None
    ix0, ix1 = min(x for x, _ in icon), max(x for x, _ in icon) + 1
    iy0, iy1 = min(y for _, y in icon), max(y for _, y in icon) + 1
    return {"body": [bx0, by0, bx1, by1], "icon": [ix0, iy0, ix1, iy1],
            "dx": (ix0 + ix1) / 2 - (bx0 + bx1) / 2, "dy": (iy0 + iy1) / 2 - (by0 + by1) / 2}


def settled(collapsed):
    def ok():
        cards = [t.card(title) for title in ("icon-left", "icon-right")]
        return all(c and not c["frame"].get("presentation") and
                   ((abs(c["frame"]["width"] - 96) < .5) == collapsed) for c in cards)
    t.wait_for(ok, timeout=6)
    time.sleep(.6)  # the goo and the client's last frame


try:
    t.ipc.call("wayfire/set-config-options", {"scottland/sounds": False})
    t.set_widget_mode("expanded")
    t.launch("icon-left", rail="left", y=200)
    t.launch("icon-right", rail="right", y=420)
    t.launch("icon-focus", rail=None)  # focus away from the cards
    t.move(t.screen["width"] / 2, 40)
    report = {}
    for scheme, colors in SCHEMES.items():
        for scale in (1, 1.64):
            tmp = palette.with_suffix(".icon-center.tmp")
            tmp.write_text(json.dumps(dict(colors, scheme=scheme, text_scale=scale)))
            tmp.replace(palette)
            time.sleep(1)
            for collapsed in (True, False):
                if collapsed:
                    t.set_widget_mode("expanded")
                    settled(False)
                    t.tap_mode_key()  # expanded -> collapsed with real input
                else:
                    t.set_widget_mode("expanded")
                settled(collapsed)
                label = f"{scheme}-{scale}-{'collapsed' if collapsed else 'expanded'}"
                pixel = screenshot(label)
                for title in ("icon-left", "icon-right"):
                    rail = title.split("-")[1]
                    m = measure(pixel, t.card(title)["frame"], rgb(colors["background"]))
                    report[f"{label}-{rail}"] = m
                    name = f"{'collapsed' if collapsed else 'expanded'} icon {'centered' if collapsed else 'vertically centered'} on the card ({scheme}, text scale {scale}, {rail} rail)"
                    ok = m is not None and abs(m["dy"]) <= TOLERANCE and (not collapsed or abs(m["dx"]) <= TOLERANCE)
                    t.check("WG10 " + name, ok, m)
    (out / "measurements.json").write_text(json.dumps(report, indent=2))
finally:
    t.set_widget_mode("expanded")
    t.cleanup()
    print(f"icon center: {t.passes} passed, {t.failures} failed", flush=True)
sys.exit(bool(t.failures))
