#!/usr/bin/env python3
"""DM1-DM7 behavior through scottland-display and Wayfire's reported output state."""
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = Path(sys.argv[1])
DISPLAY = (Path(os.environ["SCOTTLAND_HEADLESS_DIR"]) / "display").read_text().strip()
SESSION = Path(os.environ["SCOTTLAND_HEADLESS_DIR"]) / "session" / f"{DISPLAY}.env"
ENV = {}
for entry in SESSION.read_bytes().split(b"\0"):
    if b"=" in entry:
        key, value = entry.split(b"=", 1)
        ENV[key.decode()] = value.decode()
SOCKET = ENV["WAYFIRE_SOCKET"]
SESSION_DIR = Path(ENV["SCOTTLAND_SESSION_DIR"])
OVERRIDES = Path(ENV["XDG_CONFIG_HOME"]) / "scottland" / "overrides.ini"
CONFIG = Path(ENV["SCOTTLAND_CONFIG_OUTPUT"])
HOOKS = Path(ENV["SCOTTLAND_HOOKS"])
WATCH_LOG = Path(ENV["XDG_STATE_HOME"]) / "scottland" / "watch-config.log"
CLI = HOOKS / "libexec/scottland-display"
WINDOW_APP = "scottland-mirror-window"
client = None

sock = socket.socket(socket.AF_UNIX)
sock.connect(SOCKET)
sock.settimeout(5)


def ipc(method, data=None):
    body = json.dumps({"method": method, "data": data or {}}).encode()
    sock.sendall(struct.pack("<I", len(body)) + body)
    header = b""
    while len(header) < 4:
        header += sock.recv(4 - len(header))
    length = struct.unpack("<I", header)[0]
    response = b""
    while len(response) < length:
        response += sock.recv(length - len(response))
    value = json.loads(response)
    return value.get("info", value) if isinstance(value, dict) else value


def wait_for(predicate, label, timeout=12):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = predicate()
            if last:
                return last
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {label}; last observation: {last!r}")


def command(program, *args, timeout=25):
    argv = [str(program), *args]
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as error:
        process.kill()
        stdout, stderr = process.communicate()
        raise subprocess.TimeoutExpired(argv, timeout, output=stdout, stderr=stderr) from error
    result = subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
    result.pid = process.pid
    return result


mirror_commands = []


def display(*args):
    result = command(CLI, "mirror", *args)
    if args and args[0] in {"on", "off", "toggle"}:
        mirror_commands.append({
            "pid": result.pid,
            "args": list(args),
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        })
    return result


def outputs():
    return ipc("window-rules/list-outputs")


def output_named(name):
    return next((row for row in outputs() if row.get("name") == name), None)


def session_state():
    return ipc("scottland/session-state")


def view_by_app():
    views = ipc("window-rules/list-views")
    return next((view for view in views if view.get("app-id") == WINDOW_APP), None)


def capture_value(operation):
    try:
        value = operation()
    except Exception as error:
        return {"capture_error": f"{type(error).__name__}: {error}"}
    if isinstance(value, subprocess.CompletedProcess):
        return {
            "pid": getattr(value, "pid", None),
            "returncode": value.returncode,
            "stdout": value.stdout,
            "stderr": value.stderr,
        }
    return value


def watch_config_status():
    log = WATCH_LOG.read_text()
    pids = [line.rsplit("watching (pid ", 1)[1].split(")", 1)[0]
            for line in log.splitlines() if "watching (pid " in line]
    if not pids:
        return {"running": False, "reason": "watch log has no startup record"}
    proc = Path("/proc") / pids[-1]
    try:
        stat = (proc / "stat").read_text()
    except FileNotFoundError:
        return {"running": False}
    except OSError as error:
        return {"running": None, "error": str(error)}
    fields = stat[stat.rfind(")") + 2:].split()
    state = fields[0] if fields else "unknown"
    return {"running": state not in {"Z", "X", "unknown"}, "state": state}


def mirror_off_diagnostics(toggled_off, marker, failure, reloads_before_toggle):
    diagnostics = {
        "failure": str(failure),
        "toggle_command": capture_value(lambda: toggled_off),
        "config_reloads_before_toggle": reloads_before_toggle,
        "session_state": capture_value(session_state),
        "watch_config_log": capture_value(WATCH_LOG.read_text),
        "watch_config_process": capture_value(watch_config_status),
        "active_wayfire_outputs": capture_value(outputs),
        "scottland_output_list": capture_value(
            lambda: command(HOOKS / "libexec/scottland-output", "list", timeout=5)
        ),
        "display_status": capture_value(lambda: command(CLI, "mirror", "status", timeout=5)),
        "request_marker": capture_value(lambda: {
            "exists": marker.exists(),
            "content": marker.read_text() if marker.exists() else None,
        }),
        "generated_config": capture_value(CONFIG.read_text),
        "user_overrides": capture_value(OVERRIDES.read_text),
    }
    artifact = ARTIFACTS / "mirror-off-diagnostics.json"
    try:
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(json.dumps(diagnostics, indent=2, sort_keys=True) + "\n")
    except OSError as error:
        print(f"Could not write mirror-off diagnostics artifact: {error}", file=sys.stderr, flush=True)
    print("MIRROR-OFF-DIAGNOSTICS " + json.dumps(diagnostics, sort_keys=True), flush=True)


def mirror_unplug_diagnostics(marker, failure, last_poll, destroy_result, target):
    diagnostics = {
        "failure": str(failure),
        "last_poll": last_poll,
        "mirror_commands": mirror_commands,
        "simulated_unplug": {
            "target": {"id": target.get("id"), "name": target.get("name")},
            "ipc_result": destroy_result,
        },
        "session_state": capture_value(session_state),
        "watch_config_log": capture_value(WATCH_LOG.read_text),
        "watch_config_process": capture_value(watch_config_status),
        "active_wayfire_outputs": capture_value(outputs),
        "scottland_output_list": capture_value(
            lambda: command(HOOKS / "libexec/scottland-output", "list", timeout=5)
        ),
        "display_status": capture_value(lambda: command(CLI, "mirror", "status", timeout=5)),
        "request_marker": capture_value(lambda: {
            "exists": marker.exists(),
            "content": marker.read_text() if marker.exists() else None,
        }),
        "generated_config": capture_value(CONFIG.read_text),
        "user_overrides": capture_value(OVERRIDES.read_text),
    }
    artifact = ARTIFACTS / "mirror-unplug-diagnostics.json"
    try:
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(json.dumps(diagnostics, indent=2, sort_keys=True) + "\n")
    except OSError as error:
        print(f"Could not write mirror-unplug diagnostics artifact: {error}",
              file=sys.stderr, flush=True)
    print("MIRROR-UNPLUG-DIAGNOSTICS " + json.dumps(diagnostics, sort_keys=True), flush=True)


results = []


def check(name, passed, detail=None):
    results.append((name, bool(passed), detail))
    print(("PASS " if passed else "FAIL ") + name +
          ("" if passed or detail is None else f": {detail}"), flush=True)


def set_enabled(name, enabled):
    args = ("set", name, "--on" if enabled else "--off")
    result = command(HOOKS / "libexec/scottland-output", *args)
    if result.returncode != 0:
        raise AssertionError(f"scottland-output {' '.join(args)} failed: {result.stderr}")
    return result


def reset_outputs():
    result = command(HOOKS / "libexec/scottland-output", "reset")
    if result.returncode != 0:
        raise AssertionError(f"scottland-output reset failed: {result.stderr}")


def main():
    dynamic_output = ipc("wayfire/create-headless-output", {"width": 1280, "height": 720}).get("output")
    if not isinstance(dynamic_output, dict) or not isinstance(dynamic_output.get("id"), int):
        raise AssertionError(f"could not create the removable DP-1 fixture output: {dynamic_output!r}")

    expected_names = {"eDP-1", "DP-1", "HDMI-A-1"}
    def fixture_outputs_ready():
        rows = outputs()
        if ({row.get("name") for row in rows} == expected_names and
                any(row.get("id") == dynamic_output["id"] and row.get("name") == "DP-1"
                    for row in rows)):
            return rows
        return None

    found = wait_for(fixture_outputs_ready, "panel, both external outputs, and the removable DP-1 fixture")
    check("setup: panel and both external outputs are reported", found is not None, found)

    initial = session_state()
    check("initial state is extended", display("status").stdout.splitlines()[:1] == ["extended"], initial)
    set_enabled("DP-1", False)
    set_enabled("HDMI-A-1", False)
    no_external_before = session_state().get("config-reloads")
    no_external_config = CONFIG.read_bytes()
    no_external = display("on")
    no_external_toggle = display("toggle")
    check("on and toggle refuse without an enabled external output and explain why",
          no_external.returncode != 0 and no_external_toggle.returncode != 0 and
          "external output" in no_external.stderr and "external output" in no_external_toggle.stderr,
          (no_external.stderr.strip(), no_external_toggle.stderr.strip()))
    check("refusal leaves the request, config and compositor state unchanged",
          not (SESSION_DIR / "display-mirror" / DISPLAY / "request").exists() and
          CONFIG.read_bytes() == no_external_config and
          session_state().get("config-reloads") == no_external_before and
          display("status").stdout.splitlines()[:1] == ["extended"])

    reset_outputs()
    set_enabled("eDP-1", False)
    no_panel_before = session_state().get("config-reloads")
    no_panel_config = CONFIG.read_bytes()
    no_panel = display("on")
    no_panel_toggle = display("toggle")
    check("on refuses without an enabled built-in panel", no_panel.returncode != 0 and
          "built-in panel" in no_panel.stderr, no_panel.stderr.strip())
    check("on and toggle refuse without an enabled built-in panel",
          no_panel_toggle.returncode != 0 and "built-in panel" in no_panel_toggle.stderr,
          no_panel_toggle.stderr.strip())
    check("panel refusal leaves mirror state unchanged",
          not (SESSION_DIR / "display-mirror" / DISPLAY / "request").exists() and
          CONFIG.read_bytes() == no_panel_config and
          session_state().get("config-reloads") == no_panel_before)
    reset_outputs()
    wait_for(lambda: {row.get("name") for row in outputs()} == expected_names,
             "all outputs restored")

    OVERRIDES.parent.mkdir(parents=True, exist_ok=True)
    override_bytes = b"[output:DP-1]\nmode = 1280x720@60\n"
    before_override_reload = session_state().get("config-reloads", -1)
    OVERRIDES.write_bytes(override_bytes)
    wait_for(lambda: session_state().get("config-reloads", -1) > before_override_reload and
             "mode = 1280x720@60" in CONFIG.read_text(), "user mode config reload")
    check("user config mode is active before mirror", "mode = 1280x720@60" in CONFIG.read_text())

    rows = outputs()
    panel = next(row for row in rows if row.get("name") == "eDP-1")
    # Output ids can be reassigned while the fixture resets and reloads outputs. Resolve the
    # removable output from the current list instead of retaining the creation-time id.
    target = next(row for row in rows if row.get("name") == "DP-1")
    untouched = next(row for row in rows if row.get("name") == "HDMI-A-1")
    original_ids = (panel["id"], target["id"], untouched["id"])

    client = subprocess.Popen(["foot", "--app-id", WINDOW_APP,
                               "-T", "Mirror fixture", "-W", "32x8", "sh", "-c", "exec sleep 600"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    window = wait_for(view_by_app, "mirror test window")
    ipc("window-rules/configure-view", {
        "id": window["id"], "output_id": target["id"],
        "geometry": {"x": target["geometry"]["x"] + 30,
                     "y": target["geometry"]["y"] + 30,
                     "width": 480, "height": 320},
    })
    window = wait_for(lambda: (view_by_app() or {}).get("output-id") == target["id"] and view_by_app(),
                      "window placed on the first external output")
    original_window_id = window["id"]

    reloads_before_on = session_state().get("config-reloads", -1)
    on = display("on")
    check("on applies the first connector-ordered external output", on.returncode == 0,
          (on.stdout.strip(), on.stderr.strip()))
    state = wait_for(lambda: session_state() if session_state().get("config-reloads", -1) > reloads_before_on and
                     session_state().get("mirrors") else None, "Wayfire reports active mirror state")
    mirrors = state.get("mirrors", [])
    check("compositor reports DP-1 mirroring eDP-1 and leaves HDMI-A-1 unmirrored",
          any(row.get("source") == "eDP-1" and row.get("target") == "DP-1" for row in mirrors) and
          not any(row.get("target") == "HDMI-A-1" for row in mirrors), mirrors)
    status = display("status")
    lines = status.stdout.splitlines()
    check("status reports compositor mode and the overridden user mode",
          status.returncode == 0 and len(lines) >= 2 and lines[0] == "mirrored" and
          lines[1].startswith("DP-1: ") and
          "overriding the mode set in your config" in lines[1],
          (status.stdout.strip(), status.stderr.strip()))
    check("on explains that the user's output mode is overridden",
          "overrides the mode set in your config for DP-1" in on.stderr, on.stderr.strip())
    marker = SESSION_DIR / "display-mirror" / DISPLAY / "request"
    check("one session marker records the selected output pair",
          marker.read_text() == "eDP-1\nDP-1\n" if marker.exists() else False,
          marker.read_text() if marker.exists() else "marker missing")

    moved = wait_for(lambda: (view if (view := view_by_app()) and
                              view.get("output-id") == panel["id"] else None),
                     "window transferred from the mirrored output to the panel")
    check("window on the mirrored output stays open and moves to the panel",
          moved.get("id") == original_window_id, moved)

    again = display("on")
    check("on is idempotent while the compositor reports the mirror", again.returncode == 0 and
          any(row.get("target") == "DP-1" for row in session_state().get("mirrors", [])),
          (again.stdout.strip(), again.stderr.strip()))

    reloads_before_toggle = session_state().get("config-reloads")
    toggled_off = display("toggle")
    try:
        state_off = wait_for(lambda: session_state() if not session_state().get("mirrors") else None,
                             "Wayfire reports the restored output mode")
    except AssertionError as error:
        mirror_off_diagnostics(toggled_off, marker, error, reloads_before_toggle)
        raise
    off_status = display("status")
    check("toggle off returns to extended and the configured mode", toggled_off.returncode == 0 and
          off_status.stdout.splitlines()[:1] == ["extended"] and
          "mode = 1280x720@60" in CONFIG.read_text(),
          (off_status.stdout.strip(), CONFIG.read_text()))
    restored_output = output_named("DP-1")
    restored_geometry = restored_output.get("geometry", {}) if restored_output else {}
    check("off removes the marker and restores the configured output mode",
          not marker.exists() and OVERRIDES.read_bytes() == override_bytes and
          state_off.get("mirrors") == [] and
          restored_geometry.get("width") == 1280 and restored_geometry.get("height") == 720,
          {"state": state_off, "output": restored_output})
    after_off_window = view_by_app()
    check("window remains on the panel after mirror mode is removed, as after unplug and replug",
          after_off_window is not None and after_off_window.get("id") == original_window_id and
          after_off_window.get("output-id") == panel["id"],
          after_off_window)

    toggled_on = display("toggle")
    wait_for(lambda: session_state().get("mirrors"), "toggle on mirror state")
    explicit_off = display("off")
    final_state = wait_for(lambda: session_state() if not session_state().get("mirrors") else None,
                           "explicit off restores the configured mode")
    check("toggle on and explicit off complete through the watcher", toggled_on.returncode == 0 and
          explicit_off.returncode == 0 and display("status").stdout.splitlines()[:1] == ["extended"] and
          not marker.exists() and final_state.get("mirrors") == [], final_state)
    check("mirror operations do not edit the user's overrides file",
          OVERRIDES.read_bytes() == override_bytes)
    check("the active marker is confined to this recorded Wayland session",
          marker.parent == SESSION_DIR / "display-mirror" / DISPLAY and
          not (SESSION_DIR / "display-mirror" / "other-session" / "request").exists())

    unplug_on = display("on")
    wait_for(lambda: any(row.get("target") == "DP-1"
                         for row in session_state().get("mirrors", [])),
             "mirror active before output removal")
    last_unplug_poll = {}
    unplug_target = output_named("DP-1")
    if not isinstance(unplug_target, dict) or not isinstance(unplug_target.get("id"), int):
        raise AssertionError(f"removable DP-1 fixture disappeared before unplug: {unplug_target!r}")
    destroy_result = ipc("wayfire/destroy-headless-output", {"output-id": unplug_target["id"]})
    if isinstance(destroy_result, dict) and destroy_result.get("error"):
        error = AssertionError(f"simulated unplug failed: {destroy_result['error']}")
        mirror_unplug_diagnostics(marker, error, last_unplug_poll, destroy_result, target)
        raise error

    def unplug_cleared():
        state = session_state()
        marker_exists = marker.exists()
        status = display("status")
        marker_absent = not marker_exists
        mirrors_empty = not state.get("mirrors")
        status_extended = status.stdout.splitlines()[:1] == ["extended"]
        last_unplug_poll.clear()
        last_unplug_poll.update({
            "request_marker_exists": marker_exists,
            "session_state": state,
            "display_status": capture_value(lambda: status),
            "predicate": {
                "request_marker_absent": marker_absent,
                "session_mirrors_empty": mirrors_empty,
                "status_extended": status_extended,
            },
        })
        if marker_absent and mirrors_empty and status_extended:
            return state
        return None

    try:
        unplugged = wait_for(unplug_cleared, "unplug clears the mirror request")
    except AssertionError as error:
        mirror_unplug_diagnostics(marker, error, last_unplug_poll, destroy_result, target)
        raise
    remaining = wait_for(lambda: rows if len(rows := outputs()) == 2 else None,
                         "only the panel and second external remain")
    unplugged_window = wait_for(
        lambda: (view if (view := view_by_app()) and view.get("output-id") == panel["id"] else None),
        "window remains mapped to the panel after unplug")
    check("unplug ends mirroring and leaves the window visible on the panel",
          unplug_on.returncode == 0 and unplugged.get("mirrors") == [] and
          not marker.exists() and unplugged_window.get("id") == original_window_id and
          {row.get("name") for row in remaining} == {"eDP-1", "HDMI-A-1"},
          {"state": unplugged, "outputs": remaining, "window": unplugged_window})

    ipc("wayfire/create-headless-output", {"width": 1280, "height": 720})
    reconnected = wait_for(lambda: output_named("DP-1"), "the external output reconnects")
    reconnected_state = session_state()
    check("reconnected output starts extended at its configured mode",
          display("status").stdout.splitlines()[:1] == ["extended"] and
          not reconnected_state.get("mirrors") and
          reconnected.get("geometry", {}).get("width") == 1280 and
          reconnected.get("geometry", {}).get("height") == 720,
          {"state": reconnected_state, "output": reconnected})
    reconnected_window = view_by_app()
    check("reconnected output does not hide or steal the unplugged window",
          reconnected_window is not None and reconnected_window.get("id") == original_window_id and
          reconnected_window.get("output-id") == panel["id"], reconnected_window)
    check("unplug and replug leave the user's output configuration unchanged",
          OVERRIDES.read_bytes() == override_bytes and
          not (SESSION_DIR / "display-mirror" / "other-session" / "request").exists())

    (ARTIFACTS / "final-wayfire.ini").write_bytes(CONFIG.read_bytes())
    (ARTIFACTS / "override.ini").write_bytes(OVERRIDES.read_bytes())
    print(f"OBSERVED output ids: panel={original_ids[0]} mirror-target={original_ids[1]} other={original_ids[2]}")
    failures = [name for name, passed, _ in results if not passed]
    print(f"{'FAIL' if failures else 'PASS'}: {len(results) - len(failures)}/{len(results)} scenarios")
    return 1 if failures else 0


try:
    sys.exit(main())
finally:
    if client is not None and client.poll() is None:
        client.terminate()
        try:
            client.wait(timeout=3)
        except subprocess.TimeoutExpired:
            client.kill()
            client.wait(timeout=3)
    sock.close()
