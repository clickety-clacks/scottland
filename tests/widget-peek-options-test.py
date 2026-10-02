#!/usr/bin/env python3
"""WG19 timing options exercised with real pointer input in an isolated widget session."""
import importlib.util
from pathlib import Path
import time

spec = importlib.util.spec_from_file_location("widget_input", Path(__file__).with_name("widget-input-test.py"))
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)


def link():
    return next(w for w in t.widgets() if w["title"] == "Timed peek")


def over_card():
    frame = t.card("Timed peek")["frame"]
    t.move(frame["x"] + frame["width"] / 2, frame["y"] + frame["height"] / 2)


try:
    t.ipc.call("wayfire/set-config-options", {
        "scottland/sounds": False,
        "scottland/widget_peek_enter_delay": 500,
        "scottland/widget_peek_leave_delay": 400,
        "scottland/widget_attention_peek_duration": 800,
    })
    t.launch("Timed peek", rail="right", y=350)
    t.move(640, 50)
    t.toggle()
    t.wait_for(lambda: link()["collapsed"])
    time.sleep(.5)

    over_card()
    time.sleep(.18)
    assert not link()["peek"], "500 ms hover intent did not wait"
    print("PASS nondefault hover intent waits for real pointer", flush=True)
    t.wait_for(lambda: link()["peek"])
    print("PASS nondefault hover intent expands widget", flush=True)

    t.move(640, 50)
    time.sleep(.15)
    assert link()["peek"], "400 ms hover leave did not wait"
    t.wait_for(lambda: not link()["peek"])
    print("PASS nondefault leave delay holds then collapses", flush=True)

    t.launch("Timed peek focus", rail=None)
    frame = t.app("Timed peek focus")["frame"]
    t.move(frame["x"] + frame["width"] / 2, frame["y"] + frame["height"] / 2)
    t.ipc.call("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "press"})
    t.ipc.call("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "release"})
    t.move(640, 50)
    assert t.ipc.call("window-rules/get-focused-view")["info"]["id"] == t.app("Timed peek focus")["id"]
    t.ipc.call("scottland/attention", {"window": t.app("Timed peek")["id"],
        "attention": True, "source": "peek-options-test"})
    t.wait_for(lambda: link()["peek"])
    time.sleep(.3)
    assert link()["peek"], "800 ms attention peek ended early"
    t.wait_for(lambda: not link()["peek"], timeout=2)
    print("PASS nondefault attention duration expires", flush=True)
finally:
    t.cleanup()
