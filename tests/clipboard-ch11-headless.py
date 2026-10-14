#!/usr/bin/env python3
"""Real-input CH11 acceptance for one private tests/headless.sh session."""

import json
import os
from pathlib import Path
import signal
import socket
import struct
import subprocess
import sys
import time


REPO = Path(__file__).resolve().parents[1]
HEADLESS = REPO / "tests/headless.sh"
HOOKS = REPO / "build/hooks"
PICKER = REPO / "core/libexec/scottland-list-picker"
CLIPBOARD = REPO / "core/libexec/scottland-clipboard"
SERVICE = REPO / "build/scottland-clipboardd"
ART = Path(sys.argv[1]).resolve()
ART.mkdir(parents=True, exist_ok=True)
ENV = os.environ.copy()
ENV["SCOTTLAND_HOOKS"] = str(HOOKS)
ENV["PATH"] = f"{HOOKS / 'libexec'}:/usr/local/bin:/usr/bin:/bin"
RESULTS = []
CHILDREN = []
HELD_KEYS = set()


def on_signal(signum, _frame):
    raise InterruptedError(f"received signal {signum}")


signal.signal(signal.SIGTERM, on_signal)
signal.signal(signal.SIGINT, on_signal)


def check(name, condition, detail=""):
    row = {"name": name, "passed": bool(condition), "detail": detail}
    RESULTS.append(row)
    print(("PASS " if condition else "FAIL ") + name + (f" [{detail}]" if detail else ""),
          flush=True)
    if not condition:
        raise AssertionError(name + (f": {detail}" if detail else ""))


def run(args, *, input_bytes=None, env=None, timeout=8):
    return subprocess.run(args, input=input_bytes, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, env=env or ENV, timeout=timeout,
                          check=False)


def launch(label, args, *, input_bytes=None, env=None, identity=None):
    stdout_path = ART / f"{label}.stdout"
    stderr_path = ART / f"{label}.stderr"
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        process = subprocess.Popen(args, stdin=subprocess.PIPE if input_bytes is not None else
                                   subprocess.DEVNULL, stdout=stdout, stderr=stderr, env=env or ENV,
                                   start_new_session=True)
    if input_bytes is not None:
        process.stdin.write(input_bytes)
        process.stdin.close()
    item = {"label": label, "pid": process.pid, "pgid": process.pid,
            "identity": identity or str(args[0]), "status": "running"}
    CHILDREN.append((item, process))
    write_children()
    return process


def write_children():
    (ART / "owned-processes.json").write_text(
        json.dumps([item for item, _process in CHILDREN], indent=2) + "\n", encoding="utf-8")


def finish(process, label, stdout, status):
    try:
        actual_status = process.wait(timeout=8)
    except subprocess.TimeoutExpired as error:
        raise TimeoutError(f"{label} did not exit after its expected input") from error
    output = (ART / f"{label}.stdout").read_bytes()
    error = (ART / f"{label}.stderr").read_bytes()
    item = next(item for item, child in CHILDREN if child is process)
    item["status"] = actual_status
    write_children()
    check(label + " result", output == stdout and actual_status == status,
          f"stdout-bytes={len(output)} status={actual_status} stderr-bytes={len(error)}")
    return output, error


def read_exact(connection, size):
    data = bytearray()
    while len(data) < size:
        chunk = connection.recv(size - len(data))
        if not chunk:
            raise OSError("private Unix service closed its socket before the full response")
        data.extend(chunk)
    return bytes(data)


def service_call(operation):
    runtime = os.environ["XDG_RUNTIME_DIR"]
    display = Path(os.environ["WAYLAND_DISPLAY"]).name
    path = Path(runtime) / "scottland" / f"clipboard-{display}" / "service.sock"
    request = bytes([operation])
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(2)
        connection.connect(str(path))
        connection.sendall(struct.pack("!I", len(request)) + request)
        size = struct.unpack("!I", read_exact(connection, 4))[0]
        response = read_exact(connection, size)
    if not response:
        raise OSError("private clipboard service returned an empty reply")
    if response[0]:
        raise RuntimeError(response[1:].decode("utf-8", "replace"))
    return response[1:]


def history_count():
    body = service_call(2)
    if len(body) < 4:
        raise ValueError("private clipboard service returned a short history list")
    count = struct.unpack_from("!I", body)[0]
    offset = 4
    for _index in range(count):
        if offset + 12 > len(body):
            raise ValueError("private clipboard service returned a short history row")
        offset += 8
        for _field in range(2):
            if offset + 4 > len(body):
                raise ValueError("private clipboard service returned a short history field")
            field_size = struct.unpack_from("!I", body, offset)[0]
            offset += 4 + field_size
            if offset > len(body):
                raise ValueError("private clipboard service returned a truncated history field")
    if offset != len(body):
        raise ValueError("private clipboard service returned trailing history bytes")
    return count


def clipboard_text():
    result = run(["wl-paste", "--no-newline"], timeout=5)
    if result.returncode:
        raise RuntimeError("wl-paste could not read the private Wayland clipboard: " +
                           result.stderr.decode("utf-8", "replace"))
    return result.stdout


def ipc(method, data=None):
    path = os.environ.get("WAYFIRE_SOCKET")
    if not path:
        raise RuntimeError("private Wayfire session did not provide WAYFIRE_SOCKET")
    body = json.dumps({"method": method, "data": data or {}}).encode()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(8)
        connection.connect(path)
        connection.sendall(struct.pack("<I", len(body)) + body)
        size = struct.unpack("<I", read_exact(connection, 4))[0]
        value = json.loads(read_exact(connection, size))
    if isinstance(value, dict) and "error" in value:
        raise RuntimeError(f"headless IPC {method}: {value['error']}")
    return value


def ppm(data):
    pos = 0
    fields = []
    while len(fields) < 4:
        while pos < len(data) and data[pos] in b" \t\r\n":
            pos += 1
        if pos < len(data) and data[pos] == ord("#"):
            while pos < len(data) and data[pos] not in b"\r\n":
                pos += 1
            continue
        end = pos
        while end < len(data) and data[end] not in b" \t\r\n":
            end += 1
        fields.append(data[pos:end])
        pos = end
    if fields[0] != b"P6":
        raise ValueError("grim did not return a binary PPM image")
    width, height, maximum = (int(value) for value in fields[1:])
    if maximum != 255:
        raise ValueError(f"unsupported PPM maximum value {maximum}")
    pos += 2 if data[pos:pos + 2] == b"\r\n" else 1
    pixels = data[pos:pos + width * height * 3]
    if len(pixels) != width * height * 3:
        raise ValueError("grim returned a truncated PPM image")
    return width, height, pixels


def color(value):
    value = value.lstrip("#")
    return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))


def close_color(left, right, tolerance=4):
    return max(abs(a - b) for a, b in zip(left, right)) <= tolerance


def pixel(image, x, y):
    width, height, data = image
    x = max(0, min(width - 1, int(x)))
    y = max(0, min(height - 1, int(y)))
    offset = (y * width + x) * 3
    return tuple(data[offset:offset + 3])


def capture(output, name=None):
    result = run(["grim", "-t", "ppm", "-o", output, "-"], timeout=8)
    if result.returncode:
        raise RuntimeError(f"grim failed for {output}: {result.stderr.decode(errors='replace')}")
    if name:
        (ART / name).write_bytes(result.stdout)
    return ppm(result.stdout)


def palette_background():
    path = (Path(os.environ["XDG_RUNTIME_DIR"]) / "scottland" /
            f"{Path(os.environ['WAYLAND_DISPLAY']).name}.palette.json")
    if path.is_file():
        return color(json.loads(path.read_text(encoding="utf-8")).get("background", "#1c1d22"))
    return color("#1c1d22")


def different_pixels(image, expected, x_range, y_range, threshold=12):
    width, height, data = image
    count = 0
    for y in range(max(0, y_range[0]), min(height, y_range[1])):
        for x in range(max(0, x_range[0]), min(width, x_range[1])):
            if not close_color(pixel(image, x, y), expected, threshold):
                count += 1
    return count


def panel_visible(image, background):
    width, height, _pixels = image
    return close_color(pixel(image, width // 2 - 310, height // 2), background)


def text_pixel_counts(image, background):
    width, height, _pixels = image
    center_x, center_y = width // 2, height // 2
    prompt = different_pixels(image, background,
                              (center_x - 220, center_x + 220),
                              (center_y - 68, center_y - 44))
    empty_label = different_pixels(image, background,
                                   (center_x - 90, center_x + 90),
                                   (center_y + 31, center_y + 56))
    return prompt, empty_label


def wait_empty_panel(process, output, background, label):
    deadline = time.monotonic() + 8
    latest = None
    counts = (0, 0)
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stderr = (ART / f"{label}.stderr").read_text(errors="replace")
            raise RuntimeError(f"{label} exited before empty picker appeared: {stderr}")
        latest = capture(output)
        counts = text_pixel_counts(latest, background)
        if panel_visible(latest, background) and counts[0] > 8 and counts[1] > 8:
            return latest, counts
        time.sleep(0.05)
    raise TimeoutError(f"empty picker pixels did not appear: prompt={counts[0]} label={counts[1]}")


def wait_panel(process, output, background, label):
    deadline = time.monotonic() + 8
    latest = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stderr = (ART / f"{label}.stderr").read_text(errors="replace")
            raise RuntimeError(f"{label} exited before picker appeared: {stderr}")
        latest = capture(output)
        if panel_visible(latest, background):
            return latest
        time.sleep(0.05)
    raise TimeoutError(f"picker palette pixel did not appear for {label}")


def key(name):
    ipc("stipc/feed_key", {"key": "KEY_" + name, "state": True})
    HELD_KEYS.add(name)
    time.sleep(0.03)
    ipc("stipc/feed_key", {"key": "KEY_" + name, "state": False})
    HELD_KEYS.discard(name)


def click(x, y):
    ipc("stipc/move_cursor", {"x": round(x), "y": round(y)})
    ipc("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "press"})
    ipc("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "release"})


def verify_owned_process(process, identity):
    if process.poll() is not None:
        return True
    try:
        pgid = os.getpgid(process.pid)
        cmdline = Path(f"/proc/{process.pid}/cmdline").read_bytes().decode(errors="replace")
    except (OSError, ProcessLookupError):
        return False
    return pgid == process.pid and identity in cmdline


def stop_owned(process, identity):
    if process.poll() is not None:
        process.wait()
        return
    if not verify_owned_process(process, identity):
        raise RuntimeError(f"refusing to signal unverified process group {process.pid}")
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=4)
    except subprocess.TimeoutExpired:
        if not verify_owned_process(process, identity):
            raise RuntimeError(f"refusing to force-stop changed process group {process.pid}")
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=3)


def seed(value):
    result = run([str(CLIPBOARD), "copy-text", "--copy-only"], input_bytes=value, timeout=8)
    check("seed private clipboard text", result.returncode == 0 and not result.stdout,
          f"status={result.returncode} stdout-bytes={len(result.stdout)}")


def command_spy(directory, command, trace):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    executable = directory / command
    executable.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$*\" > \"$CH11_COMMAND_TRACE\"\ncat >/dev/null\nexit 73\n",
        encoding="utf-8")
    executable.chmod(0o700)
    environment = ENV.copy()
    environment["PATH"] = f"{directory}:{ENV['PATH']}"
    environment["CH11_COMMAND_TRACE"] = str(trace)
    return environment


def run_cases():
    outputs = ipc("window-rules/list-outputs")
    if len(outputs) != 1:
        raise RuntimeError(f"private CH11 session must have one output, got {len(outputs)}")
    output = outputs[0]["name"]
    geometry = outputs[0]["geometry"]
    background = palette_background()
    (ART / "session.json").write_text(json.dumps({
        "wayland_display": os.environ.get("WAYLAND_DISPLAY"),
        "output": output,
        "geometry": geometry,
        "palette_background": "#%02x%02x%02x" % background,
    }, indent=2) + "\n", encoding="utf-8")

    server = launch("clipboard-service", [str(SERVICE)], identity=str(SERVICE))
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise RuntimeError("private clipboard service exited: " +
                               (ART / "clipboard-service.stderr").read_text(errors="replace"))
        try:
            service_call(1)
            break
        except OSError:
            time.sleep(0.05)
    else:
        raise TimeoutError("private clipboard service socket did not answer ping")
    check("private clipboard service responds", True, f"pid={server.pid}")

    empty_trace = ART / "default-empty-quickshell.trace"
    empty_env = command_spy(ART / "default-empty-bin", "qs", empty_trace)
    empty = run([str(PICKER)], input_bytes=b"", env=empty_env, timeout=8)
    check("default empty stdin cancels before UI",
          empty.returncode == 1 and not empty.stdout and not empty.stderr and
          not empty_trace.exists(),
          f"status={empty.returncode} stdout-bytes={len(empty.stdout)} quickshell-started="
          f"{empty_trace.exists()}")

    normal = launch("nonempty-show-empty", [str(PICKER), "--show-empty"],
                    input_bytes=b"first\nsecond\n", identity=str(PICKER))
    wait_panel(normal, output, background, "nonempty-show-empty")
    key("DOWN")
    key("ENTER")
    normal_out, _normal_err = finish(normal, "nonempty-show-empty", b"second\n", 0)
    check("nonempty selection remains a single selected line", normal_out == b"second\n")

    seed(b"CH11 synthetic entry one")
    seed(b"CH11 synthetic entry two")
    clipboard_anchor = b"CH11 private clipboard anchor"
    seed(clipboard_anchor)
    before_clear_count = history_count()
    before_clear_clipboard = clipboard_text()
    check("three private history entries seeded", before_clear_count == 3,
          f"history-count={before_clear_count}")
    check("private clipboard anchor installed", before_clear_clipboard == clipboard_anchor,
          f"clipboard-bytes={len(before_clear_clipboard)}")

    clear = launch("clear-success", [str(CLIPBOARD), "clear"], identity=str(CLIPBOARD))
    _image, text_counts = wait_empty_panel(clear, output, background, "clear-success")
    capture(output, "empty-after-clear.ppm")
    cleared_count = history_count()
    clipboard_after_clear = clipboard_text()
    check("successful clear leaves zero private history", cleared_count == 0,
          f"history-count={cleared_count}")
    check("successful clear preserves private clipboard bytes",
          clipboard_after_clear == before_clear_clipboard,
          f"before-bytes={len(before_clear_clipboard)} after-bytes={len(clipboard_after_clear)}")
    check("empty picker prompt and No entries are visible",
          text_counts[0] > 8 and text_counts[1] > 8,
          f"prompt-pixels={text_counts[0]} empty-label-pixels={text_counts[1]}")

    empty_actions = ["DOWN", "UP", "ENTER"]
    for action in empty_actions:
        key(action)
        still_open = clear.poll() is None
        next_image = capture(output)
        next_counts = text_pixel_counts(next_image, background)
        check(f"empty view ignores {action}",
              still_open and panel_visible(next_image, background) and next_counts[1] > 8,
              f"process-running={still_open} empty-label-pixels={next_counts[1]}")

    click(geometry["x"] + geometry["width"] // 2,
          geometry["y"] + geometry["height"] // 2 + 43)
    after_click = capture(output)
    click_counts = text_pixel_counts(after_click, background)
    check("clicking No entries cannot select or dismiss",
          clear.poll() is None and panel_visible(after_click, background) and click_counts[1] > 8,
          f"process-running={clear.poll() is None} empty-label-pixels={click_counts[1]}")

    key("ESC")
    clear_out, clear_err = finish(clear, "clear-success", b"", 0)
    check("dismissing the empty clear view succeeds without output",
          not clear_out and not clear_err,
          f"stdout-bytes={len(clear_out)} stderr-bytes={len(clear_err)}")

    seed(b"CH11 presentation-failure entry")
    presentation_anchor = clipboard_text()
    fail_dir = ART / "failure-injection-bin"
    fail_dir.mkdir(mode=0o700, exist_ok=True)
    fail_picker = fail_dir / "scottland-list-picker"
    failure_trace = ART / "presentation-picker.trace"
    failure_env = ENV.copy()
    failure_env["PATH"] = f"{fail_dir}:/usr/local/bin:/usr/bin:/bin"
    failure_env["CH11_COMMAND_TRACE"] = str(failure_trace)
    fail_picker.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$*\" > \"$CH11_COMMAND_TRACE\"\n"
        "cat >/dev/null\nprintf 'injected presentation failure\\n' >&2\nexit 73\n",
        encoding="utf-8")
    fail_picker.chmod(0o700)
    presentation = run([str(CLIPBOARD), "clear"], env=failure_env, timeout=8)
    failure_count = history_count()
    failure_clipboard = clipboard_text()
    expected_error = b"history was cleared but the empty history view could not be presented"
    check("presentation failure reports already-cleared history",
          presentation.returncode != 0 and not presentation.stdout and
          expected_error in presentation.stderr and failure_trace.exists() and
          failure_trace.read_text() == "--show-empty --prompt Clipboard history\n",
          f"status={presentation.returncode} stdout-bytes={len(presentation.stdout)}")
    check("presentation failure does not restore history or alter clipboard",
          failure_count == 0 and failure_clipboard == presentation_anchor,
          f"history-count={failure_count} clipboard-bytes={len(failure_clipboard)}")

    seed(b"CH11 service-failure entry")
    stop_owned(server, str(SERVICE))
    server_item = next(item for item, child in CHILDREN if child is server)
    server_item["status"] = server.returncode
    write_children()
    service_failure_trace = ART / "service-failure-picker.trace"
    service_failure_env = command_spy(ART / "service-failure-bin", "scottland-list-picker",
                                      service_failure_trace)
    service_failure = run([str(CLIPBOARD), "clear"], env=service_failure_env, timeout=8)
    check("clear acknowledgement failure does not open the picker",
          service_failure.returncode != 0 and not service_failure.stdout and
          not service_failure_trace.exists(),
          f"status={service_failure.returncode} stdout-bytes={len(service_failure.stdout)} "
          f"picker-started={service_failure_trace.exists()}")


def cleanup():
    failures = []
    for key_name in tuple(HELD_KEYS):
        try:
            ipc("stipc/feed_key", {"key": "KEY_" + key_name, "state": False})
        except Exception as error:
            failures.append(f"could not release KEY_{key_name}: {error}")
    for item, process in reversed(CHILDREN):
        if process.poll() is None:
            try:
                stop_owned(process, item["identity"])
            except Exception as error:
                failures.append(f"could not stop owned {item['label']} PID {process.pid}: {error}")
        item["status"] = process.poll()
    write_children()
    if failures:
        raise RuntimeError("owned cleanup failed: " + "; ".join(failures))


def main():
    result = "passed"
    error = None
    try:
        run_cases()
    except Exception as caught:
        result = "failed"
        error = f"{type(caught).__name__}: {caught}"
        raise
    finally:
        cleanup_error = None
        try:
            cleanup()
        except Exception as caught:
            cleanup_error = f"{type(caught).__name__}: {caught}"
            result = "cleanup-failed"
        summary = {
            "result": result,
            "check_count": len(RESULTS),
            "checks": RESULTS,
            "error": error,
            "cleanup_error": cleanup_error,
            "source_commit": os.environ.get("SCOTTLAND_TEST_SOURCE_COMMIT", "unknown"),
        }
        (ART / "checks.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        if cleanup_error:
            print("FAIL cleanup " + cleanup_error, file=sys.stderr, flush=True)
            raise RuntimeError(cleanup_error)


if __name__ == "__main__":
    main()
