#!/usr/bin/env python3
"""Omarchy global actions reach their shell commands through imported Scottland bindings."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from omarchy_fixture import REPO, Checks, Fixture, Session  # noqa: E402

check = Checks()
scratch = Path(os.environ.get("SCOTTLAND_TEST_SCRATCH", REPO / "build"))
root = scratch / "omarchy-global-actions-fixture"
fixture = Fixture(
    root,
    helpers=False,
    recorders=("omarchy-menu", "omarchy-shell", "omarchy-audio-output-volume",
               "omarchy-brightness-display"),
    lua='''
hl.bind("SUPER + CTRL + SHIFT + SPACE", hl.dsp.global("omarchy:menu.theme"),
        { description = "Theme picker" })
hl.bind("CTRL + ALT + F6", hl.dsp.global("omarchy:panel.omarchy.emojis"))
hl.bind("CTRL + ALT + F7", hl.dsp.global("omarchy:audio.raise"))
hl.bind("CTRL + ALT + F8", hl.dsp.global("omarchy:brightness.lower"))
hl.bind("CTRL + ALT + F9", hl.dsp.global("omarchy:ipc.notifications.showHistory"))
hl.bind("CTRL + ALT + F10", hl.dsp.global("omarchy:ipc.media.playPause"))
hl.bind("CTRL + ALT + F11", hl.dsp.global("omarchy:unknown.action"))
''',
)


def press_chord(session, modifiers, key):
    for modifier in modifiers:
        session.key(modifier, True)
    session.tap(key)
    for modifier in reversed(modifiers):
        session.key(modifier, False)


with Session(fixture, "omarchy-global-actions") as session:
    cases = [
        ("Super+Ctrl+Shift+Space opens the theme picker",
         ["KEY_LEFTMETA", "KEY_LEFTCTRL", "KEY_LEFTSHIFT"], "KEY_SPACE",
         ("omarchy-menu", "toggle theme")),
        ("Omarchy emoji panel action reaches its shell operation",
         ["KEY_LEFTCTRL", "KEY_LEFTALT"], "KEY_F6",
         ("omarchy-shell", "shell toggle omarchy.emojis")),
        ("audio global action reaches its shell operation",
         ["KEY_LEFTCTRL", "KEY_LEFTALT"], "KEY_F7",
         ("omarchy-audio-output-volume", "raise")),
        ("brightness global action reaches its shell operation",
         ["KEY_LEFTCTRL", "KEY_LEFTALT"], "KEY_F8",
         ("omarchy-brightness-display", "5%-")),
        ("notification-history global action reaches its shell IPC",
         ["KEY_LEFTCTRL", "KEY_LEFTALT"], "KEY_F9",
         ("omarchy-shell", "notifications showHistory")),
        ("media play/pause global action reaches its shell IPC",
         ["KEY_LEFTCTRL", "KEY_LEFTALT"], "KEY_F10",
         ("omarchy-shell", "media playPause")),
    ]

    for name, modifiers, key, expected in cases:
        press_chord(session, modifiers, key)
        ok, calls = fixture.wait_calls(lambda current: expected in current, timeout=5)
        check(name, ok, calls)

    report_path = session.dir / "state/scottland/omarchy-overrides.txt"
    report = report_path.read_text() if report_path.is_file() else ""
    check("untranslated global action is an explicit O20 gap",
          "Unsupported in Scottland" in report and "omarchy:unknown.action" in report,
          report)

sys.exit(check.summary())
