#!/usr/bin/env python3
"""Real-input WK13/WK31 regression for calm, same-side window avoidance."""
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys
import time

if len(sys.argv) != 2:
    raise SystemExit("usage: window-avoidance-calm-test.py ARTIFACT_DIR")
artifacts = Path(sys.argv[1])
artifacts.mkdir(parents=True, exist_ok=True)
spec = importlib.util.spec_from_file_location(
    "widget_input", Path(__file__).with_name("widget-input-test.py"))
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)
passes = failures = 0
children = []
samples = []


def check(name, okay, details=""):
    global passes, failures
    print(("PASS " if okay else "FAIL ") + name +
          (f": {details}" if details and not okay else ""), flush=True)
    passes += bool(okay)
    failures += not okay


def cli(*args):
    return subprocess.check_output(
        [str(Path(__file__).resolve().parents[1] / "core/libexec/scottland-ctl"), *args],
        text=True, timeout=8)


def ipc(method, data=None):
    return t.ipc.call(method, data)


def views():
    return {int(row["id"]): row for row in ipc("window-rules/list-views")}


def open_foot(title):
    proc = subprocess.Popen(["foot", "-c", "/dev/null", "-T", title, "-W", "100x32",
                             "sh", "-c", "exec sleep 600"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    children.append((title, proc))
    t.owned.append((title, proc))


def wait_for_offset(amount=40, timeout=8):
    end = time.monotonic() + timeout
    last_state = None
    while time.monotonic() < end:
        state = ipc("scottland/hints")
        last_state = state
        if sum(math.hypot(float(row["dx"]), float(row["dy"]))
               for row in state["hints"]) >= amount:
            return state
        time.sleep(.03)
    setup = {"hints": last_state, "views": ipc("scottland/layout-state"),
             "always_setting": json.loads(cli("get")).get("window_avoidance_always")}
    (artifacts / "setup-timeout.json").write_text(json.dumps(setup, indent=2) + "\n")
    raise TimeoutError("window avoidance did not displace the overlapping stack: " +
                       json.dumps({key: last_state.get(key) for key in
                           ("avoidance_solve_count", "avoidance_solve_ms",
                            "avoidance_solve_max_ms", "avoidance_solve_deadline_count",
                            "avoidance_init_ms", "avoidance_placement_ms",
                            "avoidance_finalization_ms", "avoidance_work_count",
                            "avoidance_label_work_count", "avoidance_movement_work_count",
                            "avoidance_movement_searches", "avoidance_truncated_searches",
                            "avoidance_last_search_window",
                            "avoidance_solve_pending", "hints")}))


def take_sample(phase, step):
    state = ipc("scottland/hints")
    geometry = views()
    drawn = {row["title"]: row for row in ipc("scottland/layout-state")["views"]}
    input_state = ipc("scottland/test-input")
    sample = {"phase": phase, "step": step,
              "timestamp": time.monotonic(),
              "solve_count": state.get("avoidance_solve_count"),
              "solve_ms": state.get("avoidance_solve_ms"),
              "solve_max_ms": state.get("avoidance_solve_max_ms"),
              "easing_max_speed": state.get("avoidance_max_easing_speed_px_s"),
              "search_ms": state.get("avoidance_search_ms"),
              "search_max_ms": state.get("avoidance_search_max_ms"),
              "work_count": state.get("avoidance_work_count"),
              "label_work_count": state.get("avoidance_label_work_count"),
              "movement_work_count": state.get("avoidance_movement_work_count"),
              "movement_searches": state.get("avoidance_movement_searches"),
              "truncated_searches": state.get("avoidance_truncated_searches"),
              "solve_deadlines": state.get("avoidance_solve_deadline_count"),
              "solve_pending": state.get("avoidance_solve_pending"),
              "progress_attempts": state.get("avoidance_progress_attempts"),
              "progress_windows": state.get("avoidance_progress_windows"),
              "fallback_count": state.get("avoidance_fallback_count"),
              "way_recheck_pending": state.get("avoidance_way_recheck_pending"),
              "way_recheck_done": state.get("avoidance_way_recheck_done"),
              "way_recheck_attempts": state.get("avoidance_way_recheck_attempts"),
              "way_recheck_adoptions": state.get("avoidance_way_recheck_adoptions"),
              "way_recheck_rejections": state.get("avoidance_way_recheck_rejections"),
              "minimum_hint_size": float(state.get("minimum_window_hint_size", 48)),
              "size_upgrades_enabled": state.get("size_upgrades_enabled"),
              "input_dragging": input_state.get("dragging"),
              "drag_renderer": input_state.get("drag_renderer"),
              "drag_center": input_state.get("drag_center"),
              "windows": {}}
    for row in state["hints"]:
        identifier = int(row["window"])
        rect = geometry.get(identifier, {}).get("geometry")
        if rect is None:
            continue
        app = drawn.get(geometry[identifier].get("title", ""), {})
        frame = app.get("frame")
        sample["windows"][str(identifier)] = {
            "title": geometry[identifier].get("title", ""),
            "zone": app.get("zone"),
            "avoidance_order": int(row.get("avoidance_order", -1)),
            "true": {k: rect[k] for k in ("x", "y", "width", "height")},
            "solve_frame": row.get("solve_frame"),
            "drawn_frame": ({k: frame[k] for k in ("x", "y", "width", "height")}
                            if frame else None),
            "displayed_offset": [float(row["dx"]), float(row["dy"])],
            "target_offset": [float(row["target_dx"]), float(row["target_dy"])],
            "branch": [int(row.get("branch_owner", 0)), int(row.get("branch_axis", 0)),
                       int(row.get("branch_sign", 0))],
            "branch_base": [float(row.get("branch_base_dx", 0)),
                            float(row.get("branch_base_dy", 0))],
            "label_offset": [float(row.get("label_dx", 0)), float(row.get("label_dy", 0))],
            "clearance": float(row.get("clearance", 0)),
            "surface_patch_size": float(row.get("surface_patch_size", 0)),
            "minimum_patch_visible": bool(row.get("minimum_patch_visible", False)),
            "hint_visible": bool(row.get("visible", False)),
            "hint_rendered": bool(row.get("rendered", False)),
            "hint_size": float((row.get("circle") or {}).get("size", 0)),
            "incumbent_clearance": float(row.get("incumbent_clearance", -1)),
            "minimum_size": float(state.get("minimum_window_hint_size", 48)),
        }
    samples.append(sample)
    return state


def side_flips(trace):
    signs = [1 if item[0] > 12 else -1 if item[0] < -12 else 0 for item in trace]
    signs = [value for value in signs if value]
    return sum(a != b for a, b in zip(signs, signs[1:]))


def target_metrics(phase):
    tracks, row_tracks = {}, {}
    for sample in samples:
        if sample["phase"] != phase:
            continue
        for identifier, row in sample["windows"].items():
            tracks.setdefault(identifier, []).append(row["target_offset"])
            row_tracks.setdefault(identifier, []).append(row)
    jumps = {identifier: max((math.dist(a, b) for a, b in zip(trace, trace[1:])), default=0)
             for identifier, trace in tracks.items()}
    flips = {identifier: side_flips(trace) for identifier, trace in tracks.items()}
    branch_traces = {}
    for sample in samples:
        if sample["phase"] != phase:
            continue
        for identifier, row in sample["windows"].items():
            branch_traces.setdefault(identifier, []).append(tuple(row["branch"]))
    branch_changes = {}
    unbranched_jumps = {}
    required_jumps = {}
    replacement_jumps = {}
    unjustified_changes = {}
    for identifier, trace in tracks.items():
        branch_trace = branch_traces.get(identifier, [])
        transitions = 0
        unbranched = []
        required = []
        replacements = []
        unjustified = 0
        for index, (before, after) in enumerate(zip(trace, trace[1:])):
            previous_branch = branch_trace[index] if index < len(branch_trace) else (0, 0, 0)
            next_branch = branch_trace[index + 1] if index + 1 < len(branch_trace) else (0, 0, 0)
            previous_row = row_tracks[identifier][index]
            next_row = row_tracks[identifier][index + 1]
            change = math.dist(before, after)
            opened_new_way = next_branch[1] != 0 and next_branch != previous_branch
            transitions += opened_new_way
            if opened_new_way:
                replacements.append(change)
            minimum = float(next_row.get("minimum_size", 48))
            required_clearance = minimum * 1.06 / 2 + 1
            incumbent_fits = float(next_row.get("incumbent_clearance", -1)) + .25 >= required_clearance
            returns_to_zero = math.hypot(*after) <= .1 and \
                float(next_row.get("clearance", 0)) + .25 >= required_clearance + 4
            if opened_new_way and incumbent_fits:
                unjustified += 1
            if change > 4 and not opened_new_way and not returns_to_zero and incumbent_fits:
                unjustified += 1
            if not opened_new_way and not returns_to_zero and incumbent_fits:
                unbranched.append(change)
            if change > 4 and not opened_new_way and not returns_to_zero and not incumbent_fits:
                required.append(change)
        branch_changes[identifier] = transitions
        unbranched_jumps[identifier] = max(unbranched, default=0)
        required_jumps[identifier] = max(required, default=0)
        replacement_jumps[identifier] = max(replacements, default=0)
        unjustified_changes[identifier] = unjustified
    return (tracks, jumps, flips, branch_changes, unbranched_jumps,
            required_jumps, replacement_jumps, unjustified_changes)


def displayed_metrics(phase):
    tracks = {}
    for sample in samples:
        if sample["phase"] != phase:
            continue
        for identifier, row in sample["windows"].items():
            tracks.setdefault(identifier, []).append((sample["timestamp"], row["displayed_offset"]))
    return {identifier: max((math.dist(a[1], b[1]) / max(.001, b[0]-a[0])
                             for a, b in zip(trace, trace[1:])), default=0)
            for identifier, trace in tracks.items()}


try:
    cli("set", "window_avoidance_always", "false")
    ipc("wayfire/set-config-options", {"scottland/sounds": False})
    output = ipc("window-rules/list-outputs")[0]["geometry"]
    center_width = float(ipc("wayfire/get-config-option", {
        "option": "scottland/center_width"})["value"])
    titles = [f"avoidance-calm-{i}" for i in range(5)]
    for title in titles:
        open_foot(title)
        t.wait_for(lambda title=title: t.app(title), timeout=8)
    time.sleep(.4)

    width, height = round(output["width"] * .70), round(output["height"] * .72)
    rect = {"x": output["x"] + (output["width"] - width) // 2,
            "y": output["y"] + (output["height"] - height) // 2,
            "width": width, "height": height}
    ids = {title: int(t.app(title)["id"]) for title in titles}
    for identifier in ids.values():
        ipc("window-rules/configure-view", {"id": identifier, "geometry": rect})
    time.sleep(.4)
    dragged_id = ids[titles[-1]]
    ipc("window-rules/focus-view", {"id": dragged_id})
    ipc("scottland/attention", {"window": ids[titles[0]], "attention": True,
                                "source": "window-avoidance-calm-test"})
    cli("set", "window_avoidance_always", "true")
    t.wait_for(lambda: json.loads(cli("get")).get("window_avoidance_always") is True)
    wait_for_offset()
    time.sleep(.35)
    before = take_sample("settled-before", 0)
    check("large overlapping windows receive live avoidance offsets",
          sum(math.hypot(float(row["dx"]), float(row["dy"])) > 8
              for row in before["hints"]) >= 1)
    subprocess.run(["grim", str(artifacts / "before-one-pixel-drag.png")],
                   check=True, timeout=8)

    # Grab a different window while it is visually displaced. The pointer must stay
    # over the displayed frame as focus anchors it and the drag takes ownership.
    displaced = [row for identifier, row in samples[-1]["windows"].items()
                 if int(identifier) != dragged_id and row["title"] != titles[0]
                 and abs(row["target_offset"][1]) > 20 and row["drawn_frame"]]
    grab_stays_put = False
    grab_drop_stays_put = False
    grab_drop_drift = math.inf
    grabbed_title = None
    if displaced:
        grabbed = max(displaced, key=lambda row: math.hypot(*row["target_offset"]))
        grabbed_title = grabbed["title"]
        frame = grabbed["drawn_frame"]
        grab_x = round(frame["x"] + frame["width"] / 2)
        grab_y = round(frame["y"] + frame["height"] / 2)
        t.move(grab_x, grab_y)
        time.sleep(.05)
        t.key("LEFTMETA", True)
        ipc("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "press"})
        time.sleep(.05)
        held = take_sample("grab-shifted", 0)
        after_press = next((row for row in samples[-1]["windows"].values()
                            if row["title"] == grabbed["title"]), None)
        after_frame = after_press["drawn_frame"] if after_press else None
        press_delta = math.inf if not after_frame else math.dist(
            [frame["x"] + frame["width"] / 2, frame["y"] + frame["height"] / 2],
            [after_frame["x"] + after_frame["width"] / 2,
             after_frame["y"] + after_frame["height"] / 2])
        check("grabbing an already-shifted window keeps its drawn frame under the pointer",
              press_delta <= 5 and samples[-1]["input_dragging"],
              f"press displacement {press_delta:.2f}px; active {samples[-1]['input_dragging']}")
        ipc("stipc/move_cursor", {"x": grab_x, "y": grab_y + 1})
        time.sleep(.03)
        take_sample("grab-shifted", 1)
        moved_row = next((row for row in samples[-1]["windows"].values()
                          if row["title"] == grabbed["title"]), None)
        moved_frame = moved_row["drawn_frame"] if moved_row else None
        if moved_frame:
            actual = [moved_frame["x"] + moved_frame["width"] / 2,
                      moved_frame["y"] + moved_frame["height"] / 2]
            original = [frame["x"] + frame["width"] / 2,
                        frame["y"] + frame["height"] / 2 + 1]
            grab_stays_put = math.dist(actual, original) <= 6 and samples[-1]["input_dragging"]
        check("first 1 px move after grab preserves the pointer anchor",
              grab_stays_put,
              f"frame {moved_frame}; active {samples[-1]['input_dragging']}")
        release_frame = moved_frame
        ipc("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "release"})
        t.key("LEFTMETA", False)
        time.sleep(.08)
        take_sample("grab-shifted-drop", 0)
        released_row = next((row for row in samples[-1]["windows"].values()
                             if row["title"] == grabbed_title), None)
        released_frame = released_row["drawn_frame"] if released_row else None
        if release_frame and released_frame:
            grab_drop_drift = math.dist(
                [release_frame["x"] + release_frame["width"] / 2,
                 release_frame["y"] + release_frame["height"] / 2],
                [released_frame["x"] + released_frame["width"] / 2,
                 released_frame["y"] + released_frame["height"] / 2])
        time.sleep(.35)
        take_sample("grab-shifted-drop-settled", 0)
        settled_row = next((row for row in samples[-1]["windows"].values()
                            if row["title"] == grabbed_title), None)
        settled_frame = settled_row["drawn_frame"] if settled_row else None
        settled_drift = math.inf
        if released_frame and settled_frame:
            settled_drift = math.dist(
                [released_frame["x"] + released_frame["width"] / 2,
                 released_frame["y"] + released_frame["height"] / 2],
                [settled_frame["x"] + settled_frame["width"] / 2,
                 settled_frame["y"] + settled_frame["height"] / 2])
        grab_drop_stays_put = grab_drop_drift <= 8 and settled_drift <= 8
        check("dropping a shifted window preserves its position without an old-offset glide",
              grab_drop_stays_put,
              f"release displacement {grab_drop_drift:.2f}px; settle displacement {settled_drift:.2f}px")
        ipc("window-rules/focus-view", {"id": dragged_id})
        wait_for_offset()
        before = take_sample("settled-before", 1)
    else:
        check("an already-shifted window is available for the grab regression", False)

    view = views()[dragged_id]
    geometry = view["geometry"]
    drag_geometry_before = dict(geometry)
    # Use the compositor's drawn frame for the real pointer grab, as the shared drag
    # fixture does; the IPC geometry above is retained separately as the truth check.
    drawn_frame = t.app(titles[-1])["frame"]
    start_x = round(drawn_frame["x"] + drawn_frame["width"] / 2)
    start_y = round(drawn_frame["y"] + drawn_frame["height"] / 2)
    t.move(start_x, start_y)
    time.sleep(.1)
    t.key("LEFTMETA", True)
    time.sleep(.05)
    ipc("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "press"})
    time.sleep(.1)

    # One logical pixel per real stipc pointer update, out and back through the overlap.
    positions = list(range(start_x, start_x + 161)) + list(range(start_x + 160, start_x - 1, -1))
    for step, x in enumerate(positions):
        ipc("stipc/move_cursor", {"x": x, "y": start_y})
        time.sleep(.016)
        take_sample("drag", step)
        if step == 160:
            subprocess.run(["grim", str(artifacts / "window-at-drag-peak.png")],
                           check=True, timeout=8)
    hold_counts = []
    for step in range(10):
        time.sleep(.04)
        state = take_sample("held-still", step)
        hold_counts.append(state.get("avoidance_solve_count"))
    ipc("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "release"})
    t.key("LEFTMETA", False)
    time.sleep(.3)
    drag_geometry_after = views().get(dragged_id, {}).get("geometry")

    (tracks, jumps, flips, branches, unbranched_jumps, required_jumps,
     replacement_jumps, unjustified) = target_metrics("drag")
    (held_tracks, held_jumps, held_flips, held_branches, held_unbranched,
     held_required, held_replacements, held_unjustified) = target_metrics("held-still")
    displayed_speeds = displayed_metrics("drag")
    max_jump = max(jumps.values(), default=0)
    max_flips = max(flips.values(), default=0)
    max_required_jump = max(required_jumps.values(), default=0)
    max_replacement_jump = max(replacement_jumps.values(), default=0)
    max_display_speed = max(displayed_speeds.values(), default=0)
    max_unbranched_jump = max(unbranched_jumps.values(), default=0)
    one_hint_step_limit = max((float(row["minimum_size"]) + 8
        for sample in samples if sample["phase"] == "drag"
        for row in sample["windows"].values()), default=56)
    max_solve_ms = max((float(sample.get("search_ms") or 0) for sample in samples
                        if sample["phase"] == "drag"), default=0)
    max_work = max((int(sample.get("work_count") or 0) for sample in samples
                    if sample["phase"] == "drag"), default=0)
    check("one-pixel drag changes targets calmly and bounds a necessary move to one hint step",
          max_unbranched_jump <= 4 and max_required_jump <= one_hint_step_limit and
          max_replacement_jump <= 64 and max_jump <= one_hint_step_limit and
          max(unjustified.values(), default=0) == 0,
          f"largest target change {max_jump:.2f}px; allowed necessary step "
          f"{one_hint_step_limit:.2f}px; "
          f"same-way valid-incumbent max {max_unbranched_jump:.2f}px; "
          f"same-way required max {max_required_jump:.2f}px; "
          f"replacement-way max {max_replacement_jump:.2f}px; "
          f"unjustified {unjustified}; "
          f"per-window {unbranched_jumps}")
    check("displayed window offsets still ease toward a newly needed target",
          max((float(sample.get("easing_max_speed") or 0) for sample in samples), default=0) <= 1000.1,
          f"sampled max {max_display_speed:.1f}px/s; compositor step max "
          f"{max((float(sample.get('easing_max_speed') or 0) for sample in samples), default=0):.1f}px/s; "
          f"per-window {displayed_speeds}")
    check("live avoidance search stays within the 2 ms budget plus scheduler margin",
          max_solve_ms <= 2.5,
          f"max search {max_solve_ms:.3f}ms; max deterministic work count {max_work}")
    check("one-pixel drag does not flip a window between sides", max_flips == 0,
          f"max meaningful side flips {max_flips}; per-window {flips}")
    check("way changes happen only after the incumbent badge point loses minimum clearance",
          max(branches.values(), default=0) <= 2 and max(unjustified.values(), default=0) == 0,
          f"branch changes {branches}; unjustified changes {unjustified}")
    check("held-still targets stop changing without oscillation",
          max(held_unbranched.values(), default=0) <= .1 and
          max(held_jumps.values(), default=0) <= .1 and
          max(held_flips.values(), default=0) == 0 and
          max(held_branches.values(), default=0) == 0 and
          max(held_unjustified.values(), default=0) == 0 and
          max(hold_counts) - min(hold_counts) <= 6 and len(set(hold_counts[-3:])) == 1,
          f"max target delta {max(held_jumps.values(), default=0):.3f}px; "
          f"solve counts {hold_counts}; flips {held_flips}; branches {held_branches}")

    drag_orders = [tuple(identifier for identifier, row in sorted(sample["windows"].items(),
                          key=lambda item: item[1]["avoidance_order"]))
                   for sample in samples if sample["phase"] == "drag"]
    check("deadline retries keep a fixed front-to-back solve order",
          bool(drag_orders) and all(order == drag_orders[0] for order in drag_orders),
          f"distinct orders {len(set(drag_orders))}; first {drag_orders[0] if drag_orders else []}")

    # P1 is checked against each sample's true center, including a manually dragged window
    # whose own true frame is allowed to cross because that crossing came from the user.
    same_side = True
    crossing = []
    for sample in samples:
        for identifier, row in sample["windows"].items():
            if int(identifier) == dragged_id:
                continue
            frame = row["true"]
            target = row["target_offset"]
            center = output["x"] + output["width"] / 2
            original = frame["x"] + frame["width"] / 2
            moved_center = original + target[0]
            original_y = frame["y"] + frame["height"] / 2
            moved_y = original_y + target[1]
            if row.get("zone") == "center":
                half_zone = output["width"] * center_width / 200
                if moved_center < center - half_zone - .01 or moved_center > center + half_zone + .01:
                    same_side = False
                    crossing.append((identifier, row["zone"], original, moved_center))
            elif original < center - .01 and moved_center > center + .01:
                same_side = False; crossing.append((identifier, original, moved_center))
            elif original > center + .01 and moved_center < center - .01:
                same_side = False; crossing.append((identifier, original, moved_center))
            elif abs(original - center) <= .01 and abs(moved_center - center) > .01:
                same_side = False; crossing.append((identifier, original, moved_center))
            vertical_center = output["y"] + output["height"] / 2
            vertical_band = output["height"] * .25
            if abs(original_y - vertical_center) <= vertical_band:
                if not vertical_center - vertical_band - .01 <= moved_y <= vertical_center + vertical_band + .01:
                    same_side = False; crossing.append((identifier, "vertical-center", original_y, moved_y))
            elif original_y < vertical_center and moved_y > vertical_center + .01:
                same_side = False; crossing.append((identifier, "vertical-top", original_y, moved_y))
            elif original_y > vertical_center and moved_y < vertical_center - .01:
                same_side = False; crossing.append((identifier, "vertical-bottom", original_y, moved_y))
    check("automatic target offsets stay in each true window's horizontal and vertical zone",
          same_side, f"crossings {crossing}")
    drag_centers = [sample["drag_center"] for sample in samples if sample["phase"] == "drag"
                    and isinstance(sample["drag_center"], (int, float))
                    and sample["drag_center"] > 0]
    drag_center_span = max(drag_centers, default=0) - min(drag_centers, default=0)
    dragged_solve_frames = [sample["windows"].get(str(dragged_id), {}).get("solve_frame")
                            for sample in samples if sample["phase"] == "drag"]
    dragged_solve_frames = [frame for frame in dragged_solve_frames if frame]
    start_frame = dragged_solve_frames[0] if dragged_solve_frames else None
    solve_frame_span = max((math.hypot(frame["x"] - start_frame["x"],
        frame["y"] - start_frame["y"]) for frame in dragged_solve_frames), default=0)
    check("stipc visibly drags the selected window before returning it to origin",
          drag_geometry_after is not None and drag_center_span > 20 and solve_frame_span > 20,
          f"pointer-center span {drag_center_span:.2f}px; solve-frame span {solve_frame_span:.2f}px; "
          f"true geometry after return {drag_geometry_after}")
    check("stipc engaged a real Scottland/Wayfire move grab",
          all(sample["input_dragging"] for sample in samples if sample["phase"] == "drag"),
          str({name: samples[0].get(name) for name in ("input_dragging", "drag_renderer")}))
    subprocess.run(["grim", str(artifacts / "after-one-pixel-drag.png")],
                   check=True, timeout=8)
    time.sleep(1.2)
    # Keep the four-window pile intact. Move only the front obstruction to the top
    # edge with stipc, leaving a narrow visible strip above the pile without entering
    # a side rail (WG22 would widgetize a rail drop).
    parked_source = t.app(titles[-1])["frame"]
    grab_x = round(parked_source["x"] + parked_source["width"] / 2)
    grab_y = round(parked_source["y"] + parked_source["height"] - 6)
    t.move(grab_x, grab_y)
    time.sleep(.05)
    t.key("LEFTMETA", True)
    ipc("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "press"})
    time.sleep(.08)
    park_cursor_y = round(output["y"] + 8)
    for step in range(1, 61):
        y = round(grab_y + (park_cursor_y - grab_y) * step / 60)
        ipc("stipc/move_cursor", {"x": grab_x, "y": y})
        time.sleep(.008)
        if step in (20, 40, 60):
            take_sample("park-drag", step)
    ipc("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "release"})
    t.key("LEFTMETA", False)
    time.sleep(.4)
    parked_geometry = views().get(dragged_id, {}).get("geometry")
    take_sample("parked", 0)
    time.sleep(.8)
    take_sample("parked-settled", 0)
    settled_park = samples[-1]
    park_recheck = {key: settled_park[key] for key in (
        "way_recheck_pending", "way_recheck_done", "way_recheck_attempts",
        "way_recheck_adoptions", "way_recheck_rejections")}
    check("one bounded at-rest way reconsideration settles after the drag",
          park_recheck["way_recheck_done"] and not park_recheck["way_recheck_pending"] and
          park_recheck["way_recheck_attempts"] == 1,
          f"recheck state {park_recheck}")
    remaining = [row["geometry"] for identifier, row in views().items()
                 if identifier != dragged_id and row.get("title", "").startswith("avoidance-calm-")]
    remain_overlapping = len(remaining) == 4 and all(
        min(a["x"] + a["width"], b["x"] + b["width"]) > max(a["x"], b["x"]) and
        min(a["y"] + a["height"], b["y"] + b["height"]) > max(a["y"], b["y"])
        for index, a in enumerate(remaining) for b in remaining[index + 1:])
    parked_entry = settled_park["windows"].get(str(dragged_id), {})
    not_widgetized = parked_entry.get("zone") != "widget"
    check("real drag clears the front obstruction while the other four windows stay overlapped",
          remain_overlapping and not_widgetized and not samples[-1]["input_dragging"],
          f"parked frame {parked_geometry}; remaining pairwise overlap={remain_overlapping}; "
          f"zone={parked_entry.get('zone')}; drag settled={not samples[-1]['input_dragging']}")
    after_targets = {identifier: row["target_offset"] for identifier, row in
                     settled_park["windows"].items()}
    subprocess.run(["grim", str(artifacts / "window-parked-above-overlap.png")],
                   check=True, timeout=8)
    held_targets = []
    for step in range(5):
        time.sleep(.08)
        take_sample("parked-hold", step)
        held_targets.append({identifier: row["target_offset"]
                             for identifier, row in samples[-1]["windows"].items()})
    stable_after_park = all(
        math.dist(held_targets[step - 1][identifier], held_targets[step][identifier]) <= .1
        for step in range(1, len(held_targets)) for identifier in held_targets[step])
    check("the held overlapping always-avoid layout stops solving without target drift",
          stable_after_park and not samples[-1]["solve_pending"],
          f"stable={stable_after_park}; pending={samples[-1]['solve_pending']}; "
          f"attempts={samples[-1]['progress_attempts']}; fallbacks={samples[-1]['fallback_count']}")

    # Force a fresh solve of the unchanged true geometry. The displayed offsets
    # first return home, then the same parked layout is solved again from rest.
    # This catches a retained route that is stable but has grown beyond the least
    # fresh solution after the obstruction leaves.
    cli("set", "window_avoidance_always", "false")
    t.wait_for(lambda: json.loads(cli("get")).get("window_avoidance_always") is False)
    t.wait_for(lambda: all(math.hypot(float(row["dx"]), float(row["dy"])) <= .5
                           for row in ipc("scottland/hints")["hints"]), timeout=8)
    cli("set", "window_avoidance_always", "true")
    def fresh_solve_settled():
        state = ipc("scottland/hints")
        return (json.loads(cli("get")).get("window_avoidance_always") is True and
                state.get("avoidance_solve_count", 0) > settled_park.get("solve_count", 0) and
                not state.get("avoidance_solve_pending") and
                all(math.hypot(float(row["dx"]) - float(row["target_dx"]),
                               float(row["dy"]) - float(row["target_dy"])) <= .5
                    for row in state["hints"]))
    t.wait_for(fresh_solve_settled, timeout=8)
    take_sample("fresh-layout", 0)
    fresh_targets = {identifier: row["target_offset"] for identifier, row in
                     samples[-1]["windows"].items()}
    fresh_diffs = {identifier: math.dist(after_targets.get(identifier, [0, 0]), offset)
                   for identifier, offset in fresh_targets.items()}
    max_fresh_diff = max(fresh_diffs.values(), default=math.inf)
    check("post-drag targets equal a fresh solve of the still-overlapped layout",
          len(fresh_targets) == len(after_targets) and max_fresh_diff <= .5,
          f"max delta {max_fresh_diff:.2f}px; per-window {fresh_diffs}; "
          f"parked {after_targets}; fresh {fresh_targets}")
    print(f"trace stats: target max={max_jump:.2f}px; same-way={max_unbranched_jump:.2f}px; "
          f"required={max_required_jump:.2f}px; branch replacement={max_replacement_jump:.2f}px; "
          f"max displayed speed={max_display_speed:.1f}px/s; peak search={max_solve_ms:.3f}ms; "
          f"max work={max_work}; side flips={max_flips}; branch changes={branches}", flush=True)
    (artifacts / "calm-trace.json").write_text(json.dumps({
        "input": "real stipc Super+left drag, 1 px per pointer update, out and back, then held still",
        "screen": output,
        "center_width_percent": center_width,
        "dragged_window": dragged_id,
        "drag_geometry_before": drag_geometry_before,
        "drag_geometry_after": drag_geometry_after,
        "parked_geometry": parked_geometry,
        "drag_solve_frame_span_px": solve_frame_span,
        "drawn_frame_at_drag_start": drawn_frame,
        "drag_center_span_px": drag_center_span,
        "sample_count": len(samples),
        "drag_target_jumps_px": jumps,
        "drag_unbranched_target_jumps_px": unbranched_jumps,
        "drag_required_target_jumps_px": required_jumps,
        "drag_replacement_target_jumps_px": replacement_jumps,
        "drag_displayed_offset_speed_px_s": displayed_speeds,
        "drag_max_search_ms": max_solve_ms,
        "drag_max_work_count": max_work,
        "drag_side_flips": flips,
        "drag_branch_changes": branches,
        "drag_unjustified_target_changes": unjustified,
        "grab_keeps_displayed_frame": grab_stays_put,
        "shifted_y_grabbed_window": grabbed_title,
        "shifted_drop_stays_put": grab_drop_stays_put,
        "shifted_drop_drift_px": grab_drop_drift,
        "post_drag_targets": after_targets,
        "fresh_targets": fresh_targets,
        "fresh_target_delta_px": fresh_diffs,
        "park_recheck": park_recheck,
        "held_target_jumps_px": held_jumps,
        "held_side_flips": held_flips,
        "held_solve_counts": hold_counts,
        "samples": samples,
    }, indent=2) + "\n")
    print(f"window avoidance calm regression: {passes} passed, {failures} failed; "
          f"{len(samples)} frame samples", flush=True)
finally:
    try:
        t.key("LEFTMETA", False)
        ipc("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "release"})
        cli("set", "window_avoidance_always", "false")
    except Exception:
        pass
    for _, proc in children:
        if proc.poll() is None:
            proc.terminate()
    for _, proc in children:
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)

sys.exit(bool(failures))
