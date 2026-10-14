#!/usr/bin/env python3
import importlib.machinery
import importlib.util
import struct
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "core/libexec/scottland-clipboard"
loader = importlib.machinery.SourceFileLoader("scottland_clipboard", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
clipboard = importlib.util.module_from_spec(spec)
loader.exec_module(clipboard)


class ClipboardListTests(unittest.TestCase):
    def test_text_preview_uses_first_line_and_marks_continuation(self):
        self.assertEqual(clipboard.display_text(b"alpha\nbeta"), "alpha ↵…")
        self.assertEqual(clipboard.display_text(b"x" * 97), "x" * 95 + "…")

    def test_image_preview_includes_dimensions_when_header_has_them(self):
        png = bytearray(24)
        png[:8] = b"\x89PNG\r\n\x1a\n"
        struct.pack_into(">II", png, 16, 640, 480)
        rows = [{"mime": "image/png", "data": bytes(png)}]
        self.assertEqual(clipboard.display_rows(rows), ["Image 640×480"])

    def test_duplicate_and_literal_collision_labels_still_select_exact_rows(self):
        rows = [
            {"mime": "text/plain;charset=utf-8", "data": b"same"},
            {"mime": "text/plain;charset=utf-8", "data": b"same"},
            {"mime": "text/plain;charset=utf-8", "data": "same · entry-0:1".encode()},
        ]
        labels = clipboard.display_rows(rows)
        self.assertEqual(len(set(labels)), len(rows))
        for index, label in enumerate(labels):
            reply = SimpleNamespace(returncode=0, stdout=(label + "\n").encode(), stderr=b"")
            with patch.object(clipboard, "picker_path", return_value="/fake-picker"), \
                 patch.object(clipboard.subprocess, "run", return_value=reply):
                self.assertIs(clipboard.choose(rows, labels, "Search history"), rows[index])

    def test_empty_picker_result_is_a_cancel(self):
        reply = SimpleNamespace(returncode=1, stdout=b"", stderr=b"")
        with patch.object(clipboard, "picker_path", return_value="/fake-picker"), \
             patch.object(clipboard.subprocess, "run", return_value=reply):
            with self.assertRaisesRegex(RuntimeError, "selection cancelled"):
                clipboard.choose([], [], "Search history")


if __name__ == "__main__":
    unittest.main()
