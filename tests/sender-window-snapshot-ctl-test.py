#!/usr/bin/env python3
"""Exercise sender-window-snapshot through isolated Unix-socket session fixtures."""
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import threading
import time


REPO = Path(__file__).resolve().parents[1]
LIBEXEC = REPO / "core/libexec"
EXEC = LIBEXEC / "scottland-exec"
CTL = LIBEXEC / "scottland-ctl"
failures = 0
checks = 0


def check(name, condition):
    global failures, checks
    checks += 1
    print(("PASS " if condition else "FAIL ") + name)
    failures += not condition


class SessionFixture:
    """A fake recorded session that accepts liveness probes and at most IPC calls."""

    def __init__(self, directory, name, response=None, mode="reply", delays=()):
        self.path = directory / f"{name}.sock"
        self.env_path = directory / f"{name}.env"
        recorded_path = str(LIBEXEC) + os.pathsep + os.environ.get("PATH", "")
        self.env_path.write_bytes(
            f"WAYFIRE_SOCKET={self.path}\0WAYLAND_DISPLAY={name}\0PATH={recorded_path}\0".encode()
        )
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(str(self.path))
        self.listener.listen(4)
        self.listener.settimeout(5)
        self.response = response
        self.mode = mode
        self.delays = delays
        self.requests = []
        self.errors = []
        self.done = threading.Event()
        self.worker = threading.Thread(target=self._serve, daemon=True)
        self.worker.start()

    def _read_exact(self, conn, count):
        data = bytearray()
        while len(data) < count:
            chunk = conn.recv(count - len(data))
            if not chunk:
                return None
            data.extend(chunk)
        return bytes(data)

    def _serve(self):
        try:
            # scottland-exec makes one liveness connection per recorded session. The selected
            # session then receives one separate CLI connection with the framed IPC request.
            while not self.done.is_set():
                try:
                    conn, _ = self.listener.accept()
                except socket.timeout:
                    break
                except OSError as error:
                    if not self.done.is_set():
                        self.errors.append(repr(error))
                    break
                with conn:
                    conn.settimeout(5)
                    header = self._read_exact(conn, 4)
                    if header is None:  # liveness probe: connected and closed without bytes
                        if self.mode == "unavailable-after-probe":
                            self.done.set()
                            self.listener.close()
                            return
                        continue
                    size = struct.unpack("<I", header)[0]
                    body = self._read_exact(conn, size)
                    if body is None:
                        self.errors.append("truncated request body")
                        continue
                    request = json.loads(body)
                    self.requests.append(request)
                    if self.mode == "eof-header":
                        continue
                    if self.mode == "eof-body":
                        conn.sendall(struct.pack("<I", 20) + b"{}")
                        continue
                    if self.mode == "malformed":
                        payload = b"{not-json"
                        conn.sendall(struct.pack("<I", len(payload)) + payload)
                        continue
                    if self.mode == "timeout":
                        time.sleep(2.4)
                        try:
                            payload = json.dumps(self.response).encode()
                            conn.sendall(struct.pack("<I", len(payload)) + payload)
                        except OSError:
                            pass  # expected: the CLI has already timed out and closed
                        continue
                    if self.mode == "per-operation-timeout":
                        payload = json.dumps(self.response).encode()
                        time.sleep(self.delays[0])
                        conn.sendall(struct.pack("<I", len(payload)))
                        time.sleep(self.delays[1])
                        conn.sendall(payload)
                        continue
                    payload = json.dumps(self.response).encode()
                    conn.sendall(struct.pack("<I", len(payload)) + payload)
                    # Continue reading so a second request on the same connection is recorded.
        except Exception as error:
            self.errors.append(repr(error))
        finally:
            self.done.set()

    def close(self):
        self.done.set()
        try:
            # Wake an accept that is waiting for another request after the CLI exits.
            wake = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            wake.connect(str(self.path))
            wake.close()
        except OSError:
            pass
        self.listener.close()
        self.worker.join(timeout=6)
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def run_case(name, mode, response=None, selected=True, delays=(), add_other_session=False):
    with tempfile.TemporaryDirectory(prefix="sender-window-fixture-") as temp:
        directory = Path(temp)
        selected_fixture = None
        other_fixture = None
        if selected:
            # The caller's socket chooses beta even though alpha is also live.
            other_fixture = SessionFixture(directory, "alpha", {"views": []}) if add_other_session else None
            selected_fixture = SessionFixture(directory, "beta", response, mode, delays)
            chosen_socket = str(selected_fixture.path)
        else:
            missing = directory / "missing.sock"
            (directory / "stale.env").write_bytes(f"WAYFIRE_SOCKET={missing}\0".encode())
            chosen_socket = ""

        env = os.environ.copy()
        env.pop("SCOTTLAND_EXEC", None)
        env["SCOTTLAND_SESSION_DIR"] = str(directory)
        env["PATH"] = str(LIBEXEC) + os.pathsep + env.get("PATH", "")
        if chosen_socket:
            env["WAYFIRE_SOCKET"] = chosen_socket
        else:
            env.pop("WAYFIRE_SOCKET", None)

        started = time.monotonic()
        try:
            result = subprocess.run(
                [str(EXEC), "--", "scottland-ctl", "sender-window-snapshot"],
                env=env,
                capture_output=True,
                timeout=8,
            )
            elapsed = time.monotonic() - started
        finally:
            if selected_fixture:
                selected_fixture.close()
            if other_fixture:
                other_fixture.close()

        return result, elapsed, selected_fixture, other_fixture


def main():
    full_reply = {
        "views": [
            {"id": 41, "app_id": None, "pid": 222, "title": "Widget", "kind": "widget", "surface": "panel"},
            {"id": 42, "pid": 333, "title": None, "kind": "layer-shell", "optional": None},
            {"id": 43, "app_id": "org.example.App", "pid": 444, "title": "Regular", "extra": {"ids": [1, 2]}},
        ],
        "metadata": {"kept": True, "nullable": None},
    }
    result, _, chosen, other = run_case("full-json-and-selection", "reply", full_reply, add_other_session=True)
    expected_request = {"method": "window-rules/list-views", "data": {}}
    check("exact invocation succeeds with no route arguments", result.returncode == 0)
    check("complete JSON reply is preserved, including widget/panel, null and absent fields",
          result.stdout.endswith(b"\n") and json.loads(result.stdout) == full_reply
          and "app_id" not in json.loads(result.stdout)["views"][1])
    check("exactly one request is sent to the selected session and none to the other session",
          chosen.requests == [expected_request] and other.requests == [])
    check("success has no stderr", result.stderr == b"")
    check("fixture threads and socket handling are clean", not chosen.errors and not other.errors)

    error_reply = {"error": "fixture rejected list-views"}
    result, _, chosen, _ = run_case("error-reply", "reply", error_reply)
    check("IPC error exits 1, writes its message to stderr, and emits no success JSON",
          result.returncode == 1 and result.stdout == b"" and b"fixture rejected list-views" in result.stderr)
    check("error reply uses the exact single IPC request",
          chosen.requests == [expected_request] and not chosen.errors)

    result, _, chosen, _ = run_case("eof-header", "eof-header")
    check("EOF before reply header fails closed with no stdout",
          result.returncode != 0 and result.stdout == b"" and len(chosen.requests) == 1)

    result, _, chosen, _ = run_case("eof-body", "eof-body")
    check("EOF in reply body fails closed with no stdout",
          result.returncode != 0 and result.stdout == b"" and len(chosen.requests) == 1)

    result, _, chosen, _ = run_case("malformed-json", "malformed")
    check("malformed reply JSON fails closed with no stdout",
          result.returncode != 0 and result.stdout == b"" and len(chosen.requests) == 1)

    result, elapsed, chosen, _ = run_case("socket-timeout", "timeout", {"views": []})
    check("a blocking receive times out and fails closed", result.returncode != 0 and result.stdout == b""
          and b"timed out" in result.stderr.lower() and len(chosen.requests) == 1)
    check("socket timeout is bounded near the configured two seconds", 1.5 <= elapsed < 5.5)

    result, elapsed, chosen, _ = run_case("per-operation-timeout", "per-operation-timeout",
                                          {"views": [{"id": 99, "title": "delayed"}]},
                                          delays=(1.2, 1.2))
    check("two individually bounded socket operations may exceed two seconds in total",
          result.returncode == 0 and elapsed >= 2.0 and json.loads(result.stdout) == {"views": [{"id": 99, "title": "delayed"}]}
          and chosen.requests == [expected_request])

    result, _, _, _ = run_case("unavailable-session", "reply", selected=False)
    check("unavailable recorded session fails closed with no stdout",
          result.returncode != 0 and result.stdout == b"")

    result, _, selected, _ = run_case("socket-connect-failure", "unavailable-after-probe")
    check("socket refusal after session selection fails closed with no stdout",
          result.returncode != 0 and result.stdout == b"" and selected.requests == [])

    print(f"RESULT {checks - failures} passed, {failures} failed, {checks} total")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
