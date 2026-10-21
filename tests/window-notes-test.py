#!/usr/bin/env python3
"""Window notes behavior with real stipc pointer/key input in an isolated headless session."""
import json
import os
from pathlib import Path
import select
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time

import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf

assert os.environ.get("SCOTTLAND_TEST_MODEL") == "1", "private headless session required"
repo = Path(__file__).resolve().parents[1]
art = Path(sys.argv[1]).resolve()
art.mkdir(parents=True, exist_ok=True)
clients = []
temporary_dirs = []
checks = 0
failures = 0


def receive(sock):
    def exact(count):
        data = bytearray()
        while len(data) < count:
            part = sock.recv(count - len(data))
            if not part:
                raise RuntimeError("compositor disconnected")
            data.extend(part)
        return bytes(data)
    size = struct.unpack("<I", exact(4))[0]
    return json.loads(exact(size))


def send(sock, method, data=None):
    payload = json.dumps({"method": method, "data": data or {}}, ensure_ascii=False).encode()
    sock.sendall(struct.pack("<I", len(payload)) + payload)
    return receive(sock)


def ipc(method, data=None):
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(5)
        sock.connect(os.environ["WAYFIRE_SOCKET"])
        return send(sock, method, data)


def check(name, condition, detail=None):
    global checks, failures
    checks += 1
    if condition:
        print("PASS", name, flush=True)
    else:
        failures += 1
        print("FAIL", name, "—", repr(detail), flush=True)


def require(name, condition, detail=None):
    check(name, condition, detail)
    if not condition:
        raise AssertionError(f"{name}: {detail!r}")


def wait_for(predicate, name, seconds=12):
    deadline = time.monotonic() + seconds
    last = None
    while time.monotonic() < deadline:
        try:
            last = predicate()
            if last:
                return last
        except (KeyError, StopIteration, OSError):
            pass
        time.sleep(.05)
    raise AssertionError(f"timed out waiting for {name}; last={last!r}")


def list_views():
    return ipc("window-rules/list-views")


def window(title):
    return next((row for row in list_views() if row.get("title") == title), None)


def scene(title):
    return next((row for row in ipc("scottland/layout-state")["views"]
                 if row.get("title") == title), None)


def notes():
    return ipc("scottland/desktop-model", {"slice": "notes"})


def note_action(action, **fields):
    return ipc("scottland/notes", {"action": action, **fields})


def note_rows(snapshot=None):
    snapshot = snapshot or notes()
    return [(int(win["id"]), list(win["notes"])) for win in snapshot["windows"]]


def focused():
    return (ipc("window-rules/get-focused-view").get("info") or {}).get("id")


def ctl(*args, input_text=None):
    return subprocess.run([str(repo / "build/tools/bin/scottland-ctl"), *map(str, args)],
        input=input_text, text=True, capture_output=True, timeout=12)


def ctl_start(*args, stdout_path, stderr_path):
    with stdout_path.open("w") as stdout, stderr_path.open("w") as stderr:
        process = subprocess.Popen([str(repo / "build/tools/bin/scottland-ctl"), *map(str, args)],
            stdout=stdout, stderr=stderr)
    clients.append(process)
    return process


def rust_notes_cli_checks(anchor_id, focused_window_id):
    writer = "cli-notes-agent"
    add = ctl("notes", "add", "--window", anchor_id, "--writer", writer, "-",
        input_text="please log in\nto xyz\n")
    require("Rust notes add accepts stdin text and prints its id",
        add.returncode == 0 and add.stdout.endswith("\n") and add.stdout.strip().isdigit(),
        {"status": add.returncode, "stdout": add.stdout, "stderr": add.stderr})
    note_id = int(add.stdout.strip())
    expected_note = {"id": note_id, "writer": writer, "text": "please log in\nto xyz"}
    require("Rust notes add stores the supplied window, writer and full text",
        row_for(notes(), anchor_id)["notes"] == [expected_note], notes())

    listing = ctl("notes", "list", "--window", anchor_id, "--writer", writer)
    require("Rust notes list prints the documented multi-line form",
        listing.returncode == 0 and listing.stdout ==
            f"{anchor_id} {note_id} {writer}\n  please log in\n  to xyz\n",
        {"status": listing.returncode, "stdout": listing.stdout, "stderr": listing.stderr})
    json_listing = ctl("notes", "list", "--window", anchor_id, "--writer", writer, "--json")
    require("Rust notes list JSON returns the filtered windows array",
        json_listing.returncode == 0 and json.loads(json_listing.stdout) ==
            [{"id": anchor_id, "notes": [expected_note]}],
        {"status": json_listing.returncode, "stdout": json_listing.stdout,
         "stderr": json_listing.stderr})

    update = ctl("notes", "update", note_id, "logged in, thanks")
    expected_updated = {**expected_note, "text": "logged in, thanks"}
    require("Rust notes update changes text without changing note identity",
        update.returncode == 0 and update.stdout == "" and
            row_for(notes(), anchor_id)["notes"] == [expected_updated],
        {"status": update.returncode, "stdout": update.stdout,
         "stderr": update.stderr, "notes": notes()})

    unchanged = notes()
    invalid = ctl("notes", "add", "--window", anchor_id, "--writer", writer, "x" * 1001)
    require("Rust notes add reports rejected text as one error line",
        invalid.returncode != 0 and "text" in invalid.stderr and
            len(invalid.stderr.splitlines()) == 1 and invalid.stdout == "" and notes() == unchanged,
        {"status": invalid.returncode, "stdout": invalid.stdout,
         "stderr": invalid.stderr, "notes": notes()})

    missing = ctl("notes", "remove", 9223372036854775807)
    require("Rust notes remove reports a missing id and exits nonzero",
        missing.returncode != 0 and "note-id does not exist" in missing.stderr and
            len(missing.stderr.splitlines()) == 1,
        {"status": missing.returncode, "stdout": missing.stdout, "stderr": missing.stderr})
    remove = ctl("notes", "remove", note_id)
    require("Rust notes remove deletes the note",
        remove.returncode == 0 and not any(
            entry["id"] == note_id for _, entries in note_rows(notes()) for entry in entries),
        {"status": remove.returncode, "stdout": remove.stdout,
         "stderr": remove.stderr, "notes": notes()})
    empty = ctl("notes", "list", "--window", anchor_id, "--writer", writer)
    require("Rust notes list succeeds without output when no note matches",
        empty.returncode == 0 and empty.stdout == "" and empty.stderr == "",
        {"status": empty.returncode, "stdout": empty.stdout, "stderr": empty.stderr})

    before_focus = focused()
    focused_add = ctl("notes", "add", "--window", "focused", "--writer", writer,
        "focused note")
    require("Rust notes add resolves the focused window",
        focused_add.returncode == 0 and focused_add.stdout.strip().isdigit(),
        {"status": focused_add.returncode, "stdout": focused_add.stdout,
         "stderr": focused_add.stderr})
    focused_id = int(focused_add.stdout.strip())
    focused_json = ctl("notes", "list", "--window", "focused", "--writer", writer, "--json")
    require("Rust notes list resolves focused and leaves focus unchanged",
        focused_json.returncode == 0 and focused() == before_focus == focused_window_id and
            json.loads(focused_json.stdout) == [{"id": focused_window_id, "notes": [
                {"id": focused_id, "writer": writer, "text": "focused note"}]}],
        {"status": focused_json.returncode, "stdout": focused_json.stdout,
         "stderr": focused_json.stderr, "focused": focused()})
    focused_remove = ctl("notes", "remove", focused_id)
    require("Rust notes remove cleans up a focused-window note",
        focused_remove.returncode == 0 and not any(
            entry["id"] == focused_id for _, entries in note_rows(notes()) for entry in entries),
        {"status": focused_remove.returncode, "stderr": focused_remove.stderr,
         "notes": notes()})


def launch_with_note_behavior():
    writer = f"notes-login-{os.getpid()}"
    title = f"Notes login {os.getpid()} {time.monotonic_ns()}"
    text = "notes agent opened this page; please log in to xyz"
    marker = art / "rejected-note-command-started"
    marker.unlink(missing_ok=True)

    rejected = ctl("launch", "--launcher", writer, "--writer", writer,
        "--note", "x" * 1001, "--", sys.executable, "-c",
        "import pathlib,sys; pathlib.Path(sys.argv[1]).touch()", str(marker))
    require("a rejected launch note prevents command execution",
        rejected.returncode != 0 and "text" in rejected.stderr and not marker.exists(),
        {"status": rejected.returncode, "stdout": rejected.stdout,
         "stderr": rejected.stderr, "command_started": marker.exists()})
    require("rejected pending note is absent from the Notes slice",
        not any(note.get("writer") == writer for _, entries in note_rows(notes()) for note in entries),
        notes())

    profile = Path(tempfile.mkdtemp(prefix="chromium-user-data-", dir=art))
    temporary_dirs.append(profile)
    page = art / f"{profile.name}.html"
    page.write_text(f"<!doctype html><title>{title}</title><h1>{title}</h1>", encoding="utf-8")
    process = ctl_start("launch", "--name", title, "--launcher", writer,
        "--writer", writer, "--note", text, "--", "chromium", "--ozone-platform=wayland",
        f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
        "--new-window", page.as_uri(), stdout_path=art / "nt17-chromium.stdout",
        stderr_path=art / "nt17-chromium.stderr")

    def bound_note():
        desktop = ipc("scottland/desktop-model", {"slice": "desktop"})
        view = next((row for row in desktop.get("windows", []) if row.get("title") == title), None)
        if not view or view.get("provenance", {}).get("launcher") != writer:
            return None
        note_slice = notes()
        window_notes = row_for(note_slice, view["id"])
        if not window_notes:
            return None
        matches = [note for note in window_notes["notes"]
            if note.get("writer") == writer and note.get("text") == text]
        if len(matches) != 1:
            return None
        return desktop, note_slice, view, matches[0]

    try:
        observed = wait_for(bound_note, "Chromium launch note bound to its provenance window", seconds=35)
    except AssertionError as error:
        raise AssertionError(f"{error}; ctl={process.poll()}; stderr="
            f"{(art / 'nt17-chromium.stderr').read_text(encoding='utf-8', errors='replace')!r}")
    desktop, note_slice, view, note = observed
    require("real Chromium launch publishes provenance and its note on the same window",
        view.get("provenance", {}).get("name") == title and
            row_for(note_slice, view["id"]) is not None,
        {"window": view, "notes": note_slice})
    require("the bound window's Notes slice contains the accepted launch note id",
        note.get("id", 0) > 0 and sum(1 for _, entries in note_rows(note_slice)
            for entry in entries if entry.get("id") == note["id"]) == 1,
        {"window_id": view["id"], "note": note, "notes": note_slice})

    configured = {"x": 240, "y": 120, "width": 520, "height": 500}
    place(title, configured)
    focus("Notes focus target")
    browser = wait_for(lambda: scene(title), "Chromium scene for note rendering")
    frame = browser.get("scene_frame", browser["frame"])
    dot_x, dot_y = round(frame["x"] + frame["width"] + 8), round(frame["y"] + 4)
    _, dot_pixels = wait_shot("nt19-chromium-note-dot", lambda image:
        pixel(image, dot_x, dot_y) != pixel(image, dot_x + 6, dot_y))
    require("the bound Chromium window draws its note dot while unfocused",
        pixel(dot_pixels, dot_x, dot_y) != pixel(dot_pixels, dot_x + 6, dot_y),
        {"dot": pixel(dot_pixels, dot_x, dot_y), "nearby": pixel(dot_pixels, dot_x + 6, dot_y)})

    focus(title)
    _, panel_pixels = wait_shot("nt19-chromium-note-panel", lambda image:
        changed_pixels(dot_pixels, image,
            (dot_x - 4, dot_y - 4, dot_x + 340, dot_y + 420)) > 500)
    require("focusing Chromium renders the bound note panel",
        changed_pixels(dot_pixels, panel_pixels,
            (dot_x - 4, dot_y - 4, dot_x + 340, dot_y + 420)) > 500,
        changed_pixels(dot_pixels, panel_pixels,
            (dot_x - 4, dot_y - 4, dot_x + 340, dot_y + 420)))


def pointer(x, y):
    reply = ipc("stipc/move_cursor", {"x": round(x), "y": round(y)})
    if isinstance(reply, dict) and "error" in reply:
        raise RuntimeError(reply)


def button(down):
    reply = ipc("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "press" if down else "release"})
    if isinstance(reply, dict) and "error" in reply:
        raise RuntimeError(reply)


def key(name, down):
    reply = ipc("stipc/feed_key", {"key": "KEY_" + name, "state": down})
    if isinstance(reply, dict) and "error" in reply:
        raise RuntimeError(reply)


def focus(title):
    row = wait_for(lambda: scene(title), f"{title} scene")
    frame = row.get("scene_frame", row["frame"])
    pointer(frame["x"] + frame["width"] / 2, frame["y"] + frame["height"] / 2)
    button(True)
    button(False)
    return wait_for(lambda: focused() == row["id"], f"real pointer focus on {title}")


def launch(title, background):
    process = subprocess.Popen(["foot", "-c", "/dev/null", "-T", title, "-W", "48x14",
        "-o", f"colors-dark.background={background}", "-o", f"colors-light.background={background}",
        "sh", "-c", "exec sleep 3600"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    clients.append(process)
    return wait_for(lambda: (row if (row := window(title)) and row.get("mapped") else None),
        f"{title} mapped")


def place(title, geometry):
    row = wait_for(lambda: window(title), f"{title} listed")
    reply = ipc("window-rules/configure-view", {"id": row["id"], "geometry": geometry})
    if isinstance(reply, dict) and "error" in reply:
        raise RuntimeError(reply)
    wait_for(lambda: window(title), f"{title} after configure")


def shot(name):
    path = art / f"{name}.png"
    subprocess.run(["grim", str(path)], check=True)
    image = GdkPixbuf.Pixbuf.new_from_file(str(path))
    return path, image


def wait_shot(name, predicate, seconds=8):
    deadline = time.monotonic() + seconds
    last = None
    attempt = 0
    while time.monotonic() < deadline:
        path, image = shot(f"{name}-{attempt}")
        last = predicate(image)
        if last:
            return path, image
        attempt += 1
        time.sleep(.05)
    raise AssertionError(f"timed out waiting for {name} pixels; last={last!r}")


def stable_shot(name, seconds=12):
    deadline = time.monotonic() + seconds
    previous = None
    attempt = 0
    while time.monotonic() < deadline:
        path, image = shot(f"{name}-{attempt}")
        pixels = memoryview(image.get_pixels()).tobytes()
        if previous == pixels:
            return path, image
        previous = pixels
        attempt += 1
        time.sleep(.05)
    raise AssertionError(f"screen did not settle for {name}")


def pixel(image, x, y):
    width, height = image.get_width(), image.get_height()
    if x < 0 or y < 0 or x >= width or y >= height:
        return None
    channels, stride = image.get_n_channels(), image.get_rowstride()
    start = y * stride + x * channels
    return tuple(memoryview(image.get_pixels())[start:start + 3])


def changed_pixels(first, second, box, threshold=25):
    left, top, right, bottom = map(round, box)
    left, top = max(0, left), max(0, top)
    right, bottom = min(first.get_width(), right), min(first.get_height(), bottom)
    first_pixels, second_pixels = memoryview(first.get_pixels()), memoryview(second.get_pixels())
    first_stride, second_stride = first.get_rowstride(), second.get_rowstride()
    channels = min(first.get_n_channels(), second.get_n_channels())
    count = 0
    for y in range(top, bottom):
        first_row = y * first_stride + left * channels
        second_row = y * second_stride + left * channels
        for x in range(left, right):
            first_at = first_row + (x - left) * channels
            second_at = second_row + (x - left) * channels
            red = abs(first_pixels[first_at] - second_pixels[second_at])
            green = abs(first_pixels[first_at + 1] - second_pixels[second_at + 1])
            blue = abs(first_pixels[first_at + 2] - second_pixels[second_at + 2])
            if max(red, green, blue) >= threshold:
                count += 1
    return count


class NotesWatch:
    def __init__(self):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.settimeout(5)
        self.sock.connect(os.environ["WAYFIRE_SOCKET"])
        self.snapshot = send(self.sock, "scottland/subscribe", {"slice": "notes"})

    def event(self, seconds=8):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self.sock], [], [], min(.1, deadline - time.monotonic()))
            if ready:
                message = receive(self.sock)
                if message.get("event") == "scottland-notes#":
                    return message
        raise AssertionError("notes subscriber did not receive a complete notes event")

    def close(self):
        self.sock.close()


def state_slices():
    return {slice_name: ipc("scottland/desktop-model", {"slice": slice_name})
            for slice_name in ("desktop", "widgets", "attention")}


def row_for(snapshot, window_id):
    return next((entry for entry in snapshot["windows"] if int(entry["id"]) == int(window_id)), None)


def changed_in(image_a, image_b, frame, margin=30):
    return changed_pixels(image_a, image_b,
        (frame["x"] + frame["width"] - margin, frame["y"] - margin,
         frame["x"] + frame["width"] + margin, frame["y"] + margin))


try:
    options = ipc("wayfire/set-config-options", {
        "output:HEADLESS-1/mode": "1600x1000@60000", "scottland/goo": False,
        "scottland/sounds": False,
    })
    require("headless output accepts the fixture size", "error" not in options, options)
    wait_for(lambda: any(o.get("geometry", {}).get("width") == 1600
                         for o in ipc("window-rules/list-outputs")), "1600pt output")

    anchor = launch("Notes anchor", "183b56")
    under = launch("Notes under panel", "b03020")
    focus_window = launch("Notes focus target", "31532a")
    place("Notes anchor", {"x": 80, "y": 160, "width": 420, "height": 280})
    place("Notes under panel", {"x": 540, "y": 120, "width": 680, "height": 600})
    place("Notes focus target", {"x": 1260, "y": 700, "width": 260, "height": 170})
    focus("Notes focus target")

    anchor = scene("Notes anchor")
    anchor_id = anchor["id"]
    anchor_frame = anchor.get("scene_frame", anchor["frame"])
    rust_notes_cli_checks(anchor_id, focus_window["id"])
    baseline_path, baseline = shot("window-unnoted")
    before_slices = state_slices()
    watcher = NotesWatch()
    first = note_action("add", window=anchor_id, writer="agent-x", text="please log in to xyz")
    require("valid add returns a new id", "error" not in first and first.get("id", 0) > 0, first)
    first_id = int(first["id"])
    event = watcher.event()
    require("subscribe sends a newer complete notes replacement",
        event.get("version", 0) > watcher.snapshot.get("version", 0) and
        row_for(event, anchor_id)["notes"][0] ==
            {"id": first_id, "writer": "agent-x", "text": "please log in to xyz"}, event)
    after_slices = state_slices()
    require("note-only add leaves desktop, widgets and attention slices unchanged",
        after_slices == before_slices, {"before": before_slices, "after": after_slices})
    require("add does not change focus", focused() == focus_window["id"], focused())
    require("notes direct snapshot is a complete window replacement",
        notes().get("windows") == [{"id": anchor_id, "notes":
            [{"id": first_id, "writer": "agent-x", "text": "please log in to xyz"}]}], notes())
    anchor = scene("Notes anchor")
    frame = anchor.get("scene_frame", anchor["frame"])
    dot_x, dot_y = round(frame["x"] + frame["width"] + 8), round(frame["y"] + 4)
    _, one_dot = wait_shot("one-dot",
        lambda image: pixel(image, dot_x, dot_y) != pixel(baseline, dot_x, dot_y))
    check("one note draws an 8pt dot outside the unfocused window",
        pixel(one_dot, dot_x, dot_y) != pixel(baseline, dot_x, dot_y),
        {"point": (dot_x, dot_y), "before": pixel(baseline, dot_x, dot_y),
         "after": pixel(one_dot, dot_x, dot_y)})

    ids = [first_id]
    for i in range(2, 6):
        result = note_action("add", window=anchor_id, writer="agent-x", text=f"note {i}")
        require(f"add note {i}", "error" not in result, result)
        ids.append(int(result["id"]))
    five = notes()
    require("five notes stay ordered in one window", [n["id"] for _, rows in note_rows(five) for n in rows] == ids, five)
    require("five notes still draw individual dots", len(row_for(five, anchor_id)["notes"]) == 5, five)

    six = note_action("add", window=anchor_id, writer="agent-x", text="counted note")
    require("sixth note adds", "error" not in six, six)
    ids.append(int(six["id"]))
    six_state = notes()
    require("six notes remain available even when shown as one marker",
        [n["id"] for n in row_for(six_state, anchor_id)["notes"]] == ids, six_state)
    _, six_pixels = shot("six-note-count-marker")
    require("six notes draw a count marker in the first dot's place",
        pixel(six_pixels, dot_x + 4, dot_y) != pixel(baseline, dot_x + 4, dot_y),
        {"point": (dot_x + 4, dot_y)})

    pointer(dot_x, dot_y)
    _, hover_pixels = wait_shot("hover-stack", lambda image: changed_pixels(
        six_pixels, image, (dot_x + 4, dot_y - 4, dot_x + 340, dot_y + 420)) > 500)
    require("hovering the count marker shows the note stack",
        changed_pixels(six_pixels, hover_pixels, (dot_x + 4, dot_y - 4, dot_x + 340, dot_y + 420)) > 500,
        changed_pixels(six_pixels, hover_pixels, (dot_x + 4, dot_y - 4, dot_x + 340, dot_y + 420)))
    require("hover does not steal focus", focused() == focus_window["id"], focused())
    require("showing a note does not create attention", state_slices()["attention"] ==
        before_slices["attention"], state_slices()["attention"])

    # Focus is delivered by a real click. The dots become a stack anchored at the same corner.
    focus("Notes anchor")
    _, focused_pixels = wait_shot("focused-stack", lambda image: changed_pixels(
        six_pixels, image, (dot_x - 4, dot_y - 12, dot_x + 340, dot_y + 420)) > 500)
    require("focusing the window replaces its dots with a visible panel stack",
        changed_pixels(six_pixels, focused_pixels,
            (dot_x - 4, dot_y - 12, dot_x + 340, dot_y + 420)) > 500,
        changed_pixels(six_pixels, focused_pixels,
            (dot_x - 4, dot_y - 12, dot_x + 340, dot_y + 420)))
    focus("Notes focus target")
    watcher.close()

    # Rejected operations leave model and next-id state unchanged. UTF-8 scalar counting accepts
    # multi-byte names while control characters and overlong text fail by field.
    before_invalid = notes()
    invalid_writer = note_action("add", window=anchor_id, writer="bad\nname", text="text")
    invalid_text = note_action("add", window=anchor_id, writer="agent-x", text="x" * 1001)
    invalid_control = note_action("add", window=anchor_id, writer="agent-x", text="bad\x01text")
    invalid_window = note_action("add", window=999999999, writer="agent-x", text="text")
    invalid_update = note_action("update", id=first_id, text="bad\x7ftext")
    require("invalid writer is rejected by field", "error" in invalid_writer and "writer" in invalid_writer["error"], invalid_writer)
    require("overlong text is rejected by field", "error" in invalid_text and "text" in invalid_text["error"], invalid_text)
    require("disallowed text control is rejected", "error" in invalid_control and "text" in invalid_control["error"], invalid_control)
    require("unknown window is rejected", "error" in invalid_window, invalid_window)
    require("invalid update is rejected by text field", "error" in invalid_update and
        "text" in invalid_update["error"], invalid_update)
    require("invalid operations do not mutate notes or consume ids", notes() == before_invalid, notes())
    unicode_writer = "🍕" * 64
    unicode_note = note_action("add", window=anchor_id, writer=unicode_writer, text="ok")
    require("writer length counts Unicode scalar values rather than UTF-8 bytes",
        "error" not in unicode_note, unicode_note)
    too_long_writer = note_action("add", window=anchor_id, writer="🍕" * 65, text="ok")
    require("writer above 64 Unicode scalar values is rejected",
        "error" in too_long_writer and "writer" in too_long_writer["error"], too_long_writer)
    unicode_id = int(unicode_note["id"])

    watcher = NotesWatch()
    note_action("update", id=first_id, text="please log in to xyz")
    same_update_event = watcher.event()
    require("accepted same-text update still publishes a newer notes version",
        same_update_event.get("version", 0) > watcher.snapshot.get("version", 0), same_update_event)
    updated = note_action("update", id=first_id, text="logged in, thanks")
    updated_event = watcher.event()
    require("update changes text but preserves id, writer and position", "error" not in updated and
        row_for(notes(), anchor_id)["notes"][0] ==
            {"id": first_id, "writer": "agent-x", "text": "logged in, thanks"}, notes())
    require("text update publishes a newer complete notes replacement",
        updated_event.get("version", 0) > same_update_event.get("version", 0) and
        row_for(updated_event, anchor_id)["notes"][0]["text"] == "logged in, thanks", updated_event)
    wrong_update = note_action("update", id=987654321, text="no note")
    wrong_remove = note_action("remove", id=987654321)
    require("unknown update id is rejected", "error" in wrong_update, wrong_update)
    require("unknown remove id is rejected", "error" in wrong_remove, wrong_remove)
    filter_result = note_action("list", window=anchor_id, writer=unicode_writer)
    require("list filters by exact window and writer", "error" not in filter_result and
        len(row_for(filter_result, anchor_id)["notes"]) == 1 and
        row_for(filter_result, anchor_id)["notes"][0]["id"] == unicode_id, filter_result)
    watcher.close()

    # The next note id and notes survive a marked plugin replacement.
    before_reload = notes()
    plugins = ipc("wayfire/get-config-option", {"option": "core/plugins"})["value"]
    fresh_plugin = repo / "build" / "libscottland-window-notes-reload.so"
    shutil.copy2(repo / "build/libscottland.so", fresh_plugin)
    plugin_names = plugins.split()
    require("test config currently loads Scottland", "scottland" in plugin_names, plugins)
    replacement = " ".join(str(fresh_plugin) if name == "scottland" else name for name in plugin_names)
    session_dir = Path(os.environ.get("SCOTTLAND_SESSION_DIR",
        str(Path(os.environ["XDG_RUNTIME_DIR"]) / "scottland")))
    runtime = session_dir / (os.environ.get("WAYLAND_DISPLAY", "wayland") + ".reloading")
    runtime.parent.mkdir(parents=True, exist_ok=True)
    runtime.touch()
    try:
        reply = ipc("wayfire/set-config-options", {"core/plugins": replacement})
        require("marked plugin reload is accepted", "error" not in reply, reply)
        wait_for(lambda: row_for(notes(), anchor_id) is not None and
            row_for(notes(), anchor_id)["notes"] == row_for(before_reload, anchor_id)["notes"],
            "notes restored by marked reload", seconds=20)
    finally:
        runtime.unlink(missing_ok=True)
    after_reload = notes()
    require("marked reload preserves note ids, text, order and writer",
        row_for(after_reload, anchor_id)["notes"] == row_for(before_reload, anchor_id)["notes"], after_reload)
    next_note = note_action("add", window=anchor_id, writer="after-reload", text="next id")
    require("next id is not reused after marked reload",
        "error" not in next_note and int(next_note["id"]) > max(ids + [unicode_id]), next_note)

    # Window mode and fullscreen hide all note visuals without changing the notes slice.
    focus("Notes focus target")
    key("LEFTALT", True)
    wait_for(lambda: ipc("scottland/hints").get("active"), "Window mode entry")
    note_before_hidden = notes()
    hidden_base = stable_shot("window-mode-no-notes")[1]
    temporary = note_action("add", window=anchor_id, writer="hidden", text="window mode")
    require("note can be added while Window mode is active", "error" not in temporary, temporary)
    _, hidden_note = stable_shot("window-mode-with-note")
    require("Window mode hides note visuals", changed_pixels(hidden_base, hidden_note,
        (0, 0, hidden_base.get_width(), hidden_base.get_height())) == 0,
        changed_pixels(hidden_base, hidden_note, (0, 0, hidden_base.get_width(), hidden_base.get_height())))
    require("hidden display state does not alter notes", len(row_for(notes(), anchor_id)["notes"]) ==
        len(row_for(note_before_hidden, anchor_id)["notes"]) + 1, notes())
    key("LEFTALT", False)
    wait_for(lambda: not ipc("scottland/hints")["active"], "Window mode release")
    note_action("remove", id=temporary["id"])

    # Fullscreen uses a real compositor fullscreen state; its output's notes remain in the model.
    focus("Notes focus target")
    full = window("Notes focus target")
    full_reply = ipc("wm-actions/set-fullscreen", {"view_id": full["id"], "state": True})
    require("fullscreen fixture enters fullscreen", "error" not in full_reply, full_reply)
    wait_for(lambda: bool(window("Notes focus target").get("fullscreen")), "fullscreen promotion")
    fullscreen_base = stable_shot("fullscreen-no-notes")[1]
    temporary = note_action("add", window=anchor_id, writer="hidden", text="fullscreen")
    require("note can be added while fullscreen is active", "error" not in temporary, temporary)
    fullscreen_note = stable_shot("fullscreen-with-note")[1]
    require("fullscreen hides all notes on its output",
        changed_pixels(fullscreen_base, fullscreen_note,
            (0, 0, fullscreen_base.get_width(), fullscreen_base.get_height())) == 0,
        changed_pixels(fullscreen_base, fullscreen_note,
            (0, 0, fullscreen_base.get_width(), fullscreen_base.get_height())))
    ipc("wm-actions/set-fullscreen", {"view_id": full["id"], "state": False})
    wait_for(lambda: not bool(window("Notes focus target").get("fullscreen")), "fullscreen ends")
    wait_for(lambda: not ipc("scottland/desktop-model").get("focus"), "fullscreen focus mode ends")
    note_action("remove", id=temporary["id"])

    # Widget form keeps note state but draws one dot only. Drag input is the actual rail transition.
    focus("Notes anchor")
    anchor = scene("Notes anchor")
    frame = anchor.get("scene_frame", anchor["frame"])
    sx, sy = frame["x"] + frame["width"] / 2, frame["y"] + frame["height"] / 2
    pointer(sx, sy)
    key("LEFTMETA", True)
    button(True)
    for step in range(1, 31):
        pointer(sx + (1590 - sx) * step / 30, sy)
        time.sleep(.02)  # pace one intended drag gesture
    button(False)
    key("LEFTMETA", False)
    wait_for(lambda: (row if (row := scene("Notes anchor")) and row.get("widgetized") else None),
        "anchor window docks as a widget", seconds=25)
    widget = wait_for(lambda: next((row for row in ipc("scottland/widgets")["widgets"]
        if row.get("title") == "Notes anchor"), None),
        "widget state for notes anchor", seconds=25)
    require("dragging to a rail preserves notes on the application window",
        row_for(notes(), anchor_id) is not None and len(row_for(notes(), anchor_id)["notes"]) ==
            len(row_for(before_reload, anchor_id)["notes"]) + 1, notes())
    def mapped_widget_view_id():
        current = next((row for row in ipc("scottland/widgets")["widgets"]
            if row.get("title") == "Notes anchor"), None)
        if current is None:
            return None
        value = int(current.get("widget_view", -1))
        return value if value > 0 else None

    try:
        widget_view_id = wait_for(mapped_widget_view_id, "mapped notes widget card", seconds=25)
    except AssertionError as error:
        raise AssertionError(f"{error}; widget={widget!r}; views={ipc('scottland/layout-state')['views']!r}")
    widget_view = wait_for(lambda: next((row for row in ipc("scottland/layout-state")["views"]
        if row.get("id") == widget_view_id), None), "rendered widget view", seconds=20)
    wait_for(lambda: ipc("scottland/layout-state")["widget_transition_count"] == 0,
        "widget morph settles", seconds=25)
    widget_view = next(row for row in ipc("scottland/layout-state")["views"]
        if row.get("id") == widget_view_id)
    widget_frame = widget_view.get("scene_frame", widget_view["frame"])
    stable_shot("widget-with-notes")
    # Remove all notes for a stable before/after image, then restore one without moving the card.
    keep_id = int(row_for(notes(), anchor_id)["notes"][0]["id"])
    for entry in list(row_for(notes(), anchor_id)["notes"]):
        if int(entry["id"]) != keep_id:
            note_action("remove", id=entry["id"])
    note_action("remove", id=keep_id)
    _, widget_empty = stable_shot("widget-no-note")
    keep = note_action("add", window=anchor_id, writer="agent-x", text="secret note text must stay hidden")
    wait_for(lambda: bool(row_for(notes(), anchor_id)), "widget note state")
    _, widget_after = stable_shot("widget-one-note")
    lower_x = round(widget_frame["x"] + widget_frame["width"]) if widget.get("rail") == "left" else round(widget_frame["x"])
    lower_y = round(widget_frame["y"] + widget_frame["height"])
    require("widget form draws one dot at the lower corner away from its rail",
        pixel(widget_after, lower_x, lower_y) != pixel(widget_empty, lower_x, lower_y),
        {"point": (lower_x, lower_y), "rail": widget.get("rail")})
    require("widget note adds no text or other card changes",
        changed_pixels(widget_empty, widget_after,
            (0, 0, widget_empty.get_width(), widget_empty.get_height())) < 400,
        changed_pixels(widget_empty, widget_after,
            (0, 0, widget_empty.get_width(), widget_empty.get_height())))
    pointer(lower_x, lower_y)
    _, widget_hover = stable_shot("widget-dot-hover")
    require("widget note dot does not react to hover",
        changed_pixels(widget_after, widget_hover,
            (0, 0, widget_after.get_width(), widget_after.get_height())) < 1000,
        changed_pixels(widget_after, widget_hover,
            (0, 0, widget_after.get_width(), widget_after.get_height())))

    # Close a separate noted window and require a newer complete empty slice.
    closer = launch("Notes close lifetime", "563d50")
    close_id = closer["id"]
    close_note = note_action("add", window=close_id, writer="closer", text="gone on close")
    require("close-lifetime fixture receives a note", "error" not in close_note, close_note)
    before_close = notes()
    close_reply = ipc("window-rules/close-view", {"id": close_id})
    require("window close request succeeds", "error" not in close_reply, close_reply)
    wait_for(lambda: window("Notes close lifetime") is None and row_for(notes(), close_id) is None,
        "closing a noted window removes its notes", seconds=20)
    after_close = notes()
    require("closing a noted window publishes a newer notes slice with no entry",
        after_close["version"] > before_close["version"] and row_for(after_close, close_id) is None,
        {"before": before_close, "after": after_close})

    # One real ctl/launcher/writer/note/Chromium path covers NT-17–19. The model reads below
    # observe the same note id under the provenance-bound window; no follow-up ctl command adds it.
    launch_with_note_behavior()

    print(f"window notes: {checks - failures}/{checks} checks passed", flush=True)
    if failures:
        raise SystemExit(1)
finally:
    for process in clients:
        if process.poll() is None:
            process.terminate()
    for process in clients:
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
    for path in temporary_dirs:
        shutil.rmtree(path, ignore_errors=True)
