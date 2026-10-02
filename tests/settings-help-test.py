#!/usr/bin/env python3
"""S11-S13, S1-S5 and S10 via real stipc input in a caller-owned headless session.
Run with tests/headless.sh run. Requires two outputs; screenshots and logs are retained in
build/settings-help-evidence. No live config, session or services are used.
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
art = repo / "build/settings-help-evidence"
art.mkdir(parents=True, exist_ok=True)
layout = art / "settings-home/scottland/layout.ini"
layout.parent.mkdir(parents=True, exist_ok=True)
layout.unlink(missing_ok=True)
sock = socket.socket(socket.AF_UNIX)
sock.connect(os.environ["WAYFIRE_SOCKET"])
clients = []
passed = failed = 0
log = (art / "panel.log").open("w")


def ipc(method, data=None):
    body = json.dumps(dict(method=method, data=data or {})).encode()
    sock.sendall(struct.pack("<I", len(body)) + body)
    def read(n):
        result = b""
        while len(result) < n:
            chunk = sock.recv(n-len(result))
            if not chunk:
                raise RuntimeError("compositor disconnected")
            result += chunk
        return result
    result = json.loads(read(struct.unpack("<I", read(4))[0]))
    if isinstance(result, dict) and "error" in result:
        raise RuntimeError(f"{method}: {result}")
    return result


def check(name, condition):
    global passed, failed
    print(("PASS " if condition else "FAIL ") + name, flush=True)
    passed += bool(condition)
    failed += not condition


def option(name):
    return float(ipc("wayfire/get-config-option", {"option": "scottland/" + name})["value"])


def values():
    return {k: option(k) for k in ("center_width", "rail_width", "blend_width")}


def pointer(x, y):
    ipc("stipc/move_cursor", dict(x=round(x), y=round(y)))


def button(mode):
    ipc("stipc/feed_button", dict(combo="BTN_LEFT", mode=mode))


def key(code):
    ipc("stipc/feed_key", dict(key=code, state=True))
    ipc("stipc/feed_key", dict(key=code, state=False))
    time.sleep(.09)


def click(x, y):
    pointer(x, y)
    time.sleep(.08)
    button("press")
    button("release")
    time.sleep(.15)


def drag(x, y, dx, dy=0, live_name=None, fast=False, steps=None):
    pointer(x, y)
    time.sleep(.12)
    before = option(live_name) if live_name else None
    button("press")
    steps = steps or (48 if fast else 12)
    for i in range(1, steps+1):
        pointer(x+dx*i/steps, y+dy*i/steps)
        time.sleep(.008 if fast else .04)
        if i == steps//2 and live_name:
            check(live_name + " changes before border release", abs(option(live_name)-before) > .01)
    button("release")
    time.sleep(.25)


class Pixels:
    def __init__(self, path):
        self.img = GdkPixbuf.Pixbuf.new_from_file(str(path))
        self.data = self.img.get_pixels()
    def pixel(self, x, y):
        pos = round(y)*self.img.get_rowstride()+round(x)*self.img.get_n_channels()
        return tuple(self.data[pos:pos+3])
    def text(self, x, y, width=465, height=24):
        return sum(min(c) > 145 and max(c)-min(c) < 45
                   for yy in range(round(y), round(y+height))
                   for xx in range(round(x), round(x+width))
                   for c in [self.pixel(xx, yy)])


def shot(name):
    path = art / (name + ".png")
    subprocess.run(["grim", str(path)], check=True)
    return Pixels(path)


def open_panel():
    global panel_x
    panel = subprocess.Popen(["qs", "-n", "-p", str(repo / "core/settings")],
        env=dict(os.environ, SCOTTLAND_CTL=str(repo / "core/libexec/scottland-ctl"),
                 SCOTTLAND_LAYOUT_FILE=str(layout)), stdout=log, stderr=log)
    clients.append(panel)
    time.sleep(1)
    check("settings maps", panel.poll() is None)
    # An unassigned layer panel opens on the active output; real border input changes it.
    pixels = shot("panel-position")
    panel_output = next(o for o in outputs if
                        pixels.pixel(o["geometry"]["x"]+o["geometry"]["width"]/2, 100)[2]
                        > pixels.pixel(o["geometry"]["x"]+o["geometry"]["width"]/2, 100)[0]+2)
    panel_x = panel_output["geometry"]["x"]+(panel_output["geometry"]["width"]-560)/2
    return panel


def close_panel(panel, save=False, via_button=False):
    if via_button:
        click(panel_x + (500 if save else 409), 638)
    else:
        key("KEY_ENTER" if save else "KEY_ESC")
    panel.wait(timeout=5)
    time.sleep(.15)


def geometry(screen):
    g = screen["geometry"]
    width, origin = g["width"], g["x"]
    v = values()
    center = width*(.5-v["center_width"]/200)
    rail = width*v["rail_width"]/100
    blend = min(v["blend_width"], max(0, center-rail)/2)
    return origin, width, center, rail, blend


def bands(name):
    pointer(10, 690)
    pixels = shot(name)
    for out in outputs:
        origin, width, center, rail, blend = geometry(out)
        # Pixel evidence independent of QML: find both outer gray lines, inner blue lines,
        # and the flat shaded band (distinct from the curve) between them.
        for right in (False, True):
            inner = width-center if right else center
            outer = inner+blend if right else inner-blend
            cx = origin+round(inner)
            ox = origin+round(outer)
            check(out["name"] + " band border geometry " + str(right),
                  pixels.pixel(cx, 20)[2] > pixels.pixel(cx, 20)[0]+45
                  and max(pixels.pixel(ox, 20))-min(pixels.pixel(ox, 20)) < 30)
            if blend > 10:
                mid = origin+(inner+outer)/2
                adjacent = origin+outer+(12 if right else -12)
                check(out["name"] + " softness shaded distinctly " + str(right),
                      sum(abs(a-b) for a,b in zip(pixels.pixel(mid, 20), pixels.pixel(adjacent, 20))) > 20)


try:
    outputs = sorted(ipc("window-rules/list-outputs"), key=lambda o:o["geometry"]["x"])
    assert len(outputs) == 2 and all(o["geometry"]["height"] == 720 for o in outputs)
    # Quickshell's first screen is where the panel is anchored (leftmost on this backend).
    panel_x = outputs[0]["geometry"]["x"] + (outputs[0]["geometry"]["width"]-560)/2
    initial = values()
    panel = open_panel()
    bands("01-softness-bands")
    pointer(panel_x+200, 201); time.sleep(.12)
    p = shot("02-layout-hover")
    check("hover shows first hint over slider", p.text(panel_x+38, 205) > 80)
    pointer(panel_x+200, 259); time.sleep(.12)
    p = shot("03-layout-hover-second")
    check("hover switches hint to second row", p.text(panel_x+38, 274, width=365, height=14) > 40 and p.text(panel_x+38, 215, width=365, height=14) < 10)
    pointer(panel_x+200, 318); time.sleep(.12)
    p = shot("04-layout-hover-rail")
    check("rail row explains itself", p.text(panel_x+38, 323) > 80)
    # Click focuses the stack; pointer elsewhere then keyboard selection supplies the hint.
    click(panel_x+250, 195)
    pointer(10, 690)
    key("KEY_BACKSPACE")
    pointer(panel_x+200, 195); time.sleep(.1)
    key("KEY_DOWN")
    p = shot("05-layout-keyboard-pointer-stationary")
    check("keyboard selection wins over a stationary pointer on another row",
          p.text(panel_x+38, 274, width=365, height=14) > 40
          and p.text(panel_x+38, 215, width=365, height=14) < 10)
    pointer(10, 690)
    p = shot("05-layout-keyboard")
    check("keyboard selection shows hint without pointer hover", p.text(panel_x+38, 264) > 80)
    before = option("center_width")
    key("KEY_RIGHT")
    check("keyboard still adjusts center", abs(option("center_width")-round((before+.5)*2)/2) < .01)
    key("KEY_BACKSPACE")
    key("KEY_UP")
    key("KEY_1"); key("KEY_2"); key("KEY_0")
    check("numeric entry still edits softness", option("blend_width") == 120)
    bands("06-softness-live-slider")
    key("KEY_BACKSPACE"); key("KEY_BACKSPACE"); key("KEY_BACKSPACE"); key("KEY_BACKSPACE")
    check("reset restores opening softness", option("blend_width") == initial["blend_width"])

    # Every Goo row: keyboard reaches and reveals it, then hover it without changing value.
    click(panel_x+400, 150)
    click(panel_x+240, 250)
    key("KEY_BACKSPACE")
    pointer(10, 690)
    goo_names = ["goo_thickness", "goo_reach", "goo_thinning", "goo_swell", "goo_noise", "goo_lump",
                 "goo_drift", "goo_wave_speed", "goo_wave_damp", "goo_wave_height", "goo_spread",
                 "goo_swirl", "goo_release", "goo_shine", "goo_relief",
                 "goo_overlap_film", "goo_hover_cloudiness", "goo_hover_emissivity", "goo_hover_distance"]
    for i, name in enumerate(goo_names):
        if i:
            key("KEY_DOWN")
        # revealRow pins rows below the first seven to the bottom of the 432pt viewport.
        row_y = min(220+i*59, 176+432-58)
        p = shot("goo-keyboard-"+name)
        check(name + " keyboard hint visible", p.text(panel_x+38, row_y+30, width=365, height=23) > 35)
        pointer(panel_x+220, row_y+15); time.sleep(.1)
        p = shot("goo-hover-"+name)
        check(name + " hover hint visible", p.text(panel_x+38, row_y+30, width=365, height=23) > 35)
        pointer(10, 690)
    key("KEY_RIGHT")
    check("last Goo keyboard step preserved", abs(option("goo_hover_distance")-49)<.01)
    close_panel(panel)
    check("Escape restores Layout and Goo, writes nothing", values() == initial and abs(option("goo_hover_distance")-48)<.01 and not layout.exists())

    # Both sides of all three borders, on both outputs. Pause midway to check live preview.
    panel = open_panel()
    for out in outputs:
        for setting in ("center_width", "rail_width", "blend_width"):
            for right in (False, True):
                origin, width, center, rail, blend = geometry(out)
                edge = center if setting == "center_width" else rail if setting == "rail_width" else center-blend
                x = origin+(width-edge if right else edge)
                before = option(setting)
                dx = 20 if right else -20
                if setting == "rail_width":
                    dx = -dx
                drag(x, 40, dx, live_name=setting)
                expected = before+(40/width*100 if setting == "center_width" else 20/width*100 if setting == "rail_width" else 20)
                step = .5 if setting == "center_width" else .1 if setting == "rail_width" else 1
                expected = round(expected/step)*step
                check(f"{out['name']} {setting} {'right' if right else 'left'} border updates slider value",
                      abs(option(setting)-expected) < .011)
    origin, width, center, rail, blend = geometry(outputs[1])
    drag(origin+center, 40, -40, live_name="center_width", fast=True)
    check("continuous fast pointer motion reaches its final preview", option("center_width") > initial["center_width"])
    # A long, quick drag outruns the 12 px handle: the pointer must stay captured until release.
    origin, width, center, rail, blend = geometry(outputs[1])
    before = option("center_width")
    drag(origin+center, 40, -220, steps=5)
    got, want = option("center_width"), round((before+440/width*100)/.5)*.5
    check(f"a long quick border drag follows the pointer all the way ({got} vs {want})", abs(got-want) < .011)
    drag(origin+round(width*(.5-got/200)), 40, 220, steps=5)  # put it back for the checks below
    bands("07-border-drags")
    origin, width, center, rail, blend = geometry(outputs[1])
    pointer(origin+center, 40); time.sleep(.1)
    hover = shot("07a-handle-hover")
    check("border hover highlights its handle", min(hover.pixel(origin+round(center), 40)) > 210)
    close_panel(panel, via_button=True)
    check("Cancel button restores all border previews, writes nothing", values() == initial and not layout.exists())

    panel = open_panel()
    origin, width, center, rail, blend = geometry(outputs[1])
    drag(origin+center, 40, -32, live_name="center_width")
    saved = values()
    close_panel(panel, save=True)
    check("Return from border handle saves and closes", layout.exists() and values() == saved
          and f"center_width = {saved['center_width']:.3f}" in layout.read_text())
    panel = open_panel()
    origin, width, center, rail, blend = geometry(outputs[1])
    drag(origin+center+0, 40, -32)
    close_panel(panel)
    check("reopen uses saved values; Escape after border drag restores them", values() == saved)

    # Cap and zero: actual geometry follows place(), and coincident handles remain reachable.
    panel = open_panel()
    click(panel_x+470, 190)  # softness near 300, visibly capped at half the side span
    bands("08-capped-softness")
    check("slider can exceed the effective band width", option("blend_width") > geometry(outputs[1])[-1])
    click(panel_x+20, 190)  # zero softness
    check("softness reaches zero", option("blend_width") == 0)
    unobscured = next(o for o in outputs if not o["geometry"]["x"] <= panel_x < o["geometry"]["x"]+o["geometry"]["width"])
    origin, width, center, rail, blend = geometry(unobscured)
    drag(origin+center, 600, -30, live_name="blend_width")
    check("coincident softness handle can open a zero band", option("blend_width") == 30)
    click(panel_x+20, 190)
    origin, width, center, rail, blend = geometry(outputs[1])
    before = option("center_width")
    drag(origin+center, 40, -32)
    check("coincident center handle remains reachable", option("center_width") > before)
    close_panel(panel)
    check("Escape restores preview after cap and zero tests", values() == saved)
    panel = open_panel()
    origin, width, center, rail, blend = geometry(outputs[1])
    pointer(origin+center, 40); time.sleep(.1); button("press")
    pointer(origin+center-20, 40); time.sleep(.1)
    check("held border drag changes preview", values() != saved)
    key("KEY_ESC"); panel.wait(timeout=5); button("release"); time.sleep(.1)
    check("Escape while dragging restores all opening values", values() == saved)
    # The masked overlay must pass actual clicks and typing to an ordinary app elsewhere.
    panel = open_panel()
    received = art / "click-through.txt"
    received.unlink(missing_ok=True)
    fixture = subprocess.Popen(["foot", "-c", "/dev/null", "-T", "settings-click-through", "-W", "30x8",
        "python3", "-c", "import sys,time; from pathlib import Path; Path(sys.argv[1]).write_text(input()); time.sleep(60)",
        str(received)], stdout=log, stderr=log)
    clients.append(fixture)
    for _ in range(50):
        views = ipc("scottland/layout-state")["views"]
        view = next((v for v in views if v["title"] == "settings-click-through"), None)
        if view:
            break
        time.sleep(.1)
    assert view, "click-through fixture did not map"
    ipc("window-rules/configure-view", dict(id=view["id"], geometry=dict(x=950,y=350,width=280,height=160)))
    time.sleep(.4)
    view = next(v for v in ipc("scottland/layout-state")["views"] if v["id"] == view["id"])
    output_id = next(v for v in ipc("window-rules/list-views") if v["id"] == view["id"])["output-id"]
    origin = next(o["geometry"]["x"] for o in outputs if o["id"] == output_id)
    frame = view["frame"]
    click(origin+frame["x"]+frame["width"]/2, frame["y"]+frame["height"]/2)
    key("KEY_A"); key("KEY_ENTER")
    for _ in range(30):
        if received.exists():
            break
        time.sleep(.05)
    check("shaded overlay passes clicks and keyboard focus to apps outside handles",
          received.exists() and received.read_text() == "a" and panel.poll() is None)
    shot("09-click-through")
    click(panel_x+250, 195)  # focus settings again to exercise Escape
    close_panel(panel)
    # Restore caller's session settings; the saved fixture remains evidence.
    ipc("wayfire/set-config-options", {"scottland/"+k:v for k,v in initial.items()})
finally:
    for proc in clients:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)
    log.close()
    sock.close()
print(f"{passed} passed; {failed} failed", flush=True)
raise SystemExit(bool(failed))
