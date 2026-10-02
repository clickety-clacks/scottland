#!/usr/bin/env python3
"""GO1-GO10 in an already-running isolated headless session, with real stipc input.
Run through tests/headless.sh run; artifacts stay in build/goo-evidence. Geometry IPC is used
only to arrange fixtures. Interaction assertions use buttons, pointer motion, touches and keys.
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

class Pixels:
    def __init__(self, path):
        self.image = GdkPixbuf.Pixbuf.new_from_file(str(path))
        self.data = self.image.get_pixels()
    def getpixel(self, position):
        x, y = position
        offset = y*self.image.get_rowstride() + x*self.image.get_n_channels()
        return tuple(self.data[offset:offset+3])

repo = Path(__file__).resolve().parents[1]
art = repo / "build/goo-evidence"
art.mkdir(exist_ok=True)
sock = socket.socket(socket.AF_UNIX)
sock.connect(os.environ["WAYFIRE_SOCKET"])
passed = failed = 0
clients = []


def ipc(method, data=None):
    body = json.dumps({"method": method, "data": data or {}}).encode()
    sock.sendall(struct.pack("<I", len(body)) + body)
    def read(n):
        out = b""
        while len(out) < n:
            chunk = sock.recv(n - len(out))
            if not chunk:
                raise RuntimeError("compositor disconnected")
            out += chunk
        return out
    value = json.loads(read(struct.unpack("<I", read(4))[0]))
    if isinstance(value, dict) and "error" in value:
        raise RuntimeError(f"{method}: {value}")
    return value


def check(name, condition):
    global passed, failed
    print(("PASS " if condition else "FAIL ") + name, flush=True)
    passed += bool(condition)
    failed += not condition


def options(**values):
    ipc("wayfire/set-config-options", {"scottland/" + k: v for k, v in values.items()})


def views():
    return ipc("scottland/layout-state")["views"]


def view(title):
    return next(v for v in views() if v["title"] == title)


def spawn(title):
    p = subprocess.Popen(["foot", "-c", "/dev/null", "-T", title, "-W", "30x8", "sleep", "600"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    clients.append(p)
    for _ in range(50):
        if any(v["title"] == title for v in views()):
            return view(title)
        time.sleep(.1)
    raise RuntimeError("window failed to map")


def place(title, x, y, w=320, h=180):
    ipc("window-rules/configure-view", {"id": view(title)["id"], "geometry": {"x": x, "y": y, "width": w, "height": h}})
    time.sleep(.4)


def pointer(x, y):
    ipc("stipc/move_cursor", {"x": round(x), "y": round(y)})


def key(key, down):
    ipc("stipc/feed_key", {"key": key, "state": down})


def click(x, y):
    pointer(x, y)
    time.sleep(.1)
    ipc("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "press"})
    time.sleep(.05)
    ipc("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "release"})


def drag(x, y, dx, dy, super_key=False):
    pointer(x, y)
    time.sleep(.12)
    if super_key:
        key("KEY_LEFTMETA", True)
    ipc("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "press"})
    for i in range(1, 13):
        pointer(x + dx*i/12, y + dy*i/12)
        time.sleep(.025)
    ipc("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "release"})
    if super_key:
        key("KEY_LEFTMETA", False)
    time.sleep(.8)


def center(v):
    f = v["frame"]
    return (f["x"] + f["width"]/2, f["y"] + f["height"]/2)


def shot(name):
    subprocess.run(["grim", str(art / (name + ".png"))], check=True)
    return Pixels(art / (name + ".png"))


def sample(x, y):
    return ipc("scottland/goo-state", {"x": x, "y": y})["screens"][0]


def color(s):
    return [s[k] for k in ("red", "green", "blue")]


def change(a, b):
    return sum(abs(x-y) for x, y in zip(a, b))


try:
    default = ipc("wayfire/get-config-option", {"option": "scottland/goo"})["default"]
    check("switch is on by default", str(default).lower() in ("true", "1"))
    options(goo=False, center_width=90, min_scale=1, max_scale=1, scale_curve="0:1 1:1", sounds=False, goo_falloff="")
    spawn("goo-a"); spawn("goo-b")
    place("goo-a", 250, 230); place("goo-b", 610, 230)
    pointer(20, 20)
    off = shot("00-halo")
    before_model = ipc("scottland/desktop-model")
    options(goo=True)
    time.sleep(2)
    after_model = ipc("scottland/desktop-model")
    screen_names = [o["name"] for o in ipc("window-rules/list-outputs")]
    check("available goo screens publish a newer desktop snapshot", sorted(after_model["goo"]) == sorted(screen_names)
          and after_model["version"] > before_model["version"])
    check("goo screen state stays out of external slices", "goo" not in ipc("scottland/desktop-model", {"slice": "widgets"})
          and "goo" not in ipc("scottland/desktop-model", {"slice": "attention"}))
    check("switch enables a GPU screen field", ipc("scottland/goo-state")["enabled"] and sample(590, 320)["sources"] == 2)
    on = shot("01-bridge")
    check("goo never changes window contents", on.getpixel((400, 300)) == off.getpixel((400, 300)))
    check("bridge is present in the field", sample(590, 320)["density"] > sample(590, 320)["threshold"])
    check("bridge is drawn outside the window union", on.getpixel((590, 320)) != on.getpixel((590, 80)))
    baseline = color(sample(600, 320))
    ipc("scottland/attention", {"window": view("goo-a")["id"], "attention": True, "source": "goo-test"})
    time.sleep(3)
    dyed = color(sample(600, 320))
    check("attention dye reaches the other half of a bridge", change(baseline, dyed) > .025)
    shot("02-dye-bridge")
    drag(*center(view("goo-b")), 180, 0, True)
    pointer(20, 20)
    time.sleep(2)
    gap = sample(590, 320)
    check("pulling apart snaps the bridge", gap["density"] < gap["threshold"])
    shot("03-gap")
    b = view("goo-b")["frame"]
    isolated0 = color(sample(b["x"]-7, b["y"]+b["height"]/2))
    ipc("scottland/attention", {"window": view("goo-a")["id"], "attention": False, "source": "goo-test"})
    time.sleep(3)
    isolated1 = color(sample(b["x"]-7, b["y"]+b["height"]/2))
    check("dye stays separate across a gap", change(isolated0, isolated1) < .035)
    # Existing halo jobs, through the new field.
    a = view("goo-a"); f = a["frame"]; before = center(a)
    drag(f["x"]-6, f["y"]+f["height"]/2, 65, 30)
    after = center(view("goo-a"))
    check("goo drag moves the window", after[0] > before[0]+40 and after[1] > before[1]+15)
    a = view("goo-a"); f = a["frame"]; before = center(a)
    pointer(f["x"]+f["width"]+5, f["y"]+f["height"]-18)
    time.sleep(.35)
    check("exposed corner is a resize handle", view("goo-a")["frame"]["hovered"] == "bottom-right")
    shot("04-corner-dye")
    drag(f["x"]+f["width"]+5, f["y"]+f["height"]-18, 25, 20)
    resized = view("goo-a")["frame"]
    check("corner resize grows around the center", resized["width"] > f["width"]+20 and change(before, center(view("goo-a"))) < 2)
    f = view("goo-a")["frame"]; before = center(view("goo-a"))
    x, y = f["x"]-5, f["y"]+f["height"]/2
    ipc("stipc/touch", {"finger": 0, "x": round(x), "y": round(y)})
    for i in range(1, 11):
        ipc("stipc/touch", {"finger": 0, "x": round(x), "y": round(y+5*i)})
        time.sleep(.02)
    ipc("stipc/touch_release", {"finger": 0}); time.sleep(.8)
    check("touch moves goo immediately", center(view("goo-a"))[1] > before[1]+25)
    f = view("goo-a")["frame"]; before = center(view("goo-a"))
    x, y = f["x"]+f["width"]+5, f["y"]+f["height"]-18
    ipc("stipc/touch", {"finger": 0, "x": round(x), "y": round(y)})
    for j in range(1, 11):
        ipc("stipc/touch", {"finger": 0, "x": round(x+2*j), "y": round(y+2*j)}); time.sleep(.02)
    ipc("stipc/touch_release", {"finger": 0}); time.sleep(.8)
    check("touch corner resizes around the center", view("goo-a")["frame"]["width"] > f["width"]+15 and change(before, center(view("goo-a"))) < 2)
    # Three-finger input enters the same existing gesture path as the regression suite.
    before = center(view("goo-a")); pointer(*before)
    ipc("scottland/test-input", {"swipe": "begin", "fingers": 3})
    for _ in range(4):
        ipc("scottland/test-input", {"swipe": "update", "dx": -12, "dy": 0}); time.sleep(.03)
    ipc("scottland/test-input", {"swipe": "end"}); time.sleep(.8)
    check("three-finger drag still moves windows", center(view("goo-a"))[0] < before[0]-20)
    # Proximity reveal uses the existing pause and spring, with the goo renderer.
    f = view("goo-a")["frame"]; pointer(f["x"]-25, f["y"]+f["height"]/2); time.sleep(1.3)
    check("proximity reveal swells the goo", view("goo-a")["frame"]["swell"] > .5)
    pointer(20, 20); time.sleep(4)
    spawn("goo-close"); place("goo-close", 500, 100, 200, 120)
    f = view("goo-close")["frame"]; x = f["x"]+f["width"]/2; y = f["y"]+f["height"]+f["thickness"]/2
    pointer(x, y); time.sleep(.45); shot("05-close-dye")
    check("close glow is revealed", view("goo-close")["frame"]["dot"] > .5)
    click(x, y); time.sleep(.4)
    check("close dye target closes the window", not any(v["title"] == "goo-close" for v in views()))
    spawn("goo-touch-close"); place("goo-touch-close", 500, 90, 200, 120)
    f = view("goo-touch-close")["frame"]; x = f["x"]+f["width"]/2; y = f["y"]+f["height"]+f["thickness"]/2
    ipc("stipc/touch", {"finger": 0, "x": round(f["x"]-5), "y": round(f["y"]+50)})
    ipc("stipc/touch_release", {"finger": 0}); time.sleep(.4)
    ipc("stipc/touch", {"finger": 0, "x": round(x), "y": round(y)})
    ipc("stipc/touch_release", {"finger": 0}); time.sleep(.4)
    check("touch reveals and taps the close target", not any(v["title"] == "goo-touch-close" for v in views()))
    options(center_width=33.333, min_scale=.2, max_scale=1, scale_curve="0:1 1:.2")
    time.sleep(.5); before = center(view("goo-b")); drag(*before, 65, 0, True)
    check("Super drag still scales live in the periphery", view("goo-b")["applied_scale"] < .9)
    options(center_width=90, min_scale=1, max_scale=1, scale_curve="0:1 1:1"); time.sleep(.4)
    # Main's Alt declutter is a presentation transform, not a geometry command.
    f = view("goo-a")["frame"]; saved_b = dict(view("goo-b")["frame"])
    place("goo-b", round(f["x"]), round(f["y"]), round(f["width"]), round(f["height"]))
    key("KEY_LEFTALT", True); time.sleep(1.2)
    hints = ipc("scottland/hints")
    displaced = max(hints["hints"], key=lambda h: abs(h["dx"]) + abs(h["dy"]))
    actual = next(v["frame"] for v in views() if v["id"] == displaced["window"])
    dx, dy = displaced["dx"], displaced["dy"]
    x = actual["x"] + actual["width"]/2 + dx
    y = actual["y"] + actual["height"]/2 + dy
    if abs(dx) >= abs(dy): x = actual["x"] + dx + (actual["width"]-2 if dx > 0 else 2)
    else: y = actual["y"] + dy + (actual["height"]-2 if dy > 0 else 2)
    check("goo islands follow Alt declutter's rendered offsets", hints["active"] and abs(dx)+abs(dy) > 10
          and sample(x, y)["window_distance"] < 0)
    shot("05a-alt-declutter")
    key("KEY_LEFTALT", False); time.sleep(.8)
    check("goo follows the return from Alt declutter", not ipc("scottland/hints")["active"]
          and sample(*center(view("goo-a")))["window_distance"] < 0
          and sample(x, y)["window_distance"] > 0)
    place("goo-b", round(saved_b["x"]), round(saved_b["y"]))
    f = view("goo-a")["frame"]; old_b = view("goo-b")["frame"]
    place("goo-b", round(f["x"]+f["width"]-50), round(f["y"]+f["height"]-50))
    pointer(f["x"]+f["width"]+5, f["y"]+f["height"]-18); time.sleep(.3)
    check("a corner inside another window has no handle", view("goo-a")["frame"]["hovered"] == "none")
    shot("05b-overlap-pooling")
    place("goo-b", round(old_b["x"]), round(old_b["y"]))
    # Panel: actual controls, isolated persistence file, followed by the real config consumer.
    settings_home = art / "settings-home"; (settings_home / "scottland").mkdir(parents=True, exist_ok=True)
    layout = settings_home / "scottland/layout.ini"
    layout.unlink(missing_ok=True)
    env = dict(os.environ, SCOTTLAND_CTL=str(repo / "core/libexec/scottland-ctl"), SCOTTLAND_LAYOUT_FILE=str(layout))
    log = open(art / "panel.log", "w")
    panel = subprocess.Popen(["qs", "-n", "-p", str(repo / "core/settings")], env=env, stdout=log, stderr=log)
    clients.append(panel); time.sleep(1.3)
    click(760, 150); time.sleep(.3); shot("06-panel")
    click(740, 250); time.sleep(.4)
    thick = float(ipc("wayfire/get-config-option", {"option": "scottland/goo_thickness"})["value"])
    check("panel changes thickness live", thick > 24)
    shot("07-panel-live")
    # The whole row is a slider. Scroll with its scrollbar, keeping row drags unambiguous.
    key("KEY_RIGHT", True); key("KEY_RIGHT", False); time.sleep(.15)
    keyboard_thick = float(ipc("wayfire/get-config-option", {"option": "scottland/goo_thickness"})["value"])
    check("Goo rows use the shared Left/Right keyboard step", abs(keyboard_thick-thick-1) < .01)
    key("KEY_LEFTSHIFT", True); key("KEY_LEFT", True); key("KEY_LEFT", False); key("KEY_LEFTSHIFT", False); time.sleep(.15)
    shifted_thick = float(ipc("wayfire/get-config-option", {"option": "scottland/goo_thickness"})["value"])
    check("Shift uses the shared larger keyboard step", abs(shifted_thick-keyboard_thick+10) < .01)
    key("KEY_BACKSPACE", True); key("KEY_BACKSPACE", False); time.sleep(.15)
    check("Backspace resets the Goo row to its opening value", abs(float(ipc("wayfire/get-config-option",
          {"option": "scottland/goo_thickness"})["value"])-13) < .01)
    for code in ["KEY_2", "KEY_7"]:
        key(code, True); key(code, False)
    time.sleep(.15)
    check("digits enter a Goo row value", abs(float(ipc("wayfire/get-config-option",
          {"option": "scottland/goo_thickness"})["value"])-27) < .01)
    for _ in range(14):
        key("KEY_DOWN", True); key("KEY_DOWN", False)
    time.sleep(.3); shot("07a-panel-keyboard-last-row")
    key("KEY_RIGHT", True); key("KEY_RIGHT", False); time.sleep(.15)
    check("keyboard navigation reaches the last Goo row", abs(float(ipc("wayfire/get-config-option",
          {"option": "scottland/goo_relief"})["value"])-5.1) < .01)
    # The keyboard follows the selected row; use the now-lowered scrollbar thumb for the curve.
    drag(895, 480, 0, 120)
    shot("07b-panel-curve")
    drag(449, 426, 0, 20)
    falloff = ipc("wayfire/get-config-option", {"option": "scottland/goo_falloff"})["value"]
    curve = [tuple(map(float, p.split(":"))) for p in falloff.split()]
    check("the shared curve editor changes goo falloff live", len(curve) >= 2 and
          all(a[0] < b[0] and a[1] >= b[1] for a,b in zip(curve,curve[1:])))
    shot("07c-panel-curve-live")
    # Escape restores all settings from the open, including the switch.
    key("KEY_ESC", True); key("KEY_ESC", False); time.sleep(.4)
    restored = float(ipc("wayfire/get-config-option", {"option": "scottland/goo_thickness"})["value"])
    cancelled_curve = ipc("wayfire/get-config-option", {"option": "scottland/goo_falloff"})["value"]
    check("Cancel restores goo settings and writes nothing", abs(restored-13) < .01 and not layout.exists() and not cancelled_curve)
    if abs(restored-13) >= .01 or layout.exists() or cancelled_curve:
        print("Cancel diagnostic", restored, layout.exists(), repr(cancelled_curve), panel.poll(), flush=True)
    panel = subprocess.Popen(["qs", "-n", "-p", str(repo / "core/settings")], env=env, stdout=log, stderr=log)
    clients.append(panel); time.sleep(1)
    click(760, 150); click(740, 250); time.sleep(.3)
    drag(895, 250, 0, 350)
    drag(449,426,0,20)
    key("KEY_ENTER", True); key("KEY_ENTER", False); time.sleep(.5)
    check("Save persists goo alongside layout", layout.exists() and "goo_thickness =" in layout.read_text() and "goo_falloff = 0.000:" in layout.read_text())
    if layout.exists():
        built = art / "saved-config.ini"
        subprocess.run([str(repo / "core/session/scottland-build-config"), "--output", str(built)],
                       env=dict(os.environ, XDG_CONFIG_HOME=str(settings_home)), check=True, stdout=subprocess.DEVNULL)
        check("saved goo is consumed by the session config builder", "goo_thickness =" in built.read_text())
    panel = subprocess.Popen(["qs", "-n", "-p", str(repo / "core/settings")], env=env, stdout=log, stderr=log)
    clients.append(panel); time.sleep(1)
    click(760, 150); click(420, 638); time.sleep(.4)
    reset = float(ipc("wayfire/get-config-option", {"option": "scottland/goo_thickness"})["value"])
    check("Goo Defaults restores the shipped preset and switch", abs(reset-13)<.01 and ipc("scottland/goo-state")["enabled"])
    key("KEY_ESC", True); key("KEY_ESC", False); time.sleep(.3)
    click(*center(view("goo-b")))
    options(color_scheme="light", accent_color="#ff2040ff", attention_color="#20ff40ff")
    pointer(20, 20); time.sleep(3)
    f = view("goo-b")["frame"]; palette_sample = sample(f["x"]+f["width"]/2, f["y"]-7)
    check("goo follows live palette and light/dark changes", palette_sample["red"] > palette_sample["blue"]+.15)
    shot("07d-palette-light")
    options(color_scheme="dark", accent_color="#81a1c1ff", attention_color="#ebcb8bff")
    options(goo_thickness=13, goo_falloff="", goo=False); time.sleep(.5)
    check("switch returns live to the existing halo", not ipc("scottland/goo-state")["enabled"])
    check("live disable removes goo screens from the desktop model", ipc("scottland/desktop-model")["goo"] == [])
    shot("08-halo-restored")
    options(goo=True); pointer(20, 20)
    for _ in range(300):
        state = ipc("scottland/goo-state")["screens"][0]
        if state["sleeping"]: break
        time.sleep(.1)
    check("GPU simulation reaches sleep", state["sleeping"])
    steps = state["steps"]; time.sleep(1)
    check("sleep stops simulation steps", ipc("scottland/goo-state")["screens"][0]["steps"] == steps)
    print("TIMING " + json.dumps(state), flush=True)
finally:
    for v in views():
        if v["title"].startswith("goo-"):
            ipc("window-rules/close-view", {"id": v["id"]})
    for p in clients:
        if p.poll() is None and p.args[0] == "qs":
            p.terminate()  # only the PID recorded by Popen for this test
    print(f"RESULT {passed} passed, {failed} failed", flush=True)
    sock.close()
raise SystemExit(bool(failed))
