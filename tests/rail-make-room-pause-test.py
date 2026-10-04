#!/usr/bin/env python3
"""WG26 hold buffer, eased rail motion and all window-to-widget entry paths.

All movement is sent as real Wayfire input through stipc. Run inside a fresh
tests/headless.sh --widgets session.
"""
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import threading
import time

spec = importlib.util.spec_from_file_location("widget_input", Path(__file__).with_name("widget-input-test.py"))
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)

ipc = t.ipc
screen = t.screen
artifacts = Path(__file__).resolve().parent.parent / "build" / "rail-make-room-pause"
artifacts.mkdir(parents=True, exist_ok=True)
passed = failed = 0
metrics = {"display": os.environ.get("WAYLAND_DISPLAY")}
palette_path = Path(os.environ["XDG_RUNTIME_DIR"]) / "scottland" / (os.environ["WAYLAND_DISPLAY"] + ".palette.json")
palette_before = palette_path.read_bytes() if palette_path.exists() else None


def check(name, ok, detail=""):
    global passed, failed
    print(("PASS  " if ok else "FAIL  ") + name + (f": {detail}" if not ok else ""), flush=True)
    if ok:
        passed += 1
    else:
        failed += 1


def card_scene(title):
    view = t.card(title)
    if not view:
        raise AssertionError(f"no widget card for {title}")
    return view.get("scene_frame", view["frame"])


def begin_drag(view):
    frame = view["frame"]
    x, y = frame["x"] + frame["width"] / 2, frame["y"] + frame["height"] / 2
    t.move(x, y)
    time.sleep(.06)
    t.key("LEFTMETA", True)
    ipc.call("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "press"})
    return x, y


def release_drag(wait=.5):
    ipc.call("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "release"})
    t.key("LEFTMETA", False)
    if wait:
        time.sleep(wait)


def scene_ys(names):
    """Every named card's drawn y from one layout snapshot, so a row never mixes frames."""
    views = ipc.call("scottland/layout-state")["views"]
    row = {}
    for name in names:
        card = next((v for v in views if v["widget"] and v["title"].endswith(": " + name)), None)
        if not card:
            raise AssertionError(f"no widget card for {name}")
        row[name] = card.get("scene_frame", card["frame"])["y"]
    return row


def sample_y(names, seconds, interval=.012):
    start = time.monotonic()
    rows = []
    while time.monotonic() - start < seconds:
        rows.append({"ms": (time.monotonic() - start) * 1000, **scene_ys(names)})
        time.sleep(interval)
    return rows


def settle(names, quiet=.15, timeout=3):
    """Wait until the named cards' drawn positions stop changing."""
    last, still_since = scene_ys(names), time.monotonic()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(.02)
        now = scene_ys(names)
        if max(abs(now[n] - last[n]) for n in names) > .01:
            last, still_since = now, time.monotonic()
        elif time.monotonic() - still_since >= quiet:
            break
    return last


def total_delta(before, after, names):
    return max((abs(after[name] - before[name]) for name in names), default=0.0)


def max_step(rows, names):
    return max((max(abs(rows[i][name] - rows[i - 1][name]) for name in names)
                for i in range(1, len(rows))), default=0.0)


def vertical_gap(a, b):
    if a["y"] + a["height"] <= b["y"]:
        return b["y"] - (a["y"] + a["height"])
    if b["y"] + b["height"] <= a["y"]:
        return a["y"] - (b["y"] + b["height"])
    return -min(a["y"] + a["height"], b["y"] + b["height"]) + max(a["y"], b["y"])


def true_y(title):
    view_id = t.card(title)["id"]
    return next(v["geometry"]["y"] for v in ipc.call("window-rules/list-views") if v["id"] == view_id)


class Sampler:
    """Scene y of rail cards every ~6 ms on its own IPC connection, so input calls can't
    hold sampling back while an ease runs."""
    def __init__(self, names):
        self.names, self.rows, self.stop = names, [], threading.Event()
        self.conn = t.Ipc()
        self.t0 = time.monotonic()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        while not self.stop.is_set():
            state = self.conn.call("scottland/layout-state")
            row = {"ms": (time.monotonic() - self.t0) * 1000}
            for v in state["views"]:
                if v["widget"]:
                    for n in self.names:
                        if v["title"].endswith(": " + n):
                            row[n] = v.get("scene_frame", v["frame"])["y"]
            if len(row) == len(self.names) + 1:
                self.rows.append(row)
            time.sleep(.006)

    def mark(self):
        return (time.monotonic() - self.t0) * 1000

    def end(self):
        self.stop.set()
        self.thread.join(timeout=2)
        self.conn.sock.close()
        return self.rows


def windowed_speed(rows, names, window_ms=20):
    """Peak speed in px/s over sample pairs at least window_ms apart (sampling jitter
    makes single 6 ms steps unreliable under load)."""
    peak = 0.0
    for i, a in enumerate(rows):
        b = next((r for r in rows[i + 1:] if r["ms"] - a["ms"] >= window_ms), None)
        if b is None:
            break
        for n in names:
            peak = max(peak, abs(b[n] - a[n]) / (b["ms"] - a["ms"]) * 1000)
    return round(peak, 1)


def overshoot(rows, names):
    """Worst excursion outside each card's start..end span (0 for a monotone ease)."""
    worst = 0.0
    for n in names:
        lo, hi = sorted((rows[0][n], rows[-1][n]))
        for r in rows:
            worst = max(worst, lo - r[n], r[n] - hi)
    return round(worst, 2)


def overlap(a, b):
    return min(a["y"] + a["height"], b["y"] + b["height"]) - max(a["y"], b["y"])


def glide(x0, y0, x1, y1, steps=12, dt=.014):
    for i in range(1, steps + 1):
        t.move(x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps)
        time.sleep(dt)


def screenshot(name):
    subprocess.run(["grim", str(artifacts / name)], check=True)


def clear_case():
    t.cleanup()
    time.sleep(.4)


def release_palette():
    if palette_before is None:
        palette_path.unlink(missing_ok=True)
    else:
        palette_path.write_bytes(palette_before)
    time.sleep(.3)


try:
    ipc.call("wayfire/set-config-options", {"scottland/sounds": False, "scottland/widget_bounce": 0})
    dwell = ipc.call("wayfire/get-config-option", {"option": "scottland/widget_make_room_dwell"})["value"]
    metrics["dwell_ms"] = int(dwell)
    check("WG26 dwell is a live 350 ms setting", int(dwell) == 350, dwell)

    # A large widget shift waits for a 350 ms pause, then eases. Wobble up to 4 px
    # belongs to that same pause; motion beyond it starts a fresh pause.
    for name, y in (("pause-a", 140), ("pause-b", 320), ("pause-c", 500)):
        t.launch(name, rail="right", y=y)
    names = ("pause-b", "pause-c")
    before = {name: card_scene(name)["y"] for name in names}
    motion = Sampler(names)
    x, y = begin_drag(t.card("pause-a"))
    t.move(screen["width"] - 6, 320)
    started = time.monotonic()
    time.sleep(.12)
    t.move(screen["width"] - 8, 322)  # sqrt(8) px: within the four-pixel wobble radius
    early = sample_y(names, .20)
    early_change = max((abs(row[name] - before[name]) for row in early for name in names), default=0)
    time.sleep(.10)
    eased_start = time.monotonic()
    eased = sample_y(names, .52)
    first_movement = next((row for row in eased if max(abs(row[name] - before[name]) for name in names) > 1), None)
    first_movement_ms = ((eased_start - started) * 1000 + first_movement["ms"]) if first_movement else None
    after_pause = {name: card_scene(name)["y"] for name in names}
    first_rows = [r for r in motion.rows if r["ms"] >= (eased_start - motion.t0) * 1000 - 400]
    unique_positions = {tuple(round(row[name], 1) for name in names) for row in eased}
    metrics["large_move"] = {"before": before, "after": after_pause,
        "first_change_ms": round(first_movement_ms, 1) if first_movement else None,
        "unique_positions": len(unique_positions), "max_12ms_step_px": round(max_step(eased, names), 2)}
    screenshot("large-widget-shift.png")
    check("WG26 widgets hold position before the configured pause", early_change < .5,
          {"ms": round((time.monotonic() - started) * 1000), "max_delta": round(early_change, 3)})
    check("WG26 a large neighbor move eases over multiple frames", total_delta(before, after_pause, names) > 40 and
          len(unique_positions) >= 5 and first_movement is not None and first_movement_ms >= 250 and
          max_step(eased, names) < 35,
          {"before": before, "after": after_pause, "first_change_ms": round(first_movement_ms, 1) if first_movement else None,
           "unique_positions": len(unique_positions), "max_12ms_sample_step": round(max_step(eased, names), 2)})
    first_speed = windowed_speed(first_rows, names)
    first_overshoot = overshoot(first_rows, names)
    metrics["large_move"].update(windowed_speed_px_s=first_speed, overshoot_px=first_overshoot)
    check("WG26 the pause shift stays under the 1000 px/s automatic speed limit",
          first_speed <= 1150, {"windowed_peak_px_s": first_speed})
    check("WG26 the pause shift eases without overshoot", first_overshoot <= .5,
          {"overshoot_px": first_overshoot})
    check("WG26 sub-four-pixel wobble does not restart the dwell", first_movement is not None and
          first_movement_ms <= 470,
          {"first_change_ms": round(first_movement_ms, 1) if first_movement else None,
           "pause_ms": int(dwell), "wobble_px": round(math.hypot(2, 2), 2)})

    # Once the layout has settled, moving on does not re-solve until the next pause.
    time.sleep(.42)
    settled = {name: card_scene(name)["y"] for name in names}
    t.move(screen["width"] - 6, 410)
    moving = sample_y(names, .20)
    held_change = max((abs(row[name] - settled[name]) for row in moving for name in names), default=0)
    time.sleep(.20)
    second_pause = sample_y(names, .52)
    after_second = {name: card_scene(name)["y"] for name in names}
    second_unique = {tuple(round(row[name], 1) for name in names) for row in second_pause}
    metrics["second_pause"] = {"before": settled, "after": after_second,
        "unchanged_first_200ms_px": round(held_change, 3), "unique_positions": len(second_unique),
        "max_12ms_step_px": round(max_step(second_pause, names), 2)}
    check("WG26 moving on keeps the last layout until the next pause", held_change < .5,
          {"max_delta": round(held_change, 3)})
    check("WG26 the next pause triggers another eased layout", total_delta(settled, after_second, names) > 5 and
          len(second_unique) >= 3 and max_step(second_pause, names) < 35,
          {"before": settled, "after": after_second, "unique_positions": len(second_unique),
           "max_12ms_sample_step": round(max_step(second_pause, names), 2)})

    # Esc after a pause: the *drawn* positions come back exactly, eased, without overshoot
    # (true geometry never changes during an audition, so it alone proves nothing).
    esc_mark = motion.mark()
    t.key("ESC", True)
    t.key("ESC", False)
    time.sleep(.05)
    release_drag(wait=.8)
    rows = motion.end()
    restore = [r for r in rows if r["ms"] >= esc_mark]
    restored = {name: card_scene(name)["y"] for name in names}
    metrics["esc_after_pause"] = {"restored": restored, "before": before,
        "overshoot_px": overshoot(restore, names), "windowed_speed_px_s": windowed_speed(restore, names),
        "eased_positions": len({tuple(round(r[n], 1) for n in names) for r in restore})}
    check("WG26 Esc returns the drawn rail positions exactly", total_delta(before, restored, names) < .01,
          {"before": before, "after": restored})
    check("WG26 Esc return eases without overshoot or speeding",
          overshoot(restore, names) <= .5 and windowed_speed(restore, names) <= 1150 and
          metrics["esc_after_pause"]["eased_positions"] >= 4, metrics["esc_after_pause"])
    clear_case()

    # Review B1: pause (neighbors move aside), move on, drop elsewhere before pausing again.
    # The layout shown at the pause was for a different spot; the drop solves for the real
    # landing, so neighbors it doesn't block go home (P2) and nothing is left stacked.
    def stale_drop(label, launch, drop_y, homes_y):
        names_here = tuple(homes_y)
        homes = {n: true_y(n) for n in names_here}
        x0, y0 = begin_drag(launch())
        glide(x0, y0, screen["width"] - 6, 320)
        time.sleep(.75)   # one pause: the card at 320 is pushed and settles
        paused = {n: card_scene(n)["y"] for n in names_here}
        glide(screen["width"] - 6, 320, screen["width"] - 6, drop_y, steps=16, dt=.012)  # >4 px per update
        release_drag(wait=1.4)
        return homes, paused

    for name, y in (("sa", 140), ("sb", 320), ("sc", 500)):
        t.launch(name, rail="right", y=y)
    homes, paused = stale_drop("A", lambda: t.card("sa"), 650, ("sb", "sc"))
    landed = card_scene("sa")
    final = {n: true_y(n) for n in ("sb", "sc")}
    metrics["stale_drop_along_rail"] = {"home": homes, "paused": paused, "final": final,
        "overlap": {n: round(overlap(landed, card_scene(n)), 1) for n in ("sb", "sc")}}
    check("WG26 drop after moving on returns neighbors the landing doesn't block",
          abs(paused["sb"] - homes["sb"]) > 40 and all(abs(final[n] - homes[n]) <= 1 for n in final),
          metrics["stale_drop_along_rail"])
    check("WG26 drop after moving on leaves nothing stacked",
          max(metrics["stale_drop_along_rail"]["overlap"].values()) <= 0, metrics["stale_drop_along_rail"])
    clear_case()

    for name, y in (("qa", 140), ("qb", 320), ("qc", 560)):
        t.launch(name, rail="right", y=y)
    homes, paused = stale_drop("A2", lambda: t.card("qa"), 560, ("qb", "qc"))
    landed = card_scene("qa")
    metrics["stale_drop_onto_neighbor"] = {"home": homes, "final": {n: true_y(n) for n in ("qb", "qc")},
        "overlap": {n: round(overlap(landed, card_scene(n)), 1) for n in ("qb", "qc")}}
    check("WG26 a widget dropped on a neighbor's spot after moving on clears it",
          max(metrics["stale_drop_onto_neighbor"]["overlap"].values()) <= 0 and
          abs(metrics["stale_drop_onto_neighbor"]["final"]["qb"] - homes["qb"]) <= 1,
          metrics["stale_drop_onto_neighbor"])
    clear_case()

    for name, y in (("wb", 320), ("wc", 560)):
        t.launch(name, rail="right", y=y)
    t.move(screen["width"] / 2, screen["height"] / 2)
    t.launch("ww", rail=None)
    homes, paused = stale_drop("A3", lambda: t.app("ww"), 560, ("wb", "wc"))
    t.wait_for(lambda: t.card("ww") and not t.card("ww")["preview"], timeout=5)
    time.sleep(.6)
    landed = card_scene("ww")
    metrics["stale_window_drop_onto_neighbor"] = {"home": homes, "final": {n: true_y(n) for n in ("wb", "wc")},
        "overlap": {n: round(overlap(landed, card_scene(n)), 1) for n in ("wb", "wc")}}
    check("WG26 a window dropped on a neighbor's spot after moving on clears it",
          max(metrics["stale_window_drop_onto_neighbor"]["overlap"].values()) <= 0 and
          abs(metrics["stale_window_drop_onto_neighbor"]["final"]["wb"] - homes["wb"]) <= 1,
          metrics["stale_window_drop_onto_neighbor"])
    clear_case()

    # Review F2: five cards packed at the top of the left rail, a window dropped at the top.
    # Least total movement (for the chosen split): the drop settles just below the pinned top
    # card, 1 px clear of it, and the four below make room. Overlap is only for a full rail.
    # A small re-pause doesn't make the hole jump, and after the drop the cards stay exactly
    # as shown at the last pause: the settle shown is the settle applied (round 3, finding 3).
    packed = tuple(f"packed-{i}" for i in range(5))
    for name, y in zip(packed, (72, 168, 264, 360, 456)):
        t.launch(name, rail="left", y=y)
    packed_before = {n: card_scene(n) for n in packed}
    t.move(screen["width"] / 2, screen["height"] / 2)
    t.launch("packed-arrival", rail=None)
    reused_before = ipc.call("scottland/layout-state")["rail_settles_reused"]
    x0, y0 = begin_drag(t.app("packed-arrival"))
    glide(x0, y0, 6, 80)
    time.sleep(.9)
    shown_first = scene_ys(packed)
    glide(6, 80, 6, 86, steps=2)   # 6 px: past the 4 px wobble, so a second pause solves
    time.sleep(.9)
    shown = scene_ys(packed)
    hole_moved = max(abs(shown[n] - shown_first[n]) for n in packed)
    release_drag(wait=1.5)
    t.wait_for(lambda: t.card("packed-arrival") and not t.card("packed-arrival")["preview"], timeout=5)
    time.sleep(.6)
    landed = card_scene("packed-arrival")
    packed_after = {n: card_scene(n) for n in packed}
    rearranged = max(abs(packed_after[n]["y"] - shown[n]) for n in packed)
    settle_state = ipc.call("scottland/layout-state")
    reused = settle_state["rail_settles_reused"] - reused_before
    in_order = all(packed_after[a]["y"] < packed_after[b]["y"] for a, b in zip(packed, packed[1:]))
    metrics["packed_not_full"] = {"before_y": {n: packed_before[n]["y"] for n in packed},
        "after_y": {n: packed_after[n]["y"] for n in packed}, "landing": landed,
        "overlap": {n: round(overlap(landed, packed_after[n]), 1) for n in packed}}
    metrics["packed_not_full"].update(hole_moved_on_6px_repause=round(hole_moved, 2),
        rearranged_after_drop=round(rearranged, 2), settle_reused=reused,
        settles_resolved_total=settle_state["rail_settles_resolved"], settle_from_pointer_px=round(landed["y"] - (86 - 48), 1))
    check("WG26 a packed but not full rail makes room without overlap",
          max(metrics["packed_not_full"]["overlap"].values()) <= 0 and in_order and
          all(f["y"] + f["height"] <= screen["height"] - 23 for f in packed_after.values()),
          metrics["packed_not_full"])
    check("WG26 the drop settles exactly the least its split needs (just below the pinned card)",
          abs(landed["y"] - (packed_after["packed-0"]["y"] + packed_after["packed-0"]["height"] + 1)) <= 1 and
          abs(packed_after["packed-0"]["y"] - packed_before["packed-0"]["y"]) < .5, metrics["packed_not_full"])
    check("WG26 a small re-pause moves the hole no more than the pointer moved", hole_moved <= 6.5,
          metrics["packed_not_full"])
    check("WG26 after the drop the cards stay as shown at the last pause (no second solve)",
          rearranged <= .5 and reused == 1, metrics["packed_not_full"])
    clear_case()

    # A direct window drop before any pause still solves once on drop. Peers visibly
    # traverse the eased correction while their committed geometry catches up.
    for name, y in (("drop-near", 250), ("drop-far", 390)):
        t.launch(name, rail="right", y=y)
    drop_names = ("drop-near", "drop-far")
    drop_before = {name: card_scene(name)["y"] for name in drop_names}
    t.move(screen["width"] / 2, screen["height"] / 2)
    t.launch("drop-arrival", rail=None)
    sx, sy = begin_drag(t.app("drop-arrival"))
    end_x = screen["width"] - 6
    for i in range(1, 13):
        t.move(sx + (end_x - sx) * i / 12, sy + (320 - sy) * i / 12)
        time.sleep(.014)
    drop_started = time.monotonic()
    release_drag(wait=0)
    drop_frames = sample_y(drop_names, .72)
    t.wait_for(lambda: t.card("drop-arrival") and not t.card("drop-arrival")["preview"], timeout=5)
    # The mapped card can arrive after the drag-preview solve. Include a full correction
    # interval so the final-clearance assertion is made after its 190–360 ms ease settles.
    drop_frames.extend(sample_y(drop_names, .8))
    drop_after = {name: card_scene(name)["y"] for name in drop_names}
    drop_landing = card_scene("drop-arrival")
    drop_gaps = {name: round(vertical_gap(card_scene(name), drop_landing), 2) for name in drop_names}
    screenshot("direct-window-drop.png")
    drop_positions = {tuple(round(row[name], 1) for name in drop_names) for row in drop_frames}
    metrics["direct_drop"] = {"before": drop_before, "after": drop_after,
        "landing_gaps_px": drop_gaps, "unique_positions": len(drop_positions),
        "max_12ms_step_px": round(max_step(drop_frames, drop_names), 2)}
    check("WG26 direct drop before a pause solves on release", total_delta(drop_before, drop_after, drop_names) > 10,
          {"before": drop_before, "after": drop_after, "elapsed_ms": round((time.monotonic() - drop_started) * 1000)})
    check("WG26 direct drop clears the actual widget card footprint", min(drop_gaps.values()) >= .5,
          {"landing": drop_landing, "gaps": drop_gaps})
    check("WG26 drop-time make-room animates its correction", len(drop_positions) >= 4 and
          max_step(drop_frames, drop_names) < 45,
          {"unique_positions": len(drop_positions), "max_12ms_sample_step": round(max_step(drop_frames, drop_names), 2)})
    clear_case()

    # A real flick starts in the center and is released well before the rail boundary.
    # Its inertial coast alone reaches the rail; no second drag can accidentally cause
    # a direct widget drop instead of exercising the coast-arrival path.
    for name, y in (("coast-near", 170), ("coast-far", 330)):
        t.launch(name, rail="right", y=y)
    coast_names = ("coast-near", "coast-far")
    coast_before = {name: card_scene(name)["y"] for name in coast_names}
    t.move(screen["width"] / 2, screen["height"] / 2)
    t.launch("coast-arrival", rail=None)
    arrival_frame = t.app("coast-arrival")["frame"]
    sx, sy = arrival_frame["x"] + arrival_frame["width"] / 2, arrival_frame["y"] + arrival_frame["height"] / 2
    t.move(sx, sy); time.sleep(.06); t.key("LEFTMETA", True)
    ipc.call("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "press"})
    for i in range(1, 7):
        t.move(sx + 100 * i / 6, sy)
        time.sleep(.015)
    released_x = sx + 100
    release_drag(wait=0)
    coast_frames = sample_y(coast_names, 2.5)
    try:
        t.wait_for(lambda: t.app("coast-arrival") and t.app("coast-arrival")["widgetized"] and
                   t.card("coast-arrival") and not t.card("coast-arrival")["preview"], timeout=5)
    except AssertionError:
        print("coast landing state: " + json.dumps({"app": t.app("coast-arrival"),
            "card": t.card("coast-arrival"), "widgets": ipc.call("scottland/widgets")}, default=str), flush=True)
        raise
    time.sleep(.5)
    coast_after = {name: card_scene(name)["y"] for name in coast_names}
    coast_landing = card_scene("coast-arrival")
    coast_gaps = {name: round(vertical_gap(card_scene(name), coast_landing), 2) for name in coast_names}
    coast_positions = {tuple(round(row[name], 1) for name in coast_names) for row in coast_frames}
    metrics["coast"] = {"release_before_rail_px": round((screen["width"] - 25) - released_x, 1),
        "before": coast_before, "after": coast_after, "landing_gaps_px": coast_gaps,
        "unique_positions": len(coast_positions),
        "max_12ms_step_px": round(max_step(coast_frames, coast_names), 2)}
    check("WG26 coast released before rail entry makes room at its landing", released_x < screen["width"] - 25 and
          total_delta(coast_before, coast_after, coast_names) > 10 and min(coast_gaps.values()) >= .5,
          {"release_x": round(released_x, 1), "rail_threshold": screen["width"] - 25,
           "before": coast_before, "after": coast_after, "landing": coast_landing, "gaps": coast_gaps})
    check("WG26 coast-arrival correction eases", len(coast_positions) >= 4 and
          max_step(coast_frames, coast_names) < 45,
          {"unique_neighbor_positions": len(coast_positions),
           "max_12ms_step_px": round(max_step(coast_frames, coast_names), 2)})
    screenshot("coast-window-entry.png")
    clear_case()

    # Window-mode hint cycling uses the same arrival solve. Block both rails, then
    # check the side where the actual arriving card landed.
    blockers = {}
    for side in ("left", "right"):
        # Hint cycling can land at different heights as zones change. A five-card
        # staggered run guarantees a substantial overlap throughout the rail's center.
        for index, y in enumerate((108, 228, 348, 468, 588)):
            name = f"key-{side}-{index}"
            t.launch(name, rail=side, y=y)
            blockers[name] = side
    key_names = tuple(blockers)
    key_before = {name: card_scene(name)["y"] for name in key_names}
    t.move(screen["width"] / 2, screen["height"] / 2)
    t.launch("key-arrival", rail=None)
    app = t.app("key-arrival")
    app_id = app["id"]
    check("WG26 key fixture begins with an ordinary center window",
          app is not None and not app["widgetized"], app)
    t.key("LEFTALT", True)
    t.wait_for(lambda: ipc.call("scottland/hints")["active"], timeout=2)
    time.sleep(.32)
    label = next(h["hint"] for h in ipc.call("scottland/hints")["hints"] if h["window"] == app_id)
    key_frames = []
    key_started = time.monotonic()
    sampler = t.Ipc()
    sample_stop = threading.Event()

    def sample_key_motion():
        while not sample_stop.is_set():
            now = time.monotonic()
            state = sampler.call("scottland/layout-state")
            row = {"ms": (now - key_started) * 1000}
            for name in key_names:
                card = next((v for v in state["views"] if v["widget"] and
                    v["title"].endswith(": " + name)), None)
                if card:
                    row[name] = card.get("scene_frame", card["frame"])["y"]
            if len(row) == len(key_names) + 1:
                key_frames.append(row)
            time.sleep(.008)

    sampler_thread = threading.Thread(target=sample_key_motion, daemon=True)
    # Include the unchanged starting layout, then sample on an independent IPC connection so
    # the key presses cannot finish the ease before a synchronous state query gets a turn.
    key_frames.append({"ms": 0, **key_before})
    sampler_thread.start()
    for _ in range(2):
        for letter in label:
            t.key(letter.upper(), True)
            t.key(letter.upper(), False)
    t.key("LEFTALT", False)
    docked = False
    next_status = key_started
    deadline = key_started + 5
    while time.monotonic() < deadline:
        now = time.monotonic()
        if now >= next_status:
            arriving_app = t.app("key-arrival")
            arriving_card = t.card("key-arrival")
            docked = bool(arriving_app and arriving_app["widgetized"] and arriving_card and
                          not arriving_card["preview"])
            next_status = now + .05
        if docked and now - key_started >= 1.25:
            break
        time.sleep(.012)
    sample_stop.set()
    sampler_thread.join(timeout=2)
    sampler.sock.close()
    if not docked:
        raise AssertionError("Window-mode key did not finish widgetizing: " + json.dumps({
            "app": t.app("key-arrival"), "card": t.card("key-arrival"),
            "widgets": ipc.call("scottland/widgets")}, default=str))
    key_after = settle(key_names)
    landed = card_scene("key-arrival")
    landed_side = "left" if landed["x"] + landed["width"] / 2 < screen["width"] / 2 else "right"
    side_names = [name for name, side in blockers.items() if side == landed_side]
    changed = total_delta(key_before, key_after, side_names)
    key_landing = card_scene("key-arrival")
    key_gaps = {name: round(vertical_gap(card_scene(name), key_landing), 2) for name in side_names}
    key_unique = {tuple(round(row[name], 1) for name in side_names) for row in key_frames}
    metrics["window_mode_key"] = {"side": landed_side, "before": {n: key_before[n] for n in side_names},
        "after": {n: key_after[n] for n in side_names}, "landing_gaps_px": key_gaps,
        "unique_positions": len(key_unique), "sample_count": len(key_frames),
        "sample_start": {n: key_frames[0][n] for n in side_names} if key_frames else {},
        "sample_end": {n: key_frames[-1][n] for n in side_names} if key_frames else {}}
    screenshot("window-mode-key-entry.png")
    check("WG26 Window-mode key entry solves against the actual landing rail", changed > 10 and
          min(key_gaps.values()) >= .5,
          {"side": landed_side, "landing": landed, "before": {n: key_before[n] for n in side_names},
           "after": {n: key_after[n] for n in side_names}, "gaps": key_gaps})
    check("WG26 Window-mode entry easing is visible", len(key_unique) >= 3,
          {"unique_neighbor_positions": len(key_unique)})
    clear_case()

    # A widget dragged along its rail and dropped over a card pinned at the rail top: least
    # total movement settles the dropped card just below it (89 px) rather than sending the
    # pinned card below the drop (105 px). It lands, then eases into place; nothing overlaps.
    for name, y in (("settle-top", 72), ("settle-moved", 400)):
        t.launch(name, rail="right", y=y)
    top_home = card_scene("settle-top")
    reused_before = ipc.call("scottland/layout-state")["rail_settles_reused"]
    x0, y0 = begin_drag(t.card("settle-moved"))
    glide(x0, y0, screen["width"] - 6, 80)
    time.sleep(.75)
    release_drag(wait=1.6)
    landed = card_scene("settle-moved")
    pinned = card_scene("settle-top")
    metrics["widget_settle"] = {"pinned_before": top_home["y"], "pinned_after": pinned["y"], "landed": landed,
        "gap": round(vertical_gap(pinned, landed), 2),
        "settle_reused": ipc.call("scottland/layout-state")["rail_settles_reused"] - reused_before}
    check("WG26 a widget dropped over a pinned card settles beside it with nothing overlapping",
          abs(pinned["y"] - top_home["y"]) < .5 and vertical_gap(pinned, landed) >= .5 and
          abs(landed["y"] - (top_home["y"] + top_home["height"] + 1)) <= 1 and
          metrics["widget_settle"]["settle_reused"] == 1, metrics["widget_settle"])
    clear_case()

    # Window avoidance plans against where rail widgets are going, so a make-room ease
    # costs one avoidance re-solve per layout change, not one per animation frame (ce62f1a:
    # 11 during one ease; a drag with nothing to move: 1). The control drag is the same
    # gesture onto a spot nothing blocks; both are measured after the dragged morph settles.
    ipc.call("wayfire/set-config-options", {"scottland/window_avoidance_always": True})
    try:
        avoidance = {}
        for label, ys in (("blocking", (250, 430)), ("control", (560, 660))):
            names_here = tuple(f"avoid-{label}-{i}" for i in range(len(ys)))
            for name, y in zip(names_here, ys):
                t.launch(name, rail="right", y=y)
            t.move(screen["width"] / 2, screen["height"] / 2)
            t.launch(f"avoid-{label}-w", rail=None)
            x0, y0 = begin_drag(t.app(f"avoid-{label}-w"))
            glide(x0, y0, screen["width"] - 6, 250)
            time.sleep(.28)   # the dragged morph has settled; the 350 ms pause is still ahead
            solves_before = ipc.call("scottland/hints")["avoidance_solve_count"]
            ease = sample_y(names_here, 1.0)
            solves = ipc.call("scottland/hints")["avoidance_solve_count"] - solves_before
            avoidance[label] = {"solves": solves,
                "eased_positions": len({tuple(round(r[n], 1) for n in names_here) for r in ease})}
            t.key("ESC", True)
            t.key("ESC", False)
            time.sleep(.05)
            release_drag()
            clear_case()
        metrics["avoidance_during_rail_ease"] = avoidance
        check("WG26 a rail make-room ease re-solves window avoidance only for the layout change",
              avoidance["blocking"]["eased_positions"] >= 5 and avoidance["control"]["eased_positions"] == 1 and
              avoidance["blocking"]["solves"] <= avoidance["control"]["solves"] + 2, avoidance)
    finally:
        ipc.call("wayfire/set-config-options", {"scottland/window_avoidance_always": False})

    # Reduced motion takes the same layout target but skips the easing.
    palette_path.parent.mkdir(parents=True, exist_ok=True)
    palette_path.write_text(json.dumps({"scheme": "dark", "background": "#1f232c",
        "foreground": "#d8deea", "accent": "#81a1c1", "reduced_motion": True}) + "\n")
    time.sleep(.3)
    for name, y in (("reduced-a", 140), ("reduced-b", 320), ("reduced-c", 500)):
        t.launch(name, rail="left", y=y)
    reduced_names = ("reduced-b", "reduced-c")
    reduced_before = {name: card_scene(name)["y"] for name in reduced_names}
    begin_drag(t.card("reduced-a"))
    t.move(6, 320)
    reduced_started = time.monotonic()
    reduced_frames = sample_y(reduced_names, .46)
    reduced_after = {name: card_scene(name)["y"] for name in reduced_names}
    moved_rows = [row for row in reduced_frames if max(abs(row[n] - reduced_before[n]) for n in reduced_names) > 1]
    first_reduced = moved_rows[0] if moved_rows else None
    metrics["reduced_motion"] = {"first_change_ms": round(first_reduced["ms"], 1) if first_reduced else None,
        "first_delta_px": round(max(abs(first_reduced[n] - reduced_before[n]) for n in reduced_names), 2) if first_reduced else None,
        "final": reduced_after}
    check("WG26 reduced motion snaps when the hold expires", total_delta(reduced_before, reduced_after, reduced_names) > 40 and
          first_reduced is not None and max(abs(first_reduced[n] - reduced_before[n]) for n in reduced_names) > 35,
          {"first_change_ms": round(first_reduced["ms"], 1) if first_reduced else None,
           "first_delta": round(max(abs(first_reduced[n] - reduced_before[n]) for n in reduced_names), 2) if first_reduced else None,
           "final": reduced_after})
    release_drag()
    screenshot("reduced-motion-snap.png")
    state = ipc.call("scottland/layout-state")
    peak = state.get("rail_max_easing_speed_px_s")
    drawn = {"speed_px_s": state.get("rail_max_drawn_speed_px_s"),
             "step_px": state.get("rail_max_drawn_step_px"), "tick_gap_ms": state.get("rail_max_tick_gap_ms")}
    metrics["compositor_peak_speed_px_s"] = peak
    metrics["drawn_frame_to_frame"] = drawn
    check("WG26 no rail move in this run was planned faster than the 1000 px/s automatic speed limit",
          peak is not None and 0 < peak <= 1001, peak)
    # The drawn check reads what each tick actually put on screen (geometry plus rail offset,
    # through retargets and geometry commits) over the real time between ticks, so a jump at
    # a retarget or commit would show; a late tick draws a bigger step but over a longer gap.
    check("WG26 drawn rail motion, frame to frame, never exceeded the 1000 px/s limit",
          drawn["speed_px_s"] is not None and 0 < drawn["speed_px_s"] <= 1010, drawn)
finally:
    release_palette()
    try:
        t.cleanup()
    except (BrokenPipeError, ConnectionResetError, OSError) as error:
        print(f"cleanup after compositor exit: {error}", flush=True)
    (artifacts / "results.json").write_text(json.dumps(metrics, indent=2) + "\n")
    print(f"WG26 pause/easing/arrival input: {passed} passed, {failed} failed", flush=True)
    print("WG26 metrics: " + json.dumps(metrics, sort_keys=True), flush=True)

raise SystemExit(bool(failed))
