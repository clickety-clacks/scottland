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
    # Real input raises the back window. The old front's left edge is now hidden.
    click(350,250); pointer(30,30); time.sleep(2)
    reversed=shot("04-reversed-stacking")
    check("raising a window hides the now-obscured film", change(reversed.getpixel(film), reversed.getpixel(clear)) < 8)
    click(820,360); pointer(30,30); time.sleep(2)
    # Put the rear attention pulse beside the join, on exposed goo. In the
    # previous layout it was hundreds of pixels away around the far perimeter.
    place("goo-back",260,380,480,180)
    options(goo_wave_height=.55, goo_hover_cloudiness=0, goo_hover_emissivity=0)
    for _ in range(450):
        if sample(20,20)["sleeping"]: break
        time.sleep(.1)
    front=view("goo-front")["frame"]; back=view("goo-back")["frame"]
    probe=(front["x"]-2,back["y"]+9)
    before_dye=color(sample(*probe))
    ipc("scottland/attention", {"window":view("goo-back")["id"], "attention":True, "source":"film-join"})
    peak=0
    for _ in range(120):
        peak=max(peak,abs(sample(*probe)["wave"])); time.sleep(.025)
    print("join wave peak",peak,flush=True)
    check("rear goo waves cross into the front film", peak>.032)
    check("rear dye crosses the join into the front film", change(before_dye,color(sample(*probe)))>.015)
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
