#!/usr/bin/env python3
"""Verify a test compositor wrapper is sent only with Session's launch request."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import omarchy_fixture  # noqa: E402


def main():
    with TemporaryDirectory(prefix="scottland-session-wrap-") as temporary:
        root = Path(temporary)
        repo = root / "checkout"
        (repo / "tests").mkdir(parents=True)
        (repo / "tests/headless.sh").touch()
        home = root / "home"
        home.mkdir()
        wrapper = root / "wrap-compositor"
        wrapper.write_text("#!/bin/sh\nexit 0\n")
        fixture = SimpleNamespace(home=home, bin=root / "bin")
        fixture.bin.mkdir()
        calls = []

        def dispatch(command, *, env, check, **kwargs):
            args = tuple(command[1:])
            status = 2 if env.get("SCOTTLAND_TEST_WRAP") and (
                args[0] != "start" or "--omarchy" not in args[1:]) else 0
            stdout = "{}" if args[0] == "ipc" else "run-output" if args[0] == "run" else ""
            if args[0] == "start" and status == 0:
                session_dir = Path(env["SCOTTLAND_HEADLESS_DIR"])
                session_dir.mkdir(parents=True, exist_ok=True)
                (session_dir / "display").write_text("wayland-fake\n")
            result = SimpleNamespace(args=command, returncode=status, stdout=stdout, stderr="")
            calls.append((args, env.copy(), result))
            if check and status:
                raise omarchy_fixture.subprocess.CalledProcessError(
                    status, command, output=stdout, stderr="wrapper rejected outside start")
            return result

        original_repo = omarchy_fixture.REPO
        omarchy_fixture.REPO = root / "runner-checkout"
        try:
            with patch.object(omarchy_fixture.subprocess, "run", side_effect=dispatch):
                session = omarchy_fixture.Session(
                    fixture, "fake-session", repo=repo,
                    env={"SCOTTLAND_TEST_WRAP": str(wrapper)})
                assert "SCOTTLAND_TEST_WRAP" not in session.env
                try:
                    with session as active:
                        assert active.display == "wayland-fake"
                        assert active.ipc("fake/method") == {}
                        assert active.run("fake-command", check=True).returncode == 0
                        raise RuntimeError("exercise context-manager cleanup")
                except RuntimeError as error:
                    assert str(error) == "exercise context-manager cleanup"
                else:
                    raise AssertionError("body exception did not propagate")
        finally:
            omarchy_fixture.REPO = original_repo

        assert [call[0][0] for call in calls] == ["stop", "start", "ipc", "run", "stop"], calls
        wrapper_values = [call[1].get("SCOTTLAND_TEST_WRAP") for call in calls]
        assert wrapper_values == [None, str(wrapper), None, None, None], wrapper_values
        assert "--omarchy" in calls[1][0], calls[1][0]
        assert calls[-1][2].returncode == 0, calls[-1]
        print("PASS  wrapper is launch-only across stop/start/ipc/run/exception cleanup")
        print("PASS  context exception propagates after owned-session stop dispatch")

        calls.clear()
        plain = omarchy_fixture.Session(
            fixture, "fake-plain-session", repo=repo, omarchy=False,
            env={"SCOTTLAND_TEST_WRAP": str(wrapper)})
        with patch.object(omarchy_fixture.subprocess, "run", side_effect=dispatch):
            plain.start()
            plain.stop()
        assert [call[0][0] for call in calls] == ["stop", "start", "stop"], calls
        assert all("SCOTTLAND_TEST_WRAP" not in call[1] for call in calls), calls
        print("PASS  wrapper is withheld from a non-Omarchy start")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
