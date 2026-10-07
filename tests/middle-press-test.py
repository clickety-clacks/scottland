#!/usr/bin/env python3
"""Acceptance checks for the pointer slice of middle-press window drag."""
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


def geometry(title):
    return view(title)["geometry"]


def state():
    return ipc("scottland/test-input")


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


def all_events():
    path = out / "buttons.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def events(title):
    return [event for event in all_events() if event["name"] == title and event["button"] == 2]


def clear_events():
    (out / "buttons.jsonl").write_text("")


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


def focus(title):
    identifier = view(title)["id"]
    ipc("window-rules/focus-view", {"id": identifier})
    wait(lambda: ipc("window-rules/get-focused-view").get("info", {}).get("id") == identifier,
         what="focus Middle-" + title)


def settle(*titles, timeout=5):
    end = time.monotonic() + timeout
    previous = None
    stable = 0
    while time.monotonic() < end:
        rows = ipc("scottland/layout-state")["views"]
        scales = {row["id"]: row.get("applied_scale", 1.0) for row in rows}
        current = tuple((geometry(title), round(scales.get(view(title)["id"], 1.0), 4))
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
    focus("A")
    settle("A", "B")


passed = failed = 0


def check(ok, label, detail=""):
    global passed, failed
    print(("PASS " if ok else "FAIL ") + label + (f" [{detail}]" if detail and not ok else ""), flush=True)
    passed += bool(ok)
    failed += not bool(ok)


client = subprocess.Popen([sys.executable, str(Path(__file__).with_name("middle-press-app.py")), str(out)],
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
    wait(lambda: state()["middle_pending"], what="pointer middle pending")
    time.sleep(.10)
    check(not all_events(), "pointer: no client event arrives before the owner releases")
    pointer(x + 3, y)
    time.sleep(.04)
    button("BTN_MIDDLE", False)
    wait(lambda: len(events("A")) == 2, what="pointer quick-click replay")
    click = events("A")
    check([(event["edge"], event["button"]) for event in click] == [("press", 2), ("release", 2)],
          "pointer: a quick click reaches A once, in order")
    check(geometry("A") == a, "pointer: a quick click leaves A's geometry unchanged")

    # R3(b): the release is over B, but the click belongs only to the original surface A.
    setup(a_x=300, b_x=600)
    clear_events()
    a = geometry("A")
    x, y = a["x"] + a["width"] - 4, a["y"] + a["height"] / 2
    pointer(x, y)
    button("BTN_MIDDLE", True)
    wait(lambda: state()["middle_pending"], what="cross-surface middle pending")
    pointer(x + 8, y)
    button("BTN_MIDDLE", False)
    wait(lambda: len(events("A")) == 2, what="original-surface replay")
    check(len(events("B")) == 0, "pointer: crossing onto B never transfers A's click")
    check(geometry("A") == a, "pointer: an 8 px cross-surface click does not drag")

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

    # R3(c): if the original client unmaps during the withheld press, nobody gets the click.
    clear_events()
    a = geometry("A")
    pointer(a["x"] + a["width"] / 2, a["y"] + a["height"] / 2)
    button("BTN_MIDDLE", True)
    wait(lambda: state()["middle_pending"], what="unmap middle pending")
    command("hide", "A")
    wait(lambda: not any(row.get("title") == "Middle-A" for row in views()), what="A unmap")
    button("BTN_MIDDLE", False)
    time.sleep(.1)
    check(not all_events(), "pointer: an unmapped original recipient gets no replay")
    command("show", "A")
    wait(lambda: any(row.get("title") == "Middle-A" for row in views()), what="A remap")
    setup()

    # R4, R1: a virtual pointer middle drag follows the same live move and suppresses client input.
    movement_deltas = {}
    for source in ("pointer", "touchpad"):
        setup()
        clear_events()
        before = geometry("A")
        x, y = before["x"] + before["width"] / 2, before["y"] + before["height"] / 2
        pointer(x, y)
        old_sample = (x - 60, y + 20)
        before_shot = screenshot(source + "-middle-before")
        check(is_fixture_pixel(pixel(before_shot, *old_sample)),
              source + ": the fixture's mapped pixels are visible before dragging")
        if source == "pointer":
            button("BTN_MIDDLE", True)
        else:
            ipc("wayfire/set-config-options", {"scottland/touchpad_gestures": False})
            pad_button(True)
        wait(lambda: state()["middle_pending"], what=source + " middle pending")
        for step in range(1, 11):
            if source == "pointer":
                pointer(x + 20 * step, y)
            else:
                pad_motion(x + 20 * step, y)
            if step == 1:
                wait(lambda: state()["middle_dragging"], what=source + " live drag")
            time.sleep(.02)
        check(not all_events(), source + ": a drag sends no middle events to A")
        time.sleep(.25)  # Stop at the final position so release-coast does not change the oracle.
        if source == "pointer":
            button("BTN_MIDDLE", False)
        else:
            pad_button(False)
            ipc("wayfire/set-config-options", {"scottland/touchpad_gestures": True})
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
    wait(lambda: state()["middle_pending"], what="Esc middle pending")
    for step in range(1, 11):
        pointer(x + 20 * step, y)
        if step == 1:
            wait(lambda: state()["middle_dragging"], what="Esc live drag")
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

    # R2: fullscreen and layer-shell surfaces are not target windows; presses arrive immediately.
    setup()
    clear_events()
    full_id = view("A")["id"]
    ipc("wm-actions/set-fullscreen", {"view_id": full_id, "state": True})
    wait(lambda: view("A").get("fullscreen"), what="A fullscreen")
    full_geometry = geometry("A")
    pointer(full_geometry["x"] + full_geometry["width"] / 2,
            full_geometry["y"] + full_geometry["height"] / 2)
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

    clear_events()
    command("make-layer")
    wait(lambda: (out / "Layer.mapped").exists(), what="layer-shell map")
    pointer(20, 20)
    button("BTN_MIDDLE", True)
    wait(lambda: len(events("Layer")) == 1, what="immediate layer-shell press")
    check(events("Layer")[0]["edge"] == "press", "layer-shell: client receives press before release")
    button("BTN_MIDDLE", False)
    wait(lambda: len(events("Layer")) == 2, what="layer-shell release")
    check([event["edge"] for event in events("Layer")] == ["press", "release"],
          "layer-shell: client receives the unchanged press and release")

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
