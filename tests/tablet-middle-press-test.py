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


def hold_offer():
    return ipc("scottland/spread-state")["hold_offer"]


def geometry_pair():
    return tuple(tuple(geometry(title)[key] for key in ("x", "y", "width", "height"))
                 for title in ("Tablet-Middle-A", "Tablet-Middle-B"))


def geometry_pair_matches(left, right, tolerance=1):
    return all(abs(a - b) <= tolerance for old, new in zip(left, right)
               for a, b in zip(old, new))


def frame_state(title):
    row = layout_view(title)
    state = dict(row["frame"])
    state.update(row.get("scene_frame", {}))
    return state


def frame_diagnostic(title):
    row = layout_view(title)
    return {key: row.get(key) for key in ("frame", "scene_frame", "applied_scale")}


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

    # An 8 px release onto B's actual scene hit area remains a click for A. Replay
    # returns the cursor to A's press point, then restores the true release point.
    setup(a_x=450, b_x=810)
    focus("Tablet-Middle-B")
    settle("Tablet-Middle-A", "Tablet-Middle-B")
    a = geometry("Tablet-Middle-A")
    a_frame = frame_state("Tablet-Middle-A")
    b_before = geometry("Tablet-Middle-B")
    place("Tablet-Middle-B", round(a_frame["x"] + a_frame["width"] - 17),
          b_before["y"], 360, 260)
    settle("Tablet-Middle-A", "Tablet-Middle-B")

    # The tablet axis must dwell near B until its halo is swollen. Compute the
    # 8 px endpoints only after that state is stable, so they cannot shift into
    # B's frame band partway through the case.
    b_frame = frame_state("Tablet-Middle-B")
    hover = (round(b_frame["x"] - b_frame["thickness"] - 6),
             round(b_frame["y"] + b_frame["height"] / 2))
    if tool_in_range:
        tablet_proximity(0, 0, False)
    tablet_proximity(hover[0], hover[1], True)
    tablet_axis(*hover)
    wait(lambda: frame_state("Tablet-Middle-B")["swell"] >= .99,
         what="tablet dwell swells B's halo before R3(b) geometry")
    settle("Tablet-Middle-A", "Tablet-Middle-B")

    a_frame = frame_state("Tablet-Middle-A")
    b_frame = frame_state("Tablet-Middle-B")
    check(b_frame["swell"] >= .99,
          "fixture: B halo is swollen before computing tablet R3(b) endpoints",
          f"B={frame_diagnostic('Tablet-Middle-B')}; hover={hover}")
    b = geometry("Tablet-Middle-B")
    b_halo_left = b_frame["x"] - b_frame["thickness"]
    press = (round(b_halo_left - 6), round(a_frame["y"] + a_frame["height"] / 2))
    release = (press[0] + 8, press[1])
    a_right = a_frame["x"] + a_frame["width"]
    check(a_frame["x"] < press[0] < release[0] < a_right and
          press[0] < b_halo_left < release[0] < b_frame["x"] and
          b_frame["y"] - b_frame["thickness"] < press[1] <
          b_frame["y"] + b_frame["height"] + b_frame["thickness"],
          "fixture: 8 px endpoints span A's reported scene frame and B's reported frame hit area",
          f"A={frame_diagnostic('Tablet-Middle-A')}; "
          f"B={frame_diagnostic('Tablet-Middle-B')}; press={press}; release={release}")
    clear_events("Tablet-Middle-A", "Tablet-Middle-B")
    press_target = hit_target(press[0], press[1], "Tablet-Middle-A", "Tablet-Middle-B")
    print(f"INFO R3(b) tablet press hit target at {press}: {press_target}; "
          f"A={frame_diagnostic('Tablet-Middle-A')}; "
          f"B={frame_diagnostic('Tablet-Middle-B')}", flush=True)
    press_is_a_client = press_target["window"] == "A" and press_target["part"] == "client_surface"
    check(press_is_a_client, "tablet R3(b) press hit-tests to A client", str(press_target))
    if press_is_a_client:
        at_surface("Tablet-Middle-A", *press)
        settle("Tablet-Middle-A", "Tablet-Middle-B")
        press_target = hit_target(press[0], press[1], "Tablet-Middle-A", "Tablet-Middle-B")
        print(f"INFO R3(b) tablet target immediately before press at {press}: {press_target}", flush=True)
        press_is_a_client = press_target["window"] == "A" and press_target["part"] == "client_surface"
        check(press_is_a_client, "tablet R3(b) actual press target is A client", str(press_target))
    if press_is_a_client:
        clear_events("Tablet-Middle-A", "Tablet-Middle-B")
        tablet_button(274, True)
        wait(lambda: ipc("scottland/test-input")["tablet_middle_pending"],
             what=f"tablet middle pending after recorded A-client target {press_target}")
        tablet_axis(*release)
        cursor_before_release = cursor()
        release_target = hit_target(release[0], release[1], "Tablet-Middle-A", "Tablet-Middle-B")
        print(f"INFO R3(b) tablet release hit target at {release}: {release_target}; "
              f"cursor={cursor_before_release}", flush=True)
        check(math.dist(cursor_before_release, release) <= 1.0,
              "tablet tool reaches the recorded release point while the middle button is held",
              f"expected={release}; actual={cursor_before_release}; target={release_target}")
        release_is_b = valid_release_target(release_target)
        check(release_is_b, "tablet R3(b) release hit-tests to B client or frame/expanded area",
              str(release_target))
        tablet_button(274, False)
        wait(lambda: len(button_events("Tablet-Middle-A")) == 2,
             what=f"cross-surface tablet replay to A; release target={release_target}")
        check([(event["button"], event["state"]) for event in button_events("Tablet-Middle-A")] ==
              [(274, 1), (274, 0)], "8 px cross-window tablet replay stays with A")
        check(not button_events("Tablet-Middle-B"), "8 px cross-window tablet click never reaches B")
        actual = cursor()
        check(math.dist(actual, release) <= 1.0, "tablet replay restores the true release cursor",
              f"expected={release}; actual={actual}")
        check(geometry("Tablet-Middle-A") == a,
              "8 px cross-window tablet release does not start a drag")
        check(geometry("Tablet-Middle-B") == b,
              "8 px cross-window tablet release leaves B geometry unchanged")

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

    # R5: a still tablet BTN_MIDDLE uses the same WK39 hold offer as Super+press.
    ipc("wayfire/set-config-options", {"scottland/window_hold_delay": 600})
    setup()
    clear_events("Tablet-Middle-A", "Tablet-Middle-B")
    before_pair = geometry_pair()
    a = geometry("Tablet-Middle-A")
    press = (a["x"] + a["width"] // 2, a["y"] + a["height"] // 2)
    at_surface("Tablet-Middle-A", *press)
    tablet_button(274, True)
    wait(lambda: ipc("scottland/test-input")["tablet_middle_pending"],
         what="R5 focused tablet middle pending")
    wait(lambda: hold_offer().get("active") and
         ipc("scottland/test-input")["tablet_middle_dragging"],
         timeout=5, what="R5 focused tablet solo offer")
    check(hold_offer().get("solo") is True, "R5 tablet (a): focused target offers a solo")
    check(not button_events("Tablet-Middle-A"),
          "R5 tablet: the held press sends no middle event while offered")
    check(geometry_pair_matches(before_pair, geometry_pair()),
          "R5 tablet: the offer leaves true geometry unchanged while held")
    tablet_button(274, False)
    wait(lambda: not ipc("scottland/test-input")["dragging"],
         what="R5 focused tablet offer release")
    settle("Tablet-Middle-A", "Tablet-Middle-B")
    tablet_solo = geometry_pair()
    check(not button_events("Tablet-Middle-A"),
          "R5 tablet (a): the held middle press and release never reach A")
    if tool_in_range:
        tablet_proximity(0, 0, False)

    setup()
    focus("Tablet-Middle-A")
    a = geometry("Tablet-Middle-A")
    press = (a["x"] + a["width"] // 2, a["y"] + a["height"] // 2)
    pointer_motion(*press)
    key("LEFTMETA", True)
    pointer_button("BTN_LEFT", True)
    wait(lambda: hold_offer().get("active"), timeout=5,
         what="WK39 focused Super+press solo offer")
    pointer_button("BTN_LEFT", False)
    key("LEFTMETA", False)
    wait(lambda: not ipc("scottland/test-input")["dragging"],
         what="WK39 focused Super+press release")
    settle("Tablet-Middle-A", "Tablet-Middle-B")
    check(geometry_pair_matches(tablet_solo, geometry_pair(), 2),
          "R5 tablet (a): focused result matches the WK39 Super+press solo oracle")

    setup()
    focus("Tablet-Middle-A")
    clear_events("Tablet-Middle-A", "Tablet-Middle-B")
    before_pair = geometry_pair()
    b = geometry("Tablet-Middle-B")
    press = (b["x"] + b["width"] // 2, b["y"] + b["height"] // 2)
    at_surface("Tablet-Middle-B", *press)
    tablet_button(274, True)
    wait(lambda: ipc("scottland/test-input")["tablet_middle_pending"],
         what="R5 unfocused tablet middle pending")
    wait(lambda: hold_offer().get("active") and
         ipc("scottland/test-input")["tablet_middle_dragging"],
         timeout=5, what="R5 unfocused tablet pair offer")
    check(hold_offer().get("solo") is False, "R5 tablet (b): unfocused target offers a pair")
    check(geometry_pair_matches(before_pair, geometry_pair()),
          "R5 tablet (b): pair preview leaves true geometry unchanged")
    tablet_button(274, False)
    wait(lambda: not ipc("scottland/test-input")["dragging"],
         what="R5 unfocused tablet offer release")
    settle("Tablet-Middle-A", "Tablet-Middle-B")
    tablet_pair = geometry_pair()
    check(not button_events("Tablet-Middle-B"),
          "R5 tablet (b): held middle press and release never reach B")
    if tool_in_range:
        tablet_proximity(0, 0, False)

    setup()
    focus("Tablet-Middle-A")
    b = geometry("Tablet-Middle-B")
    press = (b["x"] + b["width"] // 2, b["y"] + b["height"] // 2)
    pointer_motion(*press)
    key("LEFTMETA", True)
    pointer_button("BTN_LEFT", True)
    wait(lambda: hold_offer().get("active"), timeout=5,
         what="WK39 unfocused Super+press pair offer")
    pointer_button("BTN_LEFT", False)
    key("LEFTMETA", False)
    wait(lambda: not ipc("scottland/test-input")["dragging"],
         what="WK39 unfocused Super+press release")
    settle("Tablet-Middle-A", "Tablet-Middle-B")
    check(geometry_pair_matches(tablet_pair, geometry_pair(), 2),
          "R5 tablet (b): unfocused result matches the WK39 Super+press pair oracle")

    # Movement beyond the hotspot refuses the pair and keeps the owner's drag live.
    setup()
    focus("Tablet-Middle-A")
    clear_events("Tablet-Middle-A", "Tablet-Middle-B")
    a_before = geometry("Tablet-Middle-A")
    b_before = geometry("Tablet-Middle-B")
    press = (b_before["x"] + b_before["width"] // 2,
             b_before["y"] + b_before["height"] // 2)
    at_surface("Tablet-Middle-B", *press)
    tablet_button(274, True)
    wait(lambda: hold_offer().get("active"), timeout=5,
         what="R5 tablet drag offer")
    for distance in (20, 40, 60, 80):
        tablet_axis(press[0] + distance, press[1])
        time.sleep(.03)
    wait(lambda: not hold_offer().get("active") and
         ipc("scottland/test-input")["dragging"],
         what="R5 tablet offer refusal resumes the live drag")
    time.sleep(.25)
    tablet_button(274, False)
    wait(lambda: not ipc("scottland/test-input")["dragging"],
         what="R5 tablet continued drag release")
    settle("Tablet-Middle-A", "Tablet-Middle-B")
    a_after = geometry("Tablet-Middle-A")
    b_after = geometry("Tablet-Middle-B")
    check(all(abs(a_before[k] - a_after[k]) <= 1 for k in ("x", "y", "width", "height")),
          "R5 tablet (c): refusing the offer does not commit the pair")
    check(b_after["x"] >= b_before["x"] + 40 and
          (b_after["width"], b_after["height"]) == (b_before["width"], b_before["height"]),
          "R5 tablet (c): movement after the offer continues the tablet drag",
          f"{b_before} -> {b_after}")
    check(not button_events("Tablet-Middle-A") and not button_events("Tablet-Middle-B"),
          "R5 tablet (c): continued drag sends no client middle event")
    if tool_in_range:
        tablet_proximity(0, 0, False)

    # Esc refuses an active offer; the tablet grab stays until the owner releases.
    setup()
    clear_events("Tablet-Middle-A", "Tablet-Middle-B")
    before_pair = geometry_pair()
    a = geometry("Tablet-Middle-A")
    press = (a["x"] + a["width"] // 2, a["y"] + a["height"] // 2)
    at_surface("Tablet-Middle-A", *press)
    tablet_button(274, True)
    wait(lambda: hold_offer().get("active"), timeout=5,
         what="R5 tablet Esc offer")
    key("ESC", True)
    key("ESC", False)
    wait(lambda: not ipc("scottland/test-input")["dragging"],
         what="R5 tablet Esc cancels the offer")
    tablet_button(274, False)
    wait(lambda: not ipc("scottland/test-input")["tablet_middle_dragging"],
         what="R5 tablet owner release after Esc")
    settle("Tablet-Middle-A", "Tablet-Middle-B")
    check(geometry_pair_matches(before_pair, geometry_pair()),
          "R5 tablet (d): Esc leaves layout unchanged")
    check(not button_events("Tablet-Middle-A") and not button_events("Tablet-Middle-B"),
          "R5 tablet (d): Esc and owner release send no client middle event")
    if tool_in_range:
        tablet_proximity(0, 0, False)

    # A virtual mouse middle click is unrelated to the tablet offer and cannot take it over.
    setup()
    focus("Tablet-Middle-A")
    clear_events("Tablet-Middle-A", "Tablet-Middle-B")
    press_b = geometry("Tablet-Middle-B")
    press = (press_b["x"] + press_b["width"] // 2,
             press_b["y"] + press_b["height"] // 2)
    at_surface("Tablet-Middle-B", *press)
    tablet_button(274, True)
    wait(lambda: hold_offer().get("active"), timeout=5,
         what="R5 tablet virtual-mouse noninterference offer")
    a = geometry("Tablet-Middle-A")
    pointer_motion(a["x"] + a["width"] // 2, a["y"] + a["height"] // 2)
    pointer_button("BTN_MIDDLE", True)
    pointer_button("BTN_MIDDLE", False)
    wait(lambda: hold_offer().get("active") and
         ipc("scottland/test-input")["tablet_middle_dragging"],
         what="R5 tablet offer remains owned after unrelated virtual mouse click")
    check(not button_events("Tablet-Middle-B"),
          "R5 tablet (e): unrelated mouse click does not deliver the held tablet button to B")
    tablet_button(274, False)
    wait(lambda: not ipc("scottland/test-input")["dragging"],
         what="R5 tablet owner release commits the pair")
    settle("Tablet-Middle-A", "Tablet-Middle-B")
    check(geometry_pair_matches(tablet_pair, geometry_pair(), 2),
          "R5 tablet (e): owner release commits the still-offered pair")
    check(not button_events("Tablet-Middle-B"),
          "R5 tablet (e): original tablet recipient receives no middle event")
    if tool_in_range:
        tablet_proximity(0, 0, False)

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
