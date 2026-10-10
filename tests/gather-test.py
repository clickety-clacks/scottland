#!/usr/bin/env python3
"""Bounded Gather acceptance checks with real stipc input in tests/headless.sh.

Fixture geometry and focus use IPC; every key press, release, repeat, hint, and hold under test is
real Wayfire input. Run as `tests/headless.sh run python3 tests/gather-test.py ARTIFACTS` from an
isolated two-output session on an admitted Linux test runner.
"""
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

assert os.environ.get("SCOTTLAND_TEST_MODEL") == "1", "requires the isolated Scottland headless test session"
art = Path(sys.argv[1]).resolve()
art.mkdir(parents=True, exist_ok=True)

GTK_APP = r"""import json, sys, gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, Gio, Gdk
title, path = sys.argv[1], sys.argv[2]
app = Gtk.Application(application_id='org.scottland.GatherTest.' + title,
                      flags=Gio.ApplicationFlags.NON_UNIQUE)
def log(edge, keyval, keycode, state):
    with open(path, 'a') as stream:
        stream.write(json.dumps({'edge': edge, 'key': Gdk.keyval_name(keyval),
                                 'keycode': keycode, 'mods': int(state)}) + '\n')
    return False
def activate(_):
    window = Gtk.ApplicationWindow(application=app, title=title)
    window.set_default_size(40, 80) if title == 'GatherRail' else window.set_default_size(400, 300)
    window.set_child(Gtk.Label(label='' if title == 'GatherRail' else title))
    keys = Gtk.EventControllerKey()
    keys.connect('key-pressed', lambda _, val, code, mods: log('down', val, code, mods))
    keys.connect('key-released', lambda _, val, code, mods: log('up', val, code, mods))
    window.add_controller(keys)
    window.present()
app.connect('activate', activate)
app.run([])
"""

passed = failed = 0
clients = []
held = set()
held_buttons = set()
geometry_origin = {}
output_by_id = {}


def ipc(method, data=None):
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(10)
        sock.connect(os.environ["WAYFIRE_SOCKET"])
        body = json.dumps({"method": method, "data": data or {}}).encode()
        sock.sendall(struct.pack("<I", len(body)) + body)

        def read(count):
            result = b""
            while len(result) < count:
                chunk = sock.recv(count - len(result))
                if not chunk:
                    raise RuntimeError("Wayfire IPC disconnected")
                result += chunk
            return result

        reply = json.loads(read(struct.unpack("<I", read(4))[0]))
    if isinstance(reply, dict) and "error" in reply:
        raise RuntimeError(f"{method}: {reply['error']}")
    return reply


def check(ok, name, detail=None):
    global passed, failed
    print(("PASS " if ok else "FAIL ") + name + (f" [{detail}]" if detail is not None and not ok else ""),
          flush=True)
    passed += bool(ok)
    failed += not ok


def wait_for(predicate, timeout=15, what="state"):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        result = predicate()
        if result:
            return result
        time.sleep(0.01)
    raise RuntimeError(what + " timeout")


def outputs():
    return ipc("window-rules/list-outputs")


def views():
    return ipc("window-rules/list-views")


def layout_views():
    return ipc("scottland/layout-state")["views"]


def view_id(title):
    return next(v["id"] for v in views() if v.get("title") == title)


def raw_view(identifier):
    return next(v for v in views() if v["id"] == identifier)


def layout(identifier):
    return next(v for v in layout_views() if v["id"] == identifier)


def center_local(identifier):
    row = raw_view(identifier)
    g = row["geometry"]
    ox = geometry_origin.get(identifier, 0)
    return (g["x"] - ox + g["width"] / 2, g["y"] - output_by_id[row["output-id"]]["geometry"]["y"] *
            (1 if ox else 0) + g["height"] / 2)


def local_geometry(identifier):
    row = raw_view(identifier)
    g = dict(row["geometry"])
    g["x"] -= geometry_origin.get(identifier, 0)
    if geometry_origin.get(identifier, 0):
        g["y"] -= output_by_id[row["output-id"]]["geometry"]["y"]
    return g


def focus(identifier):
    ipc("window-rules/focus-view", {"id": identifier})
    wait_for(lambda: ipc("window-rules/get-focused-view").get("info", {}).get("id") == identifier,
             what="focused view")


def key(code, down):
    ipc("stipc/feed_key", {"key": "KEY_" + code, "state": down})
    if down:
        held.add(code)
    else:
        held.discard(code)


def pointer(x, y):
    ipc("stipc/move_cursor", {"x": round(x), "y": round(y)})


def button(down):
    ipc("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "press" if down else "release"})
    if down:
        held_buttons.add("BTN_LEFT")
    else:
        held_buttons.discard("BTN_LEFT")


def tap(code):
    key(code, True)
    key(code, False)


def hints():
    return ipc("scottland/hints")


def hint_for(identifier):
    return next(h for h in hints()["hints"] if h["window"] == identifier)["hint"]


def type_hint(identifier, hold=0.08):
    text = hint_for(identifier)
    for letter in text[:-1]:
        tap(letter.upper())
    last = text[-1].upper()
    key(last, True)
    if hold:
        time.sleep(hold)  # deliberate hint hold or a short tap, never a readiness wait
    key(last, False)


def begin_window_mode():
    key("LEFTALT", True)
    wait_for(lambda: hints()["active"], what="Window mode entry")


def end_window_mode():
    if "LEFTALT" in held:
        key("LEFTALT", False)
    wait_for(lambda: not hints()["active"], what="Window mode exit")


def spread_state():
    return ipc("scottland/spread-state")


def wait_solves(count, timeout=45):
    return wait_for(lambda: (s if (s := spread_state())["solves"] >= count and not s["running"] else None),
                    timeout, f"{count} completed spread solves")


def wait_solve_record(sequence, timeout=45):
    return wait_for(lambda: (s["last"] if (s := spread_state())["last"].get("sequence") == sequence else None),
                    timeout, f"spread result sequence {sequence}")


def events(title):
    path = art / (title + ".keys.jsonl")
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def place(title, out, x, y, width, height):
    identifier = view_id(title)
    origin = out["geometry"]["x"]
    output_y = out["geometry"]["y"]
    for _ in range(8):
        ipc("window-rules/configure-view", {"id": identifier, "output_id": out["id"],
            "geometry": {"x": x, "y": y, "width": width, "height": height}})
        try:
            row = wait_for(lambda: next((v for v in views() if v["id"] == identifier), None),
                           2, "configured view")
            g = row["geometry"]
            if (g["x"], g["y"], g["width"], g["height"]) == (x, y, width, height):
                geometry_origin[identifier] = 0
                return identifier
            if (g["x"], g["y"], g["width"], g["height"]) == (origin + x, output_y + y, width, height):
                geometry_origin[identifier] = origin
                return identifier
        except RuntimeError:
            pass
    raise RuntimeError(f"fixture {title} did not take {width}x{height}+{x}+{y}: {raw_view(identifier)}")


def launch(title):
    path = art / (title + ".keys.jsonl")
    path.write_text("")
    process = subprocess.Popen([sys.executable, "-c", GTK_APP, title, str(path)],
        stdout=subprocess.DEVNULL, stderr=(art / (title + ".client.log")).open("w"))
    clients.append(process)
    identifier = wait_for(lambda: next((v["id"] for v in views() if v.get("title") == title), None),
                          15, "client map " + title)
    return identifier


def zone_center(identifier):
    x, _ = center_local(identifier)
    out = output_by_id[raw_view(identifier)["output-id"]]
    W = out["geometry"]["width"]
    center_half = W * 33.333 / 200
    rail = W * 0.02
    if abs(x - W / 2) <= center_half:
        return "center"
    if abs(x - W / 2) >= W / 2 - rail:
        return "rail"
    return "periphery"


def inner_distance(identifier):
    row = raw_view(identifier)
    g = local_geometry(identifier)
    out = output_by_id[row["output-id"]]
    W = out["geometry"]["width"]
    center_half = W * 33.333 / 200
    x = g["x"] + g["width"] / 2
    scale = layout(identifier)["applied_scale"]
    if x < W / 2:
        return max(0.0, (W / 2 - center_half) - (x + g["width"] * scale / 2))
    return max(0.0, (x - g["width"] * scale / 2) - (W / 2 + center_half))


def signature(identifier):
    g = raw_view(identifier)["geometry"]
    v = layout(identifier)
    return ((round(g["x"], 3), round(g["y"], 3), g["width"], g["height"]),
            round(v["target_scale"], 4), round(v["applied_scale"], 4), v["zone"])


def settle(identifiers, timeout=12):
    end = time.monotonic() + timeout
    previous = None
    stable = 0
    while time.monotonic() < end:
        current = tuple(signature(i) for i in identifiers)
        if current == previous:
            stable += 1
            if stable >= 4:
                return current
        else:
            stable = 0
            previous = current
        time.sleep(0.05)
    raise RuntimeError("views did not settle: " + repr(tuple(signature(i) for i in identifiers)))


def press_space():
    key("SPACE", True)
    key("SPACE", False)


def cycle_one_zone(identifier):
    begin_window_mode()
    ensure_selected(identifier)
    type_hint(identifier)
    end_window_mode()


def ensure_selected(identifier):
    if hints()["selected"] != identifier:
        type_hint(identifier)
        wait_for(lambda: hints()["selected"] == identifier, what="hint selection")


def drag_to(identifier, x, y):
    focus(identifier)
    settle([identifier])
    g = raw_view(identifier)["geometry"]
    start = (g["x"] + g["width"] / 2, g["y"] + g["height"] / 2)
    pointer(*start)
    key("LEFTMETA", True)
    button(True)
    try:
        for step in range(1, 13):
            pointer(start[0] + (x - start[0]) * step / 12,
                    start[1] + (y - start[1]) * step / 12)
            time.sleep(0.02)  # pace a real drop so the compositor sees the drag
        time.sleep(0.25)  # keep the pointer still so this is not an inertial drop
    finally:
        button(False)
        key("LEFTMETA", False)
    settle([identifier])


try:
    metadata = ET.parse(Path(__file__).resolve().parents[1] / "core/plugin/metadata/scottland.xml")
    gather_options = [option.get("name") for option in metadata.findall(".//option")
                      if "gather" in option.get("name", "").lower()]
    check(not gather_options, "GA4: Gather has no configurable plugin key", gather_options)

    ipc("wayfire/set-config-options", {"scottland/sounds": False, "scottland/window_hold_delay": 650,
        "scottland/alt_hold_delay": 900, "scottland/window_avoidance_always": False,
        "scottland/hint_avoidance_always": False})
    outs = sorted(outputs(), key=lambda o: o["geometry"]["x"])
    if len(outs) != 2:
        raise RuntimeError(f"expected two isolated headless outputs, got {len(outs)}")
    for out in outs:
        ipc("wayfire/set-config-options", {f"output:{out['name']}/mode": "1920x1080@60000"})
    wait_for(lambda: len(outputs()) == 2 and all(o["geometry"]["width"] == 1920 and
             o["geometry"]["height"] == 1080 for o in outputs()), what="two 1920x1080 outputs")
    outs = sorted(outputs(), key=lambda o: o["geometry"]["x"])
    output_by_id.update({o["id"]: o for o in outs})

    ids = {title: launch(title) for title in
           ("GatherLeft1", "GatherRight1", "GatherCenter", "GatherRail", "GatherCenter2", "GatherRail2",
            "GatherLeft2", "GatherRight2")}
    place("GatherLeft1", outs[0], 80, 390, 600, 300)
    place("GatherRight1", outs[0], 1240, 390, 600, 300)
    place("GatherCenter", outs[0], 760, 390, 400, 300)
    place("GatherRail", outs[0], 0, 480, 40, 120)
    place("GatherCenter2", outs[1], 760, 390, 400, 300)
    place("GatherRail2", outs[1], 0, 480, 40, 120)
    place("GatherLeft2", outs[1], 80, 390, 600, 300)
    place("GatherRight2", outs[1], 1240, 390, 600, 300)
    settle(list(ids.values()))
    if any(zone_center(ids[t]) != "periphery" for t in ("GatherLeft1", "GatherRight1", "GatherLeft2", "GatherRight2")):
        raise RuntimeError("periphery fixture was not classified on both outputs")
    if any(zone_center(ids[t]) != zone for t, zone in
           (("GatherCenter", "center"), ("GatherRail", "rail"),
            ("GatherCenter2", "center"), ("GatherRail2", "rail"))):
        raise RuntimeError("center/rail fixture did not land in its named zone")

    # GA2: outside Window mode, Space stays with the focused application; a quick Alt+Space
    # remains its chord because the hold has not entered Window mode.
    focus(ids["GatherCenter"])
    solve_before = spread_state()["solves"]
    tap("SPACE")
    wait_for(lambda: len(events("GatherCenter")) >= 2, what="plain Space delivered to focused app")
    plain = events("GatherCenter")[-2:]
    check(spread_state()["solves"] == solve_before and [e["edge"] for e in plain] == ["down", "up"] and
          all(e["key"] == "space" for e in plain), "GA2: plain Space outside Window mode remains app input")
    solve_before = spread_state()["solves"]
    key("LEFTALT", True)
    key("SPACE", True)
    key("SPACE", False)
    key("LEFTALT", False)
    wait_for(lambda: len(events("GatherCenter")) >= 4, what="quick Alt+Space delivered to focused app")
    chord = events("GatherCenter")[-2:]
    check(spread_state()["solves"] == solve_before and [e["edge"] for e in chord] == ["down", "up"] and
          all(e["key"] == "space" and e["mods"] & 8 for e in chord),
          "GA2: quick Alt+Space remains the application's chord")

    # KL7 exact focused-surface claim continues to win while hints are visible.
    ipc("scottland/key-layer", {"action": "set", "window": ids["GatherCenter"], "keys": ["8:space"]})
    focus(ids["GatherCenter"])
    begin_window_mode()
    solve_before = spread_state()["solves"]
    event_count = len(events("GatherCenter"))
    press_space()
    time.sleep(0.05)
    claimed = events("GatherCenter")[event_count:]
    check(spread_state()["solves"] == solve_before and hints()["active"] and
          [e["edge"] for e in claimed] == ["down", "up"] and all(e["key"] == "space" and e["mods"] & 8 for e in claimed),
          "GA2: exact focused-surface Space claim takes precedence in Window mode")
    end_window_mode()
    ipc("scottland/key-layer", {"action": "clear", "window": ids["GatherCenter"]})

    # GA1/GA5/GA6: a physical Space gathers both outputs once, keeps hint mode active, consumes
    # the key, moves only periphery residents on their own sides, and preserves focus/selection.
    focus(ids["GatherCenter"])
    begin_window_mode()
    selected_before = hints()["selected"]
    focus_before = ipc("window-rules/get-focused-view").get("info", {}).get("id")
    fixed_names = ("GatherCenter", "GatherRail", "GatherCenter2", "GatherRail2")
    fixed_before = {t: signature(ids[t]) for t in fixed_names}
    periphery_before = {t: (inner_distance(ids[t]), layout(ids[t])["applied_scale"], local_geometry(ids[t]))
                        for t in ("GatherLeft1", "GatherRight1", "GatherLeft2", "GatherRight2")}
    event_count = len(events("GatherCenter"))
    solve_before = spread_state()["solves"]
    key("SPACE", True)
    key("SPACE", True)  # stipc repeat: same physical key, no release between presses
    key("SPACE", False)
    state = wait_solves(solve_before + len(outs))
    settle(list(ids.values()))
    periphery_after = {t: (inner_distance(ids[t]), layout(ids[t])["applied_scale"], local_geometry(ids[t]))
                       for t in periphery_before}
    moved_and_kept_side = all(zone_center(ids[t]) == "periphery" and
        periphery_after[t][0] <= periphery_before[t][0] + 2 for t in periphery_before)
    grew_or_kept = all(periphery_after[t][1] >= periphery_before[t][1] - 0.01 for t in periphery_before)
    check(state["solves"] == solve_before + len(outs) and state["last"].get("purpose") == "gather",
          "GA1: one physical Space schedules one gather per output despite repeat")
    check(moved_and_kept_side and grew_or_kept,
          "GA5: both sides on both outputs move inward without crossing zones and do not shrink",
          periphery_after)
    check(all(signature(ids[t]) == fixed_before[t] for t in fixed_names),
          "GA5: center and rail windows on both outputs remain unchanged")
    check(hints()["active"] and hints()["selected"] == selected_before and
          ipc("window-rules/get-focused-view").get("info", {}).get("id") == focus_before,
          "GA1/GA6: the press is consumed, Window mode stays active, focus and selection stay put")
    check(len(events("GatherCenter")) == event_count,
          "GA1: unclaimed Space in Window mode does not reach the focused client")
    for modifier in ("LEFTCTRL", "LEFTSHIFT", "LEFTMETA"):
        solve_before = spread_state()["solves"]
        key(modifier, True)
        press_space()
        key(modifier, False)
        check(spread_state()["solves"] == solve_before and hints()["active"],
              f"GA1: modified Space ({modifier}) does not gather")

    # GA13: ending Window mode and cancelling its hints leaves the committed geometry in place.
    gathered_signatures = {t: signature(ids[t]) for t in periphery_before}
    tap("ESC")
    if "LEFTALT" in held:
        key("LEFTALT", False)
    wait_for(lambda: not hints()["active"], what="Esc ends Window mode")
    settle(list(ids.values()))
    check(all(signature(ids[t]) == gathered_signatures[t] for t in gathered_signatures),
          "GA13: gathered positions remain committed after Esc and Alt release")

    # GA14: the periphery position recorded by Gather is what a later cycle restores.
    left = ids["GatherLeft1"]
    gathered_position = local_geometry(left)
    gathered_scale = layout(left)["applied_scale"]
    cycle_one_zone(left)
    check(wait_for(lambda: "center" if zone_center(left) == "center" else None, what="gathered window cycles to center") == "center",
          "GA14: a gathered periphery member cycles to center")
    cycle_one_zone(left)
    settle([left])
    returned = local_geometry(left)
    check(zone_center(left) == "periphery" and abs(returned["x"] - gathered_position["x"]) <= 2 and
          abs(returned["y"] - gathered_position["y"]) <= 2 and
          abs(layout(left)["applied_scale"] - gathered_scale) < 0.02,
          "GA14: cycling back restores the position and scale committed by Gather",
          {"gathered": gathered_position, "returned": returned})

    # GA3: pressing Space while a focused hint waits for release completes that tap first, then
    # gathers the newly periphery window. If the pending tap were skipped, this center stays put.
    focus(ids["GatherCenter"])
    begin_window_mode()
    ensure_selected(ids["GatherCenter"])
    text = hint_for(ids["GatherCenter"])
    for letter in text[:-1]:
        tap(letter.upper())
    key(text[-1].upper(), True)
    time.sleep(0.20)  # deliberately shorter than the 650 ms hint-hold threshold
    ipc("scottland/spread-state", {"slow": True})
    solve_before = spread_state()["solves"]
    key("SPACE", True)
    key("SPACE", False)
    key(text[-1].upper(), False)
    first_screen = wait_solve_record(solve_before + 1)
    first_moves = first_screen.get("moves", [])
    pending_tap_was_in_first_gather = any(move.get("id") == ids["GatherCenter"] for move in first_moves)
    state = wait_solves(solve_before + len(outs))
    ipc("scottland/spread-state", {"slow": False})
    settle(list(ids.values()))
    check(pending_tap_was_in_first_gather,
          "GA3: the pending hint tap changes the first screen's Gather snapshot before solve",
          first_screen)
    check(state["last"].get("purpose") == "gather" and zone_center(ids["GatherCenter"]) == "periphery" and
          inner_distance(ids["GatherCenter"]) <= 2,
          "GA3: pending focused hint tap acts before the queued Gather")
    end_window_mode()

    # GA16: pair has no special exclusion. Give a fresh member a real, well-outside periphery
    # memory, pair it with a center obstacle, return it to that memory, then gather it inward.
    pair = launch("GatherPair")
    ids["GatherPair"] = pair
    place("GatherCenter", outs[0], 760, 390, 400, 300)
    place("GatherPair", outs[0], 760, 390, 600, 300)
    settle([ids["GatherCenter"], pair])
    drag_to(pair, outs[0]["geometry"]["x"] + 220,
            outs[0]["geometry"]["y"] + outs[0]["geometry"]["height"] / 2)
    dropped_distance = inner_distance(pair)
    check(zone_center(pair) == "periphery" and dropped_distance > 20,
          "GA16 fixture: the pair member has a real, separated periphery memory", dropped_distance)
    focus(ids["GatherCenter"])
    begin_window_mode()
    type_hint(pair, hold=0.80)
    settle([ids["GatherCenter"], pair])
    end_window_mode()
    check(zone_center(ids["GatherCenter"]) == "center" and zone_center(pair) == "center",
          "GA16: real Window-mode hint hold creates a pair on the focused output")
    cycle_one_zone(pair)
    if zone_center(pair) == "center":
        raise RuntimeError("paired member did not cycle into the periphery")
    pair_distance_before = inner_distance(pair)
    center_obstacle_before = signature(ids["GatherCenter"])
    begin_window_mode()
    solve_before = spread_state()["solves"]
    press_space()
    state = wait_solves(solve_before + len(outs))
    settle([ids["GatherCenter"], pair])
    pair_distance_after = inner_distance(pair)
    check(state["last"].get("purpose") == "gather" and zone_center(pair) == "periphery" and
          pair_distance_after + 5 < pair_distance_before,
          "GA16: a pair member in the periphery participates in Gather")
    check(signature(ids["GatherCenter"]) == center_obstacle_before and zone_center(ids["GatherCenter"]) == "center",
          "GA16: a pair member in the center remains an obstacle")
    end_window_mode()

    # GA16 queue order, both directions: no active solve is cancelled by the other Window-mode request.
    ipc("wayfire/set-config-options", {"scottland/window_hold_delay": 1})
    ipc("scottland/spread-state", {"slow": True})
    focus(ids["GatherCenter"])
    begin_window_mode()
    ensure_selected(ids["GatherCenter"])
    solve_before = spread_state()["solves"]
    press_space()
    wait_for(lambda: spread_state()["running"], what="slow Gather begins")
    type_hint(ids["GatherCenter"], hold=0.01)  # short real hold queues Solo while Gather is active
    queued = spread_state()
    check(queued["running"] and queued["solves"] < solve_before + len(outs),
          "GA16 fixture: Gather is still active when Solo is requested")
    state = wait_solves(solve_before + len(outs) + 1, timeout=90)
    check(state["solves"] == solve_before + len(outs) + 1 and state["last"].get("purpose") == "solo",
          "GA16: Gather finishes on each output before queued Solo")
    end_window_mode()

    focus(ids["GatherCenter"])
    begin_window_mode()
    ensure_selected(ids["GatherCenter"])
    solve_before = spread_state()["solves"]
    type_hint(ids["GatherCenter"], hold=0.01)  # start Solo with a short real focused-hint hold
    solo_active = spread_state()
    check(solo_active["running"] and solo_active["solves"] == solve_before,
          "GA16 fixture: Solo is active before Gather is requested")
    press_space()  # queue Gather while Solo is still running
    state = wait_solves(solve_before + len(outs) + 1, timeout=90)
    check(state["solves"] == solve_before + len(outs) + 1 and state["last"].get("purpose") == "gather",
          "GA16: Solo finishes before queued Gather")
    end_window_mode()

except Exception as error:
    check(False, "suite exception", repr(error))
finally:
    try:
        for _ in tuple(held_buttons):
            button(False)
        for code in tuple(held):
            key(code, False)
        ipc("scottland/spread-state", {"slow": False})
    except Exception:
        pass
    for process in clients:
        if process.poll() is None:
            process.terminate()
    for process in clients:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    (art / "gather-test-results.json").write_text(json.dumps({"passed": passed, "failed": failed}, indent=2))
    print(f"{passed} passed, {failed} failed", flush=True)
sys.exit(1 if failed else 0)
