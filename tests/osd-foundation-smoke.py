#!/usr/bin/env python3
"""Hosted smoke for the real Wayfire session and Quickshell OSD socket host.

The workflow initializes the evidence before building, then runs this file in the
prepared Arch container. The --probe mode is invoked through tests/headless.sh run
so QuickShell receives the environment of the test-owned Wayfire session.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import uuid


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "build/osd-foundation-smoke/report.json"
PACKAGES = ("wayfire", "wlroots0.20", "quickshell", "meson")


def run_capture(command, *, env=None, timeout=30):
    process = subprocess.Popen(
        command, cwd=ROOT, env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            output, _ = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            output, _ = process.communicate(timeout=5)
        return process.returncode or 124, output
    return process.returncode, output


def source_identity():
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    tree = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=ROOT, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    return {
        "commit": commit,
        "tree": tree,
        "github_sha": os.environ.get("GITHUB_SHA", ""),
        "ref": os.environ.get("GITHUB_REF", ""),
        "event": os.environ.get("GITHUB_EVENT_NAME", ""),
        "base_ref": os.environ.get("GITHUB_BASE_REF", ""),
        "head_ref": os.environ.get("GITHUB_HEAD_REF", ""),
    }


def run_url():
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "")
    if repository and run_id:
        suffix = f"?attempt={attempt}" if attempt else ""
        return f"{server}/{repository}/actions/runs/{run_id}{suffix}"
    return "unavailable in this invocation"


def initial_report():
    return {
        "schema": 1,
        "run_url": run_url(),
        "source": source_identity(),
        "source_paths": {
            "osd_host_hook": "core/autostart.d/11-widget-osd-host",
            "osd_session_qml": "core/widgets/session/shell.qml",
            "osd_panel": "core/widgets/osd/OsdPanel.qml (not loaded by this smoke)",
        },
        "container": {"image_id": os.environ.get("SCOTTLAND_CHECKS_IMAGE_ID", ""),
                      "base_image": os.environ.get("SCOTTLAND_BASE_IMAGE_REF", ""),
                      "packages": {}, "package_query": "NOT_RUN"},
        "commands": [],
        "rows": {
            "meson_test_hooks_build": {
                "status": "NOT_RUN", "class": "pending",
                "detail": "make test-hooks has not been observed yet",
            },
            "wayfire_session_started": {
                "status": "NOT_RUN", "class": "component_not_started",
                "detail": "the real Wayfire session has not started",
            },
            "wayfire_desktop_model_ipc": {
                "status": "NOT_RUN", "class": "assertion_not_run",
                "detail": "no Wayfire IPC assertion has run",
            },
            "quickshell_osd_socket": {
                "status": "NOT_RUN", "class": "component_not_started",
                "detail": "the real Quickshell OSD host has not started",
            },
            "quickshell_process_cleanup": {
                "status": "NOT_RUN", "class": "pending",
                "detail": "the smoke-owned Quickshell process has not started",
            },
            "visible_panel": {
                "status": "NOT_RUN", "class": "separate_acceptance",
                "detail": "visible-panel acceptance is separate from this foundation smoke",
            },
            "shown_ack": {
                "status": "NOT_RUN", "class": "separate_acceptance",
                "detail": "shown-ACK acceptance is separate; no osd.present request is sent",
            },
            "p14": {
                "status": "NOT_RUN", "class": "separate_acceptance",
                "detail": "P14 is outside this foundation smoke",
            },
            "session_cleanup": {
                "status": "NOT_RUN", "class": "pending",
                "detail": "the test-owned headless session has not been stopped",
            },
        },
    }


def load_report(path):
    if path.is_file():
        return json.loads(path.read_text())
    return initial_report()


def save_report(report, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def set_row(report, name, status, classification, detail):
    report["rows"][name] = {
        "status": status,
        "class": classification,
        "detail": detail,
    }


def set_unrun(report, reason, classification="prerequisite_unavailable", *, exclude=()):
    excluded = set(exclude)
    for name in ("wayfire_session_started", "wayfire_desktop_model_ipc",
                 "quickshell_osd_socket", "quickshell_process_cleanup", "session_cleanup"):
        if name not in excluded and report["rows"][name]["status"] == "NOT_RUN":
            set_row(report, name, "NOT_RUN", classification, reason)


class SmokePrerequisiteUnavailable(Exception):
    """Stop this smoke after recording which required component could not start."""


def initialize(path):
    report = initial_report()
    save_report(report, path)
    print(json.dumps({"report": str(path.relative_to(ROOT)),
                      "commit": report["source"]["commit"],
                      "tree": report["source"]["tree"],
                      "run_url": report["run_url"]}, sort_keys=True))


def mark_not_run(path, reason):
    report = load_report(path)
    outcome_file = ROOT / "build/osd-foundation-smoke/hooks-build.outcome"
    if outcome_file.is_file():
        outcome = outcome_file.read_text().strip()
        if outcome.startswith("FAIL\t"):
            set_row(report, "meson_test_hooks_build", "FAIL", "build_failed", outcome)
        elif outcome.startswith("PASS\t"):
            set_row(report, "meson_test_hooks_build", "PASS", "build_completed", outcome)
    set_unrun(report, reason)
    report["not_run_reason"] = reason
    save_report(report, path)
    print(json.dumps(report, indent=2, sort_keys=True))


def package_versions(report):
    command = ["pacman", "-Q", *PACKAGES]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    report["commands"].append(" ".join(command))
    report["container"]["package_query"] = {
        "command": " ".join(command),
        "exit": result.returncode,
        "output": result.stdout + result.stderr,
    }
    versions = {}
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 2:
            versions[fields[0]] = fields[1]
    report["container"]["packages"] = versions
    meson_rc, meson_version = run_capture(["meson", "--version"], timeout=5)
    report["container"]["meson_version_command"] = {
        "command": "meson --version", "exit": meson_rc, "output": meson_version.strip(),
    }
    return versions


def record_versions(path):
    report = load_report(path)
    report["container"]["image_id"] = os.environ.get("SCOTTLAND_CHECKS_IMAGE_ID", "")
    report["container"]["base_image"] = os.environ.get("SCOTTLAND_BASE_IMAGE_REF", "")
    versions = package_versions(report)
    report["container"]["versions_recorded_before_smoke"] = True
    if not report["container"]["image_id"] or not report["container"]["base_image"]:
        report["container"]["image_identity"] = "unavailable"
    else:
        report["container"]["image_identity"] = "recorded"
    missing = [package for package in PACKAGES if package not in versions]
    if missing:
        report["container"]["missing_packages"] = missing
    save_report(report, path)
    print(json.dumps({
        "report": str(path.relative_to(ROOT)),
        "image_identity": report["container"]["image_identity"],
        "packages": report["container"]["packages"],
        "meson": report["container"].get("meson_version_command"),
    }, sort_keys=True))


def live_group_executables(group):
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            if int(fields[2]) != group or fields[0] == "Z":
                continue
            executable = os.readlink(entry / "exe")
            found.append((int(entry.name), executable))
        except (OSError, ValueError, IndexError):
            continue
    return found


def live_quickshell_processes(group):
    found = []
    for pid, executable in live_group_executables(group):
        if Path(executable).name not in ("quickshell", "qs"):
            continue
        try:
            arguments = [part.decode(errors="replace") for part in
                         Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0") if part]
        except OSError:
            arguments = []
        found.append((pid, executable, arguments))
    return found


def stop_owned_group(process):
    group = process.pid
    try:
        os.killpg(group, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(group, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and live_group_executables(group):
        time.sleep(0.1)
    remaining = live_group_executables(group)
    if remaining:
        try:
            os.killpg(group, signal.SIGKILL)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and live_group_executables(group):
            time.sleep(0.1)
        remaining = live_group_executables(group)
    if remaining:
        raise RuntimeError(f"owned Quickshell process group remains live: {remaining}")
    return process.returncode


def probe(path):
    report = load_report(path)
    hook_dir = Path(os.environ.get("SCOTTLAND_HOOKS", ""))
    session_dir = Path(os.environ.get("SCOTTLAND_SESSION_DIR", ""))
    display = os.environ.get("WAYLAND_DISPLAY", "")
    owner = os.environ.get("SCOTTLAND_HEADLESS_OWNER", "")
    widget_dir = session_dir / "widgets" / display
    hook = hook_dir / "autostart.d/11-widget-osd-host"
    shell_qml = hook_dir / "widgets/session/shell.qml"
    log_path = path.parent / "quickshell-launch.log"
    process = None
    quickshell_seen = False
    session_qml_seen = False
    outcome = ("NOT_RUN", "session_prerequisite_unavailable",
               "the headless session did not supply its owner and OSD host paths")
    cleanup_error = None
    def interrupt(signum, _frame):
        raise InterruptedError(f"probe received signal {signum}")
    previous_term = signal.signal(signal.SIGTERM, interrupt)
    previous_int = signal.signal(signal.SIGINT, interrupt)
    try:
        expected_hook = ROOT / "core/autostart.d/11-widget-osd-host"
        expected_qml = ROOT / "core/widgets/session/shell.qml"
        marker = widget_dir / ".headless-owner"
        if (not owner or not display or not session_dir.is_dir()
                or not marker.is_file() or marker.is_symlink()
                or marker.read_text().strip() != owner):
            raise RuntimeError("headless widget runtime owner marker did not match")
        if (hook.resolve() != expected_hook.resolve()
                or shell_qml.resolve() != expected_qml.resolve()):
            raise RuntimeError("the session did not resolve the checkout's OSD host and shell QML")
        if not hook.is_file() or not os.access(hook, os.X_OK):
            raise RuntimeError("the checkout's OSD host hook is unavailable")

        socket_path = widget_dir / "osd.sock"
        if socket_path.exists() or socket_path.is_symlink():
            raise RuntimeError("the session OSD socket path was occupied before launch")
        with log_path.open("w") as log:
            process = subprocess.Popen(
                [str(hook)], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True, close_fds=True,
            )
            group = process.pid
            deadline = time.monotonic() + 20
            connected = False
            expected_project = str(shell_qml.resolve().parent)
            while time.monotonic() < deadline:
                quickshell_processes = live_quickshell_processes(group)
                quickshell_seen = quickshell_seen or bool(quickshell_processes)
                session_qml_seen = session_qml_seen or any(
                    any(
                        arguments[index] == "-p"
                        and Path(arguments[index + 1]).resolve() == Path(expected_project)
                        for index in range(len(arguments) - 1)
                    )
                    for _, _, arguments in quickshell_processes
                )
                if process.poll() is not None:
                    break
                if socket_path.is_socket() and not socket_path.is_symlink():
                    try:
                        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
                            peer.settimeout(2)
                            peer.connect(str(socket_path))
                            connected = process.poll() is None
                    except OSError:
                        connected = False
                    if connected:
                        break
                time.sleep(0.1)

            if quickshell_seen and session_qml_seen and connected:
                outcome = (
                    "PASS", "real_quickshell_socket_assertion",
                    "real Quickshell ran the checkout session QML and remained live; it created "
                    "a Unix socket that accepted a connection without an osd.present request",
                )
            elif quickshell_seen and session_qml_seen:
                outcome = (
                    "FAIL", "real_component_assertion_failed",
                    "real Quickshell started but the checkout session OSD socket did not accept "
                    "a connection within 20 seconds",
                )
            elif quickshell_seen:
                outcome = (
                    "FAIL", "real_component_assertion_failed",
                    "real Quickshell started but its process arguments did not identify the "
                    "checkout session QML project",
                )
            else:
                outcome = (
                    "NOT_RUN", "quickshell_not_observed",
                    "no real Quickshell executable was observed in the task-owned process group",
                )
    except Exception as error:
        if process is not None and quickshell_seen:
            outcome = ("FAIL", "real_component_assertion_failed", str(error))
        else:
            outcome = ("NOT_RUN", "session_prerequisite_unavailable", str(error))
    finally:
        if process is not None:
            try:
                stop_owned_group(process)
                set_row(report, "quickshell_process_cleanup", "PASS", "owned_process_stopped",
                        "the smoke-owned Quickshell process group was stopped and its launcher reaped")
            except Exception as error:
                cleanup_error = str(error)
                set_row(report, "quickshell_process_cleanup", "FAIL", "owned_process_cleanup_failed",
                        cleanup_error)
        else:
            set_row(report, "quickshell_process_cleanup", "NOT_RUN", "no_owned_process",
                    "no Quickshell process was started")
        if log_path.exists():
            report.setdefault("logs", {})["quickshell_launch"] = str(log_path.relative_to(ROOT))
        state_home_value = os.environ.get("XDG_STATE_HOME")
        state_home = Path(state_home_value) if state_home_value else None
        state_log = state_home / "scottland/widget-osd.log" if state_home else None
        if state_log and state_home.is_dir() and state_log.is_file() and not state_log.is_symlink():
            copied_log = path.parent / "quickshell-state.log"
            copied_log.write_bytes(state_log.read_bytes())
            report.setdefault("logs", {})["quickshell_state"] = str(copied_log.relative_to(ROOT))
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGINT, previous_int)

    set_row(report, "quickshell_osd_socket", *outcome)
    if cleanup_error:
        report["rows"]["quickshell_osd_socket"]["cleanup_error"] = cleanup_error
    save_report(report, path)
    print(json.dumps(report["rows"]["quickshell_osd_socket"], sort_keys=True))
    return 1 if outcome[0] == "FAIL" or cleanup_error else 0


def run_smoke(path):
    report = load_report(path)
    outcome_file = ROOT / "build/osd-foundation-smoke/hooks-build.outcome"
    if outcome_file.is_file():
        outcome_text = outcome_file.read_text().strip()
        if outcome_text.startswith("PASS\t"):
            set_row(report, "meson_test_hooks_build", "PASS", "build_completed", outcome_text)
            report["commands"].append("make test-hooks")
        elif outcome_text.startswith("FAIL\t"):
            set_row(report, "meson_test_hooks_build", "FAIL", "build_failed", outcome_text)
        else:
            set_row(report, "meson_test_hooks_build", "NOT_RUN", "build_outcome_unknown", outcome_text)
    else:
        set_row(report, "meson_test_hooks_build", "NOT_RUN", "build_outcome_missing",
                "the headless step did not record make test-hooks completion")

    actual = source_identity()
    if actual["commit"] != report["source"]["commit"] or actual["tree"] != report["source"]["tree"]:
        set_unrun(report, "the container source identity differs from the initialized GitHub checkout",
                  "source_identity_mismatch")
        report["container"]["observed_source"] = actual
        save_report(report, path)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    if report["rows"]["meson_test_hooks_build"]["status"] != "PASS":
        set_unrun(report, "make test-hooks did not complete successfully before compositor assertions",
                  "meson_hooks_build_unavailable")
        save_report(report, path)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    versions = report["container"].get("packages", {})
    if report["container"].get("versions_recorded_before_smoke") is not True:
        set_unrun(report, "component versions were not recorded before this smoke",
                  "component_version_record_missing")
        save_report(report, path)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    if report["container"].get("image_identity") != "recorded":
        set_unrun(report, "the exact Arch container/base image identities were unavailable",
                  "container_identity_unavailable")
        save_report(report, path)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    missing = [package for package in PACKAGES if package not in versions]
    meson_command = report["container"].get("meson_version_command", {})
    if meson_command.get("exit") != 0 or not meson_command.get("output"):
        missing.append("meson --version")
    if missing:
        set_unrun(report, f"container package versions unavailable: {', '.join(missing)}",
                  "component_version_unavailable")
        save_report(report, path)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    run_id = "".join(ch for ch in os.environ.get("GITHUB_RUN_ID", "local") if ch.isalnum())
    attempt = "".join(ch for ch in os.environ.get("GITHUB_RUN_ATTEMPT", "1") if ch.isalnum())
    scratch = ROOT / "build" / f"osd-foundation-scratch-{run_id}-{attempt}"
    headless_dir = scratch / "session"
    if scratch.exists() or scratch.is_symlink():
        set_unrun(report, "the unique smoke scratch path already exists; it was preserved",
                  "scratch_path_unavailable")
        save_report(report, path)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    scratch.mkdir(mode=0o700)
    owner = str(uuid.uuid4())
    env = os.environ.copy()
    env.update({
        "SCOTTLAND_HEADLESS_ISOLATION": "1",
        "SCOTTLAND_TEST_SCRATCH": str(scratch),
        "SCOTTLAND_HEADLESS_DIR": str(headless_dir),
        "SCOTTLAND_HEADLESS_OWNER": owner,
        "SCOTTLAND_HEADLESS_OWNER_DIR": str(scratch),
        "SCOTTLAND_SESSION_DIR": str(headless_dir / "session"),
    })
    start_attempted = False
    started = False
    try:
        start_command = ["tests/headless.sh", "start", "--widgets"]
        report["commands"].append(" ".join(start_command))
        start_attempted = True
        start_status, start_output = run_capture(start_command, env=env, timeout=120)
        (path.parent / "wayfire-start.log").write_text(start_output)
        report.setdefault("logs", {})["wayfire_start"] = str(
            (path.parent / "wayfire-start.log").relative_to(ROOT))
        if start_status != 0:
            set_row(report, "wayfire_session_started", "NOT_RUN", "compositor_not_started",
                    f"canonical headless start exited {start_status}; see wayfire-start.log")
            set_unrun(report, "the canonical wrapper did not start the test-owned Wayfire session",
                      "compositor_not_started", exclude=("wayfire_session_started",))
            raise SmokePrerequisiteUnavailable
        started = True

        pid_path = headless_dir / "compositor.pid"
        try:
            pid = int(pid_path.read_text().strip())
            executable = os.readlink(f"/proc/{pid}/exe")
        except (OSError, ValueError) as error:
            set_row(report, "wayfire_session_started", "NOT_RUN", "component_identity_unavailable",
                    f"headless start returned success but Wayfire process identity was unavailable: {error}")
            set_unrun(report, "the test could not verify the real Wayfire executable",
                      "component_identity_unavailable", exclude=("wayfire_session_started",))
            raise SmokePrerequisiteUnavailable
        if Path(executable.removesuffix(" (deleted)")).name != "wayfire":
            set_row(report, "wayfire_session_started", "NOT_RUN", "component_identity_mismatch",
                    f"the headless wrapper process resolved to {executable!r}, not Wayfire")
            set_unrun(report, "the real Wayfire executable was not confirmed",
                      "component_identity_mismatch", exclude=("wayfire_session_started",))
            raise SmokePrerequisiteUnavailable
        set_row(report, "wayfire_session_started", "PASS", "real_component_started",
                f"canonical tests/headless.sh started Wayfire pid {pid} ({executable})")

        ipc_command = ["tests/headless.sh", "ipc", "scottland/desktop-model"]
        report["commands"].append(" ".join(ipc_command))
        ipc_status, ipc_output = run_capture(ipc_command, env=env, timeout=15)
        (path.parent / "wayfire-ipc.json").write_text(ipc_output)
        try:
            snapshot = json.loads(ipc_output)
        except json.JSONDecodeError:
            snapshot = None
        if ipc_status == 0 and isinstance(snapshot, dict) and isinstance(snapshot.get("version"), int):
            set_row(report, "wayfire_desktop_model_ipc", "PASS", "real_component_assertion",
                    f"Wayfire/Scottland IPC returned a desktop-model snapshot at version {snapshot['version']}")
        else:
            set_row(report, "wayfire_desktop_model_ipc", "FAIL", "real_component_assertion_failed",
                    f"desktop-model IPC exit={ipc_status}; response lacked an integer version (see wayfire-ipc.json)")

        probe_command = [
            "tests/headless.sh", "run", "timeout", "--signal=TERM", "--kill-after=10s", "45s",
            "python3", "tests/osd-foundation-smoke.py", "--probe", "--report", str(path),
        ]
        report["commands"].append(" ".join(probe_command))
        probe_status, probe_output = run_capture(probe_command, env=env, timeout=60)
        (path.parent / "quickshell-probe-wrapper.log").write_text(probe_output)
        report = load_report(path)
        if probe_status != 0:
            row = report["rows"]["quickshell_osd_socket"]
            if row["status"] == "NOT_RUN":
                set_row(report, "quickshell_osd_socket", "NOT_RUN", "probe_not_completed",
                        f"headless run wrapper exited {probe_status}; see quickshell-probe-wrapper.log")
        save_report(report, path)
    except SmokePrerequisiteUnavailable:
        pass
    finally:
        if started:
            stop_command = ["tests/headless.sh", "stop"]
            stop_status, stop_output = run_capture(stop_command, env=env, timeout=90)
            (path.parent / "headless-stop.log").write_text(stop_output)
            report = load_report(path)
            if stop_status == 0 and not headless_dir.exists():
                set_row(report, "session_cleanup", "PASS", "owned_session_stopped",
                        "tests/headless.sh stop returned 0 and removed its owned session directory")
                try:
                    scratch.rmdir()
                except OSError:
                    report["scratch_preserved"] = str(scratch.relative_to(ROOT))
            else:
                set_row(report, "session_cleanup", "FAIL", "owned_session_cleanup_failed",
                        f"tests/headless.sh stop exited {stop_status}; owned scratch was preserved at "
                        f"{scratch.relative_to(ROOT)}")
                report["scratch_preserved"] = str(scratch.relative_to(ROOT))
            save_report(report, path)
        elif start_attempted and headless_dir.exists():
            stop_command = ["tests/headless.sh", "stop", "--preserve-scratch"]
            report["commands"].append(" ".join(stop_command))
            stop_status, stop_output = run_capture(stop_command, env=env, timeout=90)
            (path.parent / "headless-stop.log").write_text(stop_output)
            report.setdefault("logs", {})["headless_stop"] = str(
                (path.parent / "headless-stop.log").relative_to(ROOT))
            if stop_status == 0:
                set_row(report, "session_cleanup", "PASS", "owned_partial_session_stopped",
                        "canonical tests/headless.sh stop --preserve-scratch completed after startup failure")
                report["scratch_preserved"] = str(scratch.relative_to(ROOT))
            else:
                set_row(report, "session_cleanup", "FAIL", "owned_session_cleanup_failed",
                        f"canonical stop after startup failure exited {stop_status}; scratch preserved at "
                        f"{scratch.relative_to(ROOT)}")
                report["scratch_preserved"] = str(scratch.relative_to(ROOT))
            save_report(report, path)
        elif start_attempted:
            set_row(report, "session_cleanup", "NOT_RUN", "no_session_scratch_created",
                    "the canonical wrapper created no session directory")
            save_report(report, path)
        elif scratch.exists() and not any(item.exists() for item in (headless_dir / "display", headless_dir / "pid")):
            try:
                if not any(scratch.iterdir()):
                    scratch.rmdir()
            except OSError:
                pass

    report = load_report(path)
    failed_rows = [name for name, row in report["rows"].items() if row["status"] == "FAIL"]
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if failed_rows else 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--initialize", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--record-versions", action="store_true")
    parser.add_argument("--not-run", action="store_true")
    parser.add_argument("--reason", default="")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    path = args.report if args.report.is_absolute() else ROOT / args.report
    if args.initialize:
        initialize(path)
        return 0
    if args.not_run:
        mark_not_run(path, args.reason or "the hosted smoke environment was not prepared")
        return 0
    if args.probe:
        return probe(path)
    if args.record_versions:
        record_versions(path)
        return 0
    if args.run:
        return run_smoke(path)
    parser.error("choose --initialize, --run, --probe, or --not-run")


if __name__ == "__main__":
    sys.exit(main())
