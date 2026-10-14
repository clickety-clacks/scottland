#!/usr/bin/env python3
import importlib.machinery
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


emoji = load_module("scottland_emoji", ROOT / "core/libexec/scottland-emoji")
DATA = ROOT / "core/emoji/emoji.tsv"
SOURCE = ROOT / "core/emoji/data/emoji-test.txt"
GENERATOR = ROOT / "core/emoji/generate.py"
PICKER = "/test/bin/scottland-list-picker"
CLIPBOARD = "/test/bin/scottland-clipboard"


class EmojiCommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = emoji.load_entries(DATA)

    def invoke(self, *, copy=False, picker_result=None, target_result=None, snapshot_result=None):
        selected = self.rows[0]
        results = {
            "focused-target": target_result or SimpleNamespace(returncode=0, stdout=b"42\n", stderr=b""),
            "snapshot": snapshot_result or SimpleNamespace(returncode=0, stdout=b"9\n", stderr=b""),
            "picker": picker_result or SimpleNamespace(
                returncode=0, stdout=(selected[2] + "\n").encode(), stderr=b""
            ),
            "type-text": SimpleNamespace(returncode=0, stdout=b"", stderr=b""),
            "copy-text": SimpleNamespace(returncode=0, stdout=b"", stderr=b""),
            "release": SimpleNamespace(returncode=0, stdout=b"", stderr=b""),
        }
        calls = []

        def run(argv, **kwargs):
            calls.append((argv, kwargs))
            if argv[0] == CLIPBOARD:
                operation = argv[1]
            elif argv[0] == PICKER:
                operation = "picker"
            else:
                raise AssertionError(f"unexpected command: {argv!r}")
            return results[operation]

        with patch.object(emoji, "emoji_data", return_value=DATA), \
             patch.object(emoji, "command", side_effect={
                 "scottland-list-picker": PICKER,
                 "scottland-clipboard": CLIPBOARD,
             }.get), \
             patch.object(emoji.subprocess, "run", side_effect=run), \
             patch.object(emoji.sys, "argv", ["scottland-emoji"] + (["--copy"] if copy else [])):
            status = emoji.main()
        return status, calls, selected

    def test_insert_filters_shipped_rows_and_delivers_to_captured_target(self):
        status, calls, selected = self.invoke()
        self.assertEqual(status, 0)
        self.assertEqual([call[0][1] for call in calls[:2]], ["focused-target", "snapshot"])
        picker_argv, picker_kwargs = calls[2]
        self.assertEqual(picker_argv, [PICKER, "--prompt", "Choose an emoji"])
        self.assertEqual(picker_kwargs["input"].decode("utf-8").splitlines()[0], self.rows[0][2])
        self.assertEqual(len(picker_kwargs["input"].decode("utf-8").splitlines()), len(self.rows))
        self.assertEqual(calls[3][0], [CLIPBOARD, "type-text", "--snapshot", "9", "--target", "42"])
        self.assertEqual(calls[3][1]["input"], selected[0].encode("utf-8"))
        self.assertEqual([call[0][1] for call in calls if call[0][0] == CLIPBOARD],
                         ["focused-target", "snapshot", "type-text"])

    def test_cancel_releases_snapshot_without_changing_clipboard(self):
        cancel = SimpleNamespace(returncode=1, stdout=b"", stderr=b"")
        status, calls, _ = self.invoke(picker_result=cancel)
        self.assertEqual(status, 1)
        self.assertEqual([call[0][1] for call in calls if call[0][0] == CLIPBOARD],
                         ["focused-target", "snapshot", "release"])

    def test_copy_mode_skips_target_snapshot_and_paste_path(self):
        status, calls, selected = self.invoke(copy=True)
        self.assertEqual(status, 0)
        self.assertEqual(calls[0][0], [PICKER, "--prompt", "Choose an emoji"])
        self.assertEqual(calls[1][0], [CLIPBOARD, "copy-text", "--copy-only"])
        self.assertEqual(calls[1][1]["input"], selected[0].encode("utf-8"))
        self.assertEqual(len(calls), 2)

    def test_unknown_picker_output_releases_snapshot_and_fails(self):
        unknown = SimpleNamespace(returncode=0, stdout=b"not a shipped emoji\n", stderr=b"")
        status, calls, _ = self.invoke(picker_result=unknown)
        self.assertEqual(status, 2)
        self.assertEqual([call[0][1] for call in calls if call[0][0] == CLIPBOARD],
                         ["focused-target", "snapshot", "release"])

    def test_no_focused_target_stops_before_snapshot_or_picker(self):
        missing = SimpleNamespace(returncode=1, stdout=b"", stderr=b"no focused target\n")
        status, calls, _ = self.invoke(target_result=missing)
        self.assertEqual(status, 2)
        self.assertEqual([call[0][1] for call in calls], ["focused-target"])

    def test_offline_generator_recreates_the_shipped_list(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "emoji.tsv"
            subprocess.run([sys.executable, str(GENERATOR), str(SOURCE), str(output)], check=True)
            self.assertEqual(output.read_bytes(), DATA.read_bytes())

    def test_invalid_data_row_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "bad.tsv"
            source.write_text("# header\nmissing separator\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid emoji data row 2"):
                emoji.load_entries(source)


if __name__ == "__main__":
    unittest.main()
