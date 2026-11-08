#!/usr/bin/env python3
"""Exercise tablet BTN_MIDDLE delivery through Wayfire's virtual tablet path."""
import json
import math
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time

out = Path(sys.argv[1]).resolve()
app = Path(sys.argv[2]).resolve()
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
        raise RuntimeError(f"{method}: {reply}")
    return reply


def wait(predicate, timeout=10, what="state"):
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        last = predicate()
        if last:
            return last
        time.sleep(.02)
    raise RuntimeError(f"{what} timeout; last observation: {last!r}")


def views():
    return ipc("window-rules/list-views")


def view(title):
    return next((row for row in views() if row.get("title") == title), None)


def layout_view(title):
    return next(row for row in ipc("scottland/layout-state")["views"]
                if row.get("title") == title)


def geometry(title):
    return view(title)["geometry"]


def button_events(title):
    path = out / f"{title}.jsonl"
    if not path.exists():
        return []
    result = []
    for line in path.read_text().splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("event") == "button":
            result.append(event)
    return result


def other_events(title, name):
    path = out / f"{title}.jsonl"
    if not path.exists():
        return []
    result = []
    for line in path.read_text().splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("event") == name:
            result.append(event)
    return result


def clear_events(*titles):
    for title in titles:
        (out / f"{title}.jsonl").write_text("")


def focus(title):
    identifier = view(title)["id"]
    ipc("window-rules/focus-view", {"id": identifier})
    wait(lambda: ipc("window-rules/get-focused-view").get("info", {}).get("id") == identifier,
        what=f"focus {title}")


def place(title, x, y, width=360, height=260):
    identifier = view(title)["id"]
    request = {"id": identifier, "geometry": {
        "x": round(x), "y": round(y), "width": width, "height": height}}
    for _ in range(8):
        ipc("window-rules/configure-view", request)
        try:
            wait(lambda: (lambda g: g["x"] == round(x) and g["y"] == round(y) and
                         abs(g["width"] - width) <= 24 and abs(g["height"] - height) <= 24)(
                             geometry(title)), timeout=1.5, what=f"place {title}")
            return
        except RuntimeError:
            pass
    raise RuntimeError(f"could not place {title}: {geometry(title)!r}")


def settle(*titles):
    previous = None
    stable = 0

    def sample():
        nonlocal previous, stable
        rows = []
        for title in titles:
            row = layout_view(title)
            rows.append((geometry(title), row.get("applied_scale", 1.0),
                         row.get("scene_frame"), row.get("frame")))
        current = tuple(rows)
        if current == previous:
            stable += 1
        else:
            previous, stable = current, 0
        return stable >= 3

    wait(sample, what="settled tablet fixtures")


def setup(a_x=450, b_x=850):
    place("Tablet-Middle-A", a_x, 370)
    place("Tablet-Middle-B", b_x, 370)
    focus("Tablet-Middle-A")
    settle("Tablet-Middle-A", "Tablet-Middle-B")


def cursor():
    row = ipc("scottland/test-input")
    return float(row["cursor_x"]), float(row["cursor_y"])


def tablet_proximity(x, y, entering=True):
    global tool_in_range
    tool_in_range = entering
    return ipc("stipc/tablet/tool_proximity", {
        "proximity_in": entering, "x": round(x), "y": round(y)})


def tablet_axis(x, y):
    return ipc("stipc/tablet/tool_axis", {
        "x": round(x), "y": round(y), "pressure": 0.5})


def tablet_button(code, pressed):
    if pressed:
        held_buttons.add(code)
    else:
        held_buttons.discard(code)
    return ipc("stipc/tablet/tool_button", {"button": code, "state": pressed})


def tablet_tip(x, y, down):
    global tip_down, tip_position
    result = ipc("stipc/tablet/tool_tip", {
        "x": round(x), "y": round(y), "state": down})
    tip_down = down
    tip_position = (x, y) if down else None
    return result


def pointer_motion(x, y):
    return ipc("stipc/move_cursor", {"x": round(x), "y": round(y)})


def pointer_button(code, pressed):
    result = ipc("stipc/feed_button", {
        "combo": code, "mode": "press" if pressed else "release"})
    if pressed:
        held_pointer_buttons.add(code)
    else:
        held_pointer_buttons.discard(code)
    return result


def key(code, pressed):
    result = ipc("stipc/feed_key", {"key": "KEY_" + code, "state": pressed})
    if pressed:
        held_keys.add(code)
    else:
        held_keys.discard(code)
    return result


def at_surface(title, x, y):
    proximity_count = len(other_events(title, "proximity_in"))
    if tool_in_range:
        tablet_proximity(0, 0, False)
    tablet_proximity(x, y, True)
    tablet_axis(x, y)
    wait(lambda: len(other_events(title, "proximity_in")) > proximity_count,
         what=f"tablet proximity on {title}")


passed = failed = 0


def check(ok, label, detail=""):
    global passed, failed
    print(("PASS " if ok else "FAIL ") + label +
          (f" [{detail}]" if detail and not ok else ""), flush=True)
    passed += bool(ok)
    failed += not bool(ok)


processes = {}
stderr_files = {}
tool_in_range = False
held_buttons = set()
held_pointer_buttons = set()
held_keys = set()
tip_down = False
tip_position = None


def start_client(title):
    stderr_files[title] = (out / f"{title}.stderr").open("w")
    processes[title] = subprocess.Popen(
        [str(app), title, str(out / f"{title}.jsonl")],
        stdout=subprocess.DEVNULL, stderr=stderr_files[title])
    wait(lambda: view(title), what=f"map {title}")
    wait(lambda: other_events(title, "ready"), what=f"draw {title}")


def stop_client(title):
    process = processes.pop(title, None)
    if process is not None:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    stream = stderr_files.pop(title, None)
    if stream is not None:
        stream.close()


try:
    ipc("wayfire/set-config-options", {"output:HEADLESS-1/mode": "1600x1000@60000"})
    start_client("Tablet-Middle-A")
    start_client("Tablet-Middle-B")
    setup()

    # Eligible quick click: BTN_MIDDLE is held from the client until release, then replayed once.
    clear_events("Tablet-Middle-A", "Tablet-Middle-B")
    a = geometry("Tablet-Middle-A")
    press = (a["x"] + a["width"] // 2, a["y"] + a["height"] // 2)
    at_surface("Tablet-Middle-A", *press)
    tablet_button(274, True)  # Linux BTN_MIDDLE
    wait(lambda: ipc("scottland/test-input")["tablet_middle_pending"],
         what="tablet middle press held for replay")
    check(not button_events("Tablet-Middle-A") and not button_events("Tablet-Middle-B"),
          "tablet middle press stays withheld until release")
    tablet_button(274, False)
    wait(lambda: len(button_events("Tablet-Middle-A")) == 2,
         what="tablet quick-click replay to original recipient")
    middle = button_events("Tablet-Middle-A")
    check([(event["button"], event["state"]) for event in middle] == [(274, 1), (274, 0)],
          "tablet quick click reaches A once in press/release order")
    check(not button_events("Tablet-Middle-B"), "tablet quick click does not reach B")
    check(geometry("Tablet-Middle-A") == a, "tablet quick click leaves A geometry unchanged")

    # An 8 px release over B remains a click for A. Replay temporarily returns the cursor
    # to A's press point, then restores the actual release point after Wayfire sends release.
    setup(a_x=450, b_x=810)
    a = geometry("Tablet-Middle-A")
    b = geometry("Tablet-Middle-B")
    if b["x"] != a["x"] + a["width"]:
        place("Tablet-Middle-B", a["x"] + a["width"], a["y"])
        settle("Tablet-Middle-A", "Tablet-Middle-B")
        b = geometry("Tablet-Middle-B")
    press = (a["x"] + a["width"] - 4, a["y"] + a["height"] // 2)
    release = (press[0] + 8, press[1])
    check(b["x"] == a["x"] + a["width"] and release[0] > b["x"],
          "fixture: 8 px release point is inside B", f"A={a}; B={b}; press={press}; release={release}")
    clear_events("Tablet-Middle-A", "Tablet-Middle-B")
    at_surface("Tablet-Middle-A", *press)
    at_surface("Tablet-Middle-B", *release)
    check(math.dist(cursor(), release) <= 1.0,
          "fixture: tablet cursor reaches B's client area", f"release={release}; actual={cursor()}")
    clear_events("Tablet-Middle-A", "Tablet-Middle-B")
    at_surface("Tablet-Middle-A", *press)
    tablet_button(274, True)
    wait(lambda: ipc("scottland/test-input")["tablet_middle_pending"],
         what="cross-surface tablet middle press pending")
    tablet_axis(*release)
    cursor_before_release = cursor()
    check(math.dist(cursor_before_release, release) <= 1.0,
          "tablet tool reaches B while the middle button is held",
          f"expected={release}; actual={cursor_before_release}")
    tablet_button(274, False)
    wait(lambda: len(button_events("Tablet-Middle-A")) == 2,
         what="cross-surface tablet replay to A")
    check([(event["button"], event["state"]) for event in button_events("Tablet-Middle-A")] ==
          [(274, 1), (274, 0)], "8 px cross-window tablet replay stays with A")
    check(not button_events("Tablet-Middle-B"), "8 px cross-window tablet click never reaches B")
    actual = cursor()
    check(math.dist(actual, release) <= 1.0, "tablet replay restores the true release cursor",
          f"expected={release}; actual={actual}")
    check(geometry("Tablet-Middle-A") == a,
          "8 px cross-window tablet release does not start a drag")

    # More than the 12 px slop starts the same live move. The client never receives either edge.
    setup()
    clear_events("Tablet-Middle-A", "Tablet-Middle-B")
    before = geometry("Tablet-Middle-A")
    press = (before["x"] + before["width"] // 2, before["y"] + before["height"] // 2)
    at_surface("Tablet-Middle-A", *press)
    tablet_button(274, True)
    wait(lambda: ipc("scottland/test-input")["tablet_middle_pending"],
         what="tablet drag press pending")
    tablet_axis(press[0] + 80, press[1])
    wait(lambda: ipc("scottland/test-input")["tablet_middle_dragging"],
         what="tablet middle live drag")
    check(not button_events("Tablet-Middle-A") and not button_events("Tablet-Middle-B"),
          "tablet middle drag withholds both client button edges")
    tablet_button(274, False)
    wait(lambda: not ipc("scottland/test-input")["tablet_middle_dragging"],
         what="tablet middle drag release")
    settle("Tablet-Middle-A", "Tablet-Middle-B")
    after = geometry("Tablet-Middle-A")
    check(after["x"] >= before["x"] + 40 and
          (after["width"], after["height"]) == (before["width"], before["height"]),
          "tablet middle drag moves A without resizing", f"{before} -> {after}")
    check(not button_events("Tablet-Middle-A") and not button_events("Tablet-Middle-B"),
          "tablet middle drag release reaches no client")

    # Removing the original recipient before release must not transfer its click to B.
    setup()
    clear_events("Tablet-Middle-A", "Tablet-Middle-B")
    a = geometry("Tablet-Middle-A")
    b = geometry("Tablet-Middle-B")
    b_probe = (b["x"] + b["width"] // 2, b["y"] + b["height"] // 2)
    press = (a["x"] + a["width"] // 2, a["y"] + a["height"] // 2)
    at_surface("Tablet-Middle-A", *press)
    tablet_button(274, True)
    wait(lambda: ipc("scottland/test-input")["tablet_middle_pending"],
         what="unmap tablet middle press pending")
    stop_client("Tablet-Middle-A")
    wait(lambda: not any(row.get("title") == "Tablet-Middle-A" for row in views()),
         what="original tablet recipient unmapped")
    tablet_button(274, False)
    # Move onto B after release twice. The second fresh proximity-in follows any older log event.
    at_surface("Tablet-Middle-B", *b_probe)
    at_surface("Tablet-Middle-B", *b_probe)
    check(not button_events("Tablet-Middle-B"),
          "unmapped tablet recipient's middle click is not replayed to B")
    start_client("Tablet-Middle-A")

    # Tip down keeps the tablet target while a quick BTN_MIDDLE click is replayed once.
    setup()
    clear_events("Tablet-Middle-A", "Tablet-Middle-B")
    a = geometry("Tablet-Middle-A")
    b = geometry("Tablet-Middle-B")
    b_probe = (b["x"] + b["width"] // 2, b["y"] + b["height"] // 2)
    press = (a["x"] + a["width"] // 2, a["y"] + a["height"] // 2)
    at_surface("Tablet-Middle-A", *press)
    clear_events("Tablet-Middle-A", "Tablet-Middle-B")
    tablet_tip(*press, True)
    wait(lambda: other_events("Tablet-Middle-A", "tip_down"),
         what="tablet tip down reaches A")
    tablet_button(274, True)
    tablet_button(274, False)
    wait(lambda: len(button_events("Tablet-Middle-A")) == 2,
         what="tip-down tablet middle replay to A")
    tablet_tip(*press, False)
    wait(lambda: other_events("Tablet-Middle-A", "tip_up"),
         what="tablet tip up reaches A")
    # Re-enter B twice so any delayed event from the click precedes the final proximity-in.
    at_surface("Tablet-Middle-B", *b_probe)
    at_surface("Tablet-Middle-B", *b_probe)
    check([(event["button"], event["state"]) for event in button_events("Tablet-Middle-A")] ==
          [(274, 1), (274, 0)], "tip-down tablet click reaches A once in order")
    check(not button_events("Tablet-Middle-B"), "tip-down tablet click never reaches B")
    check(geometry("Tablet-Middle-A") == a,
          "tip-down tablet click leaves A geometry unchanged")
    check(geometry("Tablet-Middle-B") == b,
          "tip-down tablet click leaves B geometry unchanged")

    # R4 compares stepped tablet motion against the same Super+left reference path.
    setup()
    if tool_in_range:
        tablet_proximity(0, 0, False)
    before = geometry("Tablet-Middle-A")
    press = (before["x"] + before["width"] // 2, before["y"] + before["height"] // 2)
    pointer_motion(*press)
    key("LEFTMETA", True)
    pointer_button("BTN_LEFT", True)
    wait(lambda: ipc("scottland/test-input")["dragging"],
         what="Super+left tablet-reference drag")
    for step in range(1, 11):
        pointer_motion(press[0] + 20 * step, press[1])
        time.sleep(.02)
    time.sleep(.25)  # Stop at the final position so release-coast does not change the oracle.
    pointer_button("BTN_LEFT", False)
    key("LEFTMETA", False)
    wait(lambda: not ipc("scottland/test-input")["dragging"],
         what="Super+left tablet-reference drag end")
    settle("Tablet-Middle-A", "Tablet-Middle-B")
    super_after = geometry("Tablet-Middle-A")
    super_delta = (super_after["x"] - before["x"],
                   super_after["y"] - before["y"])

    setup()
    clear_events("Tablet-Middle-A", "Tablet-Middle-B")
    before = geometry("Tablet-Middle-A")
    press = (before["x"] + before["width"] // 2, before["y"] + before["height"] // 2)
    at_surface("Tablet-Middle-A", *press)
    tablet_button(274, True)
    wait(lambda: ipc("scottland/test-input")["tablet_middle_pending"],
         what="stepped tablet drag press pending")
    for step in range(1, 11):
        tablet_axis(press[0] + 20 * step, press[1])
        if step == 1:
            wait(lambda: ipc("scottland/test-input")["tablet_middle_dragging"],
                 what="stepped tablet middle live drag")
        time.sleep(.02)
    time.sleep(.25)  # Match the reference drag's final-position pause.
    tablet_button(274, False)
    wait(lambda: not ipc("scottland/test-input")["tablet_middle_dragging"],
         what="stepped tablet drag release")
    settle("Tablet-Middle-A", "Tablet-Middle-B")
    after = geometry("Tablet-Middle-A")
    tablet_delta = (after["x"] - before["x"], after["y"] - before["y"])
    check(all(abs(tablet_delta[i] - super_delta[i]) <= 4 for i in range(2)),
          "tablet middle 200 px motion lands like Super+left-drag",
          f"tablet {tablet_delta}px vs Super+left {super_delta}px")
    check(tablet_delta[0] >= 150,
          "tablet middle 200 px motion moves A by at least 150 px",
          f"tablet displacement {tablet_delta}px")
    check((after["width"], after["height"]) == (before["width"], before["height"]),
          "tablet middle 200 px drag preserves A size", f"{before} -> {after}")
    check(not button_events("Tablet-Middle-A") and not button_events("Tablet-Middle-B"),
          "tablet middle 200 px drag sends no client button event")

    # Escape cancels a tablet middle drag and keeps its grab until owner release.
    setup()
    clear_events("Tablet-Middle-A", "Tablet-Middle-B")
    before = geometry("Tablet-Middle-A")
    press = (before["x"] + before["width"] // 2, before["y"] + before["height"] // 2)
    at_surface("Tablet-Middle-A", *press)
    tablet_button(274, True)
    wait(lambda: ipc("scottland/test-input")["tablet_middle_pending"],
         what="tablet Escape drag press pending")
    for step in range(1, 11):
        tablet_axis(press[0] + 20 * step, press[1])
        if step == 1:
            wait(lambda: ipc("scottland/test-input")["tablet_middle_dragging"],
                 what="tablet Escape live drag")
        time.sleep(.02)
    key("ESC", True)
    key("ESC", False)
    wait(lambda: not ipc("scottland/test-input")["dragging"],
         what="Escape ends tablet live drag")
    tablet_button(274, False)
    wait(lambda: not ipc("scottland/test-input")["tablet_middle_dragging"],
         what="tablet owner release after Escape")
    settle("Tablet-Middle-A", "Tablet-Middle-B")
    check(geometry("Tablet-Middle-A") == before,
          "tablet Escape restores A's starting geometry", f"{before} -> {geometry('Tablet-Middle-A')}")
    check(not button_events("Tablet-Middle-A") and not button_events("Tablet-Middle-B"),
          "tablet Escape sends no middle event to either client")

    # Other tablet buttons are not intercepted or remapped by the middle-button path.
    setup()
    clear_events("Tablet-Middle-A", "Tablet-Middle-B")
    a = geometry("Tablet-Middle-A")
    press = (a["x"] + a["width"] // 2, a["y"] + a["height"] // 2)
    at_surface("Tablet-Middle-A", *press)
    tablet_button(331, True)  # Linux BTN_STYLUS
    wait(lambda: len(button_events("Tablet-Middle-A")) == 1,
         what="BTN_STYLUS native press")
    stylus = button_events("Tablet-Middle-A")
    check([(event["button"], event["state"]) for event in stylus] == [(331, 1)],
          "BTN_STYLUS press keeps its native button code")
    check(not ipc("scottland/test-input")["tablet_middle_pending"],
          "BTN_STYLUS does not start tablet middle handling")
    tablet_button(331, False)
    wait(lambda: len(button_events("Tablet-Middle-A")) == 2,
         what="BTN_STYLUS native release")
    check([(event["button"], event["state"]) for event in button_events("Tablet-Middle-A")] ==
          [(331, 1), (331, 0)], "BTN_STYLUS press and release reach A unchanged")

    # Fullscreen surfaces are not drag targets and retain immediate native middle delivery.
    clear_events("Tablet-Middle-A", "Tablet-Middle-B")
    identifier = view("Tablet-Middle-A")["id"]
    ipc("wm-actions/set-fullscreen", {"view_id": identifier, "state": True})
    wait(lambda: view("Tablet-Middle-A").get("fullscreen"), what="A fullscreen")
    g = geometry("Tablet-Middle-A")
    press = (g["x"] + g["width"] // 2, g["y"] + g["height"] // 2)
    at_surface("Tablet-Middle-A", *press)
    tablet_button(274, True)
    wait(lambda: len(button_events("Tablet-Middle-A")) == 1,
         what="fullscreen tablet middle press passthrough")
    check(button_events("Tablet-Middle-A")[0] ==
          {"event": "button", "button": 274, "state": 1},
          "fullscreen tablet middle press is delivered before release")
    check(not ipc("scottland/test-input")["tablet_middle_pending"],
          "fullscreen tablet middle press is not withheld")
    tablet_button(274, False)
    wait(lambda: len(button_events("Tablet-Middle-A")) == 2,
         what="fullscreen tablet middle release passthrough")
    ipc("wm-actions/set-fullscreen", {"view_id": identifier, "state": False})

finally:
    for button in tuple(held_buttons):
        try:
            tablet_button(button, False)
        except Exception:
            pass
    if tip_down and tip_position:
        try:
            tablet_tip(*tip_position, False)
        except Exception:
            pass
    for button in tuple(held_pointer_buttons):
        try:
            pointer_button(button, False)
        except Exception:
            pass
    for code in tuple(held_keys):
        try:
            key(code, False)
        except Exception:
            pass
    if tool_in_range:
        try:
            tablet_proximity(0, 0, False)
        except Exception:
            pass
    for title in list(processes):
        stop_client(title)
    sock.close()

print(f"{passed} passed, {failed} failed", flush=True)
sys.exit(1 if failed else 0)
