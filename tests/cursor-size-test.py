#!/usr/bin/env python3
"""A16: cursor-theme image sizes, upward rounding, and session environment updates."""
from importlib.machinery import SourceFileLoader
import os
from pathlib import Path
import struct
import tempfile
from subprocess import CompletedProcess
from unittest.mock import patch

root = Path(__file__).resolve().parents[1]
core = SourceFileLoader("scottland_color_scheme", str(root / "core/libexec/scottland-color-scheme")).load_module()
passed = 0


def check(name, okay):
    global passed
    assert okay, name
    passed += 1
    print("PASS", name)


def write_cursor(path, *sizes):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload_start = 16 + len(sizes) * 12
    data = struct.pack("<4I", 0x72756358, 16, 0x00010000, len(sizes))
    position = payload_start
    for size in sizes:
        data += struct.pack("<3I", 0xfffd0002, size, position)
        position += 36 + size * size * 4
    for size in sizes:
        data += struct.pack("<9I", 36, 0xfffd0002, size, 1, size, size, 0, 0, 0)
        data += b"\0" * (size * size * 4)
    path.write_bytes(data)


with tempfile.TemporaryDirectory(prefix="scottland-cursor-test-") as temporary:
    base = Path(temporary)
    theme_root = base / "icons"
    (theme_root / "CursorChild").mkdir(parents=True)
    (theme_root / "CursorChild/index.theme").write_text("[Icon Theme]\nInherits=CursorParent\n")
    (theme_root / "CursorParent").mkdir(parents=True)
    (theme_root / "CursorParent/index.theme").write_text("[Icon Theme]\n")
    (theme_root / "default").mkdir(parents=True)
    (theme_root / "default/index.theme").write_text("[Icon Theme]\n")
    write_cursor(theme_root / "CursorChild/cursors/left_ptr", 24)
    write_cursor(theme_root / "CursorParent/cursors/left_ptr", 32, 48)
    write_cursor(theme_root / "default/cursors/left_ptr", 24, 48)
    sizes = core.cursor_theme_sizes("CursorChild", [str(theme_root)])
    check("cursor theme sizes include inherited Xcursor image sizes", sizes == {24, 32, 48})
    check("a missing named theme follows Xcursor's default-theme fallback",
          core.cursor_theme_sizes("CursorMissing", [str(theme_root)]) == {24, 48})
    check("1.6364 text scale rounds upward to the next provided cursor size", core.cursor_size_for_scale(1.6364, sizes) == 48)
    check("text scale 1 keeps the 24 px base", core.cursor_size_for_scale(1.0, sizes) == 24)
    check("a missing larger image size uses the largest the theme provides", core.cursor_size_for_scale(3.0, sizes) == 48)
    check("a theme without readable size metadata falls back to the scaled size", core.cursor_size_for_scale(1.6364, set()) == 40)

    runtime = base / "runtime"
    session_dir = runtime / "scottland"
    session_dir.mkdir(parents=True)
    env_file = session_dir / "wayland-test.env"
    env_file.write_bytes(b"WAYLAND_DISPLAY=wayland-test\0PATH=/usr/bin\0XCURSOR_SIZE=24\0")
    original_runtime = core.RUNTIME
    original_display = os.environ.get("WAYLAND_DISPLAY")
    try:
        core.RUNTIME = str(runtime)
        os.environ["WAYLAND_DISPLAY"] = "wayland-test"
        core.update_recorded_environment(48)
        entries = env_file.read_bytes().split(b"\0")
        check("live size update preserves session environment and replaces XCURSOR_SIZE",
              b"PATH=/usr/bin" in entries and entries.count(b"XCURSOR_SIZE=48") == 1
              and b"XCURSOR_SIZE=24" not in entries)
    finally:
        core.RUNTIME = original_runtime
        if original_display is None:
            os.environ.pop("WAYLAND_DISPLAY", None)
        else:
            os.environ["WAYLAND_DISPLAY"] = original_display

with patch.dict(os.environ, {}, clear=False):
    os.environ.pop("XDG_SESSION_ID", None)
    with patch.object(core.subprocess, "run", side_effect=AssertionError("no-session lookup should skip")):
        check("activation environment is not changed without a known graphical session",
              core._only_graphical_session() is False)

with patch.dict(os.environ, {"XDG_SESSION_ID": "2"}), patch.object(core.subprocess, "run") as run:
    def loginctl(command, **_kwargs):
        if command[:3] == ["loginctl", "list-sessions", "--no-legend"]:
            return CompletedProcess(command, 0, "1 1000 mike seat0 tty1 active no -\n2 1000 mike seat1 tty2 active no -", "")
        if command[:2] == ["id", "-un"]:
            return CompletedProcess(command, 0, "mike\n", "")
        return CompletedProcess(command, 0, "wayland\nactive\n", "")
    run.side_effect = loginctl
    check("shared activation environment is left alone when another GUI session is active",
          core._only_graphical_session() is False)

with patch.dict(os.environ, {"XDG_SESSION_ID": "2"}), patch.object(core.subprocess, "run") as run:
    def only_session(command, **_kwargs):
        if command[:3] == ["loginctl", "list-sessions", "--no-legend"]:
            return CompletedProcess(command, 0, "2 1000 mike seat0 tty1 active no -", "")
        return CompletedProcess(command, 0, "mike\n", "")
    run.side_effect = only_session
    check("activation environment can follow Scottland when it is the only GUI session",
          core._only_graphical_session() is True)

print(f"{passed} passed")
