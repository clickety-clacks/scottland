#!/usr/bin/env python3
"""Widget regressions inside tests/headless.sh run, using real stipc input.

Run in an otherwise empty --widgets session. Optional arguments select cases.
The caller owns the headless session; this test closes only windows it launches.
"""
import argparse
import ast
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import re
import os
from pathlib import Path
import select
import socket
import struct
import subprocess
import sys
import tempfile
import time


class Ipc:
    def __init__(self):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.connect(os.environ["WAYFIRE_SOCKET"])
        self.sock.settimeout(5)

    def receive(self):
        def read(n):
            data = b""
            while len(data) < n:
                chunk = self.sock.recv(n - len(data))
                if not chunk:
                    raise ConnectionError("compositor disconnected")
                data += chunk
            return data
        return json.loads(read(struct.unpack("<I", read(4))[0]))

    def call(self, method, data=None):
        body = json.dumps({"method": method, "data": data or {}}).encode()
        self.sock.sendall(struct.pack("<I", len(body)) + body)
        reply = self.receive()
        if isinstance(reply, dict) and "error" in reply:
            raise RuntimeError(reply)
        return reply


ipc = Ipc()
passes = failures = 0
owned = []


def check(name, ok, detail=""):
    global passes, failures
    if ok:
        passes += 1
        print(f"PASS  {name}", flush=True)
    else:
        failures += 1
        print(f"FAIL  {name}: {detail}", flush=True)


def wait_for(fn, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = fn()
        if result:
            return result
        time.sleep(0.025)
    raise AssertionError("timed out waiting for " + repr(fn))


def views():
    return ipc.call("scottland/layout-state")["views"]


def app(title):
    return next((v for v in views() if v["title"] == title and not v["widget"]), None)


def card(title):
    return next((v for v in views() if v["widget"] and v["title"].endswith(": " + title)), None)


def widgets():
    return ipc.call("scottland/widgets")["widgets"]


def minimized(title):
    return next(w["minimized"] for w in widgets() if w["title"] == title)


def key(name, down):
    ipc.call("stipc/feed_key", {"key": "KEY_" + name, "state": down})


def view_geometry(identifier):
    return next(v["geometry"] for v in ipc.call("window-rules/list-views")
                if v["id"] == identifier)


def return_behavior():
    marker_root = Path(os.environ["SCOTTLAND_TEST_STATE"])
    text_marker = marker_root / "wg25-return-text"
    claim_marker = marker_root / "wg25-return-claim"
    claim_other_marker = marker_root / "wg25-return-claim-other"
    for marker in (text_marker, claim_marker, claim_other_marker):
        marker.unlink(missing_ok=True)

    title = "return-default"
    launch(title, rail=None)
    opened = app(title)
    before = view_geometry(opened["id"])
    drag_begin(opened, screen["width"] - 6, 270)
    drag_end()
    wait_for(lambda: card(title) and not card(title)["preview"])
    key("ENTER", True)
    key("ENTER", False)
    wait_for(lambda: app(title) and not app(title)["widgetized"])
    restored = app(title)
    after = view_geometry(restored["id"])
    before_center = (before["x"] + before["width"] / 2, before["y"] + before["height"] / 2)
    after_center = (after["x"] + after["width"] / 2, after["y"] + after["height"] / 2)
    wait_for(lambda: ipc.call("window-rules/get-focused-view")["info"]["id"] == restored["id"])
    focused_id = ipc.call("window-rules/get-focused-view")["info"]["id"]
    check("WG25 Return opens the default card at its remembered center, raised and focused",
          not restored["widgetized"] and
          max(abs(a - b) for a, b in zip(before_center, after_center)) < 4 and
          focused_id == restored["id"],
          {"before_center": before_center, "after_center": after_center,
           "focused_id": focused_id, "window_id": restored["id"]})

    # Keypad Enter is the same activation and uses the same WG17 placement path.
    drag_begin(restored, screen["width"] - 6, 300)
    drag_end()
    wait_for(lambda: card(title) and not card(title)["preview"])
    key("KPENTER", True)
    key("KPENTER", False)
    wait_for(lambda: app(title) and not app(title)["widgetized"])
    check("WG25 keypad Enter opens the focused default card", not app(title)["widgetized"])

    title = "return-custom"
    launch(title, app_id="scottland-test-return-plain")
    wait_for(lambda: ipc.call("window-rules/get-focused-view")["info"]["id"] ==
             next(w for w in widgets() if w["title"] == title)["widget_view"])
    key("ENTER", True)
    key("ENTER", False)
    wait_for(lambda: app(title) and not app(title)["widgetized"])
    check("WG25 Return also opens a custom widget without a text field",
          card(title) is None and not app(title)["widgetized"])

    title = "return-text"
    launch(title, app_id="scottland-test-return-text")
    wait_for(lambda: ipc.call("window-rules/get-focused-view")["info"]["id"] ==
             next(w for w in widgets() if w["title"] == title)["widget_view"])
    f = card(title)["frame"]
    move(f["x"] + f["width"] / 2, f["y"] + f["height"] / 2)
    time.sleep(.1)
    ipc.call("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "full"})
    time.sleep(.4)  # let GTK process the pointer click and focus the entry
    key("ENTER", True)
    key("ENTER", False)
    wait_for(lambda: app(title) and not app(title)["widgetized"])
    check("WG25 a focused text field does not receive Return and its window opens",
          card(title) is None and not app(title)["widgetized"] and not text_marker.exists(),
          {"card_present": card(title) is not None, "widgetized": app(title)["widgetized"],
           "text_field_activated": text_marker.exists()})

    title = "return-claim"
    launch(title, app_id="scottland-test-return-claim")
    row = wait_for(lambda: next((w for w in widgets() if w["title"] == title and
                                 w["widget_view"] >= 0), None))
    wait_for(lambda: ipc.call("window-rules/get-focused-view")["info"]["id"] == row["widget_view"])
    ipc.call("scottland/key-layer", {"action": "set", "window": row["widget_view"],
                                     "keys": ["0:Return"]})
    layer = wait_for(lambda: next((s for s in ipc.call("scottland/key-layer", {"action": "list"})["surfaces"]
                                   if s["window"] == row["widget_view"] and s["active"]), None))
    key("SPACE", True)
    key("SPACE", False)
    wait_for(lambda: claim_other_marker.exists())
    check("WG25 other unclaimed keys still reach a widget with a Return claim",
          card(title) is not None and app(title)["widgetized"] and claim_other_marker.exists(),
          {"card_present": card(title) is not None, "widgetized": app(title)["widgetized"],
           "received_space": claim_other_marker.exists()})
    key("ENTER", True)
    key("ENTER", False)
    wait_for(lambda: app(title) and not app(title)["widgetized"])
    check("WG25 a widget's Return key-layer claim is ignored and the window opens",
          card(title) is None and not app(title)["widgetized"] and not claim_marker.exists(),
          {"layer_active": layer["active"], "card_present": card(title) is not None,
           "widgetized": app(title)["widgetized"], "received_return": claim_marker.exists()})

    # The keypad key is intercepted too, including when the widget explicitly claims it.
    drag_begin(app(title), screen["width"] - 6, 340)
    drag_end()
    wait_for(lambda: card(title) and not card(title)["preview"])
    row = wait_for(lambda: next((w for w in widgets() if w["title"] == title and
                                 w["widget_view"] >= 0), None))
    ipc.call("scottland/key-layer", {"action": "set", "window": row["widget_view"],
                                     "keys": ["0:KP_Enter"]})
    layer = wait_for(lambda: next((s for s in ipc.call("scottland/key-layer", {"action": "list"})["surfaces"]
                                   if s["window"] == row["widget_view"] and s["active"]), None))
    key("KPENTER", True)
    key("KPENTER", False)
    wait_for(lambda: app(title) and not app(title)["widgetized"])
    check("WG25 a widget's keypad Enter claim is ignored and the window opens",
          card(title) is None and not app(title)["widgetized"] and not claim_marker.exists(),
          {"layer_active": layer["active"], "card_present": card(title) is not None,
           "widgetized": app(title)["widgetized"], "received_keypad_enter": claim_marker.exists()})


def toggle():
    key("LEFTMETA", True)
    key("M", True)
    key("M", False)
    key("LEFTMETA", False)


def move(x, y):
    ipc.call("stipc/move_cursor", {"x": round(x), "y": round(y)})


def drag_begin(view, x, y):
    f = view["frame"]
    sx, sy = f["x"] + f["width"] / 2, f["y"] + f["height"] / 2
    move(sx, sy)
    time.sleep(0.1)
    key("LEFTMETA", True)
    ipc.call("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "press"})
    for i in range(1, 21):
        move(sx + (x - sx) * i / 20, sy + (y - sy) * i / 20)
        time.sleep(0.025)
    time.sleep(0.4)


def drag_end():
    ipc.call("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "release"})
    key("LEFTMETA", False)
    time.sleep(0.5)


screen = ipc.call("window-rules/list-outputs")[0]["geometry"]


def launch(title, rail="right", y=260, app_id="foot"):
    process = subprocess.Popen(["foot", "--app-id", app_id, "-T", title, "-W", "40x8", "sh", "-c", "exec sleep 600"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    owned.append((title, process))
    view = wait_for(lambda: app(title))
    time.sleep(0.4)
    if rail:
        drag_begin(app(title), screen["width"] - 6 if rail == "right" else 6, y)
        drag_end()
        wait_for(lambda: card(title) and not card(title)["preview"])
        time.sleep(0.7)
    return view


def cleanup():
    for name in ("M", "TAB", "LEFTALT", "LEFTMETA", "LEFTSHIFT", "LEFTCTRL"):
        key(name, False)
    for title, process in owned:
        view = app(title)
        if view:
            ipc.call("window-rules/close-view", {"id": view["id"]})
        try:
            process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            process.terminate()  # only our recorded child PID
            process.wait(timeout=4)
    owned.clear()
    time.sleep(0.5)


def peeking():
    """WG19: production triggers, real pointer/keys and per-source attention IPC."""
    def away():
        move(screen["width"] / 2, 50)

    def link(title):
        return next(w for w in widgets() if w["title"] == title)

    def over(title, handle=False):
        f = card(title)["frame"]
        x = f["x"] + f["width"] / 2
        if handle:
            x = f["x"] - 4 if link(title)["rail"] == "right" else f["x"] + f["width"] + 4
        move(x, f["y"] + f["height"] / 2)

    def shown(title, expanded):
        wait_for(lambda: link(title)["peek"] == expanded and
                 (card(title)["frame"]["width"] > 120 if expanded else
                  abs(card(title)["frame"]["width"] - 96) < 1))
        check("WG19 expanded" if expanded else "WG19 collapsed",
              link(title)["collapsed"] and link(title)["minimized"] != expanded and
              ipc.call("scottland/desktop-model")["collapsed"], link(title))

    for rail in ("left", "right"):
        title = "peek-" + rail
        launch(title, rail=rail, y=240 if rail == "left" else 430)
        away()
        if not link(title)["collapsed"]:
            toggle()
        time.sleep(.6)
        over(title)
        time.sleep(.04)
        check("WG19 rail sweep does not expand " + rail, not link(title)["peek"])
        away()
        time.sleep(.25)
        check("WG19 leaving before hover intent cancels " + rail, not link(title)["peek"])
        over(title)
        shown(title, True)
        time.sleep(.4)
        observed = json.loads(ast.literal_eval(subprocess.check_output([
            "gdbus", "call", "--session", "--dest", "org.scottland.Widgets",
            "--object-path", "/org/scottland/Widgets", "--method",
            "org.scottland.Diagnostics.Snapshot"], text=True))[0])
        audit = ipc.call("scottland/audit-model", {"service": observed})
        check("WG19 model, service and card text agree while peeking " + rail, audit["ok"], audit)
        if args.log:
            artifacts = args.log.parent.with_name(args.log.parent.name + ".results")
            artifacts.mkdir(parents=True, exist_ok=True)
            subprocess.run(["grim", str(artifacts / (title + ".png"))], check=True)
        # Cross into the newly revealed title area (outside the original icon).
        f = card(title)["frame"]
        move(f["x"] + (f["width"] - 25 if rail == "left" else 25), f["y"] + f["height"] / 2)
        time.sleep(.25)
        check("WG19 revealed title retains hover " + rail, link(title)["peek"])
        away(); time.sleep(.03); over(title)
        time.sleep(.2)
        check("WG19 brief leave is absorbed by hysteresis " + rail, link(title)["peek"])
        over(title, handle=True)
        time.sleep(.3)
        check("WG19 goo/handle retains hover " + rail, link(title)["peek"])
        away()
        shown(title, False)
        over(title, handle=True)
        shown(title, True)
        away()
        shown(title, False)

    # Give another ordinary app focus: hover must neither focus nor answer attention.
    launch("peek-focus", rail=None)
    away()
    title = "peek-right"
    window = app(title)["id"]
    focus = ipc.call("window-rules/get-focused-view")["info"]["id"]
    def attention(on=True, source="peek-test"):
        ipc.call("scottland/attention", {"window": window, "attention": on, "source": source})

    attention()
    started = time.monotonic()
    shown(title, True)
    time.sleep(max(0, 4.7 - (time.monotonic() - started)))
    check("WG19 attention remains expanded before five seconds", link(title)["peek"])
    wait_for(lambda: not link(title)["peek"], timeout=1)
    elapsed = time.monotonic() - started
    check("WG19 attention expires at five seconds", 4.95 <= elapsed < 5.4, elapsed)
    shown(title, False)
    check("WG19 peek expiry preserves attention and focus", link(title)["urgent"] and
          ipc.call("window-rules/get-focused-view")["info"]["id"] == focus)

    attention()  # already-raised source is still a new request
    shown(title, True)
    time.sleep(2.6)
    attention()  # the same source, still raised
    time.sleep(2.6)
    check("WG19 re-raised same-source attention restarts five seconds", link(title)["peek"])
    attention(source="peek-second")
    restarted = time.monotonic()
    time.sleep(2.6)
    check("WG19 another source restarts five seconds", link(title)["peek"])
    # Enter just before expiry; remaining hover may be shorter than the enter delay.
    time.sleep(max(0, 4.92 - (time.monotonic() - restarted)))
    over(title)
    time.sleep(.4)
    check("WG19 pointer holds attention peek after deadline", link(title)["peek"])
    away(); shown(title, False)
    attention(False); attention(False, "peek-second")

    # A grab exists before its first motion: do not expand beneath a held click.
    over(title)
    key("LEFTMETA", True)
    ipc.call("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "press"})
    time.sleep(.4)
    check("WG19 pending drag does not expand under a stationary grab", not link(title)["peek"])
    move(screen["width"] - 8, 490)
    time.sleep(.3)
    check("WG19 icon-only drag stays icon-only while held", not link(title)["peek"])
    drag_end(); away(); shown(title, False)

    # Freeze an expanded peek while a real Super drag is held on the rail.
    over(title); shown(title, True)
    drag_begin(card(title), screen["width"] - 8, 520)
    time.sleep(.35)
    check("WG19 held widget drag keeps peek and collapsed intent", link(title)["peek"] and link(title)["collapsed"])
    drag_end(); away(); shown(title, False)
    check("WG19 drag release keeps widget docked", link(title)["lifecycle"] == "docked")

    over(title); shown(title, True)
    toggle(); away(); time.sleep(.6)
    check("WG19 Super+M changes intent and ends peek", not link(title)["collapsed"] and not link(title)["peek"])
    toggle(); time.sleep(.5)
    over(title); time.sleep(.03)
    ipc.call("window-rules/close-view", {"id": window})
    time.sleep(.4)
    check("WG19 closing during hover delay leaves no stale peek", not app(title) and not card(title))
    # Leave expanded mode for the older cases that follow in the same session.
    if link("peek-left")["collapsed"]:
        toggle()


def held_key():
    title = "press-regression"
    launch(title)
    # A fresh mode for this case, without bypassing the binding under test.
    if minimized(title):
        toggle()
    if args.log:
        log_start = len(args.log.read_text())
        key("M", True)
        key("M", False)
        check("WG20 plain M typing emits no collapse diagnostics",
              "minimize-key" not in args.log.read_text()[log_start:])
        key("LEFTSHIFT", True)
        key("M", True)
        key("M", False)
        key("LEFTSHIFT", False)
        check("WG20 unrelated modifiers do not trace M typing",
              "minimize-key" not in args.log.read_text()[log_start:])
    log_start = len(args.log.read_text()) if args.log else 0
    key("LEFTMETA", True)
    key("M", True)
    check("WG20 first press activates", minimized(title))
    key("M", True)  # duplicate down from the same physical/injected key
    check("WG20 duplicate down while held does not toggle", minimized(title))
    key("LEFTMETA", False)
    key("LEFTMETA", True)
    key("M", True)
    check("WG20 releasing the modifier does not rearm M", minimized(title))
    key("M", False)
    key("M", True)
    check("WG20 releasing M rearms immediately", not minimized(title))
    key("M", False)
    # No sleeps: separate presses must survive even well below an animation duration.
    states = []
    for _ in range(4):
        key("M", True)
        states.append(minimized(title))
        key("M", False)
    check("WG20 four rapid intentional presses all activate", states == [True, False, True, False], states)
    key("LEFTMETA", False)
    if args.log:
        trace = args.log.read_text()[log_start:]
        edges = [line for line in trace.splitlines() if "minimize-key edge=" in line]
        check("WG20 diagnostics record tracked binding edges with device, time and state",
              len(edges) == 14 and all(all(field in line for field in
                  ("device=stipc_keyboard@", "time_msec=", "received_msec=", "key=50", "state=",
                   "held_devices=", "activated=", "collapsed=")) for line in edges), edges)
        check("WG20 diagnostics distinguish six activations and two ignored duplicates",
              trace.count("minimize-key activation ") == 6 and
              trace.count("minimize-key ignored-duplicate ") == 2, trace)
        log_start = len(args.log.read_text())
        key("LEFTMETA", True)
        key("M", True)
        key("LEFTMETA", False)
        key("M", False)
        trace = args.log.read_text()[log_start:]
        edges = [line for line in trace.splitlines() if "minimize-key edge=" in line]
        check("WG20 tracked M release is logged after Super is released",
              len(edges) == 2 and "state=release" in edges[-1] and "held_devices=0" in edges[-1], edges)


def gravity():
    title = "gravity-regression"
    events = Ipc()
    events.call("window-rules/events/watch", {"events": ["view-mapped", "view-geometry-changed"]})

    def drain():
        result = []
        while select.select([events.sock], [], [], 0.05)[0]:
            result.append(events.receive())
        return result

    launch(title, app_id="scottland-test-gravity")
    widget_id = card(title)["id"]
    mapped = [e["view"]["geometry"] for e in drain()
              if e["event"] == "view-mapped" and e["view"]["id"] == widget_id]
    check("WG4 widget mapping is already against its right rail",
          len(mapped) == 1 and mapped[0]["x"] + mapped[0]["width"] == screen["width"] - 24, mapped)

    def resize(label, rail, target):
        before = card(title)["frame"]
        edge = before["x"] + (before["width"] if rail == "right" else 0)
        drain()
        key("SPACE", True)
        key("SPACE", False)
        wait_for(lambda: round(card(title)["frame"]["width"]) == target)
        time.sleep(0.2)
        changes = [e["view"]["geometry"] for e in drain()
                   if e["event"] == "view-geometry-changed" and e["view"]["id"] == widget_id]
        check(label + " keeps its edge in every applied geometry",
              bool(changes) and all(abs(g["x"] + (g["width"] if rail == "right" else 0) - edge) < 1
                                    for g in changes), changes)
        check(label + " has no follow-up corrective move",
              len(changes) == 1, changes)

    resize("WG4 first right-rail resize", "right", 96)
    drag_begin(card(title), 6, 330)
    drag_end()
    resize("WG4 first resize after moving right to left", "left", 320)
    drag_begin(card(title), screen["width"] - 6, 330)
    drag_end()
    resize("WG4 first resize after moving left to right", "right", 96)
    events.sock.close()


def previews():
    anchor = "preview-anchor"
    launch(anchor, y=180)
    if minimized(anchor):
        toggle()
    for collapsed in (True, False):
        title = "preview-collapse" if collapsed else "preview-expand"
        launch(title, rail=None)
        drag_begin(app(title), screen["width"] - 6, 420)
        wait_for(lambda: card(title) and card(title)["preview"])
        check("WG16 preview is still a window before commitment", not app(title)["widgetized"])
        check("WG16 previews stay out of the ordinary widgets listing",
              not any(w["title"] == title for w in widgets()))
        key("M", True)  # Super is still held for the drag
        key("M", False)
        time.sleep(1)
        width = card(title)["frame"]["width"]
        check(f"WG16 running preview follows mode before drop ({collapsed=})",
              (round(width) == 96) if collapsed else width > 96, width)
        drag_end()
        time.sleep(0.6)
        width = card(title)["frame"]["width"]
        check(f"WG16 committed preview has current logical mode ({collapsed=})",
              minimized(title) == collapsed, minimized(title))
        check(f"WG16 committed preview card has current presentation ({collapsed=})",
              (round(width) == 96) if collapsed else width > 96, width)
        wid = app(title)["id"]
        prop = subprocess.check_output(["busctl", "--user", "get-property", "org.scottland.Widgets",
                f"/org/scottland/widget/{wid}", "org.scottland.Widget", "Minimized"], text=True).strip()
        check(f"WG16 committed preview D-Bus mode agrees ({collapsed=})",
              prop == "b " + str(collapsed).lower(), prop)


def shortcuts():
    if not args.log:
        raise AssertionError("shortcut regression needs --log to locate its private config")
    path = Path(__file__).resolve().parents[1] / "omarchy/config.d/50-omarchy-shortcuts"
    loader = importlib.machinery.SourceFileLoader("shortcut_import", str(path))
    importer = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(importer)
    config = args.log.parent / "wayfire.ini"
    original = config.read_text()
    # The session's config carries the user's real imported shortcuts (e.g. an Omarchy Shift+Super+M):
    # the fixtures must be the only imports in play, or pressing their keys runs the user's commands.
    fixture_base = re.sub(r"(?m)^(repeatable_)?(binding|command)_omarchy_\w+ = .*\n", "", original)
    launch("shortcut-regression")
    # Honor the test runner's TMPDIR so fixture artifacts need not occupy runtime tmpfs.
    with tempfile.TemporaryDirectory(prefix="scottland-shortcuts-") as work:
        work = Path(work)
        base, lua, marker = work / "base.ini", work / "hyprland.lua", work / "ran"
        importer.HYPR_CONFIG = str(lua)
        # Change only the fixture's path; scan the real Lua and run the real importer.
        importer.LUA_SCAN = importer.LUA_SCAN.replace(
            'os.getenv("HOME") .. "/.config/hypr/hyprland.lua"', json.dumps(str(lua)))

        def generate(base_text, lua_text):
            base.write_text(base_text)
            lua.write_text(lua_text)
            old_argv = sys.argv
            try:
                sys.argv = [str(path), str(base)]
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    importer.main()
                return output.getvalue()
            finally:
                sys.argv = old_argv

        try:
            for name, keys, base_text, shift in (
                ("metadata default", "SUPER+M", "[scottland]\n", False),
                ("normalized modifiers", "SHIFT+SUPER+M", "[scottland]\nminimize_widget = <super> <shift> KEY_M\n", True),
                ("multiple owners", "SUPER+M", "[command]\nbinding_fixture = <super> KEY_M\ncommand_fixture = true\n", False),
            ):
                marker.unlink(missing_ok=True)
                command = "printf hit >> " + str(marker)
                generated = generate(fixture_base + "\n" + base_text,
                                     f'hl.bind("{keys}", hl.dsp.exec_cmd({json.dumps(command)}))\n')
                check(f"O5 Scottland's feature binding keeps its keys; the import is displaced ({name})",
                      "minimize_widget = none" not in generated and "displaced" in generated, generated)
                config.write_text(fixture_base + "\n" + base_text + "\n" + generated)
                time.sleep(0.8)  # Wayfire's config file watcher
                before = minimized("shortcut-regression")
                if shift:
                    key("LEFTSHIFT", True)
                toggle()
                if shift:
                    key("LEFTSHIFT", False)
                time.sleep(0.6)
                check(f"O5 real input toggles widgets ({name})", minimized("shortcut-regression") != before)
                check(f"O5 real input does not run the displaced import ({name})", not marker.exists())
                if minimized("shortcut-regression") != before:
                    if shift:
                        key("LEFTSHIFT", True)
                    toggle()  # back as it was for the next case, with the same keys
                    if shift:
                        key("LEFTSHIFT", False)
                    time.sleep(0.4)
            remap_base = """[scottland]
remap_apps_import_regression = ^org\\.scottland\\.NoMatchingBrowserApp$
remap_from_import_ctrl_w = CTRL+W
remap_to_import_ctrl_w = CTRL+BackSpace
remap_apps_import_alt_ctrl_w = ^org\\.scottland\\.NoMatchingBrowserApp$
remap_from_import_alt_ctrl_w = CTRL+ALT+W
remap_to_import_alt_ctrl_w = CTRL+F4
"""
            imports = (
                f'hl.bind("CTRL+W", hl.dsp.exec_cmd("printf ctrlw >> {marker}"))\n'
                f'hl.bind("ALT+CTRL+W", hl.dsp.exec_cmd("printf altctrlw >> {marker}"))\n'
            )
            generated = generate(fixture_base + "\n" + remap_base, imports)
            displaced = [line for line in generated.splitlines()
                         if "displaced: Scottland uses these keys for key_remaps/" in line]
            both_combos_listed = all(any(re.search(rf"^\s*#\s*{re.escape(combo)}\s+\(displaced:", line)
                                         for line in displaced)
                                        for combo in ("CTRL+W", "ALT+CTRL+W"))
            check("O5 key_remaps displace Ctrl+W and Alt+Ctrl+W imports",
                  len(displaced) == 2 and both_combos_listed and
                  not re.search(r"(?m)^(?:repeatable_)?binding_omarchy_\w+\s*=", generated),
                  generated)
            config.write_text(fixture_base + "\n" + remap_base + "\n" + generated)
            time.sleep(0.8)  # Wayfire's config watcher

            recorder_name = "ctrlw-remap-" + str(time.monotonic_ns())
            key_log = work / "ctrlw-keys.jsonl"
            recorder = subprocess.Popen(["python3", str(Path(__file__).with_name("windowing-key-recorder.py")),
                                         recorder_name, str(key_log)],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                view = wait_for(lambda: next((v for v in ipc.call("window-rules/list-views")
                                               if v.get("title") == recorder_name), None))
                ipc.call("window-rules/focus-view", {"id": view["id"]})
                time.sleep(.2)
                key("LEFTCTRL", True)
                key("W", True); key("W", False)
                key("LEFTCTRL", False)
                key("LEFTCTRL", True); key("LEFTALT", True)
                key("W", True); key("W", False)
                key("LEFTALT", False); key("LEFTCTRL", False)
                deadline = time.monotonic() + 2
                records = []
                while time.monotonic() < deadline:
                    if key_log.exists():
                        records = [json.loads(line) for line in key_log.read_text().splitlines()]
                    plain_ctrl_w = any(event["key"].lower() == "w" and event["modifiers"] & 4
                                       and not event["modifiers"] & 8 for event in records)
                    alt_ctrl_w = any(event["key"].lower() == "w" and event["modifiers"] & 4
                                     and event["modifiers"] & 8 for event in records)
                    if plain_ctrl_w and alt_ctrl_w:
                        break
                    time.sleep(.03)
                check("O5 Ctrl+W reaches an ordinary app unchanged",
                      any(event["key"].lower() == "w" and event["modifiers"] & 4
                          and not event["modifiers"] & 8 for event in records), records)
                check("O5 Alt+Ctrl+W reaches an ordinary app unchanged",
                      any(event["key"].lower() == "w" and event["modifiers"] & 4
                          and event["modifiers"] & 8 for event in records), records)
                check("O5 displaced imports did not run their commands", not marker.exists())
            finally:
                recorder.terminate()
                recorder.wait(timeout=5)

            # Overrides are appended after generated imports, so their effective remap
            # from-combos must drive collision detection too.
            old_xdg_config = os.environ.get("XDG_CONFIG_HOME")
            override_home = work / "override-config"
            override_file = override_home / "scottland/overrides.ini"
            override_file.parent.mkdir(parents=True)
            os.environ["XDG_CONFIG_HOME"] = str(override_home)
            override_base = fixture_base + """
[scottland]
remap_apps_browser_word = ^org\\.scottland\\.NoMatchingBrowserApp$
remap_from_browser_word = CTRL+W
remap_to_browser_word = CTRL+BackSpace
remap_from_browser_close =
"""
            try:
                override_file.write_text("[scottland]\nremap_from_browser_word = ALT+CTRL+W\n")
                generated = generate(override_base, imports)
                binding_lines = [line for line in generated.splitlines()
                                 if re.match(r"^(?:repeatable_)?binding_omarchy_\w+\s*=", line)]
                check("O5 overrides reserve the effective remap combo",
                      len(binding_lines) == 1 and "= <ctrl> KEY_W" in binding_lines[0]
                      and "key_remaps/browser_word (ALT+CTRL+W)" in generated,
                      generated)
                override_file.write_text("[scottland]\nremap_from_browser_word =\n")
                generated = generate(override_base, imports)
                binding_lines = [line for line in generated.splitlines()
                                 if re.match(r"^(?:repeatable_)?binding_omarchy_\w+\s*=", line)]
                check("O5 an empty override releases its remap combo",
                      len(binding_lines) == 2 and "key_remaps/" not in generated,
                      generated)
            finally:
                if old_xdg_config is None:
                    os.environ.pop("XDG_CONFIG_HOME", None)
                else:
                    os.environ["XDG_CONFIG_HOME"] = old_xdg_config

            generated = generate("[scottland]\n", 'hl.bind("SUPER+M", function() end)\n')
            check("O5 a Lua-function import on a feature binding's keys is displaced too",
                  "minimize_widget = none" not in generated and "displaced" in generated, generated)
            generated = generate("[scottland]\nminimize_widget = <super> KEY_N\n",
                                 'hl.bind("SUPER+M", "true")\n')
            check("O5 an explicit binding replaces its metadata default during collision detection",
                  "minimize_widget =" not in generated and "displaced" not in generated, generated)
            generated = generate("[scottland]\nminimize_widget = none\n", 'hl.bind("SUPER+M", "true")\n')
            check("O5 an explicit none overrides the metadata default", "minimize_widget =" not in generated)
            generated = generate("[scottland]\n", 'hl.bind("CTRL+SUPER+M", "true")\n')
            check("O5 extra modifiers are a different shortcut", "minimize_widget =" not in generated)
            marker.unlink(missing_ok=True)
            center_imports = (
                f'hl.bind("ALT+TAB", hl.dsp.exec_cmd("printf forward >> {marker}"))\n'
                f'hl.bind("ALT+SHIFT+TAB", hl.dsp.exec_cmd("printf reverse >> {marker}"))\n'
            )
            generated = generate(fixture_base + "\n[scottland]\n", center_imports)
            check("O5 center switcher owns both Alt+Tab directions",
                  "KEY_TAB" not in '\n'.join(line for line in generated.splitlines()
                      if line.startswith(('binding_omarchy_', 'repeatable_binding_omarchy_'))) and
                  generated.count('displaced') >= 2, generated)
            config.write_text(fixture_base + "\n[scottland]\n" + generated)
            time.sleep(0.8)
            key("LEFTALT", True); key("TAB", True); key("TAB", False)
            forward = ipc.call("scottland/center-switcher")
            key("LEFTSHIFT", True); key("TAB", True); key("TAB", False)
            reverse = ipc.call("scottland/center-switcher")
            key("LEFTSHIFT", False); key("LEFTALT", False)
            check("O5 real Alt+Tab and Alt+Shift+Tab keep the center preview and never run imports",
                  forward["active"] and forward["preview"] and reverse["active"] and
                  reverse["preview"] and not marker.exists())
            close_import = 'hl.bind("SUPER+W", hl.dsp.window.close(), {description = "Close window"})\n'
            generated = generate(fixture_base + "\n[scottland]\n", close_import)
            check("O9 Super+W close is omitted instead of becoming a single-press close",
                  "close_top_view = <super> KEY_W" not in generated and
                  "O9: close remains unbound" in generated, generated)
            config.write_text(fixture_base + "\n[scottland]\n" + generated)
            time.sleep(0.8)
            key("LEFTMETA", True); key("W", True); key("W", False); key("LEFTMETA", False)
            time.sleep(0.2)
            report_path = Path(os.environ["XDG_STATE_HOME"]) / "scottland/omarchy-overrides.txt"
            report = report_path.read_text() if report_path.is_file() else ""
            check("O9 Super+W leaves the window open and explains the omission",
                  app("shortcut-regression") is not None and
                  "Was: Close window (Super+W)" in report and
                  "Scottland leaves Super+W unbound" in report, report)
            # (Keys no Scottland feature uses: on those, the user's shortcuts win over other defaults.)
            generated = generate(
                "[wm-actions]\ntoggle_fullscreen = <super> KEY_K | <super> <shift> KEY_K | <super> KEY_N\n",
                'hl.bind("SUPER+K", "true")\nhl.bind("SHIFT+SUPER+K", "true")\n')
            check("O5 multiple normalized collisions preserve only the unclaimed alternative",
                  "toggle_fullscreen = <super> KEY_N\n" in generated, generated)
        finally:
            config.write_text(original)
            time.sleep(0.8)


if __name__ == "__main__":
    cases = {"key": held_key, "gravity": gravity, "previews": previews, "shortcuts": shortcuts,
             "peek": peeking, "return": return_behavior}
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, help="this headless session's wayfire.log")
    parser.add_argument("cases", nargs="*", choices=list(cases))
    args = parser.parse_args()
    try:
        ipc.call("wayfire/set-config-options", {"scottland/sounds": False})
        for name in args.cases or cases:
            try:
                cases[name]()
            finally:
                cleanup()
    finally:
        print(f"widget input regressions: {passes} passed, {failures} failed", flush=True)
    sys.exit(bool(failures))
