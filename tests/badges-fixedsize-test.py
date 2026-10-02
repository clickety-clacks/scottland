#!/usr/bin/env python3
"""WG10/A6/GO8/GO12 real pointer, key and pixel regressions in a private --widgets session.
Run through headless.sh run. Artifacts stay in this checkout's build/.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import time

import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf

repo = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("widget_input", repo / "tests/widget-input-test.py")
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)
out = repo / "build/badges-fixedsize-evidence"
out.mkdir(parents=True, exist_ok=True)


def options(**values):
    t.ipc.call("wayfire/set-config-options", {"scottland/" + k: v for k, v in values.items()})


def shot(name):
    path = out / (name + ".png")
    subprocess.run(["grim", str(path)], check=True)
    (out / (name + ".json")).write_text(json.dumps(t.views(), indent=2))
    image = GdkPixbuf.Pixbuf.new_from_file(str(path))
    data = image.get_pixels()
    def pixel(x, y):
        offset = round(y)*image.get_rowstride() + round(x)*image.get_n_channels()
        return tuple(data[offset:offset+3])
    return pixel


def click(x, y):
    t.move(x, y)
    t.ipc.call("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "full"})
    time.sleep(.15)


def drag(x, y, dx, dy):
    t.move(x, y)
    time.sleep(.1)
    t.ipc.call("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "press"})
    for i in range(1, 13):
        t.move(x + dx*i/12, y + dy*i/12)
        time.sleep(.025)
    t.ipc.call("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "release"})
    time.sleep(.7)


def badge(count):
    subprocess.run(["busctl", "--user", "emit", "/com/canonical/unity/launcherentry/1",
        "com.canonical.Unity.LauncherEntry", "Update", "sa{sv}", "application://foot.desktop",
        "2", "count", "x", str(count), "count-visible", "b", "true"], check=True)
    time.sleep(.5)


def badge_pixels(rail, collapsed, count):
    t.move(t.screen["width"]/2, t.screen["height"]/2)
    time.sleep(.8)
    f = t.card("badge-corners")["frame"]
    image = shot(f"badge-{rail}-{'collapsed' if collapsed else 'expanded'}-{count}")
    # Use the live palette, including integrations, rather than hard-coding its alert hue.
    palette = json.loads((Path(os.environ["XDG_RUNTIME_DIR"]) / "scottland" / (os.environ["WAYLAND_DISPLAY"] + ".palette.json")).read_text())
    alert = tuple(bytes.fromhex(palette.get("alert", "#bf616a").lstrip("#")))
    x, y, w, h = (round(f[k]) for k in ("x", "y", "width", "height"))
    colored = [(px, py) for py in range(y, y+h) for px in range(x, x+w)
               if max(abs(a-b) for a, b in zip(image(px, py), alert)) <= 3]
    t.check(f"badge {rail}/{collapsed}/{count}: alert pixels present", len(colored) > 70, len(colored))
    if not colored:
        return
    left, right = min(px for px, _ in colored), max(px for px, _ in colored)
    top, bottom = min(py for _, py in colored), max(py for _, py in colored)
    t.check(f"badge {rail}/{collapsed}/{count}: upper card corner opposite rail",
            top <= y+4 and bottom <= y+22 and
            (right >= x+w-4 and left >= x+w-45 if rail == "left" else left <= x+4 and right <= x+45),
            (left-x, top-y, right-x, bottom-y, w))
    t.check(f"badge {rail}/{collapsed}/{count}: readable white count inside screen",
            0 <= left < right < t.screen["width"] and 0 <= top < bottom < t.screen["height"] and
            sum(min(image(px, py)) > 230 for py in range(top, bottom+1) for px in range(left, right+1)) >= 8)
    t.check(f"badge {rail}/{collapsed}/{count}: overlaps body upper edge", top < y+6 < bottom)


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--resize-only", action="store_true")
args = parser.parse_args()
client = None
hover_pixels = {}
try:
    options(sounds=False)
    if not args.resize_only:
        t.launch("badge-corners", "left", y=60)
        for collapsed in (False, True):
            if collapsed:
                t.toggle()
                time.sleep(.7)
            for rail in ("left", "right", "left"):
                t.drag_begin(t.card("badge-corners"), 6 if rail == "left" else t.screen["width"]-6, 60)
                t.drag_end()
                for count in (7, 123):
                    badge(count)
                    badge_pixels(rail, collapsed, count)
        t.cleanup()
    options(center_width=90, min_scale=1, max_scale=1, scale_curve="0:1 1:1",
            goo_noise=0, goo_drift=0, goo_wave_height=0, goo_swell=0,
            goo_hover_cloudiness=.65, goo_hover_emissivity=.35, goo_hover_distance=48)
    client = subprocess.Popen(["quickshell", "-p", str(repo / "tests/fixedsize-fixture.qml")],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t.wait_for(lambda: t.app("fixedsize-fixture"))
    def frame():
        return t.app("fixedsize-fixture")["frame"]
    def place():
        t.ipc.call("window-rules/configure-view", {"id": t.app("fixedsize-fixture")["id"],
            "geometry": {"x": 450, "y": 240, "width": 320, "height": 200}})
        time.sleep(.6)
    for goo in (True, False):
        options(goo=goo)
        for fixed in (True, False):
            place()
            click(600, 330)
            t.move(50, 600)
            time.sleep(2)
            f = frame()
            label = f"{'goo' if goo else 'halo'}-{'fixed' if fixed else 'normal'}"
            base = shot(label + "-rest")
            # Every corner, safely inside the halo but outside the rounded client.
            for corner, (x, y) in enumerate(((f['x']-4,f['y']+4), (f['x']+f['width']+4,f['y']+4),
                                            (f['x']-4,f['y']+f['height']-4),
                                            (f['x']+f['width']+4,f['y']+f['height']-4))):
                t.move(x, y)
                time.sleep(.7)
                now = frame()
                image = shot(label + f"-corner-{corner}")
                delta = sum(abs(a-b) for a,b in zip(image(x,y), base(x,y)))
                t.check(label + f" corner {corner}: correct cloud and handle",
                        (max(now['cloud']) == 0 and now['hovered'] == 'halo') if fixed else
                        (now['cloud'][corner] > .8 and now['hovered'] in
                         ('top-left','top-right','bottom-left','bottom-right')), now)
                hover_pixels[(goo, fixed, corner)] = image(x,y)
                if not goo and not fixed:
                    difference = sum(abs(a-b) for a,b in zip(image(x,y), hover_pixels[(goo, True, corner)]))
                    t.check(label + f" corner {corner}: fallback cloud visible versus fixed window",
                            difference > 15, difference)
                if goo:
                    t.check(label + f" corner {corner}: pixel highlight {'absent' if fixed else 'visible'}",
                            delta < 12 if fixed else delta > 15, delta)
            # Disabled corners behave like the widget's move band, never resizing.
            before = frame()
            drag(before['x']-4, before['y']+4, -24, -16)
            after = frame()
            t.check(label + ": corner drag resize eligibility", 
                    abs(after['width']-before['width']) < 1 and abs(after['height']-before['height']) < 1
                    if fixed else after['width'] > before['width']+25 and after['height'] > before['height']+15,
                    (before, after))
            place()
            f = frame()
            t.move(f['x']-5, f['y']+f['height']/2)
            time.sleep(.7)
            side = shot(label + "-side")
            t.check(label + ": side still offers move", frame()['hovered'] == 'halo')
            if goo:
                t.check(label + ": side still highlights in pixels", sum(abs(a-b) for a,b in
                        zip(side(f['x']-5, f['y']+100),base(f['x']-5, f['y']+100))) > 15)
            drag(f['x']-5, f['y']+f['height']/2, 30, 10)
            moved = frame()
            t.check(label + ": side drag moves without resizing", moved['x'] > f['x']+20 and
                    abs(moved['width']-f['width']) < 1 and abs(moved['height']-f['height']) < 1)
            # Toggle live min/max hints, including when returning to fixed for the next renderer.
            click(moved['x']+100, moved['y']+80)
            t.key('SPACE', True); t.key('SPACE', False)
            time.sleep(.3)
        # An app with only one fixed axis still has a useful resize control.
        place()
        click(600, 330)
        t.key('H', True); t.key('H', False)
        time.sleep(.3)
        f = frame()
        t.move(f['x']-4, f['y']+4)
        time.sleep(.5)
        t.check(f"{goo}: one fixed axis retains corner affordance", frame()['cloud'][0] > .8)
        drag(f['x']-4, f['y']+4, -25, -20)
        after = frame()
        t.check(f"{goo}: one fixed axis resizes only its free dimension",
                after['width'] > f['width']+25 and abs(after['height']-f['height']) < 1)
        click(after['x']+100, after['y']+80)
        t.key('SPACE', True); t.key('SPACE', False)
        time.sleep(.3)
    print(f"{t.passes} passed, {t.failures} failed", flush=True)
finally:
    t.cleanup()
    if client:
        client.terminate()
        client.wait(timeout=5)
raise SystemExit(bool(t.failures))
