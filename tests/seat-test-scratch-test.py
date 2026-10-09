#!/usr/bin/env python3
"""Safe tests for the manual seat test's unique output ownership."""

import tempfile
import unittest
from pathlib import Path

from seat_test_scratch import OwnedScratch, create_after_preflight


class OwnedScratchTests(unittest.TestCase):
    def test_refused_preflight_creates_no_scratch_and_preserves_existing_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            build = Path(temporary) / "build"
            old = build / "seat"
            old.mkdir(parents=True)
            evidence = old / "keep.png"
            evidence.write_text("existing evidence")

            lock = ("hyprland", "wayland-1", 123)
            refused = (
                (None, {"42": "Hyprland"}, "42"),
                (lock, {"42": "Wayfire"}, "42"),
                (lock, {"42": "Hyprland", "43": "Wayfire"}, "42"),
                (lock, {"42": "Hyprland"}, "43"),
            )
            for before, sessions, active in refused:
                self.assertIsNone(create_after_preflight(build, before, sessions, active))

            self.assertEqual(evidence.read_text(), "existing evidence")
            self.assertEqual(list(build.iterdir()), [old])

    def test_successful_preflight_creates_unique_owned_scratch(self):
        with tempfile.TemporaryDirectory() as temporary:
            build = Path(temporary) / "build"
            before = ("hyprland", "wayland-1", 123)
            sessions = {"42": "Hyprland"}

            first = create_after_preflight(build, before, sessions, "42")
            second = create_after_preflight(build, before, sessions, "42")

            self.assertNotEqual(first.path, second.path)
            self.assertTrue(first._owner.is_file())
            self.assertEqual(first._owner.stat().st_mode & 0o777, 0o600)

    def test_refuses_unowned_collisions_and_removes_only_allocated_outputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            scratch = OwnedScratch(Path(temporary) / "build")
            own = scratch.output("capture.ppm")
            own.write_bytes(b"owned")
            foreign = scratch.path / "foreign.ppm"
            foreign.write_bytes(b"leave this")

            with self.assertRaises(FileExistsError):
                scratch.output("foreign.ppm")
            with self.assertRaises(ValueError):
                scratch.output("../outside.ppm")

            scratch.remove(own)
            self.assertFalse(own.exists())
            self.assertEqual(foreign.read_bytes(), b"leave this")


if __name__ == "__main__":
    unittest.main()
