#!/usr/bin/env python3
"""Safe tests for the manual seat test's unique output ownership."""

import tempfile
import unittest
from pathlib import Path

from seat_test_scratch import OwnedScratch


class OwnedScratchTests(unittest.TestCase):
    def test_uses_a_unique_directory_and_leaves_preexisting_seat_evidence_alone(self):
        with tempfile.TemporaryDirectory() as temporary:
            build = Path(temporary) / "build"
            old = build / "seat"
            old.mkdir(parents=True)
            evidence = old / "keep.png"
            evidence.write_text("existing evidence")

            first = OwnedScratch(build)
            second = OwnedScratch(build)

            self.assertNotEqual(first.path, second.path)
            self.assertNotEqual(first.path, old)
            self.assertEqual(evidence.read_text(), "existing evidence")
            self.assertTrue(first._owner.is_file())

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

            scratch.remove_suffix(".ppm")
            self.assertFalse(own.exists())
            self.assertEqual(foreign.read_bytes(), b"leave this")


if __name__ == "__main__":
    unittest.main()
