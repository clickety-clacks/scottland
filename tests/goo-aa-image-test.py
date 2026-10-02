#!/usr/bin/env python3
"""Check GO13's device-pixel edge width in paired goo-visual-fixture captures.

Usage: goo-aa-image-test.py ARTIFACT_ROOT
Expects visual-{before,after}-{1,1.5,2}/bridge.{png,json}. These are rendered
screenshots, not a reimplementation of the shader. The fixture disables noise
and waves; a straight neutral edge lets us measure coverage before its relief
shading falls away. Curved/bridge/film contours still need visual inspection.
"""
import json
from pathlib import Path
import sys

import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf

root = Path(sys.argv[1])
results = []
for scale in (1, 1.5, 2):
    widths = {}
    frames = []
    for version in ("before", "after"):
        directory = root / f"visual-{version}-{scale:g}"
        image = GdkPixbuf.Pixbuf.new_from_file(str(directory / "bridge.png"))
        assert (image.get_width(), image.get_height()) == (1280 * scale, 720 * scale)
        frame = next(v["frame"] for v in json.loads((directory / "bridge.json").read_text())
                     if v["title"] == "visual-a")
        frames.append({k: frame[k] for k in ("x", "y", "width", "height")})
        data, stride, channels = image.get_pixels(), image.get_rowstride(), image.get_n_channels()
        x = round((frame["x"] + frame["width"] * .4) * scale)
        pixels = []
        for y in range(round((frame["y"] - 20) * scale), round((frame["y"] - 2) * scale)):
            offset = y * stride + x * channels
            pixels.append(sum(data[offset:offset + 3]) / 3)
        background, peak = pixels[0], max(pixels)
        assert peak - background > 80, (scale, version, pixels)
        rising = pixels[:pixels.index(peak) + 1]
        # Only the initial coverage ramp; relief inside the goo is not alpha AA.
        widths[version] = sum(.05 < (p - background) / (peak - background) < .95 for p in rising)
    assert frames[0] == frames[1], (scale, frames)
    assert widths["after"] == 1, (scale, widths)
    assert widths["after"] <= widths["before"], (scale, widths)
    if scale == 2:
        assert widths["after"] < widths["before"], widths
    results.append({"scale": scale, "partially_covered_device_pixels": widths})
    print(f"PASS {scale:g}x: {widths['before']} → {widths['after']} partially covered device pixels")
(root / "aa-edge-widths.json").write_text(json.dumps(results, indent=2) + "\n")
