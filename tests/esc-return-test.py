#!/usr/bin/env python3
"""L27/WG14: Esc while a drag is held puts the item back where the move began, in its original
form. Real stipc input (Super + button, pointer, Esc) inside an isolated tests/headless.sh
--widgets session. The result is judged by Wayfire's own view list (window-rules/list-views):
whether the widget's view exists and where it is mapped, and where the app window is; the
plugin's layout report is used only to wait for a drag to be under way.

Scenarios:
  widget-made-earlier   a window dropped on the rail becomes its widget; picked up later than a
                        finger reset (1.6 s) and held in the center, Esc returns the widget to its
                        rail spot (Mike's report, 2026-10-05; P14).
  finger-reset-chain    the same, picked up again at once (0.3 s, a finger reset): the move goes
                        on, and Esc brings back the window where the move began (WG14).
  widget-held           a widget dragged off its rail and held in the center: Esc returns it.
  window-made-earlier   a widget let go in the center becomes its window; picked up 1.6 s later,
                        Esc returns the window to where that drop put it, still a window.
  window-held           a window stays a window and goes back where it was picked up.
"""
import importlib.util
import json
from pathlib import Path
import sys
import time

spec = importlib.util.spec_from_file_location("widget_input", Path(__file__).with_name("widget-input-test.py"))
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)
ipc, W, H = t.ipc, t.screen["width"], t.screen["height"]
passed = failed = 0
CHAIN_MS = 2.5          # WG14: a re-grab of the same form within this continues the move
FINGER_RESET = .3       # an immediate re-grab, as when fingers reset on a touchpad
LATER = 1.6             # picked up deliberately, later than a finger reset


def check(name, ok, detail=None):
    global passed, failed
    passed += bool(ok)
    failed += not ok
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else ": " + json.dumps(detail, default=str)), flush=True)


def views(name):
    """Wayfire's view list: the app window titled `name`, and its widget's view."""
    found = {"window": None, "widget": None}
    for v in ipc.call("window-rules/list-views"):
        if v.get("title") == name:
            found["window"] = v
        elif v.get("title") == "Scottland widget: " + name and v.get("mapped"):
            found["widget"] = v
    return found


def at(view, geometry, slack=1):
    g = view and view["geometry"]
    return bool(g) and all(abs(g[k] - geometry[k]) <= slack for k in ("x", "y", "width", "height"))


def dragging():
    return ipc.call("scottland/test-input").get("dragging")


def press_on(view):
    g = view["geometry"]
    x, y = g["x"] + g["width"] / 2, g["y"] + g["height"] / 2
    t.move(x, y)
    t.key("LEFTMETA", True)
    ipc.call("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "press"})
    return x, y


def glide(x0, y0, x1, y1, steps=20):
    for i in range(1, steps + 1):
        t.move(x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps)
        time.sleep(.016)  # paces the gesture


def release():
    ipc.call("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "release"})
    t.key("LEFTMETA", False)


def esc_while_held():
    t.wait_for(dragging, timeout=3)
    t.key("ESC", True)
    t.key("ESC", False)
    release()


def drop_window_on_rail(name):
    """Launch `name` as a window, drag it onto the right rail and let go there; return the
    window's geometry where the move began and the widget view's docked geometry."""
    t.launch(name, rail=None)
    home = t.wait_for(lambda: views(name)["window"])["geometry"]
    x, y = press_on(views(name)["window"])
    glide(x, y, W - 6, 320)
    release()
    docked = t.wait_for(lambda: views(name)["widget"] and t.card(name) and not t.card(name)["preview"] and
                        views(name)["widget"])
    return home, docked["geometry"]


def settled(predicate, timeout=4):
    """The newest state satisfying `predicate`, or the last one seen when the deadline expires."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last[0]:
            return last
        time.sleep(.03)
    return last


def scenario(fn):
    name = fn.__name__.replace("_", "-")
    if selected and name not in selected:
        return
    try:
        fn()
    except AssertionError as error:
        check(name + " ran to its result", False, str(error))
    finally:
        release()
        t.cleanup()
        time.sleep(.3)  # paces the next scenario's launch after the closes


def widget_made_earlier():
    name = "esc-made-earlier"
    home, docked = drop_window_on_rail(name)
    time.sleep(LATER)  # intended hold: a deliberate grab, later than a finger reset, within 2.5 s
    x, y = press_on(views(name)["widget"])
    glide(x, y, W / 2, H / 2)
    esc_while_held()
    ok, now = settled(lambda: (lambda v: (at(v["widget"], docked), v))(views(name)))
    check("widget-made-earlier: Esc returns the widget to its rail spot, still a widget", ok,
          {"docked": docked, "views": now, "window_began_at": home})


def finger_reset_chain():
    name = "esc-chain"
    home, docked = drop_window_on_rail(name)
    time.sleep(FINGER_RESET)  # intended: an immediate re-grab, as when fingers reset
    x, y = press_on(views(name)["widget"])
    glide(x, y, W / 2, H / 2, steps=10)
    esc_while_held()
    ok, now = settled(lambda: (lambda v: (v["widget"] is None and v["window"] is not None and
                                          abs(v["window"]["geometry"]["x"] - home["x"]) <= 1 and
                                          abs(v["window"]["geometry"]["y"] - home["y"]) <= 1, v))(views(name)))
    check("finger-reset-chain: Esc brings back the window where the move began", ok,
          {"began_at": home, "views": now})


def widget_held():
    name = "esc-held"
    t.launch(name, rail="right", y=320)
    time.sleep(CHAIN_MS + .2)  # intended: past the setup drop's re-grab chain
    docked = views(name)["widget"]["geometry"]
    x, y = press_on(views(name)["widget"])
    glide(x, y, W / 2, H / 2)
    time.sleep(.5)  # intended hold in the center
    esc_while_held()
    ok, now = settled(lambda: (lambda v: (at(v["widget"], docked), v))(views(name)))
    check("widget-held: Esc returns a widget held in the center to its rail spot", ok,
          {"docked": docked, "views": now})


def window_made_earlier():
    name = "esc-window-earlier"
    t.launch(name, rail="right", y=320)
    time.sleep(CHAIN_MS + .2)  # intended: past the setup drop's re-grab chain
    x, y = press_on(views(name)["widget"])
    glide(x, y, W / 2, H / 2)
    release()
    dropped = t.wait_for(lambda: views(name)["widget"] is None and views(name)["window"])["geometry"]
    time.sleep(LATER)  # intended: picked up again later than a finger reset
    x, y = press_on(views(name)["window"])
    glide(x, y, x - 150, y + 60)
    esc_while_held()
    ok, now = settled(lambda: (lambda v: (v["widget"] is None and v["window"] is not None and
                                          abs(v["window"]["geometry"]["x"] - dropped["x"]) <= 1 and
                                          abs(v["window"]["geometry"]["y"] - dropped["y"]) <= 1, v))(views(name)))
    check("window-made-earlier: Esc returns the window to where its drop put it, still a window", ok,
          {"dropped_at": dropped, "views": now})


def window_held():
    name = "esc-window"
    t.launch(name, rail=None)
    home = t.wait_for(lambda: views(name)["window"])["geometry"]
    x, y = press_on(views(name)["window"])
    glide(x, y, x + 200, y + 60, steps=12)
    esc_while_held()
    ok, now = settled(lambda: (lambda v: (v["widget"] is None and
                                          abs(v["window"]["geometry"]["x"] - home["x"]) <= 1 and
                                          abs(v["window"]["geometry"]["y"] - home["y"]) <= 1, v))(views(name)))
    check("window-held: Esc returns a window to where it was picked up, still a window", ok,
          {"home": home, "views": now})


selected = set(sys.argv[1:])
try:
    ipc.call("wayfire/set-config-options", {"scottland/sounds": False})
    for fn in (widget_made_earlier, finger_reset_chain, widget_held, window_made_earlier, window_held):
        scenario(fn)
finally:
    release()
    t.cleanup()
    print(f"L27/WG14 Esc return: {passed} passed, {failed} failed", flush=True)

raise SystemExit(bool(failed))
