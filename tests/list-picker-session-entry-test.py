#!/usr/bin/env python3
"""Exercise picker stdin and selection across the scottland-exec handoff."""

import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import unittest


REPO = Path(__file__).resolve().parents[1]
PICKER = REPO / "core/libexec/scottland-list-picker"
EXEC = REPO / "core/libexec/scottland-exec"


def read_exact(connection, size):
    data = bytearray()
    while len(data) < size:
        part = connection.recv(size - len(data))
        if not part:
            raise AssertionError("picker closed its Wayfire IPC connection early")
        data.extend(part)
    return bytes(data)


def fake_wayfire(path, failures):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(path))
            server.listen(1)
            server.settimeout(5)
            with server.accept()[0] as connection:
                for _ in range(2):
                    size = struct.unpack("<I", read_exact(connection, 4))[0]
                    request = json.loads(read_exact(connection, size))
                    if request["method"] == "window-rules/get-focused-output":
                        reply = {"info": {"id": 7}}
                    elif request["method"] == "window-rules/list-outputs":
                        reply = [{"id": 7, "name": "fixture-output"}]
                    else:
                        raise AssertionError(f"unexpected Wayfire request: {request!r}")
                    payload = json.dumps(reply).encode("utf-8")
                    connection.sendall(struct.pack("<I", len(payload)) + payload)
    except BaseException as error:
        failures.append(error)


class ListPickerSessionEntryTest(unittest.TestCase):
    def setUp(self):
        build = REPO / "build"
        build.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="lp-", dir=build)
        self.root = Path(self.temp.name)
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.libexec = self.root / "libexec"
        self.libexec.mkdir()
        shutil.copy2(PICKER, self.libexec / PICKER.name)
        shutil.copy2(EXEC, self.libexec / EXEC.name)
        (self.libexec / PICKER.name).chmod(0o755)

        hooks = self.root / "hooks"
        (hooks / "list-picker").mkdir(parents=True)
        (hooks / "list-picker/shell.qml").write_text("fixture UI\n", encoding="utf-8")
        self.hooks = hooks
        self.launch_log = self.root / "launcher.log"
        self.qs_log = self.root / "qs.json"
        self.socket_path = self.root / "w"

        launcher = self.libexec / "scottland-exec"
        launcher.write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            "with open(os.environ['PICKER_TEST_LAUNCH_LOG'], 'a') as log: log.write('entered\\n')\n"
            "os.environ['SCOTTLAND_EXEC'] = '1'\n"
            "os.environ['WAYFIRE_SOCKET'] = os.environ['PICKER_TEST_SOCKET']\n"
            "os.execv(sys.argv[2], sys.argv[2:])\n",
            encoding="utf-8",
        )
        launcher.chmod(0o755)

        qs = self.bin / "qs"
        qs.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os\n"
            "from pathlib import Path\n"
            "entries = json.loads(Path(os.environ['SCOTTLAND_LIST_PICKER_INPUT']).read_text())\n"
            "Path(os.environ['PICKER_TEST_QS_LOG']).write_text(json.dumps({"
            "'entries': entries, 'prompt': os.environ['SCOTTLAND_LIST_PICKER_PROMPT']}))\n"
            "Path(os.environ['SCOTTLAND_LIST_PICKER_RESULT']).write_text('1')\n",
            encoding="utf-8",
        )
        qs.chmod(0o755)

        self.env = os.environ.copy()
        self.env.pop("WAYFIRE_SOCKET", None)
        self.env.pop("SCOTTLAND_EXEC", None)
        self.env["PATH"] = f"{self.bin}:{self.env['PATH']}"
        self.env["XDG_RUNTIME_DIR"] = str(self.runtime)
        self.env["SCOTTLAND_HOOKS"] = str(self.hooks)
        self.env["PICKER_TEST_SOCKET"] = str(self.socket_path)
        self.env["PICKER_TEST_LAUNCH_LOG"] = str(self.launch_log)
        self.env["PICKER_TEST_QS_LOG"] = str(self.qs_log)

    def tearDown(self):
        self.temp.cleanup()

    def run_picker(self, input_bytes, *args, env=None):
        return subprocess.run(
            [str(self.libexec / PICKER.name), *args],
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env or self.env,
            timeout=8,
            check=False,
        )

    def test_nonempty_stdin_survives_automatic_session_entry(self):
        failures = []
        server = threading.Thread(target=fake_wayfire, args=(self.socket_path, failures), daemon=True)
        server.start()
        result = self.run_picker(b"alpha\nbeta\n", "--prompt", "Choose one")
        server.join(timeout=6)

        self.assertFalse(server.is_alive(), "fake Wayfire did not receive the output queries")
        self.assertEqual(failures, [])
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b"beta\n", b""))
        self.assertEqual(self.launch_log.read_text(encoding="utf-8"), "entered\n")
        self.assertEqual(
            json.loads(self.qs_log.read_text(encoding="utf-8")),
            {"entries": ["alpha", "beta"], "prompt": "Choose one"},
        )

    def test_empty_input_cancels_without_entering_a_session(self):
        result = self.run_picker(b"")

        self.assertEqual((result.returncode, result.stdout, result.stderr), (1, b"", b""))
        self.assertFalse(self.launch_log.exists(), "empty input must not start the session launcher")
        self.assertFalse(self.qs_log.exists(), "empty input must not start the picker UI")


if __name__ == "__main__":
    unittest.main(verbosity=2)
