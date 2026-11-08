#!/usr/bin/env python3
"""Hosted smoke for the real Wayfire session and Quickshell OSD socket host.

The workflow initializes the evidence before building, then runs this file in the
prepared Arch container. The --probe mode is invoked through tests/headless.sh run
so QuickShell receives the environment of the test-owned Wayfire session.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import signal
import socket
import struct
import subprocess
import sys
import time
import uuid


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "build/osd-foundation-smoke/report.json"
DEFAULT_SHOWN_ACK_REPORT = ROOT / "build/osd-shown-ack-evidence/report.json"
PACKAGES = ("wayfire", "wlroots0.20", "quickshell", "meson")
SHOWN_ACK_BASE_COMMIT = "48299defac7d71806324cafc4e7696466dcdb19a"
SHOWN_ACK_PRODUCT_COMMIT = "bbd89461b18f97ee0387f6433f0e14bebbf12016"
SHOWN_ACK_PRODUCT_TREE = "e71eb27bbe8c65aee3ac62b68d79e0af0fae4dea"
SHOWN_ACK_LABEL = "osd-shown-ack-reviewed"
SHOWN_ACK_HARNESS_PATHS = {
    ".github/workflows/fast-checks.yml",
    "tests/osd-foundation-smoke.py",
}


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


def source_identity(*, sanitize_branch=False):
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    tree = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=ROOT, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    identity = {
        "commit": commit,
        "tree": tree,
        "github_sha": os.environ.get("GITHUB_SHA", ""),
        "ref": os.environ.get("GITHUB_REF", ""),
        "event": os.environ.get("GITHUB_EVENT_NAME", ""),
        "base_ref": os.environ.get("GITHUB_BASE_REF", ""),
        "head_ref": os.environ.get("GITHUB_HEAD_REF", ""),
    }
    if sanitize_branch:
        identity.pop("ref")
        identity.pop("head_ref")
    return identity


def run_url():
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "")
    if repository and run_id:
        suffix = f"?attempt={attempt}" if attempt else ""
        return f"{server}/{repository}/actions/runs/{run_id}{suffix}"
    return "unavailable in this invocation"


def initial_report(*, shown_ack=False):
    report = {
        "schema": 1,
        "run_url": run_url(),
        "source": source_identity(sanitize_branch=shown_ack),
        "source_paths": {
            "osd_host_hook": "core/autostart.d/11-widget-osd-host",
            "osd_session_qml": "core/widgets/session/shell.qml",
            "osd_panel": (
                "core/widgets/osd/OsdPanel.qml (target of the shown-ACK request)"
                if shown_ack else "core/widgets/osd/OsdPanel.qml (not loaded by this smoke)"
            ),
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
                "detail": (
                    "the shown-ACK driver has not captured a frame"
                    if shown_ack else "visible-panel acceptance is separate from this foundation smoke"
                ),
            },
            "shown_ack": {
                "status": "NOT_RUN", "class": "separate_acceptance",
                "detail": (
                    "the single osd.present request has not been sent"
                    if shown_ack else "shown-ACK acceptance is separate; no osd.present request is sent"
                ),
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
    if shown_ack:
        report["product_source"] = {
            "commit": os.environ.get("SCOTTLAND_PRODUCT_SOURCE", ""),
            "tree": os.environ.get("SCOTTLAND_PRODUCT_TREE", ""),
        }
        report["harness_source"] = report["source"].copy()
        report["activation"] = {
            "event": os.environ.get("GITHUB_EVENT_NAME", ""),
            "action": os.environ.get("SCOTTLAND_EVENT_ACTION", ""),
            "label": os.environ.get("SCOTTLAND_SHOWN_ACK_LABEL", ""),
            "reviewed_head": os.environ.get("SCOTTLAND_REVIEWED_HEAD", ""),
            "pull_request_base_sha": os.environ.get("SCOTTLAND_PULL_REQUEST_BASE_SHA", ""),
            "status": "NOT_RUN",
        }
        report["requests"] = {
            "osd.present": {
                "target": "focused output selected by the session shell",
                "command_invocations": 0,
                "delivery": "NOT_RUN",
                "ack": "NOT_RUN",
            }
        }
        report["rows"]["rendered_frame"] = {
            "status": "NOT_RUN", "class": "pending",
            "detail": "no OSD frame has been captured",
        }
        report["rows"]["focused_output"] = {
            "status": "NOT_RUN", "class": "pending",
            "detail": "the focused output has not been queried",
        }
        report["rows"]["full_c10_acceptance"] = {
            "status": "NOT_RUN", "class": "separate_acceptance",
            "detail": "full C10 OSD acceptance is separate from the single shown-ACK check",
        }
    return report


def load_report(path, *, shown_ack=False):
    if path.is_file():
        return json.loads(path.read_text())
    return initial_report(shown_ack=shown_ack)


def save_report(report, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def set_row(report, name, status, classification, detail, **evidence):
    report["rows"][name] = {
        "status": status,
        "class": classification,
        "detail": detail,
        **evidence,
    }


def set_unrun(report, reason, classification="prerequisite_unavailable", *, exclude=()):
    excluded = set(exclude)
    for name in ("wayfire_session_started", "wayfire_desktop_model_ipc",
                 "quickshell_osd_socket", "quickshell_process_cleanup", "session_cleanup"):
        if name not in excluded and report["rows"][name]["status"] == "NOT_RUN":
            set_row(report, name, "NOT_RUN", classification, reason)


class SmokePrerequisiteUnavailable(Exception):
    """Stop this smoke after recording which required component could not start."""


def initialize(path, *, shown_ack=False):
    report = initial_report(shown_ack=shown_ack)
    save_report(report, path)
    print(json.dumps({"report": str(path.relative_to(ROOT)),
                      "commit": report["source"]["commit"],
                      "tree": report["source"]["tree"],
                      "run_url": report["run_url"]}, sort_keys=True))


def command_output(*command):
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    return result.returncode, result.stdout.strip()


def validate_shown_ack_activation(report):
    expected_context = {
        "event": "pull_request",
        "action": "labeled",
        "label": SHOWN_ACK_LABEL,
        "base_ref": "0.4",
        "base_sha": SHOWN_ACK_BASE_COMMIT,
        "product_commit": SHOWN_ACK_PRODUCT_COMMIT,
        "product_tree": SHOWN_ACK_PRODUCT_TREE,
    }
    actual_context = {
        "event": os.environ.get("GITHUB_EVENT_NAME", ""),
        "action": os.environ.get("SCOTTLAND_EVENT_ACTION", ""),
        "label": os.environ.get("SCOTTLAND_SHOWN_ACK_LABEL", ""),
        "base_ref": os.environ.get("GITHUB_BASE_REF", ""),
        "base_sha": os.environ.get("SCOTTLAND_PULL_REQUEST_BASE_SHA", ""),
        "product_commit": os.environ.get("SCOTTLAND_PRODUCT_SOURCE", ""),
        "product_tree": os.environ.get("SCOTTLAND_PRODUCT_TREE", ""),
    }
    failures = [f"{key} expected {value!r}, observed {actual_context[key]!r}"
                for key, value in expected_context.items() if actual_context[key] != value]

    current = source_identity()
    if current["github_sha"] != current["commit"]:
        failures.append("checked-out harness commit differs from GITHUB_SHA")
    reviewed_head = os.environ.get("SCOTTLAND_REVIEWED_HEAD", "")
    base_sha = os.environ.get("SCOTTLAND_PULL_REQUEST_BASE_SHA", "")
    parent_status, parent_output = command_output("git", "rev-list", "--parents", "-n", "1", "HEAD")
    parents = parent_output.split()[1:] if parent_status == 0 else []
    if parents != [base_sha, reviewed_head]:
        failures.append("the checked-out pull-request merge parents do not match the recorded base and reviewed head")

    tree_status, product_tree = command_output("git", "rev-parse", f"{SHOWN_ACK_PRODUCT_COMMIT}^{{tree}}")
    if tree_status != 0 or product_tree != SHOWN_ACK_PRODUCT_TREE:
        failures.append("the pinned product commit is unavailable or has a different tree")
    paths_status, paths_output = command_output(
        "git", "diff", "--name-only", SHOWN_ACK_PRODUCT_COMMIT, "HEAD")
    changed_paths = sorted(paths_output.splitlines()) if paths_status == 0 else []
    if paths_status != 0:
        failures.append("the harness changed-path list could not be read")
    elif set(changed_paths) != SHOWN_ACK_HARNESS_PATHS:
        failures.append("the harness diff does not contain exactly the two shown-ACK test files")

    report["activation"].update({
        "status": "NOT_RUN" if failures else "PASS",
        "checked_context": actual_context,
        "harness_commit": current["commit"],
        "harness_tree": current["tree"],
        "reviewed_head": reviewed_head,
        "changed_paths": changed_paths,
        "failures": failures,
    })
    return not failures


def validate_shown_ack_handoff(report):
    """Recheck the outer activation result without relying on session env vars."""
    activation = report.get("activation", {})
    expected_context = {
        "event": "pull_request",
        "action": "labeled",
        "label": SHOWN_ACK_LABEL,
        "base_ref": "0.4",
        "base_sha": SHOWN_ACK_BASE_COMMIT,
        "product_commit": SHOWN_ACK_PRODUCT_COMMIT,
        "product_tree": SHOWN_ACK_PRODUCT_TREE,
    }
    failures = []
    if activation.get("status") != "PASS":
        failures.append("outer shown-ACK activation validation was not recorded as PASS")
    if activation.get("checked_context") != expected_context:
        failures.append("the recorded activation context does not match the required event, label, base, and product")

    current = source_identity(sanitize_branch=True)
    source = report.get("source", {})
    if (activation.get("harness_commit") != current["commit"]
            or activation.get("harness_tree") != current["tree"]
            or source.get("commit") != current["commit"]
            or source.get("tree") != current["tree"]):
        failures.append("the session harness identity differs from the outer validated report")

    reviewed_head = activation.get("reviewed_head", "")
    parent_status, parent_output = command_output("git", "rev-list", "--parents", "-n", "1", "HEAD")
    parents = parent_output.split()[1:] if parent_status == 0 else []
    if parents != [SHOWN_ACK_BASE_COMMIT, reviewed_head] or not reviewed_head:
        failures.append("the session pull-request merge parents differ from the recorded base and reviewed head")

    tree_status, product_tree = command_output("git", "rev-parse", f"{SHOWN_ACK_PRODUCT_COMMIT}^{{tree}}")
    if tree_status != 0 or product_tree != SHOWN_ACK_PRODUCT_TREE:
        failures.append("the pinned product commit is unavailable or has a different tree")
    paths_status, paths_output = command_output(
        "git", "diff", "--name-only", SHOWN_ACK_PRODUCT_COMMIT, "HEAD")
    changed_paths = sorted(paths_output.splitlines()) if paths_status == 0 else []
    if paths_status != 0 or set(changed_paths) != SHOWN_ACK_HARNESS_PATHS:
        failures.append("the session harness diff no longer contains exactly the two shown-ACK test files")

    report["probe_activation_handoff"] = {
        "status": "FAIL" if failures else "PASS",
        "detail": (
            "the outer event/label validation was handed into the session and exact source identity was rechecked"
            if not failures else "the recorded activation handoff or exact source identity did not validate"
        ),
        "harness_commit": current["commit"],
        "harness_tree": current["tree"],
        "reviewed_head": reviewed_head,
        "changed_paths": changed_paths,
        "failures": failures,
    }
    return not failures


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


def wait_for_osd_layer_view(path, report, focus_name, *, timeout=3.0):
    """Wait for Wayfire to report the OSD layer mapped on the focused output."""
    outputs_command = ["python3", "tests/wfipc.py", "window-rules/list-outputs"]
    report["commands"].append(shlex.join(outputs_command))
    outputs_status, outputs_output = run_capture(outputs_command, timeout=2)
    outputs_path = path.parent / "osd-wayfire-outputs.json"
    outputs_path.write_text(outputs_output)
    report.setdefault("logs", {})["osd_wayfire_outputs"] = str(outputs_path.relative_to(ROOT))
    try:
        outputs = json.loads(outputs_output)
    except json.JSONDecodeError:
        outputs = None
    focused = next((output for output in outputs if isinstance(output, dict)
                    and output.get("name") == focus_name), None) if isinstance(outputs, list) else None
    if outputs_status != 0 or focused is None or focused.get("id") is None:
        return None, {
            "outputs_command": shlex.join(outputs_command),
            "outputs_exit": outputs_status,
            "outputs_output": outputs_output,
            "focused_output": focus_name,
            "detail": "Wayfire did not report the focused output in window-rules/list-outputs",
        }

    views_command = ["python3", "tests/wfipc.py", "window-rules/list-views"]
    report["commands"].append(shlex.join(views_command))
    deadline = time.monotonic() + timeout
    observations = []
    matched = None
    while time.monotonic() < deadline:
        remaining = max(0.1, deadline - time.monotonic())
        status, output = run_capture(views_command, timeout=min(1.0, remaining))
        try:
            views = json.loads(output)
        except json.JSONDecodeError:
            views = None
        osd_views = [view for view in views if isinstance(view, dict)
                     and view.get("app-id") == "scottland-osd"
                     and view.get("layer") == "overlay"
                     and view.get("mapped") is True
                     and view.get("output-id") == focused["id"]] if isinstance(views, list) else []
        observations.append({"exit": status, "output": output, "matching_views": osd_views})
        if status == 0 and osd_views:
            matched = osd_views[0]
            break
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(0.1, remaining))

    views_path = path.parent / "osd-wayfire-layer-views.jsonl"
    views_path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in observations))
    report.setdefault("logs", {})["osd_wayfire_layer_views"] = str(views_path.relative_to(ROOT))
    evidence = {
        "outputs_command": shlex.join(outputs_command),
        "outputs_exit": outputs_status,
        "focused_output": focus_name,
        "focused_output_id": focused["id"],
        "views_command": shlex.join(views_command),
        "view_query_count": len(observations),
        "views_log": str(views_path.relative_to(ROOT)),
        "matching_view": matched,
    }
    if matched is None:
        evidence["detail"] = (
            "Wayfire did not report a mapped scottland-osd overlay layer on the focused output "
            "within the bounded wait after shown ACK"
        )
    return matched, evidence


def capture_shown_ack(path, report, quickshell_process):
    binary = ROOT / "build/tools/bin/scottland-widget"
    if not binary.is_file() or not os.access(binary, os.X_OK):
        set_row(report, "shown_ack", "NOT_RUN", "built_client_unavailable",
                "the checkout-built scottland-widget executable is unavailable")
        set_row(report, "visible_panel", "NOT_RUN", "shown_ack_unavailable",
                "no shown acknowledgement was received")
        return

    report.setdefault("components", {})["scottland_widget"] = {
        "path": str(binary.relative_to(ROOT)),
        "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
    }
    version_command = [str(binary), "--version"]
    report["commands"].append(shlex.join(version_command))
    version_status, version_output = run_capture(version_command, timeout=5)
    (path.parent / "scottland-widget-version.log").write_text(version_output)
    report.setdefault("logs", {})["scottland_widget_version"] = str(
        (path.parent / "scottland-widget-version.log").relative_to(ROOT))
    report["components"]["scottland_widget"].update({
        "version_command_exit": version_status,
        "version_output": version_output,
    })
    if version_status != 0 or not version_output.startswith("scottland-widget "):
        set_row(report, "shown_ack", "NOT_RUN", "built_client_identity_unavailable",
                "the checkout-built scottland-widget version could not be identified",
                command=shlex.join(version_command), command_exit=version_status,
                command_output=version_output)
        set_row(report, "visible_panel", "NOT_RUN", "shown_ack_unavailable",
                "no shown acknowledgement was received")
        return

    focus_command = [str(binary), "__focused-output"]
    report["commands"].append(shlex.join(focus_command))
    focus_status, focus_output = run_capture(focus_command, timeout=10)
    (path.parent / "focused-output.log").write_text(focus_output)
    report.setdefault("logs", {})["focused_output"] = str(
        (path.parent / "focused-output.log").relative_to(ROOT))
    focus_name = focus_output.strip()
    if focus_status != 0 or not focus_name or "\n" in focus_name or quickshell_process.poll() is not None:
        set_row(report, "focused_output", "FAIL", "focused_output_unavailable",
                "the checkout-built client could not identify the live session's focused output",
                command=shlex.join(focus_command), command_exit=focus_status,
                command_output=focus_output)
        set_row(report, "shown_ack", "NOT_RUN", "focused_output_unavailable",
                "no osd.present request was sent because focused output was unavailable")
        set_row(report, "visible_panel", "NOT_RUN", "shown_ack_unavailable",
                "no shown acknowledgement was received")
        return

    set_row(report, "focused_output", "PASS", "real_wayfire_focused_output",
            "the checkout-built client resolved the focused Wayfire output",
            output=focus_name, command=shlex.join(focus_command),
            command_exit=focus_status, command_output=focus_output)
    osd_command = [str(binary), "osd", "--message", "Shown acknowledgement check", "--duration", "12000"]
    report["commands"].append(shlex.join(osd_command))
    request = report["requests"]["osd.present"]
    request["command_invocations"] = 1
    request["delivery"] = "not_confirmed_until_ack"
    request["focused_output_before_request"] = focus_name
    save_report(report, path)
    osd_status, osd_output = run_capture(osd_command, timeout=15)
    (path.parent / "osd-present.log").write_text(osd_output)
    report.setdefault("logs", {})["osd_present"] = str(
        (path.parent / "osd-present.log").relative_to(ROOT))
    request.update({
        "command": shlex.join(osd_command),
        "command_exit": osd_status,
        "command_output": osd_output,
        "ack": "shown" if osd_status == 0 else "not_shown",
        "delivery": "acknowledged" if osd_status == 0 else "unconfirmed_without_ack",
        "ack_source": "the client exits 0 only when its session reply is result=shown with no error",
    })
    if osd_status == 0:
        set_row(report, "shown_ack", "PASS", "real_session_shown_ack",
                "the session acknowledged this single osd.present request as shown",
                command=shlex.join(osd_command), command_exit=osd_status,
                command_output=osd_output, command_invocations=1)
        save_report(report, path)
    else:
        set_row(report, "shown_ack", "FAIL", "real_session_shown_ack_failed",
                "the single osd.present invocation did not receive a shown acknowledgement",
                command=shlex.join(osd_command), command_exit=osd_status,
                command_output=osd_output, command_invocations=1)
        set_row(report, "visible_panel", "NOT_RUN", "shown_ack_failed",
                "no frame was captured because the session did not acknowledge shown")
        save_report(report, path)
        return

    layer_view, layer_evidence = wait_for_osd_layer_view(path, report, focus_name)
    if layer_view is None:
        set_row(report, "visible_panel", "FAIL", "osd_layer_not_observed",
                layer_evidence.pop("detail"), **layer_evidence)
        set_row(report, "rendered_frame", "NOT_RUN", "osd_layer_not_observed",
                "no frame was captured because Wayfire did not report the mapped OSD layer",
                output=focus_name)
        save_report(report, path)
        return
    set_row(report, "visible_panel", "PASS", "wayfire_mapped_osd_layer",
            "Wayfire reported the mapped scottland-osd overlay layer on the focused output after shown ACK",
            output=focus_name, wayfire_view=layer_view, **layer_evidence)
    save_report(report, path)

    frame_path = path.parent / "osd-panel.png"
    frame_command = ["grim", "-o", focus_name, str(frame_path)]
    report["commands"].append(shlex.join(frame_command))
    frame_status, frame_output = run_capture(frame_command, timeout=5)
    (path.parent / "grim-frame.log").write_text(frame_output)
    report.setdefault("logs", {})["grim_frame"] = str(
        (path.parent / "grim-frame.log").relative_to(ROOT))
    frame_info = {
        "command": shlex.join(frame_command),
        "command_exit": frame_status,
        "command_output": frame_output,
        "path": str(frame_path.relative_to(ROOT)),
    }
    if frame_status == 0 and frame_path.is_file():
        frame_bytes = frame_path.read_bytes()
        if frame_bytes.startswith(b"\x89PNG\r\n\x1a\n") and len(frame_bytes) >= 24:
            width, height = struct.unpack(">II", frame_bytes[16:24])
        else:
            width, height = 0, 0
        if width > 0 and height > 0:
            frame_info.update({
                "sha256": hashlib.sha256(frame_bytes).hexdigest(),
                "width": width,
                "height": height,
                "format": "PNG",
            })
            set_row(report, "rendered_frame", "PASS", "real_wayfire_frame_captured",
                    "grim captured a valid frame from the focused output after shown acknowledgement",
                    **frame_info)
        else:
            set_row(report, "rendered_frame", "FAIL", "invalid_frame_capture",
                    "grim returned success but did not produce a valid PNG frame", **frame_info)
    else:
        set_row(report, "rendered_frame", "FAIL", "frame_capture_failed",
                "grim did not capture the focused output after shown acknowledgement", **frame_info)
    save_report(report, path)


def probe(path, *, shown_ack=False):
    report = load_report(path, shown_ack=shown_ack)
    if shown_ack and not validate_shown_ack_handoff(report):
        save_report(report, path)
        set_row(report, "quickshell_osd_socket", "NOT_RUN", "activation_handoff_unavailable",
                "the session probe did not receive a valid outer activation record")
        save_report(report, path)
        print(json.dumps(report["probe_activation_handoff"], indent=2, sort_keys=True))
        return 1
    if shown_ack:
        save_report(report, path)
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
                    "a Unix socket that accepted a preflight connection",
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
            if shown_ack and outcome[0] == "PASS":
                report.setdefault("components", {})["quickshell"] = {
                    "package_version": report.get("container", {}).get("packages", {}).get("quickshell", ""),
                    "processes": [{"executable": executable, "arguments": arguments}
                                  for _, executable, arguments in quickshell_processes],
                }
                capture_shown_ack(path, report, process)
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
    failed_rows = [name for name, row in report.get("rows", {}).items()
                   if row.get("status") == "FAIL"]
    required_rows = ("focused_output", "shown_ack", "rendered_frame", "visible_panel")
    incomplete_shown_ack = shown_ack and any(
        report.get("rows", {}).get(name, {}).get("status") != "PASS"
        for name in required_rows)
    return 1 if outcome[0] == "FAIL" or cleanup_error or failed_rows or incomplete_shown_ack else 0


def run_smoke(path, *, shown_ack=False):
    report = load_report(path, shown_ack=shown_ack)
    activation_valid = validate_shown_ack_activation(report) if shown_ack else True
    if shown_ack:
        save_report(report, path)
    if not activation_valid:
        report["not_run_reason"] = "shown-ACK activation or source identity did not match"
        save_report(report, path)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1
    outcome_file = ROOT / "build/osd-foundation-smoke/hooks-build.outcome"
    if outcome_file.is_file():
        report.setdefault("logs", {})["hooks_build_outcome"] = str(
            outcome_file.relative_to(ROOT))
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

    actual = source_identity(sanitize_branch=shown_ack)
    if actual["commit"] != report["source"]["commit"] or actual["tree"] != report["source"]["tree"]:
        set_unrun(report, "the container source identity differs from the initialized GitHub checkout",
                  "source_identity_mismatch")
        report["container"]["observed_source"] = actual
        save_report(report, path)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1 if shown_ack else 0
    if report["rows"]["meson_test_hooks_build"]["status"] != "PASS":
        set_unrun(report, "make test-hooks did not complete successfully before compositor assertions",
                  "meson_hooks_build_unavailable")
        save_report(report, path)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1 if shown_ack else 0

    versions = report["container"].get("packages", {})
    if report["container"].get("versions_recorded_before_smoke") is not True:
        set_unrun(report, "component versions were not recorded before this smoke",
                  "component_version_record_missing")
        save_report(report, path)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1 if shown_ack else 0
    if report["container"].get("image_identity") != "recorded":
        set_unrun(report, "the exact Arch container/base image identities were unavailable",
                  "container_identity_unavailable")
        save_report(report, path)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1 if shown_ack else 0
    missing = [package for package in PACKAGES if package not in versions]
    meson_command = report["container"].get("meson_version_command", {})
    if meson_command.get("exit") != 0 or not meson_command.get("output"):
        missing.append("meson --version")
    if missing:
        set_unrun(report, f"container package versions unavailable: {', '.join(missing)}",
                  "component_version_unavailable")
        save_report(report, path)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1 if shown_ack else 0

    if shown_ack:
        report.setdefault("components", {})["wayfire"] = {
            "package_version": versions.get("wayfire", ""),
            "wlroots_version": versions.get("wlroots0.20", ""),
        }
        report["components"]["meson"] = {
            "package_version": versions.get("meson", ""),
            "version_command": meson_command,
        }

    run_id = "".join(ch for ch in os.environ.get("GITHUB_RUN_ID", "local") if ch.isalnum())
    attempt = "".join(ch for ch in os.environ.get("GITHUB_RUN_ATTEMPT", "1") if ch.isalnum())
    scratch_kind = "osd-shown-ack" if shown_ack else "osd-foundation"
    scratch = ROOT / "build" / f"{scratch_kind}-scratch-{run_id}-{attempt}"
    headless_dir = scratch / "session"
    if scratch.exists() or scratch.is_symlink():
        set_unrun(report, "the unique smoke scratch path already exists; it was preserved",
                  "scratch_path_unavailable")
        save_report(report, path)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1 if shown_ack else 0
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
            save_report(report, path)
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
            save_report(report, path)
            raise SmokePrerequisiteUnavailable
        if Path(executable.removesuffix(" (deleted)")).name != "wayfire":
            set_row(report, "wayfire_session_started", "NOT_RUN", "component_identity_mismatch",
                    f"the headless wrapper process resolved to {executable!r}, not Wayfire")
            set_unrun(report, "the real Wayfire executable was not confirmed",
                      "component_identity_mismatch", exclude=("wayfire_session_started",))
            save_report(report, path)
            raise SmokePrerequisiteUnavailable
        set_row(report, "wayfire_session_started", "PASS", "real_component_started",
                f"canonical tests/headless.sh started Wayfire pid {pid} ({executable})")
        save_report(report, path)

        ipc_command = ["tests/headless.sh", "ipc", "scottland/desktop-model"]
        report["commands"].append(" ".join(ipc_command))
        ipc_status, ipc_output = run_capture(ipc_command, env=env, timeout=15)
        ipc_path = path.parent / "wayfire-ipc.json"
        ipc_path.write_text(ipc_output)
        report.setdefault("logs", {})["wayfire_ipc"] = str(ipc_path.relative_to(ROOT))
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
        save_report(report, path)

        probe_deadline = "90s" if shown_ack else "45s"
        probe_command = [
            "tests/headless.sh", "run", "timeout", "--signal=TERM", "--kill-after=10s", probe_deadline,
            "python3", "tests/osd-foundation-smoke.py", "--probe",
        ]
        if shown_ack:
            probe_command.append("--shown-ack")
        probe_command.extend(["--report", str(path)])
        report["commands"].append(" ".join(probe_command))
        save_report(report, path)
        probe_status, probe_output = run_capture(
            probe_command, env=env, timeout=120 if shown_ack else 60)
        (path.parent / "quickshell-probe-wrapper.log").write_text(probe_output)
        report = load_report(path)
        if probe_status != 0:
            row = report["rows"]["quickshell_osd_socket"]
            if row["status"] == "NOT_RUN":
                set_row(report, "quickshell_osd_socket", "NOT_RUN", "probe_not_completed",
                        f"headless run wrapper exited {probe_status}; see quickshell-probe-wrapper.log")
        save_report(report, path)
    except SmokePrerequisiteUnavailable:
        save_report(report, path)
    except Exception as error:
        report["smoke_error"] = f"{type(error).__name__}: {error}"
        save_report(report, path)
        raise
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
    required_rows = ("focused_output", "shown_ack", "rendered_frame", "visible_panel")
    incomplete_shown_ack = shown_ack and any(
        report.get("rows", {}).get(name, {}).get("status") != "PASS"
        for name in required_rows)
    return 1 if failed_rows or incomplete_shown_ack else 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--initialize", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--shown-ack", action="store_true")
    parser.add_argument("--record-versions", action="store_true")
    parser.add_argument("--not-run", action="store_true")
    parser.add_argument("--reason", default="")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report_path = args.report or (DEFAULT_SHOWN_ACK_REPORT if args.shown_ack else DEFAULT_REPORT)
    path = report_path if report_path.is_absolute() else ROOT / report_path
    if args.initialize:
        initialize(path, shown_ack=args.shown_ack)
        return 0
    if args.not_run:
        mark_not_run(path, args.reason or "the hosted smoke environment was not prepared")
        return 0
    if args.probe:
        return probe(path, shown_ack=args.shown_ack)
    if args.record_versions:
        record_versions(path)
        return 0
    if args.run:
        return run_smoke(path, shown_ack=args.shown_ack)
    parser.error("choose --initialize, --run, --probe, or --not-run")


if __name__ == "__main__":
    sys.exit(main())
