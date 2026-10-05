#!/usr/bin/env python3
"""S1-S19 via real stipc input in a caller-owned headless session.
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
solar = art / "settings-home/scottland/solar.ini"
solar.unlink(missing_ok=True)
probe_panel = None
probe_instance_pid = None
last_snapshot = {}
def settings_quickshell_pid(wrapper_pid):
    """Resolve the QML process below the headless qs/bwrap wrapper for targeted IPC."""
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        pending = [wrapper_pid]
        seen = set()
        while pending:
            pid = pending.pop()
            if pid in seen:
                continue
            seen.add(pid)
            process = Path(f"/proc/{pid}")
            try:
                comm = (process / "comm").read_text().strip()
                argv = (process / "cmdline").read_bytes().decode(errors="replace").split("\0")
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                continue
            if comm == "quickshell" and str(repo / "core/settings") in argv:
                return pid
            try:
                children = (process / "task" / str(pid) / "children").read_text().split()
                pending.extend(int(child) for child in children)
            except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError):
                pass
        time.sleep(.03)
    raise RuntimeError(f"could not find QuickShell below test wrapper PID {wrapper_pid}")


def snapshot():
    global last_snapshot
    if probe_panel and probe_panel.poll() is None:
        last_snapshot=json.loads(subprocess.check_output(
            ["qs","ipc","--pid",str(probe_instance_pid),"call","settings-test","snapshot"],text=True,timeout=5))
    return last_snapshot

def screen_point(p):
    return panel_x+p["x"]-panel_output_origin, panel_y+p["y"]

def control_point(name, dx=0, dy=0):
    p=snapshot()[name]; x,y=screen_point(p); return x+dx,y+dy

def scroll_to(value):
    # Drag the real scrollbar thumb; use observed geometry to compute its travel.
    q=snapshot(); v=q["viewport"]; x,y=screen_point(v)
    thumb=v["height"]*v["height"]/q["contentHeight"]
    start=y+q["scroll"]/q["contentHeight"]*v["height"]+thumb/2
    end=y+max(0,min(q["contentHeight"]-v["height"],value))/q["contentHeight"]*v["height"]+thumb/2
    drag(x+v["width"]-5,start,0,end-start)

def reveal(name, dx=0, dy=0, margin=24):
    # Test the actual control coordinate against the live clipped viewport.
    q=snapshot(); target=q[name]["y"]+dy; v=q["viewport"]
    if target < v["y"]+margin:
        scroll_to(q["scroll"]+target-(v["y"]+margin))
    elif target > v["y"]+v["height"]-margin:
        scroll_to(q["scroll"]+target-(v["y"]+v["height"]-margin))
    return control_point(name, dx, dy)

def tab(index):
    for _ in range(3):
        click(panel_x+36+(index+.5)*(snapshot()["panel"]["width"]-72)/6,panel_y+100)
        if snapshot()["tab"]==index: return
        time.sleep(.12)
    print("TAB MISS",index, snapshot()["tab"],"panel",panel_x,panel_y,flush=True)

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

def string_option(name):
    return str(ipc("wayfire/get-config-option", {"option": "scottland/" + name})["value"])

def bool_option(name):
    return str(ipc("wayfire/get-config-option", {"option": "scottland/" + name})["value"]).lower() in ("true", "1")

def bool_option_reaches(name, expected, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if bool_option(name) is expected:
            return True
        time.sleep(.03)
    return False


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


def option_reaches_change(name, before, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if option(name) != before: return True
        time.sleep(.05)
    return False

def option_reaches(name, expected, timeout=2):
    # QML debounces previews and invokes an asynchronous ctl process. Wait for its result,
    # not an assumed process-start/IPC latency; never resend input to make a check pass.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if abs(option(name)-expected) < .01:
            return True
        time.sleep(.03)
    return False

def string_option_reaches(name, expected, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if string_option(name) == expected:
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
        observations = []
        for line in (art / "panel.log").read_text().splitlines():
            if "SCOTTLAND_HINT " in line:
                observations.append(json.loads(line.split("SCOTTLAND_HINT ", 1)[1]))
        active = {getattr(proc, "hint_probe", None) for proc in clients if proc.poll() is None}
        # The QML observer can log a hide after the frame being checked was captured. Pixels
        # establish visibility for that frame; this history only identifies the right control.
        entry = next((v for v in reversed(observations)
                      if v["probe"] in active and v["label"] == label and v["visible"]), None)
        if not entry or entry["width"] != 240 or entry["height"] < 40:
            return False
        width, height = round(entry["width"]), round(entry["height"])
        background, border = (tuple(bytes.fromhex(entry[k].lstrip("#")))
                              for k in ("background", "border"))
        expected_y = max(0, min(self.img.get_height()-height,round(row_y+34-height/2)))
        xs = range(max(0,round(x)-3),min(self.img.get_width()-width,round(x)+3)+1) if x is not None else range(0,self.img.get_width()-width)
        for y in range(max(0,expected_y-20),min(self.img.get_height()-height,expected_y+20)+1):
            for xx in xs:
                top=[(xx+20,y),(xx+width//2,y),(xx+width-20,y)]
                sides=[(xx,y+height//2),(xx+width-1,y+height//2)]
                inside=[(xx+5,y+height//2),(xx+width-5,y+height//2),
                        (xx+width//2,y+height-6)]
                if all(self.pixel(px,py)==border for px,py in top) and any(
                    self.pixel(px,py)==border for px,py in sides) and any(
                    self.pixel(px,py)==background for px,py in inside):
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
    global panel_x, panel_y, panel_output_origin, probe_panel, probe_instance_pid
    probe = str(len(clients))
    panel = subprocess.Popen(["qs", "-n", "-p", str(repo / "core/settings")],
        env=dict(os.environ, QS_DISABLE_FILE_WATCHER="1", SCOTTLAND_CTL=str(repo / "core/libexec/scottland-ctl"),
                 SCOTTLAND_LAYOUT_FILE=str(layout), SCOTTLAND_PALETTE=str(palette_path),
                 SCOTTLAND_SOLAR_FILE=str(solar),
                 SCOTTLAND_HINT_PROBE=probe, SCOTTLAND_SETTINGS_TEST="1"), stdout=log, stderr=log)
    panel.hint_probe = probe
    clients.append(panel)
    probe_panel=panel
    probe_instance_pid=settings_quickshell_pid(panel.pid)
    for _ in range(100):
        if panel.poll() is not None:break
        try:
            if snapshot().get("screen"):break
        except (json.JSONDecodeError,subprocess.CalledProcessError):pass
        time.sleep(.05)
    check("settings maps", panel.poll() is None)
    time.sleep(.4)
    panel_output=next(o for o in outputs if o["name"]==snapshot()["screen"])
    panel_output_origin=panel_output["geometry"]["x"]
    panel_x=panel_output["geometry"]["x"]+(panel_output["geometry"]["width"]-snapshot()["panel"]["width"])/2
    panel_y=panel_output["geometry"]["y"]+panel_output["geometry"]["height"]-max(24,round(panel_output["geometry"]["height"]*.04))-snapshot()["panel"]["height"]
    shot("panel-position")
    return panel


def close_panel(panel, save=False, via_button=False):
    if via_button:
        click(panel_x + snapshot()["panel"]["width"] - (86 if save else 196), panel_y + snapshot()["panel"]["height"]-56)
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
    panel_x = outputs[0]["geometry"]["x"] + (outputs[0]["geometry"]["width"]-806)/2
    panel_y = 58
    initial = values()
    initial_edge = {name: option(name) for name in (
        "unfocused_edge_tone_light", "unfocused_edge_tone_dark", "unfocused_edge_strength")}
    initial_attention_family = string_option("attention_color_family")
    check("GO22 defaults to the palette attention color", initial_attention_family == "theme")
    palette_path = art / "palette.json"
    palette_path.write_text(json.dumps(dict(scheme="dark",background="#1c1d22",foreground="#e6e6e9",accent="#7aa2f7")))
    baseline_motion=motion_trial()
    baseline_resize=motion_trial(True)
    panel = open_panel()
    check("S15 heading/application name",snapshot()["title"] == "Scottland Settings")
    check("S15 launcher name", "Name=Scottland Settings" in (repo/"core/settings/scottland-settings.desktop").read_text())
    bands("01-softness-bands")
    tab(1)
    check("GO22 selector appears in Goo Settings", set(snapshot()["attentionColor"]) == {"theme", "warm", "cool"})
    for family in ("warm", "cool", "theme"):
        rect = snapshot()["attentionColor"][family]
        click(*screen_point({"x":rect["x"]+rect["width"]/2,"y":rect["y"]+rect["height"]/2}))
        check("GO22 " + family + " selection previews through real input",
              string_option_reaches("attention_color_family", family)
              and snapshot()["values"]["attention_color_family"] == family)
    rect = snapshot()["attentionColor"]["cool"]
    click(*screen_point({"x":rect["x"]+rect["width"]/2,"y":rect["y"]+rect["height"]/2}))
    close_panel(panel,save=True,via_button=True)
    check("GO22 Save stores the selected family", "attention_color_family = cool" in layout.read_text())
    panel=open_panel();tab(1)
    check("GO22 saved family is restored on reopen", snapshot()["values"]["attention_color_family"] == "cool")
    rect = snapshot()["attentionColor"]["warm"]
    click(*screen_point({"x":rect["x"]+rect["width"]/2,"y":rect["y"]+rect["height"]/2}))
    close_panel(panel)
    check("GO22 Cancel restores its opening family", string_option_reaches("attention_color_family", "cool"))
    panel=open_panel();tab(1)
    rect = snapshot()["attentionColor"]["warm"]
    click(*screen_point({"x":rect["x"]+rect["width"]/2,"y":rect["y"]+rect["height"]/2}))
    click(panel_x+80,panel_y+snapshot()["panel"]["height"]-56)
    check("GO22 Defaults previews Theme", string_option_reaches("attention_color_family", "theme"))
    close_panel(panel,save=True,via_button=True)
    check("GO22 Theme choice saves", "attention_color_family = theme" in layout.read_text())
    layout.unlink(missing_ok=True)  # Keep the existing suite's fresh-file Save/Cancel baseline.
    panel=open_panel()
    tab(0)
    for i,label in enumerate(["Center edge softness","Center zone width","Widget rail width"]):
        point = control_point("zones",200,34+69*i)
        row_top = point[1]-34
        pointer(*point);time.sleep(.25)
        check(label+" hover hint",snapshot()["zones"]["hint"]==label)
        if i == 0:
            check("first Layout hint has its expected visible bubble",
                  shot("02-layout-hover").hint(row_top,label))
    point = control_point("zones",200,34+69*2)
    row_top = point[1]-34
    pointer(10,690);time.sleep(.15)
    check("leaving a hovered row hides its bubble",
          not shot("02a-layout-leave").hint(row_top,"Widget rail width",expected=False))
    click(*control_point("zones",200,34));pointer(10,690);key("KEY_BACKSPACE")
    before=option("center_width");key("KEY_DOWN");key("KEY_RIGHT")
    check("keyboard step previews zone live",option_reaches("center_width",round((before+.5)*2)/2))
    check("keyboard hint follows selection",snapshot()["zones"]["hint"]=="Center zone width")
    center_row = control_point("zones",200,34+69)[1]-29
    check("keyboard selection draws its hint bubble",
          shot("02b-keyboard-hint").hint(center_row,"Center zone width"))
    key("KEY_UP");key("KEY_1");key("KEY_2");key("KEY_0")
    check("numeric entry previews softness",option_reaches("blend_width",120))
    for _ in range(4):key("KEY_BACKSPACE")
    check("Backspace restores opening row",option_reaches("blend_width",initial["blend_width"]))
    # Knobs have 36pt hit disks; select offset from the small visible handle.
    e=snapshot()["editor"];p=e["plot"]
    reveal("editor",0,p["y"]-e["y"]+p["height"]*.5)
    e=snapshot()["editor"];p=e["plot"]
    click(*screen_point(dict(x=p["x"]+p["width"]*.45,y=p["y"]+p["height"]*.4)))
    check("curve click adds and selects",len(snapshot()["editor"]["knots"])==3 and snapshot()["editor"]["selected"]==1)
    key("KEY_DELETE")
    check("Delete removes selected interior knot",len(snapshot()["editor"]["knots"])==2)
    e=snapshot()["editor"];p=e["plot"]
    click(*screen_point(dict(x=p["x"]+p["width"]*.5,y=p["y"]+p["height"]*.5)))
    key("KEY_BACKSPACE")
    check("Backspace removes selected interior knot",len(snapshot()["editor"]["knots"])==2)
    knot=snapshot()["editor"]["knots"][0]
    q=snapshot();v=q["viewport"]
    if knot["y"]<v["y"]+24: scroll_to(q["scroll"]+knot["y"]-(v["y"]+24))
    elif knot["y"]>v["y"]+v["height"]-24: scroll_to(q["scroll"]+knot["y"]-(v["y"]+v["height"]-24))
    knot=snapshot()["editor"]["knots"][0];x,y=screen_point(knot)
    click(x+14,y);key("KEY_DELETE")
    check("36pt endpoint target selects but cannot be deleted",snapshot()["editor"]["selected"]==0 and len(snapshot()["editor"]["knots"])==2)
    shot("03-curve-selected")
    tab(1);check("Goo tab selects",snapshot()["tab"]==1)
    edge_rows = snapshot()["goo"]["edgeControls"]
    check("edge tone slider follows active dark scheme",
          edge_rows[0]["name"] == "unfocused_edge_tone_dark")
    drag(*control_point("goo",350,34),-180,live_name="unfocused_edge_tone_dark")
    check("tone slider applies while held",abs(option("unfocused_edge_tone_dark")-initial_edge["unfocused_edge_tone_dark"])>.01)
    key("KEY_BACKSPACE")
    check("tone Backspace restores opening value",option_reaches("unfocused_edge_tone_dark",initial_edge["unfocused_edge_tone_dark"]))
    drag(*reveal("goo",350,103),-180,live_name="unfocused_edge_strength")
    check("edge strength slider applies while held",abs(option("unfocused_edge_strength")-initial_edge["unfocused_edge_strength"])>.01)
    key("KEY_BACKSPACE")
    check("edge strength Backspace restores opening value",option_reaches("unfocused_edge_strength",initial_edge["unfocused_edge_strength"]))
    click(*reveal("goo",240,2*69+34));key("KEY_BACKSPACE");pointer(10,690)
    labels=["Border thickness","Reach","Bridge draw","Swell","Mess","Lump size","Drift","Wave speed","Wave persistence","Wave height","Dye spread","Dye swirl","Dye release","Shine","Relief","Liquid depth","Wall wetting","Wallpaper soak","Overlap film","Control cloudiness","Control glow","Control proximity","Dye strength"]
    for i,label in enumerate(labels):
        if i:key("KEY_DOWN")
        for _ in range(20):
            if snapshot()["goo"]["hint"]==label:break
            time.sleep(.025)
        check(label+" keyboard hint and automatic reveal",snapshot()["goo"]["hint"]==label)
    key("KEY_RIGHT");check("GO23 Dye strength previews live",option_reaches("goo_dye_strength",1.01))
    shot("04-goo-keyboard")
    tab(0);tab(1)
    # A discrete wheel burst ends before the position samples; the coast must continue,
    # then settle without a position-animation restart or an edge jump.
    pointer(*control_point("viewport",300,200))
    ipc("scottland/test-input",dict(scroll_y=90,wheel=True))
    time.sleep(.03)
    check("wheel burst moves a comfortable notch distance",abs(snapshot()["scroll"]-576)<120)
    wheel_samples=[]
    for _ in range(30):
        time.sleep(.05);q=snapshot();wheel_samples.append((q["scroll"],q["wheelVelocity"]))
    (art/"wheel-samples.json").write_text(json.dumps(wheel_samples))
    check("wheel continues moving after input stops",len(set(round(y,1) for y,v in wheel_samples))>=3)
    a=snapshot()["scroll"];time.sleep(.25)
    check("wheel decelerates to rest",abs(snapshot()["scroll"]-a)<.1 and snapshot()["wheelVelocity"]==0)
    tab(0);tab(1)
    pointer(*control_point("viewport",300,200))
    before_pad=snapshot()["scroll"]
    for _ in range(4):
        ipc("scottland/test-input",dict(scroll_y=20,wheel=False,touchpad=True));time.sleep(.02)
    check("touchpad content tracks 80 pt gesture",abs(snapshot()["scroll"]-before_pad-80)<16)
    ipc("scottland/test-input",dict(scroll_y=0,wheel=False,touchpad=True))
    pad_samples=[]
    for _ in range(30):
        time.sleep(.05);q=snapshot();pad_samples.append((q["scroll"],q["wheelVelocity"]))
    (art/"touchpad-samples.json").write_text(json.dumps(pad_samples))
    check("touchpad coasts after axis stop",len(set(round(y,1) for y,v in pad_samples))>=3)
    a=snapshot()["scroll"];time.sleep(.25);check("touchpad coast settles",abs(snapshot()["scroll"]-a)<.1)
    tab(0);tab(1)
    # After mouse editing that same row, touch must still be able to take over for scrolling.
    click(*reveal("goo",250,4*69+34));key("KEY_BACKSPACE")
    # A vertical touch gesture on a slider scrolls without changing its value.
    q=snapshot(); v=q["viewport"]
    x,y=screen_point(dict(x=v["x"]+250,y=v["y"]+min(340,v["height"]-35)))
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
    edge_x = outputs[0]["geometry"]["x"] + outputs[0]["geometry"]["width"] - 24 - 560
    pointer(edge_x+180,220);time.sleep(.2)
    check("right-edge hint bubble is visible after flipping left",
          shot("06c-popout-flipped-left").hint(196,"Center zone width",x=edge_x-264))
    pointer(10,690);time.sleep(.15)
    check("flipped hint hides when pointer leaves",
          not shot("06d-flipped-popout-leave").hint(196,"Center zone width",x=edge_x-264,expected=False))
    fixture.terminate();fixture.wait(timeout=5)

    panel=open_panel();tab(2)
    check("Window mode tab selects",snapshot()["tab"]==2)
    check("always-avoid setting defaults off", not bool_option("window_avoidance_always") and
          snapshot()["motion"]["window_avoidance_always"] is False)
    click(*reveal("alwaysAvoidance",160,21))
    check("Window mode toggle switches always-avoid on live",
          bool_option_reaches("window_avoidance_always",True) and
          snapshot()["motion"]["window_avoidance_always"] is True)
    click(*reveal("alwaysAvoidance",160,21))
    check("Window mode toggle switches always-avoid off live", bool_option_reaches("window_avoidance_always",False))
    playground = snapshot()["playground"]
    click(*reveal("playground",playground["width"]/2,192))
    key("KEY_RIGHT");time.sleep(.7)
    check("playground arrow moves and stops at analytic distance",abs(snapshot()["playground"]["distance"]-335**2/(2*608))<.1 and snapshot()["playground"]["velocity"]==0)
    # The velocity arrow edits its compositor option live; rail motion has no rebound control.
    click(*reveal("playground",200,325))
    check("impulse arrow edits live option",option_reaches("key_impulse",10000))
    playground = snapshot()["playground"]
    click(*reveal("playground",playground["width"]/2,192))
    for _ in range(8):
        key("KEY_RIGHT")
        if snapshot()["playground"]["widgetized"]:break
    check("side contact morphs the sample into a rail widget",
          snapshot()["playground"]["widgetized"] and snapshot()["playground"]["widgetSide"]==1)
    shot("05-window-playground")
    click(panel_x+80,panel_y+snapshot()["panel"]["height"]-56);time.sleep(.25) # Defaults keeps impulse identical for the motion comparison.
    def edit_coast(name, impulse_name, friction_name, seconds=.9, points=170):
        q=snapshot()
        scroll_to(q["scroll"]+q[name]["y"]-q["viewport"]["y"]-20)
        q=snapshot();g=q[name];p=g["plot"]
        target=dict(x=p["x"]+seconds/2.5*p["width"],
                    y=p["y"]+(1-points/600)*p["height"])
        bottom=max(g["endpoint"]["y"],target["y"])
        v=q["viewport"]
        if bottom>v["y"]+v["height"]-24:
            scroll_to(q["scroll"]+bottom-(v["y"]+v["height"]-24))
            q=snapshot();g=q[name];p=g["plot"]
            target=dict(x=p["x"]+seconds/2.5*p["width"],
                        y=p["y"]+(1-points/600)*p["height"])
        x,y=screen_point(g["endpoint"]);tx,ty=screen_point(target)
        drag(x,y,tx-x,ty-y)
        observed=snapshot()[name]
        duration=observed["duration"];distance=observed["distance"]
        expected_impulse=2*distance/duration if duration else 0
        expected_friction=2*distance/(duration*duration) if duration else 0
        check(name+" endpoint sets impulse and deceleration live",
              abs(option(impulse_name)-expected_impulse)<2 and
              abs(option(friction_name)-expected_friction)<3 and
              abs(duration-seconds)<.04 and abs(distance-points)<5)
    edit_coast("movement","key_impulse","key_friction")
    shot("06-window-coast")
    edit_coast("resize","resize_impulse","resize_friction")
    click(*reveal("alwaysAvoidance",160,21))
    check("always-avoid toggle previews on before Save", bool_option("window_avoidance_always"))
    saved_motion=dict(snapshot()["motion"])
    close_panel(panel,save=True,via_button=True)
    changed_motion=motion_trial(); changed_resize=motion_trial(True)
    print("coast distances",baseline_motion,changed_motion,baseline_resize,changed_resize,flush=True)
    check("movement graph sets real arrow travel",abs(changed_motion-170)<4 and baseline_motion>80)
    check("resize graph sets real size coast",abs(changed_resize-170)<4 and baseline_resize>80)
    check("Save persists all Window mode options",all(k+" =" in layout.read_text() for k in saved_motion))
    check("Save writes the always-avoid choice", "window_avoidance_always = true" in layout.read_text())
    panel=open_panel();tab(2)
    check("reopen retains both coast endpoints",
          all(abs(snapshot()["motion"][k]-saved_motion[k])<.01 for k in
              ("key_impulse","key_friction","resize_impulse","resize_friction")) and
          snapshot()["motion"]["window_avoidance_always"] is True and bool_option("window_avoidance_always"))
    q=snapshot();scroll_to(q["scroll"]+q["motionSettings"]["y"]-q["viewport"]["y"]-20)
    click(*control_point("motionSettings",50,34));key("KEY_1")
    check("speed limit row accepts its positive minimum",option_reaches("key_max_velocity",1))
    scroll_to(10000)
    click(*reveal("holdTiming",120,60));key("KEY_RIGHT")
    check("hold timeline edits live timing",option("alt_hold_delay")>300)
    click(*reveal("doubleTiming",180,60));key("KEY_RIGHT")
    check("double-tap timeline edits live timing",option("window_double_tap_delay")>300)
    click(*reveal("hintHoldTiming",180,60));key("KEY_RIGHT")
    check("hint hold timeline edits live timing",option("window_hold_delay")>500)
    click(*reveal("soloPause",180,60));key("KEY_RIGHT")
    check("solo pause timeline edits the live audition delay",option_reaches_change("solo_audition_delay",3000))
    click(*reveal("soloHotspot",180,60));key("KEY_RIGHT")
    check("solo hotspot row edits the live hotspot",option_reaches_change("solo_audition_hotspot",50))
    shot("06a-window-timelines")
    click(panel_x+80,panel_y+snapshot()["panel"]["height"]-56);time.sleep(.2)
    check("Window Defaults restores original feel",option("key_impulse")==335 and option("key_friction")==608
          and option("resize_impulse")==335 and option("resize_friction")==608
          and not bool_option("window_avoidance_always") and option("solo_audition_delay")==3000
          and option("solo_audition_hotspot")==50)
    close_panel(panel,via_button=True)
    check("Cancel restores saved motion after Defaults",all(abs(option(k)-saved_motion[k])<.01 for k in
          ("key_impulse","key_friction","resize_impulse","resize_friction")))
    # Reset via the actual Defaults action and Save before the border regression checks.
    panel=open_panel();tab(2);click(panel_x+80,panel_y+snapshot()["panel"]["height"]-56);close_panel(panel,save=True)
    check("Window Defaults saves always-avoid off", not bool_option("window_avoidance_always") and
          "window_avoidance_always = false" in layout.read_text())
    layout.unlink()
    # Theme applies to every control, not only hints.
    panel=open_panel()
    palette_path.write_text(json.dumps(dict(scheme="light",background="#eff1f8",foreground="#20212a",accent="#3855aa",font_family="DejaVu Serif",text_scale=1.5)))
    time.sleep(.6);p=shot("07-light-theme")
    check("panel follows light session palette",p.pixel(panel_x+10,panel_y+10)==(239,241,248))
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
    click(*control_point("zones",470,34))  # softness near 300, visibly capped at half the side span
    bands("08-capped-softness")
    check("slider can exceed the effective band width", option("blend_width") > geometry(outputs[1])[-1])
    click(*control_point("zones",20,34));key("KEY_0")  # zero softness
    check("softness reaches zero", option("blend_width") == 0)
    unobscured = next(o for o in outputs if not o["geometry"]["x"] <= panel_x < o["geometry"]["x"]+o["geometry"]["width"])
    origin, width, center, rail, blend = geometry(unobscured)
    drag(origin+center, 600, -30, live_name="blend_width")
    check("coincident softness handle can open a zero band", option("blend_width") == 30)
    click(*control_point("zones",20,34));key("KEY_0")
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
    hint_row = control_point("zones",250,34)[1]-34
    click(*control_point("zones",250,34))
    key("KEY_RIGHT")
    pointer(panel_x+snapshot()["panel"]["width"]+40, 205); time.sleep(.15)
    p = shot("09a-popout-over-app")
    check("hint is visible above the click-through fixture", p.hint(hint_row, "Center edge softness"))
    click(panel_x+snapshot()["panel"]["width"]+40, 205)
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
    click(*control_point("zones",250,34))  # focus settings again to exercise Escape
    close_panel(panel)
    panel=open_panel();tab(3)
    check("Translucency tab selects",snapshot()["tab"]==3)
    click(*control_point("opacitySettings",361,34))
    check("center opacity slider previews live",option_reaches("center_opacity_focused",.5))
    close_panel(panel,save=True,via_button=True)
    check("Save persists opacity", "center_opacity_focused = 0.5" in layout.read_text())
    panel=open_panel();tab(3)
    click(panel_x+80,panel_y+snapshot()["panel"]["height"]-56)
    check("Translucency Defaults previews opaque",option_reaches("center_opacity_focused",1))
    close_panel(panel)
    check("Translucency Cancel restores saved opacity",option_reaches("center_opacity_focused",.5))

    panel=open_panel();tab(4)
    check("Widgets tab selects",snapshot()["tab"]==4)
    click(*control_point("widgetSettings",361,34))
    check("widget expand bounce previews live",option_reaches("widget_bounce",.05))
    click(*control_point("widgetSettings",361,103))
    check("hover intent timing previews live",option("widget_peek_enter_delay")>1000)
    dwell_before=option("widget_make_room_dwell")
    # Focus the stack, then use its real keyboard selection so pointer placement does not
    # itself change the dwell slider's value before the one-step preview assertion.
    click(*control_point("widgetSettings",361,34))
    for _ in range(4): key("KEY_DOWN")
    key("KEY_RIGHT")
    check("rail make-room pause previews from the Widgets tab",
          option_reaches("widget_make_room_dwell",min(1500,dwell_before+10)))
    saved_widgets=dict(snapshot()["widgets"])
    close_panel(panel,save=True,via_button=True)
    check("Save persists widget settings",all(k+" =" in layout.read_text() for k in saved_widgets))
    panel=open_panel();tab(4)
    click(panel_x+80,panel_y+snapshot()["panel"]["height"]-56)
    check("Widgets Defaults preview shipped bounce",option_reaches("widget_bounce",.04))
    check("Widgets Defaults preview the rail pause default",option_reaches("widget_make_room_dwell",350))
    close_panel(panel)
    check("Widgets Cancel restores saved bounce",option_reaches("widget_bounce",saved_widgets["widget_bounce"]))
    check("Widgets Cancel restores saved rail pause",
          option_reaches("widget_make_room_dwell",saved_widgets["widget_make_room_dwell"]))

    panel=open_panel();tab(5)
    check("Sunlight tab selects",snapshot()["tab"]==5)
    enable=snapshot()["solarEnable"]
    click(*control_point("solarEnable",enable["width"]/2,21))
    click(*control_point("solarSettings",400,34))
    click(*control_point("solarSettings",450,103))
    check("manual location becomes active after both coordinates",snapshot()["solar"]["location_set"])
    network=snapshot()["solarNetwork"]
    click(*control_point("solarNetwork",network["width"]/2,21))
    check("network location requires an explicit toggle",snapshot()["solar"]["allow_ip"])
    close_panel(panel,save=True,via_button=True)
    check("Sunlight Save writes isolated location and opt-in",solar.exists() and
          all(line in solar.read_text() for line in ("enabled = true","allow_ip = true","location_set = true")))
    panel=open_panel();tab(5)
    click(panel_x+80,panel_y+snapshot()["panel"]["height"]-56)
    check("Sunlight Defaults restore shipped following and network location",
          snapshot()["solar"]["enabled"] and snapshot()["solar"]["allow_ip"])
    close_panel(panel)
    check("Sunlight Cancel retains saved location policy",all(line in solar.read_text() for line in
          ("enabled = true","allow_ip = true","location_set = true")))
    # Restore caller's session settings; the saved fixture remains evidence.
    ipc("wayfire/set-config-options", {"scottland/"+k:v for k,v in {**initial,**initial_edge}.items()})
    ipc("wayfire/set-config-options", {"scottland/attention_color_family":initial_attention_family})
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
