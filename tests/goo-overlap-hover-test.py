#!/usr/bin/env python3
"""GO11-GO12 in an already-running isolated headless session, with real stipc input.
Run through tests/headless.sh run; artifacts stay in build/goo-overlap-hover-evidence. Geometry IPC is used
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
art = repo / "build/goo-overlap-hover-evidence"
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


def film_band(pixels, y=370):
    # The fixture's back content is flat dark gray at this row. The film is
    # brighter blue-gray, ending at the front window's dark content edge.
    start = next(x for x in range(480,720) if sum(pixels.getpixel((x,y))) > 150)
    end = next(x for x in range(start+1,720) if sum(pixels.getpixel((x,y))) <= 120)
    return start, end-start


try:
    options(goo=True, center_width=90, min_scale=1, max_scale=1, scale_curve="0:1 1:1", sounds=False,
            goo_noise=0, goo_drift=0, goo_wave_height=0, goo_swell=0, goo_overlap_film=4,
            goo_hover_cloudiness=.65, goo_hover_emissivity=.35, goo_hover_distance=48)
    spawn("goo-back"); place("goo-back", 260, 160, 480, 360)
    spawn("goo-front"); place("goo-front", 530, 280, 360, 260)
    click(700, 350); pointer(30, 30); time.sleep(4)
    (art / "geometry.json").write_text(json.dumps(views(), indent=2))
    base = shot("01-overlap-film")
    # Along the front left edge: inside the back content, away from its border.
    film = (round(view("goo-front")["frame"]["x"])-2, 370); clear = (film[0]-16, 370)
    check("front edge film is visible over back content", change(base.getpixel(film), base.getpixel(clear)) > 15)
    check("film stays thin over back content", change(base.getpixel((519,370)), base.getpixel(clear)) < 8)
    check("front content remains untouched", base.getpixel((600,370)) == base.getpixel((700,370)))
    options(goo_overlap_film=12); time.sleep(1)
    wide=shot("02-wide-film")
    check("film width changes live", change(wide.getpixel((523,370)),base.getpixel((523,370))) > 10)
    options(goo_overlap_film=0); time.sleep(1)
    zero=shot("03-no-film")
    check("zero hides overlap film", change(zero.getpixel(film),zero.getpixel(clear)) < 8)
    options(goo_overlap_film=4); time.sleep(1)
    # GO11 correction: the inner film follows the very same window swell as
    # the outer goo. Probe a point outside the resting 4 pt film but inside a
    # swollen one, over the back window's content and far from the shore.
    options(goo_swell=.7)
    pointer(30,30); time.sleep(3)
    left = round(view("goo-front")["frame"]["x"])
    swell_probe = (left-6, 370)
    rest = shot("01a-film-swell-rest")
    rest_field = sample(*swell_probe)
    check("resting overlap film keeps its set width", rest_field["density"] < rest_field["threshold"])
    pointer(898,360); time.sleep(1.5)
    hovered = shot("01b-film-swell-hover")
    hover_field = sample(*swell_probe)
    check("real pointer hover thickens the inner film", view("goo-front")["frame"]["swell"] > .7
          and hover_field["density"] > hover_field["threshold"]
          and change(hovered.getpixel(swell_probe), rest.getpixel(swell_probe)) > 12)
    pointer(30,30); time.sleep(.08)
    shot("01c-film-swell-leaving")
    check("inner film begins easing after pointer leaves", view("goo-front")["frame"]["swell"] > .3)
    time.sleep(3)
    eased = shot("01d-film-swell-restored")
    check("inner film eases back to the set width", sample(*swell_probe)["density"] < rest_field["threshold"]
          and change(eased.getpixel(swell_probe),rest.getpixel(swell_probe)) < 12)
    options(goo_swell=0)
    pointer(898,360); time.sleep(1.5)
    no_swell = shot("01e-film-swell-zero")
    check("zero swell setting keeps film at rest during hover", view("goo-front")["frame"]["swell"] > .7
          and sample(*swell_probe)["density"] < rest_field["threshold"]
          and change(no_swell.getpixel(swell_probe),rest.getpixel(swell_probe)) < 12)
    pointer(30,30); time.sleep(3)
    options(goo_swell=.7)
    # A separate non-overlapping focus target lets the front window request
    # attention while it remains visually in front of the back window.
    spawn("goo-attention-sink"); place("goo-attention-sink",1000,100,200,150)
    click(1090,160); pointer(30,30); time.sleep(.5)
    ipc("scottland/attention", {"window":view("goo-front")["id"], "attention":True, "source":"film-swell"})
    attention_swells = []
    attention_fields = []
    for _ in range(60):
        attention_swells.append(sample(*swell_probe)["breath"])
        attention_fields.append(sample(*swell_probe)["density"])
        time.sleep(.09)
    shot("01f-film-attention-breathing")
    print("attention swell range",min(attention_swells),max(attention_swells),
          "film density range",min(attention_fields),max(attention_fields),flush=True)
    check("attention breathing gently swells the inner film", max(attention_fields) > min(attention_fields)*1.05)
    check("attention no longer drives the simulation spring", abs(view("goo-front")["frame"]["swell"]) < .001)
    check("attention film breathes rather than freezing", max(attention_swells)-min(attention_swells) > .08)
    ipc("scottland/attention", {"window":view("goo-front")["id"], "attention":False, "source":"film-swell"})
    ipc("window-rules/close-view", {"id":view("goo-attention-sink")["id"]})
    time.sleep(3)
    f = view("goo-front")["frame"]
    touch_x, touch_y = round(f["x"]+f["width"]*.55), round(f["y"]+f["height"]*.35)
    ipc("stipc/touch", {"finger":0,"x":touch_x,"y":touch_y})
    time.sleep(.7)  # a held finger lifts the window
    for i in range(1,6):
        ipc("stipc/touch", {"finger":0,"x":touch_x+round(i*4),"y":touch_y})
        time.sleep(.04)
    dragged = view("goo-front")["frame"]
    lifted = shot("01g-film-lift-drag")
    rest_band, lifted_band = film_band(rest), film_band(lifted)
    print("lift", "swell",dragged["swell"],"rest/lift film bands",rest_band,lifted_band,flush=True)
    check("touch lift and drag swell the moving inner film", dragged["swell"] > .5
          and ipc("scottland/desktop-model")["drag"]["started"]
          and lifted_band[0] > rest_band[0]+10 and lifted_band[1] > rest_band[1]+2)
    ipc("stipc/touch_release", {"finger":0})
    pointer(30,30); time.sleep(3)
    settled = shot("01h-film-lift-settled")
    check("dropped film returns to resting width", film_band(settled)[1] <= rest_band[1]+1)
    # Restore this fixture before the original stacking, join and control checks.
    place("goo-front",530,280,360,260)
    options(goo_swell=0)
    pointer(30,30); time.sleep(2)
    # Real input raises the back window. The old front's left edge is now hidden.
    click(350,250); pointer(30,30); time.sleep(2)
    reversed=shot("04-reversed-stacking")
    check("raising a window hides the now-obscured film", change(reversed.getpixel(film), reversed.getpixel(clear)) < 8)
    click(820,360); pointer(30,30); time.sleep(2)
    # Put the rear hover impulse beside the join, on exposed goo.
    # Attention itself must no longer inject waves (GO17).
    place("goo-back",260,320,480,180)
    options(goo_wave_height=.55, goo_hover_cloudiness=0, goo_hover_emissivity=0)
    for _ in range(450):
        if sample(20,20)["sleeping"]: break
        time.sleep(.1)
    front=view("goo-front")["frame"]; back=view("goo-back")["frame"]
    probe=(front["x"]-2,back["y"]+back["height"]-9)
    before_dye=color(sample(*probe))
    ipc("scottland/attention", {"window":view("goo-back")["id"], "attention":True, "source":"film-join"})
    time.sleep(3)
    check("rear dye crosses the join into the front film", change(before_dye,color(sample(*probe)))>.015)
    # Hover excites the rear edge without raising it over the film under test.
    pointer(back["x"]+back["width"]/2-50, back["y"]+back["height"]+3)
    peak=0
    for _ in range(120):
        peak=max(peak,abs(sample(*probe)["wave"])); time.sleep(.025)
    print("join wave peak",peak,flush=True)
    check("rear goo waves cross into the front film", peak>.032)
    pointer(30,30)
    shot("04a-shared-join")
    ipc("scottland/attention", {"window":view("goo-back")["id"], "attention":False, "source":"film-join"})
    place("goo-back",260,160,480,360)
    options(goo_wave_height=0,goo_hover_cloudiness=.65,goo_hover_emissivity=.35)
    pointer(30,30);time.sleep(3)
    # A side highlights along its full exposed length, far from the pointer.
    normal=shot("05-control-rest")
    pointer(898,360); time.sleep(.4)
    bright=shot("06-side-hover")
    check("whole side highlights far from pointer", change(bright.getpixel((895,465)),normal.getpixel((895,465))) > 25)
    check("side hover leaves opposite side clear", change(bright.getpixel((527,370)),normal.getpixel((527,370))) < 15)
    pointer(940,360); time.sleep(.4)
    near=shot("07-side-near")
    check("proximity is weaker than full hover", sum(near.getpixel((895,465))) < sum(bright.getpixel((895,465)))-10)
    pointer(30,30); time.sleep(.04); leaving=shot("08-side-leave")
    time.sleep(.6); left=shot("09-side-restored")
    check("leave eases instead of snapping", sum(leaving.getpixel((895,465))) > sum(left.getpixel((895,465)))+5)
    check("whole side returns to rest", change(left.getpixel((895,465)),normal.getpixel((895,465))) < 15)
    pointer(897,528); time.sleep(.4)
    corner=shot("10-corner-hover")
    check("corner highlights both legs", change(corner.getpixel((877,545)),normal.getpixel((877,545))) > 20
          and change(corner.getpixel((895,524)),normal.getpixel((895,524))) > 20)
    options(goo_hover_emissivity=0); time.sleep(.4)
    cloudy=shot("11-cloud-without-emission")
    check("zero emissivity removes internal light", sum(corner.getpixel((895,524))) > sum(cloudy.getpixel((895,524)))+10)
    # The new film itself remains a real move handle, not a painted line.
    pointer(30,30); time.sleep(.6)
    old=center(view("goo-front")); drag(528,370,45,0)
    check("film drag moves its front window", center(view("goo-front"))[0] > old[0]+25)
    dropped=shot("12-film-drag")
    check("drag clears the old film over back content", change(dropped.getpixel(film),dropped.getpixel(clear)) < 8)
    options(goo_noise=.32,goo_drift=.12,goo_wave_height=.55,goo_swell=.7,goo_hover_emissivity=.35)
    pointer(30,30);time.sleep(3)
    shot("13-shipped-feel")
    drag(*center(view("goo-front")),250,0,True)
    pointer(30,30);time.sleep(1)
    separate=shot("14-separated-again")
    check("drag out of overlap restores the desktop surface without stale film",
          view("goo-front")["frame"]["x"]>view("goo-back")["frame"]["x"]+view("goo-back")["frame"]["width"]
          and change(separate.getpixel(film),separate.getpixel(clear))<8)
finally:
    for v in views():
        if v["title"].startswith("goo-"): ipc("window-rules/close-view", {"id":v["id"]})
    print(f"RESULT {passed} passed, {failed} failed", flush=True)
    sock.close()
raise SystemExit(bool(failed))
