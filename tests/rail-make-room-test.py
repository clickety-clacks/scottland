#!/usr/bin/env python3
"""WG26 rail presentation with real stipc drags in an isolated Scottland session."""
import importlib.util
from pathlib import Path
import time

spec = importlib.util.spec_from_file_location("widget_input", Path(__file__).with_name("widget-input-test.py"))
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)

ipc = t.ipc
screen = t.screen
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    print(("PASS  " if ok else "FAIL  ") + name + (f": {detail}" if not ok else ""), flush=True)
    if ok:
        passed += 1
    else:
        failed += 1


def scene(view):
    return view.get("scene_frame", view["frame"])


def card_scene(title):
    return scene(t.card(title))


def app_scene(title):
    return scene(t.app(title))


def geometry(view_id):
    return next(v["geometry"] for v in ipc.call("window-rules/list-views") if v["id"] == view_id)


def card_geometry(title):
    return geometry(t.card(title)["id"])


def start_drag(view, x, y, steps=24):
    f = view["frame"]
    sx, sy = f["x"] + f["width"] / 2, f["y"] + f["height"] / 2
    t.move(sx, sy)
    time.sleep(0.1)
    t.key("LEFTMETA", True)
    ipc.call("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "press"})
    for i in range(1, steps + 1):
        x1 = sx + (x - sx) * i / steps
        y1 = sy + (y - sy) * i / steps
        started = time.monotonic()
        t.move(x1, y1)
        durations.append(time.monotonic() - started)
        time.sleep(0.025)
    time.sleep(0.45)


def end_drag():
    ipc.call("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "release"})
    t.key("LEFTMETA", False)
    time.sleep(0.65)


def cancel_drag():
    t.key("ESC", True)
    t.key("ESC", False)
    end_drag()


durations = []

try:
    ipc.call("wayfire/set-config-options", {"scottland/sounds": False})
    width, height = screen["width"], screen["height"]
    edge_right, edge_left = width - 6, 6

    t.launch("rail-a", rail="right", y=230)
    t.launch("rail-b", rail="right", y=480)
    before_a, before_b = card_geometry("rail-a"), card_geometry("rail-b")
    before_a_scene, before_b_scene = card_scene("rail-a"), card_scene("rail-b")
    t.launch("rail-arrive", rail=None)
    start_drag(t.app("rail-arrive"), edge_right, 230)
    during_a, during_b = card_scene("rail-a"), card_scene("rail-b")
    landing = app_scene("rail-arrive")
    moved = abs(during_a["y"] - before_a_scene["y"]) > 0.5 or abs(during_b["y"] - before_b_scene["y"]) > 0.5
    check("window drag starts an eased reflow after the pause", moved, (during_a, during_b))
    time.sleep(0.42)  # allow the 190–360 ms rail animation to reach its landing target
    settled_a, settled_b = card_scene("rail-a"), card_scene("rail-b")
    landing = app_scene("rail-arrive")
    clear = all(f["y"] + f["height"] <= landing["y"] - 0.5 or
        f["y"] >= landing["y"] + landing["height"] + 0.5 for f in (settled_a, settled_b))
    check("window drag moves only its obstructing rail cards live", moved, (settled_a, settled_b, landing))
    check("live window landing keeps one-pixel clearance on both sides", clear,
          (settled_a, settled_b, landing))
    end_drag()
    t.wait_for(lambda: t.card("rail-arrive") and not t.card("rail-arrive")["preview"])
    after_a, after_b = card_geometry("rail-a"), card_geometry("rail-b")
    persisted = after_a["y"] != before_a["y"] or after_b["y"] != before_b["y"]
    check("drop commits the audition as real widget geometry", persisted,
          {"before": (before_a, before_b), "after": (after_a, after_b)})

    stable_before_cancel = {name: card_geometry(name) for name in ("rail-a", "rail-b", "rail-arrive")}
    drawn_before_cancel = {name: card_scene(name) for name in ("rail-a", "rail-b", "rail-arrive")}
    t.launch("rail-cancel", rail=None)
    home_before = geometry(t.app("rail-cancel")["id"])
    start_drag(t.app("rail-cancel"), edge_right, 230)
    during_cancel = {name: card_scene(name) for name in ("rail-a", "rail-b", "rail-arrive")}
    auditioned = any(abs(during_cancel[name]["y"] - stable_before_cancel[name]["y"]) > 0.5
                     for name in during_cancel)
    check("Esc fixture first shows a live rail audition", auditioned,
          (stable_before_cancel, during_cancel))
    cancel_drag()
    stable_after_cancel = {name: card_geometry(name) for name in ("rail-a", "rail-b", "rail-arrive")}
    drawn_after_cancel = {name: card_scene(name) for name in ("rail-a", "rail-b", "rail-arrive")}
    home_after = geometry(t.app("rail-cancel")["id"])
    check("Esc restores every rail widget to its exact committed position",
          stable_before_cancel == stable_after_cancel, (stable_before_cancel, stable_after_cancel))
    # An audition never changes true geometry, so also check what is drawn: the eased
    # presentation offsets must be gone, not just the committed positions intact.
    check("Esc returns every rail widget to its exact drawn position",
          all(abs(drawn_before_cancel[n]["y"] - drawn_after_cancel[n]["y"]) < 0.01 and
              abs(drawn_before_cancel[n]["x"] - drawn_after_cancel[n]["x"]) < 0.01 for n in drawn_before_cancel),
          (drawn_before_cancel, drawn_after_cancel))
    check("Esc returns the dragged window to its exact origin",
          (home_before["x"], home_before["y"]) == (home_after["x"], home_after["y"]),
          (home_before, home_after))

    stable_before_exit = {name: card_scene(name) for name in ("rail-a", "rail-b", "rail-arrive")}
    t.launch("rail-back", rail=None)
    start_drag(t.app("rail-back"), edge_right, 230)
    t.move(width / 2, 355)
    time.sleep(0.5)
    stable_after_exit = {name: card_scene(name) for name in ("rail-a", "rail-b", "rail-arrive")}
    restored = all(abs(stable_before_exit[name]["y"] - stable_after_exit[name]["y"]) < 0.01
                   for name in stable_before_exit)
    check("dragging back out of the rail eases the audition away completely", restored,
          (stable_before_exit, stable_after_exit))
    end_drag()
    check("drag back out leaves no widget preview", t.card("rail-back") is None)

    before_along = {name: card_geometry(name) for name in ("rail-a", "rail-b", "rail-arrive")}
    neighbor_scene = card_scene("rail-b")
    start_drag(t.card("rail-a"), edge_right, neighbor_scene["y"] + neighbor_scene["height"] / 2)
    during_neighbor = card_scene("rail-b")
    check("widget dragged along its rail moves its neighbors live",
          abs(during_neighbor["y"] - neighbor_scene["y"]) > 0.5,
          (neighbor_scene, during_neighbor))
    end_drag()
    after_along = {name: card_geometry(name) for name in ("rail-a", "rail-b", "rail-arrive")}
    check("widget rail drag commits the neighbor moves", before_along != after_along,
          (before_along, after_along))

    # Seven 96 px cards fill the 672 px usable span on the 720 px headless output.
    rail_ys = [72, 168, 264, 360, 456, 552, 648]
    names = []
    for i, y in enumerate(rail_ys):
        name = f"rail-full-{i}"
        names.append(name)
        t.launch(name, rail="left", y=y)
    t.launch("rail-full-arrive", rail=None)
    start_drag(t.app("rail-full-arrive"), edge_left, height / 2)
    full_during = {name: card_scene(name) for name in names}
    in_span = all(f["y"] >= 23 and f["y"] + f["height"] <= height - 23 for f in full_during.values())
    end_drag()
    t.wait_for(lambda: t.card("rail-full-arrive") and not t.card("rail-full-arrive")["preview"])
    full_after = {name: card_scene(name) for name in names}
    real_inside = all(g["y"] >= 23 and g["y"] + g["height"] <= height - 23
                      for name in names for g in [card_geometry(name)])
    check("full rail keeps every chained card between both rail ends", in_span and real_inside,
          {"during": full_during, "after": full_after})
    check("stipc pointer updates remain bounded while the rail is full",
          bool(durations) and max(durations) < 0.25, max(durations) if durations else None)
finally:
    t.cleanup()
    print(f"WG26 rail input: {passed} passed, {failed} failed", flush=True)

raise SystemExit(bool(failed))
