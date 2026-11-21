#!/usr/bin/env python3
"""Imported non-consuming shortcuts run actions and pass real key input to the focused client.

The test loads the pinned Omarchy input-method binding and a fixture binding through the real
importer, then uses stipc input with GTK and terminal clients in an isolated headless session.
"""
import json
import shlex
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from omarchy_fixture import REPO, Checks, Fixture, OMARCHY, Session  # noqa: E402

check = Checks()
root = REPO / "build/omarchy-non-consuming-fixture"
terminal_byte = root / "terminal-byte.txt"
fixture = Fixture(
    root,
    modules=["default.hypr.bindings.input-method"],
    recorders=["fcitx5-remote", "mark"],
    lua=f'''
o.window("foot", {{tag = "terminal"}})
hl.bind("CTRL + F11", function() os.execute("{root}/bin/mark non-consuming") end,
        {{description = "Pass-through fixture", non_consuming = true}})
hl.bind("SUPER + ENTER", function() os.execute("{root}/bin/mark command-collision") end,
        {{description = "Pass-through command collision", non_consuming = true}})
hl.bind("CTRL + F12", function() os.execute("{root}/bin/mark consuming") end,
        {{description = "Consuming fixture"}})
''',
)


def view(session, title):
    return next((item for item in session.ipc("scottland/layout-state")["views"]
                 if item.get("title") == title), None)


def focus(session, title):
    window = view(session, title)
    assert window, f"missing focused-client test window: {title}"
    frame = window["frame"]
    x = int(frame["x"] + frame["width"] / 2)
    y = int(frame["y"] + frame["height"] / 2)
    session.ipc("stipc/move_cursor", {"x": x, "y": y})
    session.ipc("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "press"})
    session.ipc("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "release"})
    def focused_title():
        active = active_title(session)
        return active if active == title else None

    focused, active = session.wait(focused_title, timeout=3)
    assert focused and active == title, f"focused-client click did not focus {title}: {active}"


def active_title(session):
    active = session.hyprctl("-j", "activewindow")
    try:
        return json.loads(active.stdout).get("title")
    except (json.JSONDecodeError, AttributeError):
        return None


def chord(session, key, modifier="KEY_LEFTCTRL"):
    session.key(modifier, True)
    session.key(key, True)
    time.sleep(0.08)
    session.key(key, False)
    session.key(modifier, False)
    time.sleep(0.15)


def key_events(path, keycode):
    if not path.is_file():
        return []
    return [event for line in path.read_text().splitlines()
            if (event := json.loads(line)).get("keycode") == keycode]


def no_key_events(path, keycode, after=0, duration=0.5):
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        events = key_events(path, keycode)[after:]
        if events:
            return False, events
        time.sleep(0.025)
    return True, []


def no_call(name, args, count_before=0, duration=0.5):
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        calls = fixture.calls()
        if calls.count((name, args)) > count_before:
            return False, calls
        time.sleep(0.025)
    return True, fixture.calls()


def call_count(name, args):
    return fixture.calls().count((name, args))


scottland_commit = subprocess.run(
    ["git", "-C", str(REPO), "rev-parse", "HEAD"],
    check=True, capture_output=True, text=True).stdout.strip()
print(f"Scottland source: {scottland_commit}", flush=True)
omarchy_commit = subprocess.run(
    ["git", "-C", str(OMARCHY), "rev-parse", "HEAD"],
    check=True, capture_output=True, text=True).stdout.strip()
print(f"Omarchy binding source: {omarchy_commit}", flush=True)
print(subprocess.run(["python3", "--version"], check=True,
                     capture_output=True, text=True).stdout.strip(), flush=True)
for package in ("wayfire", "foot", "gtk4", "gtk4-layer-shell", "lua", "wayland"):
    result = subprocess.run(["pacman", "-Q", package], check=True, capture_output=True, text=True)
    print(result.stdout.strip(), flush=True)


with Session(fixture, "omarchy-non-consuming") as session:
    client_log = root / "gtk-client.log"
    client_pid = session.spawn(
        f"exec env LD_PRELOAD=libgtk4-layer-shell.so python3 "
        f"{REPO / 'tests/key-layer-app.py'} {root}",
        client_log,
    )
    try:
        ready, _ = session.wait(lambda: view(session, "KL-one"), timeout=15)
        check("real GTK focused client starts", ready, client_log.read_text() if client_log.exists() else "")
        if ready:
            focus(session, "KL-one")
            gtk_keys = root / "keys.jsonl"

            before = len(key_events(gtk_keys, 95))
            chord(session, "KEY_F11")
            action_ran, calls = fixture.wait_calls(
                lambda current: ("mark", "non-consuming") in current)
            received = session.wait(lambda: len(key_events(gtk_keys, 95)) >= before + 2, timeout=3)[0]
            f11 = key_events(gtk_keys, 95)[before:]
            check("imported non-consuming action runs", action_ran, calls)
            check("non-consuming chord reaches focused client on both edges",
                  received and [event["edge"] for event in f11] == ["down", "up"] and
                  all(event["mods"] & 4 for event in f11), f11)

            before = len(key_events(gtk_keys, 36))
            chord(session, "KEY_ENTER", "KEY_LEFTMETA")
            collision_action_ran, calls = fixture.wait_calls(
                lambda current: ("mark", "command-collision") in current)
            collision_received = session.wait(
                lambda: len(key_events(gtk_keys, 36)) >= before + 2, timeout=3)[0]
            collision = key_events(gtk_keys, 36)[before:]
            check("non-consuming action runs on Scottland's terminal-default chord",
                  collision_action_ran, calls)
            check("non-consuming terminal-default collision passes both key edges",
                  collision_received and [event["edge"] for event in collision] == ["down", "up"] and
                  all(event["mods"] & 64 for event in collision), collision)

            before = len(key_events(gtk_keys, 96))
            chord(session, "KEY_F12")
            action_ran, calls = fixture.wait_calls(
                lambda current: ("mark", "consuming") in current)
            stayed_consumed, f12 = no_key_events(gtk_keys, 96, before)
            check("consuming action still runs", action_ran, calls)
            check("consuming chord stays out of focused client", stayed_consumed, f12)

            before = len(key_events(gtk_keys, 65))
            fcitx_before = call_count("fcitx5-remote", "--check -c")
            chord(session, "KEY_SPACE")
            space_received = session.wait(
                lambda: len(key_events(gtk_keys, 65)) >= before + 2, timeout=3)[0]
            space = key_events(gtk_keys, 65)[before:]
            no_input_method_action, calls = no_call("fcitx5-remote", "--check -c", fcitx_before)
            check("Ctrl+Space outside a terminal passes through without switching input mode",
                  space_received and [event["edge"] for event in space] == ["down", "up"] and
                  all(event["mods"] & 4 for event in space) and no_input_method_action,
                  {"events": space, "calls": calls})
    finally:
        alive = session.terminate(client_pid)
        check("GTK focused client stops", not alive, alive)

    terminal_command = (
        "stty -echo -icanon min 1 time 0; "
        f"dd bs=1 count=1 2>/dev/null | od -An -tx1 -N1 >{shlex.quote(str(terminal_byte))}; "
        "sleep 2"
    )
    terminal_pid = session.spawn(
        f"exec foot --app-id=foot --title='terminal input test' sh -c {shlex.quote(terminal_command)}",
        root / "terminal.log",
    )
    try:
        terminal_ready, _ = session.wait(lambda: view(session, "terminal input test"), timeout=15)
        check("real terminal client starts", terminal_ready)
        if terminal_ready:
            focus(session, "terminal input test")
            active = session.hyprctl("-j", "activewindow")
            try:
                tags = json.loads(active.stdout).get("tags", [])
            except (json.JSONDecodeError, AttributeError):
                tags = []
            is_terminal = any(str(tag).removesuffix("*") == "terminal" for tag in tags)
            check("focused terminal receives Omarchy terminal tag", is_terminal, tags)
            if is_terminal:
                chord(session, "KEY_SPACE")
                action_ran, calls = fixture.wait_calls(
                    lambda current: ("fcitx5-remote", "--check -c") in current)
                byte_received = session.wait(
                    lambda: terminal_byte.is_file() and terminal_byte.read_text().strip() == "00",
                    timeout=5)[0]
                check("terminal input-method action runs", action_ran, calls)
                check("Ctrl+Space byte reaches the focused terminal", byte_received,
                      terminal_byte.read_text() if terminal_byte.exists() else "no byte captured")
    finally:
        alive = session.terminate(terminal_pid)
        check("terminal client stops", not alive, alive)

sys.exit(check.summary())
