#!/usr/bin/env python3
"""Behavior checks for the shortcut-sheet command around its session IPC boundary."""

import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import threading
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
COMMAND = ROOT / "core/libexec/scottland-keybindings"


def receive_exact(connection, size):
    data = bytearray()
    while len(data) < size:
        chunk = connection.recv(size - len(data))
        if not chunk:
            raise ConnectionError("client closed an incomplete IPC request")
        data.extend(chunk)
    return bytes(data)


class IpcSession:
    def __init__(self, runtime, windows, request_count, run_reply=None):
        self.path = runtime / "wayfire.sock"
        self.windows = windows
        self.request_count = request_count
        self.run_reply = run_reply or {"result": "ok"}
        self.requests = []
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(self.path))
        self.server.listen()
        self.server.settimeout(0.1)
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        deadline = time.monotonic() + 5
        while len(self.requests) < self.request_count and time.monotonic() < deadline:
            try:
                connection, _ = self.server.accept()
            except socket.timeout:
                continue
            with connection:
                size = struct.unpack("<I", receive_exact(connection, 4))[0]
                request = json.loads(receive_exact(connection, size))
                self.requests.append(request)
                if request.get("method") == "scottland/desktop-model":
                    reply = {"windows": self.windows}
                elif request.get("method") == "scottland/run-binding":
                    reply = self.run_reply
                else:
                    reply = {"error": "unexpected method"}
                payload = json.dumps(reply).encode("utf-8")
                connection.sendall(struct.pack("<I", len(payload)) + payload)

    def finish(self):
        self.thread.join(timeout=5)
        self.server.close()
        if self.thread.is_alive():
            raise AssertionError("session IPC did not receive its expected requests")


class ShortcutSheetBehavior(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="scottland-keybindings-")
        self.root = Path(self.tmp.name)
        self.runtime = self.root / "runtime"
        self.session_dir = self.runtime / "scottland"
        self.session_dir.mkdir(parents=True)
        self.picker = self.root / "scottland-list-picker"
        self.picker.write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            "from pathlib import Path\n"
            "payload = sys.stdin.read()\n"
            "capture = os.environ.get('PICKER_CAPTURE')\n"
            "if capture: Path(capture).write_text(payload)\n"
            "selection = os.environ.get('PICKER_SELECTION', '')\n"
            "if selection not in payload.splitlines(): sys.exit(4)\n"
            "print(selection)\n"
        )
        self.picker.chmod(0o755)

    def tearDown(self):
        self.tmp.cleanup()

    def config(self, text):
        (self.session_dir / "wayfire.ini").write_text(text, encoding="utf-8")

    def env(self, selection=None, capture=None, extra=None):
        environment = os.environ.copy()
        environment["XDG_RUNTIME_DIR"] = str(self.runtime)
        environment["PATH"] = str(self.root) + os.pathsep + environment.get("PATH", "")
        if selection is not None:
            environment["PICKER_SELECTION"] = selection
        if capture is not None:
            environment["PICKER_CAPTURE"] = str(capture)
        if extra:
            environment.update(extra)
        return environment

    def execute(self, args=(), selection=None, capture=None, server=None, extra=None, timeout=8):
        environment = self.env(selection=selection, capture=capture, extra=extra)
        if server:
            environment["WAYFIRE_SOCKET"] = str(server.path)
        result = subprocess.run(
            [str(COMMAND), *args], capture_output=True, text=True,
            env=environment, timeout=timeout, check=False,
        )
        if server:
            server.finish()
        return result

    def test_print_reflects_live_override_labels_and_alternate_chords(self):
        self.config(
            "[command]\n"
            "binding_terminal = <super> KEY_ENTER\n"
            "command_terminal = foot\n"
            "binding_terminal = <super> KEY_T\n"
            "[core]\n"
            "close_top_view = <super> KEY_Q | <alt> KEY_F4\n"
        )

        result = self.execute(args=("--print",))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            result.stdout.splitlines(),
            ["Super + T → Open terminal", "Super + Q / Alt + F4 → Close focused window"],
        )

    def test_shipped_shortcuts_have_plain_labels(self):
        base_config = (ROOT / "core/config/scottland.ini").read_text(encoding="utf-8")
        self.config(base_config)

        result = self.execute(args=("--print",))
        lines = result.stdout.splitlines()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Super + Enter → Open terminal", lines)
        self.assertIn("Super + comma → Open Scottland Settings", lines)
        self.assertTrue(lines)
        self.assertFalse(any("binding_" in line or "KEY_" in line for line in lines), lines)

    def test_runnable_binding_uses_the_window_focused_before_picker(self):
        self.config("[core]\nclose_top_view = <super> KEY_Q\n")
        server = IpcSession(self.runtime, [{"id": 42, "focused": True}], 2)

        result = self.execute(selection="Super + Q → Close focused window", server=server)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([request["method"] for request in server.requests],
                         ["scottland/desktop-model", "scottland/run-binding"])
        self.assertEqual(server.requests[1]["data"], {"key": "Q", "mods": "SUPER", "target": 42})

    def test_lost_target_returns_one_reason_without_retrying_action(self):
        self.config("[core]\nclose_top_view = <super> KEY_Q\n")
        server = IpcSession(
            self.runtime, [{"id": 42, "focused": True}], 2,
            run_reply={"error": "this shortcut's target window is no longer focused"},
        )

        result = self.execute(selection="Super + Q → Close focused window", server=server)

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr,
                         "scottland-keybindings: this shortcut's target window is no longer focused\n")
        self.assertEqual(len(server.requests), 2)

    def test_configured_command_runs_without_a_focused_window(self):
        marker = self.root / "command-ran"
        action = self.root / "record-action"
        action.write_text(
            "#!/usr/bin/env python3\n"
            "import os\n"
            "from pathlib import Path\n"
            "Path(os.environ['COMMAND_MARKER']).write_text('ran')\n"
        )
        action.chmod(0o755)
        self.config(
            "[command]\n"
            "binding_terminal = <super> KEY_ENTER\n"
            f"command_terminal = {action}\n"
        )
        server = IpcSession(self.runtime, [], 1)

        result = self.execute(
            selection="Super + Enter → Open terminal", server=server,
            extra={"COMMAND_MARKER": str(marker)},
        )
        deadline = time.monotonic() + 2
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(marker.exists(), "configured command did not run")
        self.assertEqual([request["method"] for request in server.requests], ["scottland/desktop-model"])

    def test_pointer_chord_closes_without_invoking_binding(self):
        self.config("[core]\nclose_top_view = <super> BTN_LEFT\n")
        server = IpcSession(self.runtime, [], 1)

        result = self.execute(selection="Super + Left mouse button → Close focused window", server=server)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([request["method"] for request in server.requests], ["scottland/desktop-model"])

    def test_bad_config_fails_before_picker_receives_partial_list(self):
        self.config("[command]\nbinding_terminal = <super> KEY_ENTER\nmalformed line\n")
        capture = self.root / "picker-input"

        result = self.execute(capture=capture)

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(len(result.stderr.splitlines()), 1)
        self.assertIn("malformed line", result.stderr)
        self.assertFalse(capture.exists(), "picker opened before full configuration parsing completed")


if __name__ == "__main__":
    unittest.main()
