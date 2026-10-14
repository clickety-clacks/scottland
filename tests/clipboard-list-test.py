#!/usr/bin/env python3
import contextlib
import importlib.machinery
import importlib.util
import io
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

    def test_clear_acks_before_showing_empty_picker_and_ignores_dismissal_diagnostics(self):
        events = []

        def run(argv, **kwargs):
            events.append(("picker", argv))
            self.assertEqual(argv, ["/fake-picker", "--show-empty", "--prompt", "Clipboard history"])
            self.assertEqual(kwargs["input"], b"")
            return SimpleNamespace(returncode=1, stdout=b"", stderr=b"runtime warning\n")

        def call(operation, payload=b""):
            events.append(("service", operation))
            self.assertEqual(operation, "clear")
            self.assertEqual(payload, b"")
            return b""

        with patch.object(clipboard, "call", side_effect=call), \
             patch.object(clipboard, "picker_path", return_value="/fake-picker"), \
             patch.object(clipboard.subprocess, "run", side_effect=run):
            result = clipboard.command_clear(None)

        self.assertEqual(result, 0)
        self.assertEqual(events[0], ("service", "clear"))
        self.assertEqual(events[1][0], "picker")

    def test_clear_failure_does_not_open_picker(self):
        with patch.object(clipboard, "call", side_effect=RuntimeError("clear rejected")), \
             patch.object(clipboard, "picker_path") as picker_path, \
             patch.object(clipboard.subprocess, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "clear rejected"):
                clipboard.command_clear(None)

        picker_path.assert_not_called()
        run.assert_not_called()

    def test_clear_presentation_failure_reports_history_was_cleared(self):
        failed = SimpleNamespace(returncode=2, stdout=b"", stderr=b"picker surface failed\n")
        with patch.object(clipboard, "call") as call, \
             patch.object(clipboard, "picker_path", return_value="/fake-picker"), \
             patch.object(clipboard.subprocess, "run", return_value=failed):
            with self.assertRaisesRegex(RuntimeError,
                                        "history was cleared but the empty history view could not be presented: "
                                        "picker surface failed"):
                clipboard.command_clear(None)

        call.assert_called_once_with("clear")

    def test_clear_rejects_picker_success_or_selection_output(self):
        for result in (
            SimpleNamespace(returncode=0, stdout=b"", stderr=b""),
            SimpleNamespace(returncode=1, stdout=b"unexpected selection\n", stderr=b""),
        ):
            with self.subTest(result=result), \
                 patch.object(clipboard, "call"), \
                 patch.object(clipboard, "picker_path", return_value="/fake-picker"), \
                 patch.object(clipboard.subprocess, "run", return_value=result):
                with self.assertRaisesRegex(RuntimeError, "history was cleared"):
                    clipboard.command_clear(None)

    def test_private_snapshot_returns_a_session_token(self):
        output = io.StringIO()
        with patch.object(clipboard, "call", return_value=clipboard.u64(23)) as call, \
             contextlib.redirect_stdout(output):
            result = clipboard.command_snapshot(None)

        self.assertEqual(result, 0)
        self.assertEqual(output.getvalue(), "23\n")
        call.assert_called_once_with("snapshot")

    def test_temporary_text_requires_the_captured_target_and_sends_utf8(self):
        text = "🌱".encode("utf-8")

        class Input:
            buffer = io.BytesIO(text)

        args = SimpleNamespace(target=42, snapshot=23)
        with patch.object(clipboard, "require_target") as require_target, \
             patch.object(clipboard, "call") as call, \
             patch.object(clipboard.sys, "stdin", Input()):
            result = clipboard.command_type_text(args)

        self.assertEqual(result, 0)
        require_target.assert_called_once_with(42)
        call.assert_called_once_with("type-text", clipboard.u64(42) + clipboard.u64(23) +
                                     clipboard.field(text))

    def test_temporary_text_rejects_non_utf8_before_service_mutation(self):
        class Input:
            buffer = io.BytesIO(b"\xff")

        args = SimpleNamespace(target=42, snapshot=23)
        with patch.object(clipboard, "require_target"), \
             patch.object(clipboard, "call") as call, \
             patch.object(clipboard.sys, "stdin", Input()):
            with self.assertRaisesRegex(ValueError, "not valid UTF-8"):
                clipboard.command_type_text(args)
        call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
