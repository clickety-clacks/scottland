#!/usr/bin/env python3
"""Filter/latch behavior in an already-started private Scottland headless session."""
import json
import os
import socket
import struct
import subprocess
import time


def ipc(method, data=None):
    with socket.socket(socket.AF_UNIX) as peer:
        peer.settimeout(5)
        peer.connect(os.environ["WAYFIRE_SOCKET"])
        body = json.dumps({"method": method, "data": data or {}}).encode()
        peer.sendall(struct.pack("<I", len(body)) + body)

        def read(size):
            result = b""
            while len(result) < size:
                part = peer.recv(size - len(result))
                if not part:
                    raise ConnectionError("Wayfire IPC disconnected")
                result += part
            return result

        return json.loads(read(struct.unpack("<I", read(4))[0]))


def view(title):
    return next((row for row in ipc("scottland/layout-state")["views"]
                 if row["title"] == title), None)


def until(predicate, label):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError(label)


def key(name, pressed):
    reply = ipc("stipc/feed_key", {"key": "KEY_" + name, "state": pressed})
    assert reply["result"] == "ok", reply


def tap(name):
    key(name, True)
    key(name, False)


def opacity(title):
    row = view(title)
    return row["opacity"] if row else None


children = []
try:
    for title in ("Filter Match", "Filter Other"):
        children.append(subprocess.Popen(["foot", "-T", title, "sleep", "90"],
                                         stdin=subprocess.DEVNULL,
                                         stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL))
        until(lambda: view(title), title + " did not map")
    match = "Filter Match"
    other = "Filter Other"

    tap("LEFTALT")
    until(lambda: ipc("scottland/hints")["active"], "Alt tap did not latch Window mode")
    print("PASS Alt tap latches Window mode after release", flush=True)

    tap("ENTER")
    for letter in "MATCH":
        tap(letter)
    until(lambda: opacity(other) == 0.0, "nonmatching window did not fade to zero")
    assert opacity(match) != 0.0
    print("PASS active filter keeps matching window and fades other window", flush=True)

    tap("ENTER")
    assert ipc("scottland/hints")["active"] and opacity(other) == 0.0
    print("PASS Return commits filter and retains latched hints", flush=True)

    tap("ESC")
    until(lambda: opacity(other) != 0.0, "Window mode exit did not restore opacity")
    assert not ipc("scottland/hints")["active"]
    print("PASS Escape exits latched mode and restores opacity", flush=True)

    solves = ipc("scottland/spread-state")["solves"]
    tap("LEFTALT")
    until(lambda: ipc("scottland/hints")["active"], "Gather Alt tap did not latch")
    tap("SPACE")
    until(lambda: ipc("scottland/spread-state")["solves"] > solves,
          "latched Space did not invoke Gather")
    assert ipc("scottland/hints")["active"]
    tap("ESC")
    print("PASS latched Space invokes Gather and stays in Window mode", flush=True)

    widget_title = "Filter Widget"
    children.append(subprocess.Popen(["foot", "-T", widget_title, "sleep", "90"],
                                     stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL))
    until(lambda: view(widget_title), "widget candidate did not map")
    frame = view(widget_title)["frame"]
    x = round(frame["x"] + frame["width"] / 2)
    y = round(frame["y"] + frame["height"] / 2)
    ipc("stipc/move_cursor", {"x": x, "y": y})
    key("LEFTMETA", True)
    ipc("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "press"})
    for step in range(1, 7):
        ipc("stipc/move_cursor", {"x": round(x + (10 - x) * step / 6), "y": y})
        time.sleep(0.02)
    ipc("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "release"})
    key("LEFTMETA", False)
    card = until(lambda: next((row for row in ipc("scottland/layout-state")["views"]
                               if row["widget"] and row["title"].endswith(": " + widget_title)), None),
                 "widget card did not map")

    tap("LEFTALT")
    until(lambda: ipc("scottland/hints")["active"], "widget filter Alt tap did not latch")
    tap("ENTER")
    for letter in "MATCH":
        tap(letter)
    until(lambda: next((row["opacity"] for row in ipc("scottland/layout-state")["views"]
                       if row["id"] == card["id"]), None) == 0.0,
          "nonmatching widget did not fade to zero")
    tap("ESC")
    until(lambda: next((row["opacity"] for row in ipc("scottland/layout-state")["views"]
                       if row["id"] == card["id"]), None) != 0.0,
          "cancel did not restore widget opacity")
    tap("ESC")
    assert not ipc("scottland/hints")["active"]
    print("PASS nonmatching widget fades and cancel/exit restores it", flush=True)

    ipc("wm-actions/set-fullscreen", {"view_id": view(other)["id"], "state": True})
    until(lambda: view(other) and "frame" not in view(other), "other did not enter fullscreen")
    tap("LEFTALT")
    until(lambda: ipc("scottland/hints")["active"], "fullscreen Alt tap did not latch")
    tap("ENTER")
    for letter in "MATCH":
        tap(letter)
    until(lambda: opacity(other) == 0.0, "nonmatching fullscreen window did not fade")
    print("PASS nonmatching fullscreen window fades to zero", flush=True)

    tap("ESC")
    until(lambda: opacity(other) == 1.0, "cancel did not restore fullscreen opacity")
    assert ipc("scottland/hints")["active"]
    tap("ESC")
    assert not ipc("scottland/hints")["active"] and opacity(other) == 1.0
    print("PASS filter cancel and mode exit restore fullscreen opacity", flush=True)
finally:
    for child in children:
        child.terminate()
    for child in children:
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
