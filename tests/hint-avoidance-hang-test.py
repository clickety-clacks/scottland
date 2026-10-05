#!/usr/bin/env python3
"""Reproduce WK13's live-drag solve feedback and bound window-avoidance work."""
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys
import time

if len(sys.argv) != 2:
    raise SystemExit("usage: hint-avoidance-hang-test.py ARTIFACT_DIR")
artifacts = Path(sys.argv[1])
artifacts.mkdir(parents=True, exist_ok=True)
spec = importlib.util.spec_from_file_location(
    "widget_input", Path(__file__).with_name("widget-input-test.py"))
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)
passes = failures = 0
children = []
latencies = []


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


def timed(method, data=None):
    start = time.monotonic()
    result = t.ipc.call(method, data)
    latencies.append({"method": method, "ms": (time.monotonic() - start) * 1000})
    return result


def offsets(state=None):
    state = state or timed("scottland/hints")
    return {str(row["window"]): (float(row["dx"]), float(row["dy"]))
            for row in state["hints"]}


def magnitude(values):
    return sum(math.hypot(*v) for v in values.values())


def open_foot(title, columns, rows):
    proc = subprocess.Popen(["foot", "-c", "/dev/null", "-T", title, "-W",
                             f"{columns}x{rows}", "sh", "-c", "exec sleep 600"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    children.append((title, proc)); t.owned.append((title, proc))


try:
    cli("set", "window_avoidance_always", "false")
    t.ipc.sock.settimeout(.8)
    t.ipc.call("wayfire/set-config-options", {"scottland/sounds": False})
    output = timed("window-rules/list-outputs")[0]["geometry"]
    specs = [f"avoidance-hang-large-{i}" for i in range(6)]
    ids = {}
    for title in specs:
        open_foot(title, 100, 32)
        t.wait_for(lambda title=title: t.app(title), timeout=8)
    time.sleep(.5)
    width = round(output["width"] * .72)
    height = round(output["height"] * .76)
    rect = {"x": output["x"] + (output["width"] - width) // 2,
            "y": output["y"] + (output["height"] - height) // 2,
            "width": width, "height": height}
    for title in specs:
        view = t.app(title)
        ids[title] = int(view["id"])
        timed("window-rules/configure-view", {"id": view["id"], "geometry": rect})
    time.sleep(.5)
    timed("window-rules/focus-view", {"id": ids[specs[-1]]})
    time.sleep(.35)

    attention_id = ids[specs[0]]
    attention_state = timed("scottland/attention", {"window": attention_id, "attention": True,
                                      "source": "avoidance-live-drag-regression"})
    cli("set", "window_avoidance_always", "true")
    t.wait_for(lambda: json.loads(cli("get")).get("window_avoidance_always") is True)
    wait_end = time.monotonic() + 8
    initial_state = timed("scottland/hints")
    while magnitude(offsets(initial_state)) <= 30 and time.monotonic() < wait_end:
        time.sleep(.08)
        initial_state = timed("scottland/hints")
    if magnitude(offsets(initial_state)) <= 30:
        raise AssertionError("initial displacement timed out: " + json.dumps({
            "solve_ms": initial_state.get("avoidance_solve_ms"),
            "solve_count": initial_state.get("avoidance_solve_count"),
            "deadline_count": initial_state.get("avoidance_solve_deadline_count"),
            "windows": [{key: row.get(key) for key in (
                "window", "dx", "dy", "target_dx", "target_dy", "clearance")}
                for row in initial_state.get("hints", [])],
        }))
    time.sleep(.35)
    before = offsets()
    check("always-on exposure has moved large overlapping windows", magnitude(before) > 50)
    goo = timed("scottland/goo-state")
    attention_active = any(row["id"] == attention_id and
                           "avoidance-live-drag-regression" in row["attention"]
                           for row in attention_state.get("windows", []))
    check("attention source and breathing goo are active",
          attention_active and bool(goo.get("screens")) and
          any(screen.get("sources", 0) > 0 and "breath" in screen for screen in goo["screens"]))
    breath_before = next((screen.get("breath") for screen in goo.get("screens", [])
                          if screen.get("sources", 0) > 0), None)
    time.sleep(.35)
    goo_after = timed("scottland/goo-state")
    breath_after = next((screen.get("breath") for screen in goo_after.get("screens", [])
                         if screen.get("sources", 0) > 0), None)
    check("attention breathing phase advances during the drag setup",
          isinstance(breath_before, (int, float)) and isinstance(breath_after, (int, float)) and
          abs(breath_after - breath_before) > .002,
          f"breath {breath_before} -> {breath_after}")
    subprocess.run(["grim", str(artifacts / "before-live-drag.png")], check=True, timeout=8)

    # A held Super+left pointer drag through several points is the input path that changes the
    # dragged anchor while the always-on solver runs. Keep the large window on screen throughout.
    window = t.app(specs[-1])
    frame = window["frame"]
    drag_geometry_before = next(row["geometry"] for row in timed("window-rules/list-views")
                                if int(row["id"]) == ids[specs[-1]])
    start = (frame["x"] + frame["width"] / 2, frame["y"] + frame["height"] / 2)
    t.move(*start)
    time.sleep(.1)
    latencies.clear()
    t.key("LEFTMETA", True)
    timed("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "press"})
    points = []
    for i in range(36):
        phase = i / 35
        x = output["x"] + output["width"] * (.38 + .24 * phase)
        y = output["y"] + output["height"] * (.34 + .28 * (0.5 - abs(phase - .5)) * 2)
        points.append((x, y))
    for x, y in points:
        timed("stipc/move_cursor", {"x": round(x), "y": round(y)})
        time.sleep(.018)
    # Hold the real drag still long enough to expose a feedback loop where the drawn avoidance
    # transform keeps changing the next solve's input even though the pointer did not move.
    time.sleep(.1)
    hold_state = timed("scottland/hints")
    hold_count = hold_state.get("avoidance_solve_count")
    stationary_solve_counts = [hold_count]
    for _ in range(6):
        time.sleep(.04)
        stationary_solve_counts.append(timed("scottland/hints").get("avoidance_solve_count"))
    timed("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "release"})
    t.key("LEFTMETA", False)
    time.sleep(.35)
    check("stationary live drag does not re-solve from its own draw transform",
          all(count == stationary_solve_counts[0] for count in stationary_solve_counts),
          f"solve counts while pointer held still: {stationary_solve_counts}")
    dragged_window = t.app(specs[-1])
    drag_geometry_after = next((row["geometry"] for row in timed("window-rules/list-views")
                                if int(row["id"]) == ids[specs[-1]]), None)
    in_widgets = any(int(row["id"]) == ids[specs[-1]]
                     for row in timed("scottland/widgets").get("widgets", []))
    check("live-drag input moved the selected large window without widgetizing it",
          dragged_window is not None and not in_widgets and drag_geometry_after is not None and
          math.hypot(drag_geometry_after["x"] - drag_geometry_before["x"],
                     drag_geometry_after["y"] - drag_geometry_before["y"]) > 20,
          f"before={drag_geometry_before}, after={drag_geometry_after}, widget={in_widgets}")
    state = timed("scottland/hints")
    after = offsets(state)
    check("real drag input changes avoidance positions",
          magnitude({key: (after.get(key, (0, 0))[0] - before.get(key, (0, 0))[0],
                           after.get(key, (0, 0))[1] - before.get(key, (0, 0))[1])
                     for key in before.keys() | after.keys()}) > 5)
    solve_ms = state.get("avoidance_solve_max_ms")
    search_ms = state.get("avoidance_search_max_ms")
    profile = {name: state.get(name) for name in (
        "avoidance_init_ms", "avoidance_placement_ms", "avoidance_finalization_ms")}
    solve_count = state.get("avoidance_solve_count")
    solve_budget_ms = state.get("avoidance_solve_budget_ms")
    deadline_count = state.get("avoidance_solve_deadline_count")
    check("solve telemetry is published for the live-drag regression",
          isinstance(solve_ms, (int, float)) and isinstance(solve_count, int) and solve_count > 0 and
          solve_budget_ms == 2.0 and isinstance(deadline_count, int) and
          isinstance(search_ms, (int, float)),
          f"solve_ms={solve_ms}, search_ms={search_ms}, solve_count={solve_count}, budget={solve_budget_ms}, "
          f"deadline_count={deadline_count}")
    print(f"maximum complete solve {solve_ms} ms; exposure body {search_ms} ms "
          f"(init/placement/final={profile}, search deadline 2 ms)", flush=True)
    count_at_rest = solve_count
    time.sleep(.25)
    settled_state = timed("scottland/hints")
    check("settled drag geometry does not re-solve from its own visual offset",
          settled_state.get("avoidance_solve_count") == count_at_rest,
          f"solve count {count_at_rest} -> {settled_state.get('avoidance_solve_count')}")
    check("Wayfire IPC remains responsive throughout the drag",
          max(row["ms"] for row in latencies) < 250,
          f"maximum request {max(row['ms'] for row in latencies):.1f} ms")
    subprocess.run(["grim", str(artifacts / "after-live-drag.png")], check=True, timeout=8)
    (artifacts / "metrics.json").write_text(json.dumps({
        "ipc_latencies": latencies,
        "max_ipc_ms": max(row["ms"] for row in latencies),
        "avoidance_solve_max_ms": solve_ms,
        "avoidance_solve_budget_ms": solve_budget_ms,
        "avoidance_solve_count": solve_count,
        "avoidance_solve_deadline_count": deadline_count,
        "stationary_drag_solve_counts": stationary_solve_counts,
        "before_offsets": before,
        "after_offsets": after,
        "drag_points": points,
        "attention_window": attention_id,
        "attention_active": attention_active,
        "breath_before": breath_before,
        "breath_after": breath_after,
        "drag_geometry_before": drag_geometry_before,
        "drag_geometry_after": drag_geometry_after,
        "goo": goo,
    }, indent=2) + "\n")
    print(f"avoidance live-drag regression: {passes} passed, {failures} failed", flush=True)
finally:
    t.key("LEFTMETA", False)
    t.ipc.sock.settimeout(5)
    try:
        timed("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "release"})
    except Exception:
        pass
    try:
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
            proc.kill(); proc.wait(timeout=2)

sys.exit(bool(failures))
