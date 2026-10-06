#!/usr/bin/env python3
"""Screen sharing and screenshots work through the desktop portal (adapter-gaps AG02).

Isolated headless --omarchy session (fixture HOME) on a private D-Bus bus, with the machine's
installed portal backends (gtk, Hyprland's, keyrings) plus xdg-desktop-portal-wlr, and the
shipped core/config/scottland-portals.conf found where the package installs it (a data dir).
If xdg-desktop-portal-wlr is not installed, XDPW_ROOT names an extracted package of it.

An app's view, through tests/portal-client.py: a non-interactive Screenshot, and a ScreenCast of
a monitor, picked in xdg-desktop-portal-wlr's own chooser (slurp) with a stipc pointer click.
Oracle: the pixels the app received (the PNG, and a frame read from the PipeWire stream) show
the window filled with #E0A030 where Wayfire placed it (a corner counter in it keeps frames coming).
Setup waits until the compositor's own screencopy shows that color there, so a window still fading
in is not mistaken for a portal fault. Every process the test starts is stopped by its PID.

  [XDPW_ROOT=...] [GST_PLUGIN_PATH=...] tests/portal-test.py
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from omarchy_fixture import REPO, Checks, Fixture, Session, pixel, read_png, screenshot  # noqa: E402

COLOR = (0xE0, 0xA0, 0x30)
check = Checks()
build = REPO / "build"
system_xdpw = Path("/usr/lib/xdg-desktop-portal-wlr")
xdpw_root = Path(os.environ.get("XDPW_ROOT", "/"))
xdpw = system_xdpw if system_xdpw.exists() else xdpw_root / "usr/lib/xdg-desktop-portal-wlr"
if not xdpw.exists():
    sys.exit("xdg-desktop-portal-wlr is not installed; set XDPW_ROOT to an extracted package")

# Portal backends as installed, plus wlr; the shipped selection where the package puts it.
share = build / "portal-test-share"
portals = share / "xdg-desktop-portal/portals"
portals.mkdir(parents=True, exist_ok=True)
for existing in portals.iterdir():
    existing.unlink()
for backend in Path("/usr/share/xdg-desktop-portal/portals").glob("*.portal"):
    (portals / backend.name).symlink_to(backend)
if not (portals / "wlr.portal").exists():
    (portals / "wlr.portal").symlink_to(xdpw_root / "usr/share/xdg-desktop-portal/portals/wlr.portal")
# Where the package installs it (a data dir). With XDG_DESKTOP_PORTAL_DIR (needed only when
# xdg-desktop-portal-wlr is not installed), the frontend reads configs from that dir alone.
for conf in (share / "xdg-desktop-portal/scottland-portals.conf", portals / "scottland-portals.conf"):
    conf.unlink(missing_ok=True)
    conf.symlink_to(REPO / "core/config/scottland-portals.conf")
portal_dir = "" if xdpw == system_xdpw else f"XDG_DESKTOP_PORTAL_DIR={portals} "
data_home = build / "portal-test-data-home"
data_home.mkdir(exist_ok=True)
gst = os.environ.get("GST_PLUGIN_PATH", "")

fixture = Fixture(build / "portal-fixture")


def views(session):
    reply = session.ipc("window-rules/list-views")
    return reply if isinstance(reply, list) else []


def read_json(path):
    """The JSON a client wrote, or None while it is absent or incomplete."""
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def owned(session, name):
    reply = session.run("dbus-send", "--session", "--print-reply", "--dest=org.freedesktop.DBus",
                        "/org/freedesktop/DBus", "org.freedesktop.DBus.NameHasOwner", f"string:{name}")
    return "boolean true" in reply.stdout


started = []
with Session(fixture, "hl-portal") as session:
    check("shim answers", session.wait_shim()[0])
    session.run("dbus-update-activation-environment", "WAYLAND_DISPLAY", "XDG_CURRENT_DESKTOP")
    if xdpw != system_xdpw:
        started.append(session.spawn(f"exec {xdpw} {os.environ.get('XDPW_ARGS', '')}",
                                      build / "portal-xdpw.log"))
        check("xdg-desktop-portal-wlr is on the bus",
              session.wait(lambda: owned(session, "org.freedesktop.impl.portal.desktop.wlr"))[0])
    started.append(session.spawn(f"exec env {portal_dir}XDG_DATA_HOME={data_home} "
                                 f"XDG_DATA_DIRS={share}:/usr/local/share:/usr/share "
                                 f"/usr/lib/xdg-desktop-portal -r -v", build / "portal-frontend.log"))
    check("portal frontend is on the bus",
          session.wait(lambda: owned(session, "org.freedesktop.portal.Desktop"))[0])

    started.append(session.spawn(f"exec python3 {REPO}/tests/solid-color-app.py portal '#E0A030' --tick"))
    ok, found = session.wait(lambda: [v for v in views(session)
                                      if v.get("app-id") == "org.scottland.SolidColor.portal"
                                      and v.get("mapped")], timeout=20)
    if not check("window maps", ok):
        sys.exit(check.summary())
    box = found[0]["geometry"]
    center = (int(box["x"] + box["width"] / 2), int(box["y"] + box["height"] / 2))
    # Judge pixels away from where the pointer clicks (a cursor or hover effect may be drawn there).
    sample = (int(box["x"] + box["width"] / 4), int(box["y"] + box["height"] / 4))
    shown = {}
    ok, _ = session.wait(lambda: shown.update(rgb=(lambda shot: pixel(shot, *sample) if shot else None)(
        screenshot(session, "portal-ready"))) or shown["rgb"] == COLOR, timeout=10, interval=0.2)
    check("setup: the compositor's screencopy shows the window's color", ok, shown.get("rgb"))

    # Screenshot, as an app asks for one.
    out = build / "portal-screenshot.json"
    out.unlink(missing_ok=True)
    session.run("python3", str(REPO / "tests/portal-client.py"), "screenshot", str(out), timeout=90)
    result = read_json(out) or {}
    uri = result.get("results", {}).get("uri", "")
    check("Screenshot portal answers with an image", result.get("response") == 0 and uri, result)
    if uri:
        width, height, rgb = read_png(uri.removeprefix("file://"))
        offset = (sample[1] * width + sample[0]) * 3
        check("the screenshot shows the window's color where Wayfire placed it",
              tuple(rgb[offset:offset + 3]) == COLOR, tuple(rgb[offset:offset + 3]))
        Path(uri.removeprefix("file://")).unlink(missing_ok=True)

    # Screen sharing: share a monitor, picked in the backend's chooser with a pointer click.
    out, frame = build / "portal-screencast.json", build / "portal-frame.rgb"
    out.unlink(missing_ok=True)
    frame.unlink(missing_ok=True)
    started.append(session.spawn(f"exec env GST_PLUGIN_PATH={gst} python3 {REPO}/tests/portal-client.py "
                                 f"screencast {out} {frame}", build / "portal-screencast.log"))
    ok, _ = session.wait(lambda: [v for v in views(session) if v.get("app-id") == "slurp"
                                  or "slurp" in str(v.get("title", "")).lower()
                                  or v.get("layer") == "overlay"], timeout=20)
    check("the backend's output chooser appears", ok,
          [(v.get("role"), v.get("app-id"), v.get("layer")) for v in views(session)])
    session.ipc("stipc/move_cursor", {"x": center[0], "y": center[1]})
    time.sleep(0.2)  # paces the gesture: motion, then the click
    session.ipc("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "press"})
    session.ipc("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "release"})
    # Frames come when something on screen changes (a static screen sends one): the window's
    # corner counter ticks, away from the sampled point.
    ok, result = session.wait(lambda: read_json(out), timeout=60)
    result = result or {}
    check("ScreenCast portal starts a stream for the picked monitor",
          result.get("response") == 0 and result.get("streams"), result or
          (build / "portal-screencast.log").read_text()[-500:])
    if frame.exists() and result.get("streams"):
        width, height = result["streams"][0][1]["size"]
        stride = (width * 3 + 3) // 4 * 4
        # The newest frame: the stream's first buffer can predate the first completed capture.
        data = frame.read_bytes()[-stride * height:]
        offset = sample[1] * stride + sample[0] * 3
        check("the shared stream shows the window's color where Wayfire placed it",
              tuple(data[offset:offset + 3]) == COLOR,
              (tuple(data[offset:offset + 3]), width, height, len(data)))
    else:
        check("a frame arrives over PipeWire", False, result)
    # Diagnostic, from xdg-desktop-portal's own log: which backend it chose for each interface.
    chosen = [line for line in (build / "portal-frontend.log").read_text().splitlines()
              if line.startswith("XDP: Using ")]
    check("xdg-desktop-portal chose wlr for ScreenCast and Screenshot, Hyprland's backend for "
          "nothing (its log)",
          any("wlr.portal for org.freedesktop.impl.portal.ScreenCast" in line for line in chosen)
          and any("wlr.portal for org.freedesktop.impl.portal.Screenshot" in line for line in chosen)
          and not any("hyprland.portal" in line for line in chosen), chosen)
    session.terminate(*reversed(started))

sys.exit(check.summary())
