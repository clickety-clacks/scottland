#!/usr/bin/env python3
"""S1-S18 via real stipc input in a caller-owned headless session.
Run with tests/headless.sh run. Requires two outputs; screenshots and logs are retained in
build/settings-help-evidence. No live config, session or services are used.
"""
import json
import os
import shutil
from pathlib import Path
import socket
import struct
import subprocess
import time

import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf

repo = Path(__file__).resolve().parents[1]
art = repo / "build/settings-help-evidence" / f"run-{os.getpid()}-{time.time_ns()}"
art.mkdir(parents=True)
layout = art / "settings-home/scottland/layout.ini"
layout.parent.mkdir(parents=True, exist_ok=True)
layout.unlink(missing_ok=True)
probe_panel = None
last_snapshot = {}
def snapshot():
    global last_snapshot
    if probe_panel and probe_panel.poll() is None:
        last_snapshot=json.loads(subprocess.check_output(
            ["qs","ipc","--pid",str(probe_panel.pid),"call","settings-test","snapshot"],text=True,timeout=5))
    return last_snapshot

def screen_point(p):
    return panel_x+p["x"], 720-48-snapshot()["panel"]["height"]+p["y"]

def control_point(name, dx=0, dy=0):
    p=snapshot()[name]; x,y=screen_point(p); return x+dx,y+dy

def scroll_to(value):
    # Drag the real scrollbar thumb; use observed geometry to compute its travel.
    q=snapshot(); v=q["viewport"]; x,y=screen_point(v)
    thumb=v["height"]*v["height"]/q["contentHeight"]
    start=y+q["scroll"]/q["contentHeight"]*v["height"]+thumb/2
    end=y+max(0,min(q["contentHeight"]-v["height"],value))/q["contentHeight"]*v["height"]+thumb/2
    drag(x+v["width"]-5,start,0,end-start)

def tab(index):
    click(panel_x+20+(index+.5)*520/3,720-48-snapshot()["panel"]["height"]+77)
    time.sleep(.12)

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
    time.sleep(.03)  # Let the client see the held key before its release.
    ipc("stipc/feed_key", dict(key=code, state=False))
    time.sleep(.12)


def option_reaches(name, expected, timeout=2):
    # QML debounces previews and invokes an asynchronous ctl process. Wait for its result,
    # not an assumed process-start/IPC latency; never resend input to make a check pass.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if abs(option(name)-expected) < .01:
            return True
        time.sleep(.03)
    return False


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
    time.sleep(.4)


class Pixels:
    def __init__(self, path):
        self.path = path
        self.img = GdkPixbuf.Pixbuf.new_from_file(str(path))
        self.data = self.img.get_pixels()
    def pixel(self, x, y):
        pos = round(y)*self.img.get_rowstride()+round(x)*self.img.get_n_channels()
        return tuple(self.data[pos:pos+3])
    def hint(self, row_y, label, x=None, expected=True):
        if not expected:
            return self.hint_now(row_y, label, x)
        # Qt may handle input after stipc acknowledges it. Wait for the identified hint and
        # its pixels, retaining the final frame as evidence without repeating input.
        deadline = time.monotonic() + 1
        while True:
            if self.hint_now(row_y, label, x):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(.05)
            subprocess.run(["grim", str(self.path)], check=True)
            self.__init__(self.path)

    def hint_now(self, row_y, label, x=None):
        observations = {}
        for line in (art / "panel.log").read_text().splitlines():
            if "SCOTTLAND_HINT " in line:
                entry = json.loads(line.split("SCOTTLAND_HINT ", 1)[1])
                observations[entry["probe"], entry["label"]] = entry
        active = {getattr(proc, "hint_probe", None) for proc in clients if proc.poll() is None}
        entry = next((v for (probe, name), v in observations.items()
                      if probe in active and name == label and v["visible"]), None)
        if not entry or entry["width"] != 320 or entry["height"] < 40:
            return False
        x = panel_x + 568 if x is None else x
        height = round(entry["height"])
        background, border = (tuple(bytes.fromhex(entry[k].lstrip("#")))
                              for k in ("background", "border"))
        expected_y = max(0, min(self.img.get_height()-height,
                                round(row_y+29-height/2)))
        for y in range(max(0, expected_y-2), min(self.img.get_height()-height,
                                                 expected_y+2)+1):
            samples = [(x+16, y), (x+160, y), (x+303, y),
                       (x+160, y+height-1), (x, y+height//2), (x+319, y+height//2)]
            inside = [(x+6, y+16), (x+313, y+16), (x+6, y+height-17),
                      (x+160, y+height-7)]
            if all(self.pixel(xx, yy) == border for xx, yy in samples) and all(
                    self.pixel(xx, yy) == background for xx, yy in inside):
                return True
        return False
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
    global panel_x, probe_panel
    probe = str(len(clients))
    panel = subprocess.Popen(["qs", "-n", "-p", str(repo / "core/settings")],
        env=dict(os.environ, QS_DISABLE_FILE_WATCHER="1", SCOTTLAND_CTL=str(repo / "core/libexec/scottland-ctl"),
                 SCOTTLAND_LAYOUT_FILE=str(layout), SCOTTLAND_PALETTE=str(palette_path),
                 SCOTTLAND_HINT_PROBE=probe, SCOTTLAND_SETTINGS_TEST="1"), stdout=log, stderr=log)
    panel.hint_probe = probe
    clients.append(panel)
    probe_panel=panel
    for _ in range(100):
        if panel.poll() is not None:break
        try:
            if snapshot().get("screen"):break
        except (json.JSONDecodeError,subprocess.CalledProcessError):pass
        time.sleep(.05)
    check("settings maps", panel.poll() is None)
    time.sleep(.4)
    panel_output=next(o for o in outputs if o["name"]==snapshot()["screen"])
    panel_x=panel_output["geometry"]["x"]+360
    shot("panel-position")
    return panel


def close_panel(panel, save=False, via_button=False):
    if via_button:
        click(panel_x + (500 if save else 409), 638)
    else:
        key("KEY_ENTER" if save else "KEY_ESC")
    panel.wait(timeout=5)
    time.sleep(.15)


def motion_trial(resize=False):
    name="SettingsMotion"+str(time.monotonic_ns())
    fixture=subprocess.Popen(["python3",str(repo/"tests/windowing-key-recorder.py"),name,str(art/(name+".keys"))],stdout=log,stderr=log)
    clients.append(fixture)
    for _ in range(80):
        view=next((v for v in ipc("window-rules/list-views") if v.get("title")==name),None)
        if view:break
        time.sleep(.05)
    assert view
    ipc("window-rules/configure-view",dict(id=view["id"],geometry=dict(x=480,y=250,width=250,height=150)))
    ipc("window-rules/focus-view",dict(id=view["id"]))
    time.sleep(.3)
    def geometry():return next(v["geometry"] for v in ipc("window-rules/list-views") if v["id"]==view["id"])
    before=geometry()
    ipc("stipc/feed_key",dict(key="KEY_LEFTALT",state=True));time.sleep(.4)
    if resize:ipc("stipc/feed_key",dict(key="KEY_LEFTCTRL",state=True))
    key("KEY_RIGHT")
    if resize:ipc("stipc/feed_key",dict(key="KEY_LEFTCTRL",state=False))
    ipc("stipc/feed_key",dict(key="KEY_LEFTALT",state=False));time.sleep(.8)
    after=geometry()
    fixture.terminate();fixture.wait(timeout=5)
    return after["width"]-before["width"] if resize else after["x"]+after["width"]/2-before["x"]-before["width"]/2


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
    assert os.environ["WAYLAND_DISPLAY"] != "wayland-1", "isolated headless session required"
    outputs = sorted(ipc("window-rules/list-outputs"), key=lambda o:o["geometry"]["x"])
    assert len(outputs) == 2 and all(o["geometry"]["height"] == 720 for o in outputs)
    # Quickshell's first screen is where the panel is anchored (leftmost on this backend).
    panel_x = outputs[0]["geometry"]["x"] + (outputs[0]["geometry"]["width"]-560)/2
    initial = values()
    palette_path = art / "palette.json"
    palette_path.write_text(json.dumps(dict(scheme="dark",background="#1c1d22",foreground="#e6e6e9",accent="#7aa2f7")))
    baseline_motion=motion_trial()
    baseline_resize=motion_trial(True)
    panel = open_panel()
    check("S15 heading/application name",snapshot()["title"] == "Scottland Settings")
    check("S15 launcher name", "Name=Scottland Settings" in (repo/"core/settings/scottland-settings.desktop").read_text())
    bands("01-softness-bands")
    for i,label in enumerate(["Center edge softness","Center zone width","Widget rail width"]):
        point = control_point("zones",200,29+59*i)
        row_top = point[1]-29
        pointer(*point);time.sleep(.25)
        check(label+" hover hint",snapshot()["zones"]["hint"]==label)
        if i == 0:
            check("first Layout hint has its expected visible bubble",
                  shot("02-layout-hover").hint(row_top,label))
    point = control_point("zones",200,29+59*2)
    row_top = point[1]-29
    pointer(10,690);time.sleep(.15)
    check("leaving a hovered row hides its bubble",
          not shot("02a-layout-leave").hint(row_top,"Widget rail width",expected=False))
    click(*control_point("zones",200,29));pointer(10,690);key("KEY_BACKSPACE")
    before=option("center_width");key("KEY_DOWN");key("KEY_RIGHT")
    check("keyboard step previews zone live",option_reaches("center_width",round((before+.5)*2)/2))
    check("keyboard hint follows selection",snapshot()["zones"]["hint"]=="Center zone width")
    center_row = control_point("zones",200,29+59)[1]-29
    check("keyboard selection draws its hint bubble",
          shot("02b-keyboard-hint").hint(center_row,"Center zone width"))
    key("KEY_UP");key("KEY_1");key("KEY_2");key("KEY_0")
    check("numeric entry previews softness",option_reaches("blend_width",120))
    for _ in range(4):key("KEY_BACKSPACE")
    check("Backspace restores opening row",option_reaches("blend_width",initial["blend_width"]))
    # Knobs have 36pt hit disks; select offset from the small visible handle.
    e=snapshot()["editor"];p=e["plot"]
    click(*screen_point(dict(x=p["x"]+p["width"]*.45,y=p["y"]+p["height"]*.4)))
    check("curve click adds and selects",len(snapshot()["editor"]["knots"])==3 and snapshot()["editor"]["selected"]==1)
    key("KEY_DELETE")
    check("Delete removes selected interior knot",len(snapshot()["editor"]["knots"])==2)
    click(*screen_point(dict(x=p["x"]+p["width"]*.5,y=p["y"]+p["height"]*.5)))
    key("KEY_BACKSPACE")
    check("Backspace removes selected interior knot",len(snapshot()["editor"]["knots"])==2)
    knot=snapshot()["editor"]["knots"][0];x,y=screen_point(knot)
    click(x+14,y);key("KEY_DELETE")
    check("36pt endpoint target selects but cannot be deleted",snapshot()["editor"]["selected"]==0 and len(snapshot()["editor"]["knots"])==2)
    shot("03-curve-selected")
    tab(1);check("Goo tab selects",snapshot()["tab"]==1)
    click(*control_point("goo",240,29));key("KEY_BACKSPACE");pointer(10,690)
    labels=["Border thickness","Reach","Bridge draw","Swell","Mess","Lump size","Drift","Wave speed","Wave persistence","Wave height","Dye spread","Dye swirl","Dye release","Shine","Relief","Overlap film","Control cloudiness","Control glow","Control proximity"]
    for i,label in enumerate(labels):
        if i:key("KEY_DOWN")
        for _ in range(20):
            if snapshot()["goo"]["hint"]==label:break
            time.sleep(.025)
        check(label+" keyboard hint and automatic reveal",snapshot()["goo"]["hint"]==label)
    key("KEY_RIGHT");check("Goo last row live",option_reaches("goo_hover_distance",49))
    shot("04-goo-keyboard")
    tab(0);tab(1)
    # A discrete wheel burst ends before the position samples; the coast must continue,
    # then settle without a position-animation restart or an edge jump.
    pointer(*control_point("viewport",300,200))
    ipc("scottland/test-input",dict(scroll_y=90,wheel=True))
    wheel_samples=[]
    for _ in range(30):
        time.sleep(.05);q=snapshot();wheel_samples.append((q["scroll"],q["wheelVelocity"]))
    (art/"wheel-samples.json").write_text(json.dumps(wheel_samples))
    check("wheel continues moving after input stops",len(set(round(y,1) for y,v in wheel_samples))>=3)
    a=snapshot()["scroll"];time.sleep(.25)
    check("wheel decelerates to rest",abs(snapshot()["scroll"]-a)<.1 and snapshot()["wheelVelocity"]==0)
    tab(0);tab(1)
    pointer(*control_point("viewport",300,200))
    for _ in range(4):
        ipc("scottland/test-input",dict(scroll_y=20,wheel=False));time.sleep(.02)
    ipc("scottland/test-input",dict(scroll_y=0,wheel=False))
    pad_samples=[]
    for _ in range(30):
        time.sleep(.05);q=snapshot();pad_samples.append((q["scroll"],q["wheelVelocity"]))
    (art/"touchpad-samples.json").write_text(json.dumps(pad_samples))
    check("touchpad coasts after axis stop",len(set(round(y,1) for y,v in pad_samples))>=3)
    a=snapshot()["scroll"];time.sleep(.25);check("touchpad coast settles",abs(snapshot()["scroll"]-a)<.1)
    tab(0);tab(1)
    # After mouse editing that same row, touch must still be able to take over for scrolling.
    click(*control_point("goo",250,4*59+29));key("KEY_BACKSPACE")
    # A vertical touch gesture on a slider scrolls without changing its value.
    x,y=control_point("viewport",250,340)
    old_goo=snapshot()["values"]
    ipc("stipc/touch",dict(finger=0,x=round(x),y=round(y)))
    for i in range(1,9):
        ipc("stipc/touch",dict(finger=0,x=round(x),y=round(y-12*i)));time.sleep(.012)
    ipc("stipc/touch_release",dict(finger=0))
    touch_samples=[]
    for _ in range(35):
        time.sleep(.04);q=snapshot();touch_samples.append((q["scroll"],q["touchVelocity"],q["flicking"]))
    (art/"touch-samples.json").write_text(json.dumps(touch_samples))
    check("touch flick coasts after release",len(set(round(y,1) for y,v,f in touch_samples))>=3)
    check("vertical touch scroll preserves slider values",snapshot()["values"]==old_goo)
    for _ in range(40):
        if not snapshot()["flicking"]:break
        time.sleep(.05)
    a=snapshot()["scroll"];time.sleep(.2)
    check("touch coast settles",abs(snapshot()["scroll"]-a)<.1 and not snapshot()["flicking"])
    close_panel(panel)
    check("Escape restores both tabs without writing",values()==initial and option("goo_hover_distance")==48 and not layout.exists())

    # Place the real ParameterStack at the output's right edge. The passive popout must
    # flip left while the underlying fixture remains click-through.
    fixture_dir = art / f"edge-fixture-{os.getpid()}"
    fixture_dir.mkdir()
    shutil.copyfile(repo / "tests/HintPopoutFixture.qml", fixture_dir / "shell.qml")
    shutil.copyfile(repo / "core/settings/ParameterStack.qml", fixture_dir / "ParameterStack.qml")
    edge_probe = "edge-" + str(os.getpid())
    fixture = subprocess.Popen(["qs", "-n", "-p", str(fixture_dir)],
        env=dict(os.environ, SCOTTLAND_HINT_PROBE=edge_probe), stdout=log, stderr=log)
    fixture.hint_probe = edge_probe
    clients.append(fixture)
    time.sleep(.8)
    check("right-edge hint fixture maps", fixture.poll() is None)
    edge_x = panel_x + 280 + 640 - 24 - 560
    pointer(edge_x+180,220);time.sleep(.2)
    check("right-edge hint bubble is visible after flipping left",
          shot("06c-popout-flipped-left").hint(196,"Center zone width",x=edge_x-328))
    pointer(10,690);time.sleep(.15)
    check("flipped hint hides when pointer leaves",
          not shot("06d-flipped-popout-leave").hint(196,"Center zone width",x=edge_x-328,expected=False))
    fixture.terminate();fixture.wait(timeout=5)

    panel=open_panel();tab(2)
    check("Window mode tab selects",snapshot()["tab"]==2)
    key("KEY_RIGHT");time.sleep(.7)
    check("playground arrow moves and stops at analytic distance",abs(snapshot()["playground"]["distance"]-335**2/(2*608))<.1 and snapshot()["playground"]["velocity"]==0)
    # The arrow and bounce trace handles edit the compositor options live.
    drag(*control_point("playground",80,238),45)
    check("impulse arrow edits live option",option("key_impulse")>335)
    drag(*control_point("playground",403,228),0,-10)
    check("bounce trace edits live restitution",option("key_restitution")>.5)
    key("KEY_RIGHT");time.sleep(.8)
    check("playground draws edge bounce",snapshot()["playground"]["bounces"]>0)
    shot("05-window-playground")
    click(panel_x+70,638);time.sleep(.25) # Defaults keeps impulse identical for the motion comparison.
    scroll_to(350)
    e=snapshot()["movement"];p=e["plot"]
    # A flat 2x curve: both endpoint drags are real input.
    for knot in (0,1):
        k=snapshot()["movement"]["knots"][knot];x,y=screen_point(k)
        target=screen_point(dict(x=k["x"],y=p["y"]+p["height"]*(4-2)/3.95))[1]
        drag(x,y,0,target-y)
    curve=ipc("wayfire/get-config-option",{"option":"scottland/move_friction_curve"})["value"]
    check("movement friction curve previews through ctl",len(curve.split())==2 and all(float(v.split(":")[1])>1.9 for v in curve.split()))
    shot("06-window-friction")
    scroll_to(650)
    p=snapshot()["resize"]["plot"]
    for knot in (0,1):
        k=snapshot()["resize"]["knots"][knot];x,y=screen_point(k)
        target=screen_point(dict(x=k["x"],y=p["y"]+p["height"]*(4-2)/3.95))[1]
        drag(x,y,0,target-y)
    check("resize friction law previews independently",ipc("wayfire/get-config-option",{"option":"scottland/resize_friction_curve"})["value"]!="")
    close_panel(panel,save=True,via_button=True)
    changed_motion=motion_trial(); changed_resize=motion_trial(True)
    print("curve distances",baseline_motion,changed_motion,baseline_resize,changed_resize,flush=True)
    check("edited movement curve halves real arrow travel",abs(changed_motion-baseline_motion/2)<3 and baseline_motion>80)
    check("edited resize curve halves real size coast",abs(changed_resize-baseline_resize/2)<3 and baseline_resize>80)
    check("Save persists all Window mode options",all(k+" =" in layout.read_text() for k in snapshot()["motion"]))
    panel=open_panel();tab(2)
    check("reopen retains movement curve",snapshot()["motion"]["move_friction_curve"]==curve)
    scroll_to(10000)
    click(*control_point("motionSettings",0,29))
    check("deceleration row respects its positive minimum despite coarse steps",option("key_friction")==1)
    click(*control_point("motionSettings",0,88))
    check("speed limit row respects its positive minimum despite coarse steps",option("key_max_velocity")==1)
    click(*control_point("holdTiming",120,60));key("KEY_RIGHT")
    check("hold timeline edits live timing",option("alt_hold_delay")>300)
    click(*control_point("doubleTiming",180,60));key("KEY_RIGHT")
    check("double-tap timeline edits live timing",option("window_double_tap_delay")>300)
    shot("06a-window-timelines")
    click(panel_x+70,638);time.sleep(.2)
    check("Window Defaults restores original feel",option("key_impulse")==335 and ipc("wayfire/get-config-option",{"option":"scottland/move_friction_curve"})["value"]=="")
    close_panel(panel,via_button=True)
    check("Cancel restores saved motion after Defaults",ipc("wayfire/get-config-option",{"option":"scottland/move_friction_curve"})["value"]==curve)
    # Reset via the actual Defaults action and Save before the border regression checks.
    panel=open_panel();tab(2);click(panel_x+70,638);close_panel(panel,save=True)
    layout.unlink()
    # Theme applies to every control, not only hints.
    panel=open_panel()
    palette_path.write_text(json.dumps(dict(scheme="light",background="#eff1f8",foreground="#20212a",accent="#3855aa",font_family="DejaVu Serif",text_scale=1.5)))
    time.sleep(.6);p=shot("07-light-theme")
    check("panel follows light session palette",p.pixel(panel_x+10,100)==(239,241,248))
    check("type scale and family read live",snapshot()["palette"]["text_scale"]==1.5 and snapshot()["palette"]["font_family"]=="DejaVu Serif")
    close_panel(panel)
    palette_path.write_text(json.dumps(dict(scheme="dark",background="#1c1d22",foreground="#e6e6e9",accent="#7aa2f7")))

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
                check(f"{out['name']} {setting} {'right' if right else 'left'} border updates slider value ({option(setting)} vs {expected})",
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
    for _ in range(30):
        if values()!=saved:break
        time.sleep(.05)
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
    ipc("window-rules/configure-view", dict(id=view["id"], geometry=dict(x=950,y=150,width=280,height=160)))
    time.sleep(.4)
    view = next(v for v in ipc("scottland/layout-state")["views"] if v["id"] == view["id"])
    output_id = next(v for v in ipc("window-rules/list-views") if v["id"] == view["id"])["output-id"]
    origin = next(o["geometry"]["x"] for o in outputs if o["id"] == output_id)
    frame = view["frame"]
    # Keep the keyboard hint visible while moving onto the app beneath it.
    hint_row = control_point("zones",250,29)[1]-29
    click(panel_x+250, 195)
    key("KEY_RIGHT")
    pointer(panel_x+670, 205); time.sleep(.15)
    p = shot("09a-popout-over-app")
    check("hint is visible above the click-through fixture", p.hint(hint_row, "Center edge softness")
          and origin+frame["x"] < panel_x+670 < origin+frame["x"]+frame["width"]
          and frame["y"] < 205 < frame["y"]+frame["height"])
    click(panel_x+670, 205)
    key("KEY_A"); key("KEY_ENTER")
    for _ in range(30):
        if received.exists():
            break
        time.sleep(.05)
    check("shaded overlay passes clicks and keyboard focus to apps outside handles",
          received.exists() and received.read_text() == "a" and panel.poll() is None)
    check("hint popout passes clicks and does not retain keyboard focus",
          received.exists() and received.read_text() == "a"
          and not shot("09b-popout-focus-lost").hint(hint_row,"Center edge softness",expected=False))
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
    if "palette_path" in globals():
        palette_path.unlink(missing_ok=True)
    log.close()
    sock.close()
print(f"{passed} passed; {failed} failed", flush=True)
raise SystemExit(bool(failed))
