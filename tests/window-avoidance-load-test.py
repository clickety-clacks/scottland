#!/usr/bin/env python3
"""WK13/P12 load regression: dense held layouts converge, publish hints, and go idle."""
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

if len(sys.argv) != 3:
    raise SystemExit("usage: window-avoidance-load-test.py HEADLESS_DIR ARTIFACT_DIR")
hdir, artifacts = Path(sys.argv[1]), Path(sys.argv[2])
artifacts.mkdir(parents=True, exist_ok=True)
spec = importlib.util.spec_from_file_location(
    "widget_input", Path(__file__).with_name("widget-input-test.py"))
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)
children = []
passes = failures = 0
titles = [f"avoidance-load-{i}" for i in range(12)]


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


def open_foot(title):
    proc = subprocess.Popen(["foot", "-c", "/dev/null", "-T", title, "-W", "120x40",
                             "sh", "-c", "exec sleep 600"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    children.append(proc)
    t.owned.append((title, proc))


def cpu_ticks(pid):
    raw = Path(f"/proc/{pid}/stat").read_text()
    rest = raw[raw.rfind(")") + 2:].split()
    return int(rest[11]) + int(rest[12])


def cpu_sample(pid, seconds=2.5):
    before = cpu_ticks(pid)
    started = time.monotonic()
    load_before = os.getloadavg()
    time.sleep(seconds)
    elapsed = time.monotonic() - started
    percent = 100 * (cpu_ticks(pid) - before) / os.sysconf("SC_CLK_TCK") / elapsed
    return {"main_thread_cpu_percent": percent, "sample_seconds": elapsed,
            "load_average_before": load_before, "load_average_after": os.getloadavg()}


def hints_for(ids):
    state = ipc("scottland/hints")
    rows = {int(row["window"]): row for row in state["hints"]}
    return state, [rows.get(identifier) for identifier in ids]


def minimum_patch_rows(rows, minimum):
    return [row for row in rows if row and row.get("minimum_patch_visible") and
            row.get("surface_patch_size", 0) + 1 >= minimum]


try:
    cli("set", "window_avoidance_always", "false")
    output = ipc("window-rules/list-outputs")[0]["geometry"]
    titles_by_id = {}
    for title in titles:
        open_foot(title)
        app = t.wait_for(lambda title=title: t.app(title), timeout=10)
        titles_by_id[title] = int(app["id"])
    time.sleep(.35)

    # Keep the pile heavily overlapped while leaving enough area inside the
    # configured center zone for each window's one minimum-size peek.
    width = round(output["width"] * .57)
    height = round(output["height"] * .47)
    rect = {"x": round(output["x"] + (output["width"] - width) / 2),
            "y": round(output["y"] + (output["height"] - height) / 2),
            "width": width, "height": height}
    ids = [titles_by_id[title] for title in titles]
    for identifier in ids:
        ipc("window-rules/configure-view", {"id": identifier, "geometry": rect})
    time.sleep(.3)
    pid = int((hdir / "compositor.pid").read_text())
    no_hint_cpu = cpu_sample(pid)
    cli("set", "window_avoidance_always", "true")
    setting_on = json.loads(cli("get")).get("window_avoidance_always") is True
    check("always-on avoidance setting is live before the dense solve", setting_on)
    t.key("LEFTALT", True)
    end = time.monotonic() + 18
    final_state, rows = {}, []
    while time.monotonic() < end:
        final_state, rows = hints_for(ids)
        complete = (not final_state.get("avoidance_solve_pending") and
                    all(row and row.get("visible") and row.get("rendered") and
                        row.get("circle", {}).get("size", 0) + .25 >=
                            final_state.get("minimum_window_hint_size", 48)
                        for row in rows))
        if complete:
            break
        time.sleep(.04)

    mapped = [row for row in rows if row]
    visible = [row for row in mapped if row.get("visible") and row.get("rendered") and
               row.get("circle", {}).get("size", 0) + .25 >=
                   final_state.get("minimum_window_hint_size", 48)]
    patch_visible = minimum_patch_rows(rows,
        final_state.get("minimum_window_hint_size", 48))
    check("12 overlapping windows remain a pairwise-overlapping layout",
          len(ids) == 12 and rect["width"] > output["width"] / 2 and
          rect["height"] > output["height"] * .4,
          f"count={len(ids)}; frame={rect}")
    check("held layout converges with every window's minimum hint rendered",
          len(visible) == 12 and not final_state.get("avoidance_solve_pending"),
          f"mapped={len(mapped)} rendered minimum hints={len(visible)}; "
          f"pending={final_state.get('avoidance_solve_pending')}; "
          f"attempts={final_state.get('avoidance_progress_attempts')}; "
          f"fallbacks={final_state.get('avoidance_fallback_count')}")
    active_no_room = len(rows) - len(patch_visible)
    check("the converged pile has a checked patch or a bounded P1 no-room result per window",
          len(patch_visible) + min(active_no_room,
              final_state.get("avoidance_fallback_count", 0)) == 12,
          f"checked patches={len(patch_visible)}/12; no-room={active_no_room}; "
          f"fallbacks={final_state.get('avoidance_fallback_count')}; "
          f"sizes={[round(row.get('surface_patch_size', 0), 1) if row else None for row in rows]}")
    check("retry count stays within the per-window bound",
          final_state.get("avoidance_progress_attempts", 0) <= 12 and
          final_state.get("avoidance_fallback_count", 0) <= 12,
          f"attempts={final_state.get('avoidance_progress_attempts')}; "
          f"fallbacks={final_state.get('avoidance_fallback_count')}")

    subprocess.run(["grim", str(artifacts / "overlapping-12-hints.png")],
                   check=True, timeout=8)
    settled_count = final_state.get("avoidance_solve_count")
    window_mode_hint_count = len(visible)
    time.sleep(.8)
    idle_state, idle_rows = hints_for(ids)
    settled_tick_count = idle_state.get("hint_step_count")
    check("unchanged converged layout stops requesting avoidance solves",
          not idle_state.get("avoidance_solve_pending") and
          idle_state.get("avoidance_solve_count") == settled_count,
          f"settled solve count={settled_count}; after idle={idle_state.get('avoidance_solve_count')}")

    active_hint_cpu = cpu_sample(pid)
    active_state, active_rows = hints_for(ids)
    check("settled Window mode stops its animation tick",
          active_state.get("hint_step_count") == settled_tick_count and
          active_state.get("avoidance_solve_count") == settled_count and
          not active_state.get("avoidance_solve_pending"),
          f"steps {settled_tick_count} -> {active_state.get('hint_step_count')}; "
          f"solves {settled_count} -> {active_state.get('avoidance_solve_count')}; "
          f"last animation={active_state.get('hint_step_animation')}; "
          f"last offset={active_state.get('hint_step_offset')}")

    # Keep always-on avoidance active but remove Window mode's hint overlays. This
    # isolates avoidance's settled idle cost from the cost of drawing twelve badges.
    t.key("LEFTALT", False)
    time.sleep(.8)
    always_state, always_rows = hints_for(ids)
    always_patch_visible = minimum_patch_rows(always_rows,
        always_state.get("minimum_window_hint_size", 48))
    setting_still_on = json.loads(cli("get")).get("window_avoidance_always") is True
    always_no_room = len(always_rows) - len(always_patch_visible)
    check("always-on mode retains patches and bounded no-room results after Alt release",
          setting_still_on and len(always_patch_visible) + min(always_no_room,
              always_state.get("avoidance_fallback_count", 0)) == 12,
          f"setting={setting_still_on}; patches={len(always_patch_visible)}/12; "
          f"no-room={always_no_room}; fallbacks={always_state.get('avoidance_fallback_count')}; "
          f"sizes={[round(row.get('surface_patch_size', 0), 1) if row else None for row in always_rows]}")
    subprocess.run(["grim", str(artifacts / "overlapping-12-always.png")],
                   check=True, timeout=8)
    always_solve_count = always_state.get("avoidance_solve_count")
    always_tick_count = always_state.get("hint_step_count")
    always_cpu = cpu_sample(pid)
    settled_state, settled_rows = hints_for(ids)
    always_delta = always_cpu["main_thread_cpu_percent"] - \
        no_hint_cpu["main_thread_cpu_percent"]
    cpu_record = {"no_hint_baseline": no_hint_cpu,
                  "window_mode_hints": active_hint_cpu,
                  "always_on_without_hints": always_cpu,
                  "always_on_excess_over_baseline_pp": always_delta,
                  "solve_count_before": always_solve_count,
                  "solve_count_after": settled_state.get("avoidance_solve_count"),
                  "hint_step_count_before": always_tick_count,
                  "hint_step_count_after": settled_state.get("hint_step_count"),
                  "last_hint_animation_pending": settled_state.get("hint_step_animation"),
                  "last_hint_offset_pending": settled_state.get("hint_step_offset"),
                  "pending_after": settled_state.get("avoidance_solve_pending")}
    check("settled always-on avoidance adds no idle solve/tick loop",
          settled_state.get("hint_step_count") == always_tick_count and
          settled_state.get("avoidance_solve_count") == always_solve_count and
          not settled_state.get("avoidance_solve_pending"),
          f"steps {always_tick_count} -> {settled_state.get('hint_step_count')}; "
          f"solves {always_solve_count} -> {settled_state.get('avoidance_solve_count')}; "
          f"pending={settled_state.get('avoidance_solve_pending')}")
    check("settled always-on avoidance stays near the same-layout CPU baseline",
          always_delta <= 5,
          f"Wayfire main thread {always_cpu['main_thread_cpu_percent']:.2f}% always-on vs "
          f"{no_hint_cpu['main_thread_cpu_percent']:.2f}% no-hint "
          f"(delta {always_delta:.2f} points); load "
          f"{no_hint_cpu['load_average_before']} -> {always_cpu['load_average_after']}")
    (artifacts / "load-state.json").write_text(json.dumps({
        "windows": titles,
        "geometry": rect,
        "settled_state": settled_state,
        "window_mode_state": active_state,
        "always_on_state": always_state,
        "window_mode_hint_count": window_mode_hint_count,
        "window_mode_surface_patch_count": len(patch_visible),
        "always_on_surface_patch_count": len(always_patch_visible),
        "window_mode_no_room_count": active_no_room,
        "always_on_no_room_count": always_no_room,
        "window_avoidance_always_live": setting_still_on,
        "cpu": cpu_record,
        "rendered_hint_count": len([row for row in active_rows if row and row.get("visible") and
            row.get("rendered") and row.get("circle", {}).get("size", 0) + .25 >=
                active_state.get("minimum_window_hint_size", 48)]),
    }, indent=2) + "\n")
    print(f"avoidance load regression: {passes} passed, {failures} failed", flush=True)
finally:
    try:
        t.key("LEFTALT", False)
        cli("set", "window_avoidance_always", "false")
    except Exception:
        pass
    for proc in children:
        if proc.poll() is None:
            proc.terminate()
    for proc in children:
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)

sys.exit(bool(failures))
