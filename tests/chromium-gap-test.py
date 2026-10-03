#!/usr/bin/env python3
"""Capture CSD and system-decoration window bounds in an isolated Scottland session."""
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import time
from urllib.parse import quote

if len(sys.argv) != 2:
    raise SystemExit("usage: chromium-gap-test.py ARTIFACT_DIR")

artifacts = Path(sys.argv[1]).resolve()
artifacts.mkdir(parents=True, exist_ok=True)
socket_path = os.environ["WAYFIRE_SOCKET"]
owned = []
records = {"cases": [], "other_csd_apps": {}}


class IPC:
    def __init__(self):
        self.socket = socket.socket(socket.AF_UNIX)
        self.socket.settimeout(4)
        self.socket.connect(socket_path)

    def call(self, method, data=None):
        body = json.dumps({"method": method, "data": data or {}}).encode()
        self.socket.sendall(struct.pack("<I", len(body)) + body)

        def read(size):
            result = b""
            while len(result) < size:
                block = self.socket.recv(size - len(result))
                if not block:
                    raise ConnectionError("Wayfire IPC disconnected")
                result += block
            return result

        return json.loads(read(struct.unpack("<I", read(4))[0]))

    def close(self):
        self.socket.close()


ipc = IPC()


def find_view(predicate, timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        views = ipc.call("window-rules/list-views")
        found = next((view for view in views if predicate(view) and view.get("mapped") and
                      view.get("geometry", {}).get("width", 0) > 0 and
                      view.get("geometry", {}).get("height", 0) > 0), None)
        if found:
            return found
        time.sleep(.1)
    raise TimeoutError("test window did not map")


def snapshot(view, label):
    state = ipc.call("scottland/layout-state")
    frame = next((entry for entry in state["views"] if entry["id"] == view["id"]), None)
    geometry = view.get("geometry", {})
    base = view.get("base-geometry", {})
    delta = {key: float(geometry.get(key, 0)) - float(base.get(key, 0))
             for key in ("x", "y", "width", "height")}
    return {
        "case": label,
        "wayfire": view,
        "frame": frame,
        "surface_geometry_delta": delta,
        "goo": ipc.call("scottland/goo-state"),
        "decoration_preference": json.loads((Path(view["_profile"]) / "Default/Preferences").read_text())
            .get("browser", {}).get("custom_chrome_frame") if view.get("_profile") else None,
    }


def check_alpha_contour(info, expected, label):
    frame = (info.get("frame") or {}).get("frame", {})
    shape = frame.get("alpha_shape")
    if expected:
        if not shape or shape.get("builds", 0) < 1:
            raise RuntimeError(f"{label}: inset CSD surface did not build its alpha contour: {shape}")
    elif shape:
        raise RuntimeError(f"{label}: matching surface/xdg bounds unexpectedly allocated an alpha contour")


def has_size_insets(info):
    # Position includes window/presentation transforms; client-decoration extents change size.
    delta = info["surface_geometry_delta"]
    return abs(delta["width"]) > .5 or abs(delta["height"]) > .5


def wait_for_attention(window, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = ipc.call("scottland/layout-state")
        entry = next((row for row in state["views"] if row["id"] == window), None)
        if entry and entry.get("frame", {}).get("attention"):
            return
        time.sleep(.05)
    raise TimeoutError(f"attention did not become visible for window {window}")


def wait_for_goo_sleep(timeout=12):
    deadline = time.monotonic() + timeout
    latest = None
    while time.monotonic() < deadline:
        latest = ipc.call("scottland/goo-state")
        screens = latest.get("screens", [])
        if screens and all(screen.get("sleeping") for screen in screens):
            return latest
        time.sleep(.1)
    raise TimeoutError(f"settled goo cache did not sleep: {latest}")


def move_pointer_to_empty_corner():
    geometry = ipc.call("window-rules/list-outputs")[0]["geometry"]
    ipc.call("stipc/move_cursor", {
        "x": round(geometry["x"] + geometry["width"] - 2),
        "y": round(geometry["y"] + geometry["height"] - 2),
    })


def focus_tiny_anchor():
    output = open(os.devnull, "wb")
    proc = subprocess.Popen([
        "foot", "-c", "/dev/null", "-T", "gap-focus-anchor", "-W", "1x1",
        "sh", "-c", "exec sleep 600",
    ], stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
    owned.append((proc, output))
    anchor = find_view(lambda item: item.get("title") == "gap-focus-anchor")
    ipc.call("window-rules/configure-view", {
        "id": anchor["id"], "geometry": {"x": 1, "y": 1, "width": 8, "height": 16},
    })
    # Focus the small anchor with the session's real input path. This allows the
    # browser to retain an attention halo while leaving nearly all of its border clear.
    ipc.call("stipc/move_cursor", {"x": 4, "y": 5})
    time.sleep(.1)
    ipc.call("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "press"})
    ipc.call("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "release"})
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        current = next((item for item in ipc.call("window-rules/list-views")
                        if item.get("id") == anchor["id"]), None)
        if current and current.get("activated"):
            # Keep the focused anchor out of the screenshots. The click above is
            # real stipc input; parking the tiny helper off-output does not change
            # which client owns focus or the attention state being captured.
            ipc.call("window-rules/configure-view", {
                "id": anchor["id"],
                "geometry": {"x": -2000, "y": -2000, "width": 8, "height": 16},
            })
            return anchor
        time.sleep(.05)
    raise RuntimeError("stipc click did not focus the tiny attention anchor")


def screenshot(path):
    subprocess.run(["grim", "-o", "HEADLESS-1", str(path)], check=True, timeout=8)


def launch_chromium(name, system_titlebar):
    profile = artifacts / ("profile-" + name)
    if profile.exists():
        shutil.rmtree(profile)
    (profile / "Default").mkdir(parents=True, exist_ok=True)
    (profile / "Default/Preferences").write_text(json.dumps({
        "browser": {"custom_chrome_frame": not system_titlebar},
    }))
    page_title = "Scottland gap " + name
    html = (f"<title>{page_title}</title>"
            "<body style='margin:0;background:#394552;color:white;font:32px sans-serif'>"
            "<div style='padding:28px'>Client content reaches the visible page edge</div></body>")
    url = "data:text/html," + quote(html, safe="")
    log = artifacts / (name + ".chromium.log")
    output = log.open("wb")
    chrome_env = os.environ.copy()
    chrome_env["WAYLAND_DEBUG"] = "client"
    proc = subprocess.Popen([
        "chromium", "--ozone-platform=wayland", f"--user-data-dir={profile}",
        "--no-first-run", "--no-default-browser-check", "--password-store=basic",
        "--window-size=1100,650", url,
    ], stdout=output, stderr=subprocess.STDOUT, start_new_session=True, env=chrome_env)
    owned.append((proc, output))
    view = find_view(lambda item: page_title in item.get("title", ""))
    view["_profile"] = str(profile)
    focus_tiny_anchor()
    response = ipc.call("scottland/attention", {
        "window": view["id"], "attention": True, "source": "chromium-goo-gap-test",
    })
    if response.get("in_front"):
        raise RuntimeError("Chromium remained focused; attention would not be visible")
    wait_for_attention(view["id"])
    move_pointer_to_empty_corner()
    wait_for_goo_sleep()
    return view


def capture_chromium(name, system_titlebar):
    view = launch_chromium(name, system_titlebar)
    info = snapshot(view, name)
    if not all(screen.get("sleeping") for screen in info["goo"].get("screens", [])):
        raise RuntimeError(f"{name}: capture did not exercise the settled goo path")
    has_insets = has_size_insets(info)
    check_alpha_contour(info, has_insets, name)
    del info["wayfire"]["_profile"]
    screenshot(artifacts / (name + "-goo.png"))
    ipc.call("wayfire/set-config-options", {"scottland/goo": False})
    time.sleep(.5)
    screenshot(artifacts / (name + "-halo.png"))
    ipc.call("wayfire/set-config-options", {"scottland/goo": True})
    time.sleep(.5)
    stop_owned()
    log_text = (artifacts / (name + ".chromium.log")).read_text(errors="replace")
    decoration_modes = [int(value) for value in re.findall(
        r"zxdg_toplevel_decoration_v1#\d+\.configure\((\d+)\)", log_text)]
    expected_mode = 2 if system_titlebar else 1  # server-side=2, client-side=1
    if expected_mode not in decoration_modes:
        raise RuntimeError(f"{name}: requested decoration mode {expected_mode}, saw {decoration_modes}")
    info["decoration_modes"] = decoration_modes
    info["alpha_contour_checked"] = has_insets
    records["cases"].append(info)


def stop_owned():
    while owned:
        proc, output = owned.pop()
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=3)
        output.close()


try:
    config = ipc.call("wayfire/get-config-option", {"option": "core/preferred_decoration_mode"})
    records["wayfire_preferred_decoration_mode"] = config.get("value")
    capture_chromium("chromium-system-titlebar-off", False)
    capture_chromium("chromium-system-titlebar-on", True)

    if shutil.which("nautilus"):
        proc = subprocess.Popen([
            "nautilus", "--new-window", str(artifacts),
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        owned.append((proc, open(os.devnull, "wb")))
        view = find_view(lambda item: item.get("app-id") == "org.gnome.Nautilus", timeout=15)
        focus_tiny_anchor()
        ipc.call("scottland/attention", {
            "window": view["id"], "attention": True, "source": "chromium-goo-gap-test",
        })
        wait_for_attention(view["id"])
        move_pointer_to_empty_corner()
        wait_for_goo_sleep()
        info = snapshot(view, "nautilus-csd")
        if not all(screen.get("sleeping") for screen in info["goo"].get("screens", [])):
            raise RuntimeError("nautilus-csd: capture did not exercise the settled goo path")
        has_insets = has_size_insets(info)
        check_alpha_contour(info, has_insets, "nautilus-csd")
        info["alpha_contour_checked"] = has_insets
        records["other_csd_apps"]["nautilus"] = info
        screenshot(artifacts / "nautilus-csd-goo.png")
        ipc.call("wayfire/set-config-options", {"scottland/goo": False})
        time.sleep(.5)
        screenshot(artifacts / "nautilus-csd-halo.png")
        ipc.call("wayfire/set-config-options", {"scottland/goo": True})
        stop_owned()
    else:
        records["other_csd_apps"]["nautilus"] = {"available": False}

    firefox = (os.environ.get("SCOTTLAND_FIREFOX_BINARY") or shutil.which("firefox") or
               shutil.which("firefox-bin"))
    if firefox:
        profile = artifacts / "profile-firefox-csd"
        if profile.exists():
            shutil.rmtree(profile)
        profile.mkdir(parents=True)
        title = "Scottland CSD Firefox"
        url = "data:text/html," + quote(
            f"<title>{title}</title><body style='margin:0;background:#394552;color:white'>"
            "Firefox client-decoration contour check</body>", safe="")
        log = artifacts / "firefox-csd.firefox.log"
        output = log.open("wb")
        firefox_env = os.environ.copy()
        firefox_env["MOZ_ENABLE_WAYLAND"] = "1"
        firefox_env["WAYLAND_DEBUG"] = "client"
        proc = subprocess.Popen([
            firefox, "--no-remote", "--profile", str(profile), "--new-window", url,
        ], stdout=output, stderr=subprocess.STDOUT, start_new_session=True, env=firefox_env)
        owned.append((proc, output))
        view = find_view(lambda item: title in item.get("title", ""), timeout=40)
        focus_tiny_anchor()
        ipc.call("scottland/attention", {
            "window": view["id"], "attention": True, "source": "chromium-goo-gap-test",
        })
        wait_for_attention(view["id"])
        move_pointer_to_empty_corner()
        wait_for_goo_sleep()
        info = snapshot(view, "firefox-csd")
        has_insets = has_size_insets(info)
        check_alpha_contour(info, has_insets, "firefox-csd")
        info["alpha_contour_checked"] = has_insets
        screenshot(artifacts / "firefox-csd-goo.png")
        ipc.call("wayfire/set-config-options", {"scottland/goo": False})
        time.sleep(.5)
        screenshot(artifacts / "firefox-csd-halo.png")
        ipc.call("wayfire/set-config-options", {"scottland/goo": True})
        stop_owned()
        log_text = log.read_text(errors="replace")
        info["decoration_modes"] = [int(value) for value in re.findall(
            r"zxdg_toplevel_decoration_v1#\d+\.configure\((\d+)\)", log_text)]
        records["other_csd_apps"]["firefox"] = {"available": True, "binary": firefox, **info}
    else:
        records["other_csd_apps"]["firefox"] = {"available": False, "binary": None}
    (artifacts / "geometry-captures.json").write_text(json.dumps(records, indent=2) + "\n")
    print(json.dumps({
        "preferred_decoration_mode": records["wayfire_preferred_decoration_mode"],
        "chromium": [{"case": c["case"], "geometry": c["wayfire"]["geometry"],
                      "base": c["wayfire"]["base-geometry"], "bbox": c["wayfire"]["bbox"],
                      "frame": c["frame"]["frame"]} for c in records["cases"]],
        "other_csd_apps": records["other_csd_apps"],
    }, indent=2), flush=True)
finally:
    stop_owned()
    ipc.close()
