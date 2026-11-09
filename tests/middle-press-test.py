#!/usr/bin/env python3
"""Acceptance checks for touchpad and pointer middle-button behavior."""
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time

out = Path(sys.argv[1]).resolve()
out.mkdir(parents=True, exist_ok=True)
sock = socket.socket(socket.AF_UNIX)
sock.settimeout(10)
sock.connect(os.environ["WAYFIRE_SOCKET"])


def recv(size):
    data = b""
    while len(data) < size:
        part = sock.recv(size - len(data))
        if not part:
            raise RuntimeError("compositor disconnected")
        data += part
    return data


def ipc(method, data=None):
    body = json.dumps({"method": method, "data": data or {}}).encode()
    sock.sendall(struct.pack("<I", len(body)) + body)
    reply = json.loads(recv(struct.unpack("<I", recv(4))[0]))
    if isinstance(reply, dict) and "error" in reply:
        raise RuntimeError(reply)
    return reply


def wait(predicate, timeout=10, what="state"):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        result = predicate()
        if result:
            return result
        time.sleep(.02)
    raise RuntimeError(what + " timeout")


def views():
    return ipc("window-rules/list-views")


def view(title):
    return next((row for row in views() if row.get("title") == "Middle-" + title), None)


def layer_surface():
    rows = ipc("scottland/key-layer", {"action": "list"})["surfaces"]
    return next((row for row in rows if row.get("pid") == client.pid and
                 row.get("namespace") == "scottland-middle-press-test"), None)


def geometry(title):
    return view(title)["geometry"]


def layout_view(title):
    return next(row for row in ipc("scottland/layout-state")["views"]
                if row.get("title") == "Middle-" + title)


def frame_state(title):
    row = layout_view(title)
    state = dict(row["frame"])
    # In the isolated test model, scene_frame includes the live view transform.
    # Use the compositor's scene geometry for fixture placement and input points.
    state.update(row.get("scene_frame", {}))
    return state


def frame_diagnostic(title):
    row = layout_view(title)
    return {key: row.get(key) for key in ("frame", "scene_frame", "applied_scale")}


def state():
    return ipc("scottland/test-input")


def hold_offer():
    return ipc("scottland/spread-state")["hold_offer"]


def geometry_pair():
    return tuple(tuple(geometry(title)[key] for key in ("x", "y", "width", "height"))
                 for title in ("A", "B"))


def geometry_pair_matches(left, right, tolerance=1):
    return all(abs(a - b) <= tolerance for old, new in zip(left, right)
               for a, b in zip(old, new))


def hit_target(x, y, a_title, b_title):
    raw = ipc("scottland/test-hit-target", {"x": round(x), "y": round(y)})
    a_id = view(a_title)["id"]
    b_id = view(b_title)["id"]
    target_id = raw.get("window_id", -1)
    window = "A" if target_id == a_id else "B" if target_id == b_id else "other"
    return {"window": window, "part": raw.get("part", "other"),
            "window_id": target_id, "title": raw.get("window", "other")}


def valid_release_target(target):
    return target["window"] == "B" and target["part"] in (
        "client_surface", "frame_or_expanded_hit_area")


def pointer(x, y):
    ipc("stipc/move_cursor", {"x": round(x), "y": round(y)})


def button(code, pressed):
    ipc("stipc/feed_button", {"combo": code, "mode": "press" if pressed else "release"})


def key(code, pressed):
    ipc("stipc/feed_key", {"key": "KEY_" + code, "state": pressed})


def pad_button(pressed):
    return ipc("scottland/test-touchpad", {
        "event": "button", "button": "middle", "pressed": pressed})


def pad_motion(x, y):
    return ipc("scottland/test-touchpad", {"event": "motion", "x": x, "y": y})


def pad_swipe_begin(fingers=3):
    return ipc("scottland/test-touchpad", {"event": "swipe_begin", "fingers": fingers})


def pad_swipe_update(dx, dy=0):
    return ipc("scottland/test-touchpad", {
        "event": "swipe_update", "fingers": 3, "dx": dx, "dy": dy})


def pad_swipe_end():
    return ipc("scottland/test-touchpad", {"event": "swipe_end", "cancelled": False})


def all_events():
    path = out / "buttons.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def pointer_events(title):
    path = out / "pointer.jsonl"
    if not path.exists():
        return []
    return [event for event in map(json.loads, path.read_text().splitlines())
            if event["name"] == title]


def events(title):
    return [event for event in all_events() if event["name"] == title and event["button"] == 2]


def clear_events():
    (out / "buttons.jsonl").write_text("")
    (out / "pointer.jsonl").write_text("")


def screenshot(name):
    path = out / (name + ".png")
    subprocess.run(["grim", str(path)], check=True)
    return path


def pixel(path, x, y):
    data = subprocess.check_output([
        "magick", str(path), "-crop", f"1x1+{round(x)}+{round(y)}", "-depth", "8", "RGB:-"])
    return tuple(data[:3])


def is_fixture_pixel(rgb):
    red, green, blue = rgb
    return green > 100 and green > red * 1.5 and green > blue * 1.5


def wait_fixture_pixel(name, x, y, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if is_fixture_pixel(pixel(screenshot(name), x, y)):
            return True
        time.sleep(.1)
    return False


def command(action, name="A"):
    path = out / "command.json"
    wait(lambda: not path.exists(), what="previous GTK command")
    path.write_text(json.dumps({"action": action, "name": name}))


def place(title, x, y, width, height):
    identifier = view(title)["id"]
    data = {"id": identifier, "geometry": {
        "x": x, "y": y, "width": width, "height": height}}
    for _ in range(8):
        ipc("window-rules/configure-view", data)
        try:
            wait(lambda: (lambda g: (g["x"], g["y"]) == (x, y) and
                         abs(g["width"] - width) <= 24 and abs(g["height"] - height) <= 24)(geometry(title)),
                 timeout=1.5, what="fixture placement")
            return
        except RuntimeError:
            pass
    raise RuntimeError(f"could not place {title}: {geometry(title)}")


def focused_view_id():
    reply = ipc("window-rules/get-focused-view")
    info = reply.get("info") if isinstance(reply, dict) else None
    return info.get("id") if isinstance(info, dict) else None


def focus(title):
    identifier = view(title)["id"]
    ipc("window-rules/focus-view", {"id": identifier})
    wait(lambda: focused_view_id() == identifier,
         what="focus Middle-" + title)


def settle(*titles, timeout=5):
    end = time.monotonic() + timeout
    previous = None
    stable = 0
    while time.monotonic() < end:
        rows = ipc("scottland/layout-state")["views"]
        by_title = {row.get("title"): row for row in rows}
        if any("Middle-" + title not in by_title or
               "frame" not in by_title["Middle-" + title] for title in titles):
            previous, stable = None, 0
            time.sleep(.05)
            continue
        # Frame swell animates independently of layout. Scenario-specific checks
        # wait for the frame state they use; general setup waits on view geometry.
        current = tuple((geometry(title),
                         round(by_title["Middle-" + title].get("applied_scale", 1.0), 4))
                        for title in titles)
        if current == previous:
            stable += 1
            if stable >= 3:
                return current
        else:
            previous, stable = current, 0
        time.sleep(.05)
    raise RuntimeError("layout did not settle: " + ", ".join(titles))


def setup(a_x=280, b_x=680):
    for title in ("A", "B"):
        if not any(row.get("title") == "Middle-" + title for row in views()):
            command("show", title)
            wait(lambda: any(row.get("title") == "Middle-" + title for row in views()),
                 what="show Middle-" + title)
    place("A", a_x, 240, 360, 260)
    place("B", b_x, 240, 360, 260)
    # After a swipe, the fixtures can still be animating back to their setup
    # positions. Sample their center pixels only after compositor geometry and
    # scale have settled, then keep the independent pixel readiness check.
    settle("A", "B")
    for title in ("A", "B"):
        g = geometry(title)
        if not wait_fixture_pixel(f"ready-{title}",
                                  g["x"] + g["width"] / 2,
                                  g["y"] + g["height"] / 2):
            raise RuntimeError(f"Middle-{title} fixture surface did not draw before focus")
    focus("A")
    settle("A", "B")


passed = failed = 0


def check(ok, label, detail=""):
    global passed, failed
    print(("PASS " if ok else "FAIL ") + label + (f" [{detail}]" if detail and not ok else ""), flush=True)
    passed += bool(ok)
    failed += not bool(ok)


layer_shell_preload = os.environ.get("LD_PRELOAD", "")
if layer_shell_preload:
    layer_shell_preload += ":"
layer_shell_preload += "libgtk4-layer-shell.so"
client = subprocess.Popen([sys.executable, str(Path(__file__).with_name("middle-press-app.py")), str(out)],
                          env={**os.environ, "LD_PRELOAD": layer_shell_preload},
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    wait(lambda: view("A"), what="Middle-A map")
    wait(lambda: view("B"), what="Middle-B map")
    ipc("wayfire/set-config-options", {
        "output:HEADLESS-1/mode": "1600x1000@60000",
        "scottland/touchpad_gestures": True})
    setup()

    # R3(a): a still click is withheld for the press interval, then replayed exactly once.
    clear_events()
    a = geometry("A")
    x, y = a["x"] + a["width"] / 2, a["y"] + a["height"] / 2
    pointer(x, y)
    button("BTN_MIDDLE", True)
    wait(lambda: state()["mouse_middle_pending"], what="pointer middle pending")
    check(not all_events(), "pointer: no client event arrives before the owner releases")
    pointer(x + 3, y)
    button("BTN_MIDDLE", False)
    wait(lambda: len(events("A")) == 2, what="pointer quick-click replay")
    click = events("A")
    check([(event["edge"], event["button"]) for event in click] == [("press", 2), ("release", 2)],
          "pointer: a quick click reaches A once, in order")
    check(geometry("A") == a, "pointer: a quick click leaves A's geometry unchanged")

    # B1: the frame halo belongs to A but has no focused client surface. Its press is
    # consumed without replay, while movement beyond L24's slop still moves the window.
    setup()
    clear_events()
    before = geometry("A")
    x, y = before["x"] + before["width"] / 2, before["y"] - 6
    pointer(x, y)
    check(not pointer_events("A"), "halo: the press point is outside A's client surface")
    button("BTN_MIDDLE", True)
    wait(lambda: state()["mouse_middle_pending"], what="halo middle pending")
    button("BTN_MIDDLE", False)
    time.sleep(.1)
    check(not events("A"), "halo: a null client recipient is not replayed")
    check(geometry("A") == before, "halo: a quick middle click does not move A")

    clear_events()
    before = geometry("A")
    x, y = before["x"] + before["width"] / 2, before["y"] - 6
    pointer(x, y)
    button("BTN_MIDDLE", True)
    wait(lambda: state()["mouse_middle_pending"], what="halo drag pending")
    for step in range(1, 11):
        pointer(x + 20 * step, y)
        if step == 1:
            wait(lambda: state()["mouse_middle_dragging"], what="halo live drag")
        time.sleep(.02)
    check(not all_events(), "halo: dragging does not deliver middle input to A")
    time.sleep(.25)
    button("BTN_MIDDLE", False)
    wait(lambda: not state()["dragging"], what="halo drag end")
    settle("A", "B")
    after = geometry("A")
    check(after["x"] >= before["x"] + 150 and
          (after["width"], after["height"]) == (before["width"], before["height"]),
          "halo: middle press and move drags A without resizing",
          f"{before} -> {after}")
    check(not all_events(), "halo: middle drag release is not replayed to A")

    # R3(b): the release is over B's frame, but the click belongs only to A's original surface.
    setup(a_x=300, b_x=600)
    focus("B")
    settle("A", "B")
    a = geometry("A")
    a_frame = frame_state("A")
    # A may be displayed scaled, so position B against A's reported frame edge.
    # The focused halo animates beyond B's client; derive the press point below.
    place("B", round(a_frame["x"] + a_frame["width"] - 17), a["y"], 360, 260)
    settle("A", "B")
    b_frame = frame_state("B")
    hover_x = round(b_frame["x"] - b_frame["thickness"] - 6)
    hover_y = round(b_frame["y"] + b_frame["height"] / 2)
    pointer(hover_x, hover_y)
    wait(lambda: frame_state("B")["swell"] >= .99,
         what="B's reported halo swell after pointer dwell")
    settle("A", "B")
    clear_events()
    a = geometry("A")
    b = geometry("B")
    a_frame = frame_state("A")
    b_frame = frame_state("B")
    b_halo_left = b_frame["x"] - b_frame["thickness"]
    x = round(b_halo_left - 6)
    y = round(a_frame["y"] + a_frame["height"] / 2)
    release_x = x + 8
    a_right = a_frame["x"] + a_frame["width"]
    check(a_frame["x"] < x < release_x < a_right and
          x < b_halo_left < release_x < b_frame["x"] and
          b_frame["y"] - b_frame["thickness"] < y <
          b_frame["y"] + b_frame["height"] + b_frame["thickness"],
          "fixture: A's scene frame contains the press point and B's settled frame contains the 8 px release",
          f"A frame={a_frame}; B frame={b_frame}; press=({x},{y}); release=({release_x},{y})")
    print(f"INFO R3(b) input geometry: A={frame_diagnostic('A')}; "
          f"B={frame_diagnostic('B')}; press=({x},{y}); release=({release_x},{y})", flush=True)
    # Approach from A's center so the readiness check observes input at the press point.
    pointer(round(a_frame["x"] + a_frame["width"] / 2), y)
    wait(lambda: bool(pointer_events("A")), what="A surface at the R3(b) approach point")
    clear_events()
    press_target = hit_target(x, y, "A", "B")
    print(f"INFO R3(b) pointer hit target at press ({x},{y}): {press_target}", flush=True)
    press_is_a_client = press_target["window"] == "A" and press_target["part"] == "client_surface"
    check(press_is_a_client, "pointer R3(b) press hit-tests to A client", str(press_target))
    if press_is_a_client:
        pointer(x, y)
        press_target = hit_target(x, y, "A", "B")
        print(f"INFO R3(b) pointer target immediately before press at ({x},{y}): {press_target}", flush=True)
        press_is_a_client = press_target["window"] == "A" and press_target["part"] == "client_surface"
        check(press_is_a_client, "pointer R3(b) actual press target is A client", str(press_target))
    if press_is_a_client:
        wait(lambda: bool(pointer_events("A")),
             what=f"A client at ({x},{y}) with recorded hit target {press_target}")
        button("BTN_MIDDLE", True)
        wait(lambda: state()["mouse_middle_pending"], what="cross-surface middle pending")
        pointer(x + 8, y)
        release_target = hit_target(x + 8, y, "A", "B")
        print(f"INFO R3(b) pointer hit target at release ({x + 8},{y}): {release_target}", flush=True)
        release_is_b = valid_release_target(release_target)
        check(release_is_b, "pointer R3(b) release hit-tests to B", str(release_target))
        if release_is_b:
            wait(lambda: any(event["edge"] == "leave" for event in pointer_events("A")),
                 what=f"8 px move leaves A for recorded B target {release_target}")
        button("BTN_MIDDLE", False)
        wait(lambda: len(events("A")) == 2, what="original-surface replay")
        check([(event["edge"], event["button"]) for event in events("A")] ==
              [("press", 2), ("release", 2)], "pointer: cross-window replay sends one ordered click to A")
        check(len(events("B")) == 0, "pointer: crossing onto B never transfers A's click")
        check(geometry("A") == a, "pointer: an 8 px click ending over B's frame does not drag")
        check(geometry("B") == b, "pointer: an 8 px click leaves B geometry unchanged")

    # R3(a), R1: the virtual touchpad's middle button uses the same replay path.
    setup()
    clear_events()
    a = geometry("A")
    x, y = a["x"] + a["width"] / 2, a["y"] + a["height"] / 2
    pointer(x, y)
    pad_button(True)
    wait(lambda: state()["middle_pending"], what="touchpad middle pending")
    check(not all_events(), "touchpad: no client event arrives before release")
    pad_motion(x + 3, y)
    pad_button(False)
    wait(lambda: len(events("A")) == 2, what="touchpad quick-click replay")
    check([(event["edge"], event["button"]) for event in events("A")] ==
          [("press", 2), ("release", 2)], "touchpad: a quick click reaches A once, in order")

    # The touchpad setting controls three-finger gestures, not BTN_MIDDLE click replay.
    setup()
    clear_events()
    ipc("wayfire/set-config-options", {"scottland/touchpad_gestures": False})
    a = geometry("A")
    pointer(a["x"] + a["width"] / 2, a["y"] + a["height"] / 2)
    pad_button(True)
    wait(lambda: state()["middle_pending"], what="touchpad setting off: middle click pending")
    check(not all_events(), "touchpad setting off: quick middle press stays withheld")
    pad_button(False)
    wait(lambda: len(events("A")) == 2, what="touchpad setting off: quick middle replay")
    check([(event["edge"], event["button"]) for event in events("A")] ==
          [("press", 2), ("release", 2)], "touchpad setting off: quick middle click is replayed once")
    ipc("wayfire/set-config-options", {"scottland/touchpad_gestures": True})

    # R3(c): if the original client unmaps during the withheld press, nobody gets the click.
    clear_events()
    a = geometry("A")
    pointer(a["x"] + a["width"] / 2, a["y"] + a["height"] / 2)
    button("BTN_MIDDLE", True)
    wait(lambda: state()["mouse_middle_pending"], what="unmap middle pending")
    command("hide", "A")
    wait(lambda: not any(row.get("title") == "Middle-A" for row in views()), what="A unmap")
    button("BTN_MIDDLE", False)
    time.sleep(.1)
    check(not all_events(), "pointer: an unmapped original recipient gets no replay")
    command("show", "A")
    wait(lambda: any(row.get("title") == "Middle-A" for row in views()), what="A remap")
    setup()

    # R4: ordinary mouse middle-drag follows the same live move and suppresses client input.
    movement_deltas = {}
    source = "mouse"
    setup()
    clear_events()
    before = geometry("A")
    x, y = before["x"] + before["width"] / 2, before["y"] + before["height"] / 2
    pointer(x, y)
    old_sample = (x - 60, y + 20)
    before_shot = screenshot(source + "-middle-before")
    check(is_fixture_pixel(pixel(before_shot, *old_sample)),
          source + ": the fixture's mapped pixels are visible before dragging")
    button("BTN_MIDDLE", True)
    wait(lambda: state()["mouse_middle_pending"], what=source + " middle pending")
    for step in range(1, 11):
        pointer(x + 20 * step, y)
        if step == 1:
            wait(lambda: state()["mouse_middle_dragging"], what=source + " live drag")
        time.sleep(.02)
    check(not all_events(), source + ": a drag sends no middle events to A")
    time.sleep(.25)  # Stop at the final position so release-coast does not change the oracle.
    button("BTN_MIDDLE", False)
    wait(lambda: not state()["dragging"], what=source + " drag end")
    settle("A", "B")
    check(not all_events(), source + ": drag release sends no middle event to A")
    after = geometry("A")
    movement_deltas[source] = after["x"] - before["x"]
    check(after["x"] >= before["x"] + 150 and
          (after["width"], after["height"]) == (before["width"], before["height"]),
          source + ": 200 px drag moves A and preserves its size",
          f"{before} -> {after}")
    after_shot = screenshot(source + "-middle-after")
    new_sample = (after["x"] + after["width"] / 2 - 60,
                  after["y"] + after["height"] / 2 + 20)
    check(is_fixture_pixel(pixel(after_shot, *new_sample)),
          source + ": composited client pixels follow the dragged window")
    check(not is_fixture_pixel(pixel(after_shot, *old_sample)),
          source + ": the old position no longer shows the moved client")

    # R7: clickfinger BTN_MIDDLE drags the window without resizing it, even when the
    # three-finger gesture setting is off.
    setup()
    clear_events()
    ipc("wayfire/set-config-options", {"scottland/touchpad_gestures": False})
    before = geometry("A")
    x, y = before["x"] + before["width"] / 2, before["y"] + before["height"] / 2
    pointer(x, y)
    pad_button(True)
    wait(lambda: state()["middle_pending"], what="touchpad middle drag pending")
    for step in range(1, 11):
        pad_motion(x + 20 * step, y)
        if step == 1:
            wait(lambda: state()["middle_dragging"], what="touchpad live middle drag")
        time.sleep(.02)
    check(not all_events(), "touchpad middle drag: no client event is delivered")
    time.sleep(.25)  # Stop at the final position so release-coast does not change the oracle.
    pad_button(False)
    wait(lambda: not state()["dragging"], what="touchpad middle drag end")
    settle("A", "B")
    after = geometry("A")
    movement_deltas["touchpad"] = after["x"] - before["x"]
    check(after["x"] >= before["x"] + 150 and
          (after["width"], after["height"]) == (before["width"], before["height"]),
          "touchpad R7: 200 px middle drag moves A and preserves its size",
          f"{before} -> {after}")
    check(not all_events(), "touchpad R7: no middle event reaches A")
    ipc("wayfire/set-config-options", {"scottland/touchpad_gestures": True})

    # L23: an unclicked three-finger swipe still moves the window and preserves its size.
    setup()
    clear_events()
    before = geometry("A")
    x, y = before["x"] + before["width"] / 2, before["y"] + before["height"] / 2
    pointer(x, y)
    pad_swipe_begin()
    wait(lambda: state()["swipe_moving"], what="three-finger swipe begins window move")
    for _ in range(10):
        pad_swipe_update(20)
        time.sleep(.02)
    pad_swipe_end()
    wait(lambda: not state()["dragging"], what="three-finger swipe move ends")
    settle("A", "B")
    after = geometry("A")
    check(after["x"] >= before["x"] + 150 and
          (after["width"], after["height"]) == (before["width"], before["height"]),
          "three-finger swipe without click moves A and preserves its size",
          f"{before} -> {after}")
    check(not all_events(), "three-finger swipe without click sends no middle-button event")

    # R4's amount and grab point match the existing Super+left-drag path.
    setup()
    before = geometry("A")
    x, y = before["x"] + before["width"] / 2, before["y"] + before["height"] / 2
    pointer(x, y)
    key("LEFTMETA", True)
    button("BTN_LEFT", True)
    wait(lambda: state()["dragging"], what="Super+left reference drag")
    for step in range(1, 11):
        pointer(x + 20 * step, y)
        time.sleep(.02)
    time.sleep(.25)
    button("BTN_LEFT", False)
    key("LEFTMETA", False)
    wait(lambda: not state()["dragging"], what="Super+left reference drag end")
    settle("A", "B")
    super_delta = geometry("A")["x"] - before["x"]
    for source, delta in movement_deltas.items():
        check(abs(delta - super_delta) <= 4,
              source + ": the same 200 px motion lands like Super+left-drag",
              f"middle {delta}px vs Super+left {super_delta}px")

    # R4: Esc cancels a live middle drag back to its starting geometry.
    setup()
    clear_events()
    before = geometry("A")
    x, y = before["x"] + before["width"] / 2, before["y"] + before["height"] / 2
    pointer(x, y)
    button("BTN_MIDDLE", True)
    wait(lambda: state()["mouse_middle_pending"], what="Esc middle pending")
    for step in range(1, 11):
        pointer(x + 20 * step, y)
        if step == 1:
            wait(lambda: state()["mouse_middle_dragging"], what="Esc live drag")
        time.sleep(.02)
    key("ESC", True)
    key("ESC", False)
    wait(lambda: not state()["dragging"], timeout=5, what="Esc ends live drag")
    button("BTN_MIDDLE", False)
    restored = wait_fixture_pixel("pointer-middle-escape", before["x"] + before["width"] / 2 - 60,
                                  before["y"] + before["height"] / 2 + 20)
    check(restored and geometry("A") == before,
          "pointer: Esc restores the client's pixels at its starting position")
    check(not all_events(), "pointer: Esc cancels without delivering a middle event")

    # R5: a still BTN_MIDDLE press uses the same WK39 offer and commit path as Super+press.
    # Use the specified 600 ms delay and compare committed geometry to the existing gesture.
    setup()
    ipc("wayfire/set-config-options", {"scottland/window_hold_delay": 600})
    clear_events()
    before = geometry_pair()
    a = geometry("A")
    x, y = a["x"] + a["width"] / 2, a["y"] + a["height"] / 2
    pointer(x, y)
    button("BTN_MIDDLE", True)
    wait(lambda: state()["mouse_middle_pending"], what="R5 focused pointer middle pending")
    check(not events("A"), "R5 pointer: focused hold sends no client middle event while pending")
    wait(lambda: hold_offer().get("active") and state()["mouse_middle_dragging"],
         timeout=5, what="R5 focused pointer solo offer")
    check(hold_offer().get("solo") is True, "R5 pointer (a): focused target offers a solo")
    check(geometry_pair_matches(before, geometry_pair()),
          "R5 pointer: the offer leaves true geometry unchanged while held")
    button("BTN_MIDDLE", False)
    wait(lambda: not state()["dragging"], what="R5 focused pointer offer release")
    settle("A", "B")
    pointer_solo = geometry_pair()
    check(not events("A"), "R5 pointer (a): held middle press and release never reach A")

    setup()
    focus("A")
    a = geometry("A")
    x, y = a["x"] + a["width"] / 2, a["y"] + a["height"] / 2
    pointer(x, y)
    key("LEFTMETA", True)
    button("BTN_LEFT", True)
    wait(lambda: hold_offer().get("active"), timeout=5, what="WK39 focused Super+press solo offer")
    button("BTN_LEFT", False)
    key("LEFTMETA", False)
    wait(lambda: not state()["dragging"], what="WK39 focused Super+press release")
    settle("A", "B")
    check(geometry_pair_matches(pointer_solo, geometry_pair(), 2),
          "R5 pointer (a): focused result matches the WK39 Super+press solo oracle")

    setup()
    focus("A")
    clear_events()
    before = geometry_pair()
    b = geometry("B")
    x, y = b["x"] + b["width"] / 2, b["y"] + b["height"] / 2
    pointer(x, y)
    button("BTN_MIDDLE", True)
    wait(lambda: state()["mouse_middle_pending"], what="R5 unfocused pointer middle pending")
    wait(lambda: hold_offer().get("active") and state()["mouse_middle_dragging"],
         timeout=5, what="R5 unfocused pointer pair offer")
    check(hold_offer().get("solo") is False, "R5 pointer (b): unfocused target offers a pair")
    check(geometry_pair_matches(before, geometry_pair()),
          "R5 pointer (b): pair preview leaves true geometry unchanged")
    button("BTN_MIDDLE", False)
    wait(lambda: not state()["dragging"], what="R5 unfocused pointer offer release")
    settle("A", "B")
    pointer_pair = geometry_pair()
    check(not events("B"), "R5 pointer (b): held middle press and release never reach B")

    setup()
    focus("A")
    b = geometry("B")
    x, y = b["x"] + b["width"] / 2, b["y"] + b["height"] / 2
    pointer(x, y)
    key("LEFTMETA", True)
    button("BTN_LEFT", True)
    wait(lambda: hold_offer().get("active"), timeout=5, what="WK39 unfocused Super+press pair offer")
    button("BTN_LEFT", False)
    key("LEFTMETA", False)
    wait(lambda: not state()["dragging"], what="WK39 unfocused Super+press release")
    settle("A", "B")
    check(geometry_pair_matches(pointer_pair, geometry_pair(), 2),
          "R5 pointer (b): unfocused result matches the WK39 Super+press pair oracle")

    # A movement beyond the offer hotspot refuses the pair and resumes the same live drag.
    setup()
    focus("A")
    clear_events()
    a_before = geometry("A")
    b_before = geometry("B")
    b = geometry("B")
    x, y = b["x"] + b["width"] / 2, b["y"] + b["height"] / 2
    pointer(x, y)
    button("BTN_MIDDLE", True)
    wait(lambda: hold_offer().get("active"), timeout=5, what="R5 pointer drag offer")
    for distance in (20, 40, 60, 80):
        pointer(x + distance, y)
        time.sleep(.03)
    wait(lambda: not hold_offer().get("active") and state()["dragging"],
         what="R5 pointer offer refusal resumes the live drag")
    time.sleep(.25)
    button("BTN_MIDDLE", False)
    wait(lambda: not state()["dragging"], what="R5 pointer continued drag release")
    settle("A", "B")
    a_after = geometry("A")
    b_after = geometry("B")
    check(all(abs(a_before[k] - a_after[k]) <= 1 for k in ("x", "y", "width", "height")),
          "R5 pointer (c): refusing the offer does not commit the pair")
    check(b_after["x"] >= b_before["x"] + 40 and
          (b_after["width"], b_after["height"]) == (b_before["width"], b_before["height"]),
          "R5 pointer (c): movement after the offer continues the middle drag",
          f"{b_before} -> {b_after}")
    check(not events("A") and not events("B"),
          "R5 pointer (c): continued drag sends no client middle event")

    # Esc refuses an active offer; the still-held BTN_MIDDLE release remains consumed.
    setup()
    clear_events()
    before = geometry_pair()
    a = geometry("A")
    x, y = a["x"] + a["width"] / 2, a["y"] + a["height"] / 2
    pointer(x, y)
    button("BTN_MIDDLE", True)
    wait(lambda: hold_offer().get("active"), timeout=5, what="R5 pointer Esc offer")
    key("ESC", True)
    key("ESC", False)
    wait(lambda: not state()["dragging"], what="R5 pointer Esc cancels the offer")
    button("BTN_MIDDLE", False)
    settle("A", "B")
    check(geometry_pair_matches(before, geometry_pair()),
          "R5 pointer (d): Esc leaves layout unchanged")
    check(not events("A") and not events("B"),
          "R5 pointer (d): Esc and owner release send no client middle event")

    # R5: the virtual touchpad's held BTN_MIDDLE uses the same WK39 offer and commit path.
    setup()
    focus("A")
    clear_events()
    before = geometry_pair()
    a = geometry("A")
    x, y = a["x"] + a["width"] / 2, a["y"] + a["height"] / 2
    pointer(x, y)
    pad_button(True)
    wait(lambda: state()["middle_pending"], what="R5 focused touchpad middle pending")
    check(not events("A"), "R5 touchpad: held middle press stays out of the client")
    wait(lambda: hold_offer().get("active") and state()["middle_dragging"],
         timeout=5, what="R5 focused touchpad solo offer")
    check(hold_offer().get("solo") is True, "R5 touchpad (a): focused target offers a solo")
    check(geometry_pair_matches(before, geometry_pair()),
          "R5 touchpad: solo offer leaves true geometry unchanged while held")
    pad_button(False)
    wait(lambda: not state()["dragging"], what="R5 focused touchpad offer release")
    settle("A", "B")
    touchpad_solo = geometry_pair()
    check(geometry_pair_matches(pointer_solo, touchpad_solo, 2),
          "R5 touchpad (a): focused result matches the WK39 Super+press oracle")
    check(not events("A") and not events("B"),
          "R5 touchpad (a): the held middle press and release never reach either client")

    setup()
    focus("A")
    clear_events()
    before = geometry_pair()
    b = geometry("B")
    x, y = b["x"] + b["width"] / 2, b["y"] + b["height"] / 2
    pointer(x, y)
    pad_button(True)
    wait(lambda: state()["middle_pending"], what="R5 unfocused touchpad middle pending")
    wait(lambda: hold_offer().get("active") and state()["middle_dragging"],
         timeout=5, what="R5 unfocused touchpad pair offer")
    check(hold_offer().get("solo") is False, "R5 touchpad (b): unfocused target offers a pair")
    check(geometry_pair_matches(before, geometry_pair()),
          "R5 touchpad (b): pair offer leaves true geometry unchanged while held")
    pad_button(False)
    wait(lambda: not state()["dragging"], what="R5 unfocused touchpad offer release")
    settle("A", "B")
    touchpad_pair = geometry_pair()
    check(geometry_pair_matches(pointer_pair, touchpad_pair, 2),
          "R5 touchpad (b): paired result matches the WK39 Super+press oracle")
    check(not events("A") and not events("B"),
          "R5 touchpad (b): held middle press and release never reach either client")

    # Moving past the offer hotspot refuses the pair and continues the same live drag.
    setup()
    focus("A")
    clear_events()
    a_before, b_before = geometry("A"), geometry("B")
    x, y = b_before["x"] + b_before["width"] / 2, b_before["y"] + b_before["height"] / 2
    pointer(x, y)
    pad_button(True)
    wait(lambda: hold_offer().get("active"), timeout=5, what="R5 touchpad drag offer")
    for step in range(1, 6):
        pad_motion(x + 20 * step, y)
        if step == 2:
            wait(lambda: not hold_offer().get("active") and state()["dragging"],
                 what="R5 touchpad offer refusal resumes live drag")
        time.sleep(.03)
    time.sleep(.25)
    pad_button(False)
    wait(lambda: not state()["dragging"], what="R5 touchpad continued drag release")
    settle("A", "B")
    a_after, b_after = geometry("A"), geometry("B")
    check(all(abs(a_before[k] - a_after[k]) <= 1 for k in ("x", "y", "width", "height")),
          "R5 touchpad (c): refusing the offer does not commit the pair")
    check(b_after["x"] >= b_before["x"] + 40 and
          (b_after["width"], b_after["height"]) == (b_before["width"], b_before["height"]),
          "R5 touchpad (c): movement after the offer continues the middle drag",
          f"{b_before} -> {b_after}")
    check(not events("A") and not events("B"),
          "R5 touchpad (c): continued drag sends no client middle event")

    # Esc cancels a touchpad middle offer; its owner's eventual release stays consumed.
    setup()
    clear_events()
    before = geometry_pair()
    a = geometry("A")
    x, y = a["x"] + a["width"] / 2, a["y"] + a["height"] / 2
    pointer(x, y)
    pad_button(True)
    wait(lambda: hold_offer().get("active"), timeout=5, what="R5 touchpad Esc offer")
    key("ESC", True)
    key("ESC", False)
    wait(lambda: not state()["dragging"], what="R5 touchpad Esc cancels the offer")
    pad_button(False)
    settle("A", "B")
    check(geometry_pair_matches(before, geometry_pair()),
          "R5 touchpad (d): Esc leaves layout unchanged")
    check(not events("A") and not events("B"),
          "R5 touchpad (d): Esc and owner release send no client middle event")

    # A separate virtual mouse click cannot take over a touchpad-owned offer.
    setup()
    focus("A")
    clear_events()
    a, b = geometry("A"), geometry("B")
    ax, ay = a["x"] + a["width"] / 2, a["y"] + a["height"] / 2
    bx, by = b["x"] + b["width"] / 2, b["y"] + b["height"] / 2
    pointer(bx, by)
    pad_button(True)
    wait(lambda: hold_offer().get("active"), timeout=5, what="R5 touchpad cross-device offer")
    pointer(ax, ay)
    button("BTN_MIDDLE", True)
    button("BTN_MIDDLE", False)
    check(hold_offer().get("active") and state()["middle_dragging"],
          "R5 touchpad (e): mouse click and release leave the offer open")
    pad_button(False)
    wait(lambda: not state()["dragging"], what="R5 touchpad owner releases after mouse click")
    settle("A", "B")
    check(geometry_pair_matches(pointer_pair, geometry_pair(), 2),
          "R5 touchpad (e): only the touchpad owner release commits the pair")
    check(not events("B"), "R5 touchpad (e): held middle event never reaches B")

    # R2: fullscreen surfaces are not target windows; presses arrive immediately.
    setup()
    clear_events()
    full_id = view("A")["id"]
    ipc("wm-actions/set-fullscreen", {"view_id": full_id, "state": True})
    wait(lambda: view("A").get("fullscreen") and geometry("A")["width"] >= 1000 and
         geometry("A")["height"] >= 700, what="A fullscreen geometry")
    full_geometry = geometry("A")
    center_x = full_geometry["x"] + full_geometry["width"] / 2
    center_y = full_geometry["y"] + full_geometry["height"] / 2

    # Fullscreen geometry jumps before its drawn transition settles. Keep making
    # distinct moves until the client reports the final fullscreen center.
    def fullscreen_center_event():
        pointer(center_x - 16, center_y)
        pointer_epoch = len(pointer_events("A"))
        pointer(center_x, center_y)
        return next((event for event in pointer_events("A")[pointer_epoch:]
                     if event["edge"] in ("enter", "motion") and
                     abs(event["x"] - full_geometry["width"] / 2) <= 1 and
                     abs(event["y"] - full_geometry["height"] / 2) <= 1), None)

    wait(fullscreen_center_event,
         what="fullscreen client receives fresh center pointer motion")
    button("BTN_MIDDLE", True)
    wait(lambda: len(events("A")) == 1, what="immediate fullscreen press")
    check(events("A")[0]["edge"] == "press", "fullscreen: client receives press before release")
    button("BTN_MIDDLE", False)
    wait(lambda: len(events("A")) == 2, what="fullscreen release")
    check([event["edge"] for event in events("A")] == ["press", "release"],
          "fullscreen: client receives the unchanged press and release")
    check(geometry("A") == full_geometry, "fullscreen: middle click leaves geometry unchanged")
    ipc("wm-actions/set-fullscreen", {"view_id": full_id, "state": False})
    wait(lambda: not view("A").get("fullscreen"), what="A exits fullscreen")

    # Layer-shell surfaces are not framed Scottland windows. Verify both the Wayfire-side
    # layer namespace and client-observed input so an ordinary GTK toplevel cannot pass here.
    setup()
    place("A", 20, 20, 360, 260)
    before = geometry("A")
    clear_events()
    command("make-layer")
    wait(layer_surface, what="server-recognized layer-shell surface")
    pointer(120, 100)
    wait(lambda: pointer_events("Layer"), what="layer-shell client receives pointer motion")
    check(layer_surface() is not None,
          "layer-shell: Wayfire recognizes the fixture's real layer surface")
    button("BTN_MIDDLE", True)
    wait(lambda: len(events("Layer")) == 1, what="immediate layer-shell middle press")
    check([event["edge"] for event in events("Layer")] == ["press"],
          "layer-shell: middle press reaches the client before release")
    button("BTN_MIDDLE", False)
    wait(lambda: len(events("Layer")) == 2, what="layer-shell middle release")
    check([event["edge"] for event in events("Layer")] == ["press", "release"],
          "layer-shell: middle click reaches the client in order")
    check(geometry("A") == before,
          "layer-shell: middle click does not move the overlapping framed window")

finally:
    try:
        command("quit")
    except Exception:
        pass
    if client.poll() is None:
        client.terminate()
    try:
        client.wait(timeout=5)
    except subprocess.TimeoutExpired:
        client.kill()
        client.wait()

print(f"{passed} passed, {failed} failed", flush=True)
sys.exit(1 if failed else 0)
