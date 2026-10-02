#!/usr/bin/env python3
"""Behavioral key-layer tests inside a headless session. No live desktop/config writes."""
import configparser
import json
import os
from pathlib import Path
import shlex
import socket
import struct
import subprocess
import tempfile
import time

repo = Path(__file__).resolve().parents[1]
root = Path(tempfile.mkdtemp(prefix="scottland-key-layers-", dir=os.environ["XDG_RUNTIME_DIR"]))
passes = failures = 0
processes = []


def ipc(method, data=None):
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(5)
        sock.connect(os.environ["WAYFIRE_SOCKET"])
        body = json.dumps(dict(method=method, data=data or {})).encode()
        sock.sendall(struct.pack("<I", len(body)) + body)
        def read(count):
            result = b""
            while len(result) < count:
                chunk = sock.recv(count - len(result))
                if not chunk:
                    raise RuntimeError("IPC disconnected")
                result += chunk
            return result
        return json.loads(read(struct.unpack("<I", read(4))[0]))


def check(name, condition):
    global passes, failures
    if condition:
        passes += 1
        print("PASS ", name, flush=True)
    else:
        failures += 1
        print("FAIL ", name, flush=True)


def wait(predicate):
    for _ in range(100):
        if predicate():
            return True
        time.sleep(.05)
    raise RuntimeError("timed out waiting for test client/state")


def rows():
    return ipc("scottland/key-layer", {"action": "list"})["surfaces"]


def row(name):
    return next((v for v in rows() if v["pid"] == client.pid and
                 (v["title"] == "KL-" + name or
                  name == "popup" and "namespace" in v)), None)


def command(action, name="one"):
    path = root / "command.json"
    path.write_text(json.dumps(dict(action=action, name=name)))
    wait(lambda: not path.exists())
    time.sleep(.25)


def options(data):
    result = ipc("wayfire/set-config-options", data)
    if "error" in result:
        raise RuntimeError(result)
    time.sleep(.15)


def bind(name, key, tag):
    # Command bindings are configured only in the private compositor, never user files.
    options({"command/bindings": {name: {"binding": key, "command":
              "echo " + shlex.quote(tag) + " >> " + shlex.quote(str(root / "bindings"))}}})


def bindings():
    path = root / "bindings"
    return path.read_text().splitlines() if path.exists() else []


def key(code, state):
    assert "error" not in ipc("stipc/feed_key", {"key": "KEY_" + code, "state": state})
    time.sleep(.035)


def stroke(code, mods=()):
    for mod in mods:
        key(mod, True)
    key(code, True)
    key(code, False)
    for mod in reversed(mods):
        key(mod, False)
    time.sleep(.2)


def keys(name, code):
    path = root / "keys.jsonl"
    if not path.exists():
        return []
    return [e for line in path.read_text().splitlines() if
            (e := json.loads(line))["name"] == name and e["keycode"] == code + 8]


def focus(name):
    # Focus with real pointer input, not focus/present IPC.
    if name == "popup":
        x, y = 130, 90
    else:
        view = next(v for v in ipc("scottland/layout-state")["views"] if v["title"] == "KL-" + name)
        f = view["frame"]
        x, y = f["x"] + f["width"] / 2, f["y"] + f["height"] / 2
    ipc("stipc/move_cursor", {"x": int(x), "y": int(y)})
    ipc("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "press"})
    ipc("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "release"})
    time.sleep(.15)


def set_layer(name, chords):
    result = ipc("scottland/key-layer", dict(action="set", window=row(name)["window"], keys=chords))
    assert "error" not in result, result


try:
    client = subprocess.Popen(["python3", str(repo / "tests/key-layer-app.py"), str(root)],
                              env={**os.environ, "LD_PRELOAD": "libgtk4-layer-shell.so"},
                              stdout=(root / "client.log").open("w"), stderr=subprocess.STDOUT)
    processes.append(client)
    wait(lambda: row("one"))
    focus("one")
    bind("test", "KEY_F8", "F8")
    set_layer("one", ["0:F8"])
    stroke("F8")
    events = keys("one", 66)
    check("claimed press and release reach the focused toplevel", [e["edge"] for e in events] == ["down", "up"])
    check("claimed key suppresses the compositor binding", bindings() == [])
    check("one-shot IPC disconnect preserves the Wayland surface's layer", row("one")["active"])
    before = len(keys("one", 66))
    key("F8", True)
    time.sleep(.9)
    key("F8", False)
    time.sleep(.15)
    repeated = keys("one", 66)[before:]
    check("claimed held key keeps ordinary client repeat", len(repeated) > 2 and repeated[-1]["edge"] == "up" and bindings() == [])

    bind("test", "KEY_F9", "old")
    stroke("F9")
    check("unclaimed key fires user's binding", bindings() == ["old"])
    bind("test", "KEY_F9", "new")
    stroke("F9")
    check("binding changed while layer is active fires its new command", bindings() == ["old", "new"])

    bind("test", "<ctrl> KEY_J", "ctrl-j")
    set_layer("one", ["4:j"])
    stroke("J", ("LEFTCTRL",))
    events = keys("one", 36)
    check("claimed modified key preserves Ctrl on both edges", len(events) == 2 and all(e["mods"] & 4 for e in events))
    check("modified claim suppresses binding", "ctrl-j" not in bindings())
    bind("test", "<ctrl> <shift> KEY_J", "shift-j")
    stroke("J", ("LEFTCTRL", "LEFTSHIFT"))
    check("extra Shift is not claimed by 4:j", "shift-j" in bindings())

    options({"scottland/release_bindings": {"test": {"key": "F8", "command":
             "echo release >> " + shlex.quote(str(root / "bindings"))}}})
    bind("test", "KEY_F8", "press")
    set_layer("one", ["0:F8"])
    stroke("F8")
    check("claimed release suppresses release binding", "release" not in bindings())
    stroke("F9")
    options({"scottland/release_bindings": {"test": {"key": "F9", "command":
             "echo release >> " + shlex.quote(str(root / "bindings"))}}})
    stroke("F9")
    check("unclaimed release binding bleeds through", bindings().count("release") == 1)

    options({"scottland/key_remaps": {"test": {"apps": "org.scottland.TestKeyLayer",
             "from": "CTRL+K", "to": "CTRL+BackSpace"}}})
    set_layer("one", ["4:j"])
    stroke("K", ("LEFTCTRL",))
    check("unclaimed key retains per-app remap", len(keys("one", 14)) == 2 and not keys("one", 37))
    set_layer("one", ["4:k"])
    stroke("K", ("LEFTCTRL",))
    check("claim overrides remap with ordinary physical key events", len(keys("one", 37)) == 2 and len(keys("one", 14)) == 2)

    # Native command release bindings arm on press. A claimed press must not arm one.
    options({"command/bindings": {}, "command/release_bindings": {"test": {
             "binding": "KEY_F12", "command": "echo native-release >> " + shlex.quote(str(root / "bindings"))}}})
    set_layer("one", ["0:F12"])
    stroke("F12")
    check("claim suppresses native Wayfire release binding", "native-release" not in bindings() and len(keys("one", 88)) == 2)
    set_layer("one", ["0:F8"])
    stroke("F12")
    check("unclaimed native release binding works", bindings().count("native-release") == 1)
    key("F12", True)
    set_layer("one", ["0:F12"])
    key("F12", False)
    time.sleep(.2)
    check("new claim cannot take an already pressed native release binding", bindings().count("native-release") == 2)
    options({"command/release_bindings": {}})
    options({"input/xkb_options": "lv3:ralt_switch"})
    set_layer("one", ["0:F8"])
    before = bindings().count("release")
    options({"scottland/release_bindings": {"test": {"key": "F8", "command":
             "echo release >> " + shlex.quote(str(root / "bindings"))}}})
    stroke("F8", ("RIGHTALT",))
    check("extra AltGr/Mod5 does not match an unmodified claim", bindings().count("release") == before + 1)
    options({"input/xkb_options": "", "scottland/release_bindings": {}})

    # Import a real Lua function binding into this private compositor. Only the fixture
    # host gets a sandbox HOME; the user's config and host are never changed.
    home = root / "home"
    (home / ".config/hypr").mkdir(parents=True)
    (home / ".config/hypr/hyprland.lua").write_text(
        "hl.bind('F11', function() hl.exec_cmd(" + json.dumps("echo lua >> " + shlex.quote(str(root / "bindings"))) + ") end)\n")
    env = {**os.environ, "HOME": str(home)}
    fragment = subprocess.check_output([str(repo / "omarchy/config.d/50-omarchy-shortcuts"),
                                       str(repo / "core/config/scottland.ini")], env=env, text=True)
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.read_string(fragment)
    imported = {}
    for key_name, value in parser["command"].items():
        if key_name.startswith("binding_"):
            suffix = key_name[len("binding_"):]
            imported[suffix] = dict(binding=value, command=parser["command"]["command_" + suffix])
    options({"command/bindings": imported})
    fifo = Path(os.environ["XDG_RUNTIME_DIR"]) / "scottland" / (os.environ["WAYLAND_DISPLAY"] + ".lua.fifo")
    lua_host = subprocess.Popen([str(repo / "omarchy/libexec/scottland-luahost"), str(fifo)], env=env,
                                stdout=(root / "lua.log").open("w"), stderr=subprocess.STDOUT)
    processes.append(lua_host)
    wait(fifo.exists)
    stroke("F11")
    wait(lambda: "lua" in bindings())
    check("unclaimed imported Lua-function shortcut runs through the host", bindings().count("lua") == 1)
    set_layer("one", ["0:F11"])
    count = len(keys("one", 87))
    stroke("F11")
    check("claim overrides imported Lua shortcut without changing it", bindings().count("lua") == 1 and len(keys("one", 87)) == count + 2)
    set_layer("one", ["0:F8"])
    stroke("F11")
    check("imported Lua shortcut works again after claim replacement", bindings().count("lua") == 2)

    # Combined Alt hints: exact surface claims bleed through, unclaimed navigation remains.
    options({"scottland/alt_hold_delay": 300})
    set_layer("one", ["0:Alt_L", "0:Alt_R"])
    for alt in ("LEFTALT", "RIGHTALT"):
        key(alt, True)
        time.sleep(.45)
        check("claimed " + alt + " bypasses hint hold", not ipc("scottland/hints")["active"])
        key(alt, False)
    set_layer("one", ["8:a"])
    before = len(keys("one", 30))
    key("LEFTALT", True)
    key("A", True)
    key("A", False)
    time.sleep(.4)
    check("claimed quick Alt chord cancels pending hints and reaches app",
          not ipc("scottland/hints")["active"] and len(keys("one", 30)) == before + 2)
    key("LEFTALT", False)
    key("LEFTALT", True)
    wait(lambda: ipc("scottland/hints")["active"])
    before = len(keys("one", 30))
    original = next(v["frame"] for v in ipc("scottland/layout-state")["views"] if v["title"] == "KL-one")
    for _ in range(2):
        key("A", True)
        key("A", False)
    current = next(v["frame"] for v in ipc("scottland/layout-state")["views"] if v["title"] == "KL-one")
    check("claimed letters during hints reach app without cycling its window",
          len(keys("one", 30)) == before + 4 and ipc("scottland/hints")["active"] and
          abs(original["x"] - current["x"]) < 2 and abs(original["y"] - current["y"]) < 2)
    set_layer("one", ["8:F8"])
    before = len(keys("one", 66))
    key("F8", True)
    set_layer("one", [])
    key("F8", False)
    check("claim cleared during hints still completes its app press/release pair",
          len(keys("one", 66)) == before + 2)
    set_layer("one", ["8:F8"])
    before = len(keys("one", 30))
    key("A", True)
    key("A", False)
    check("unclaimed hint still selects with a registered layer",
          ipc("scottland/hints")["selected"] == row("one")["window"] and len(keys("one", 30)) == before and
          ipc("scottland/hints")["active"])
    key("LEFTALT", False)
    time.sleep(.4)
    check("claimed Alt events leave physical hint tracking ready for next chord", not ipc("scottland/hints")["active"])

    # Real compositor drag grab keeps Esc, even if the focused app claims it.
    set_layer("one", ["0:Escape"])
    def frame():
        return next(v["frame"] for v in ipc("scottland/layout-state")["views"] if v["title"] == "KL-one")
    origin = frame()
    x, y = int(origin["x"] + origin["width"] / 2), int(origin["y"] + origin["height"] / 2)
    ipc("stipc/move_cursor", {"x": x, "y": y})
    key("LEFTMETA", True)
    ipc("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "press"})
    for offset in range(10, 81, 10):
        ipc("stipc/move_cursor", {"x": x + offset, "y": y})
        time.sleep(.025)
    key("LEFTMETA", False)
    time.sleep(.1)
    stroke("ESC")
    ipc("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "release"})
    time.sleep(.6)
    after = frame()
    check("compositor drag keeps Esc ahead of surface claims", abs(after["x"] - origin["x"]) < 2 and abs(after["y"] - origin["y"]) < 2 and not keys("one", 1))

    set_layer("one", ["0:F8"])
    command("hide", "one")
    command("show", "two")
    wait(lambda: row("two"))
    set_layer("two", ["0:F10"])
    command("show", "one")
    focus("one")
    check("unmap removes layer even if same surface maps again", not row("one")["registered"])
    set_layer("one", ["0:F8"])
    command("hide", "two")
    focus("one")
    command("show", "two")
    focus("two")
    # Re-register after unmap (the layer intentionally does not survive hiding).
    set_layer("two", ["0:F10"])
    bind("test", "KEY_F8", "away")
    stroke("F8")
    check("moving focus deactivates first window's claim", not row("one")["active"] and "away" in bindings())
    stroke("F10")
    check("second window owns its own layer in the same process", len(keys("two", 68)) == 2)
    command("hide", "two")
    focus("one")
    check("returning focus reactivates still-mapped window's layer", row("one")["active"])

    # The popup and the toplevel share a process but have distinct surface identities.
    command("show", "popup")
    wait(lambda: row("popup"))
    selector = dict(pid=client.pid, namespace="scottland-test-key-layer")
    reply = ipc("scottland/key-layer", dict(action="set", **selector, keys=["0:F8"]))
    check("pid + namespace selects the layer-shell surface", reply.get("window") == row("popup")["window"])
    focus("popup")
    count = len(bindings())
    stroke("F8")
    check("claimed press/release reaches layer-shell popup", len(keys("popup", 66)) == 2 and len(bindings()) == count)
    check("layer-shell focus activates only the popup's layer", row("popup")["active"] and not row("one")["active"])
    stroke("F11")
    check("unclaimed key reaches popup normally", len(keys("popup", 87)) == 2)

    command("make", "duplicate")
    wait(lambda: len([v for v in rows() if v["pid"] == client.pid and "namespace" in v]) == 2)
    check("ambiguous namespace rejected instead of guessing", "error" in ipc("scottland/key-layer", dict(action="set", **selector, keys=["0:F9"])))
    command("close", "duplicate")
    focus("popup")
    set_layer("popup", ["0:F9"])
    bind("test", "KEY_F8", "replaced")
    stroke("F8")
    check("re-register replaces old claims", "replaced" in bindings())
    stroke("F9")
    check("replacement's new claim reaches popup", len(keys("popup", 67)) == 2)
    for bad in ["999:F8", "2:F8", "4:NotAKeysym", "-1:F8", "4:", "F8", 4]:
        check("reject malformed chord " + repr(bad), "error" in ipc("scottland/key-layer", dict(action="set", **selector, keys=[bad])))
    check("failed registration preserves previous set", row("popup")["keys"] == ["0:F9"])

    set_layer("popup", ["4:plus"])
    bind("test", "<ctrl> <shift> KEY_EQUAL", "plus")
    stroke("EQUAL", ("LEFTCTRL", "LEFTSHIFT"))
    check("produced plus matches Ctrl plus consumed Shift", len(keys("popup", 13)) == 2 and "plus" not in bindings())
    set_layer("popup", ["4:equal"])
    stroke("EQUAL", ("LEFTCTRL", "LEFTSHIFT"))
    check("plain equal does not also claim Ctrl+Shift+plus", "plus" in bindings())

    set_layer("popup", ["0:F8"])
    before = len(keys("popup", 66))
    key("F8", True)
    ipc("scottland/key-layer", dict(action="clear", **selector))
    options({"scottland/release_bindings": {"test": {"key": "F8", "command":
             "echo held-release >> " + shlex.quote(str(root / "bindings"))}}})
    key("F8", False)
    time.sleep(.15)
    check("clear during held key finishes pair without release action", len(keys("popup", 66)) == before + 2 and "held-release" not in bindings())
    check("explicit clear removes registration", not row("popup")["registered"])
    set_layer("popup", ["0:F8"])
    ipc("scottland/key-layer", dict(action="set", **selector, keys=[]))
    check("empty set clears the layer", not row("popup")["registered"])

    # The driver inherits the session environment through scottland-exec.
    subprocess.run(["grim", str(root / "layers.png")], check=True)
    set_layer("popup", ["0:F8"])
    popup_id = row("popup")["window"]
    command("close", "popup")
    wait(lambda: not row("popup"))
    check("closing surface removes registration and rejects stale id", "error" in ipc("scottland/key-layer", dict(action="set", window=popup_id, keys=["0:F8"])))
    focus("one")
    one_id = row("one")["window"]
    command("quit")
    client.wait(timeout=5)
    wait(lambda: not any(v["pid"] == client.pid for v in rows()))
    check("Wayland client disconnect removes all surface layers", not any(v["window"] == one_id for v in rows()))
    # A pre-existing blanket inhibitor must stay inhibited. Wayfire 0.11's reference
    # counter must not cross zero when the layer temporarily suspends a claimed event.
    options({"shortcuts-inhibit/inhibit_by_default": 'app_id is "org.scottland.TestKeyLayer"'})
    client = subprocess.Popen(["python3", str(repo / "tests/key-layer-app.py"), str(root)],
                              env={**os.environ, "LD_PRELOAD": "libgtk4-layer-shell.so"},
                              stdout=(root / "inhibited-client.log").open("w"), stderr=subprocess.STDOUT)
    processes.append(client)
    wait(lambda: row("one"))
    focus("one")
    before = len(keys("one", 66))
    bind("test", "KEY_F8", "inhibited-leak")
    set_layer("one", ["0:F8"])
    stroke("F8")
    check("claim preserves a pre-existing blanket shortcut inhibitor", "inhibited-leak" not in bindings() and len(keys("one", 66)) == before + 2)
    bind("test", "KEY_F9", "inhibited-unclaimed")
    before = len(keys("one", 67))
    stroke("F9")
    check("unclaimed key retains pre-existing blanket inhibition", "inhibited-unclaimed" not in bindings() and len(keys("one", 67)) == before + 2)
    command("quit")
    client.wait(timeout=5)
    wait(lambda: not any(v["pid"] == client.pid for v in rows()))
    stroke("F9")
    check("closing inhibited client restores user's bindings", "inhibited-unclaimed" in bindings())
    check("unknown action rejected", "error" in ipc("scottland/key-layer", {"action": "other"}))
    check("missing action rejected", "error" in ipc("scottland/key-layer", {}))

    print(f"{passes} passed, {failures} failed; artifacts: {root}", flush=True)
finally:
    for process in processes:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)

raise SystemExit(bool(failures))
