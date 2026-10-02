#!/usr/bin/env python3
"""Seeded real-input model/scene/replica/render audits. Invoked by state-model-test.sh only."""
import ast
import json
import os
from pathlib import Path
import random
import select
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time

if os.environ.get("SCOTTLAND_TEST_MODEL") != "1":
    sys.exit("model tests require the isolated headless harness")

seed = int(sys.argv[1])
steps = int(sys.argv[2])
rng = random.Random(seed)
trace = []
passes = 0
artifacts = Path(os.environ["XDG_RUNTIME_DIR"]) / "scottland-model-artifacts"
artifacts.mkdir(exist_ok=True)


class Ipc:
    def __init__(self):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.settimeout(5)
        self.sock.connect(os.environ["WAYFIRE_SOCKET"])

    def exactly(self, count):
        data = b""
        while len(data) < count:
            chunk = self.sock.recv(count - len(data))
            if not chunk:
                raise ConnectionError("compositor disconnected")
            data += chunk
        return data

    def receive(self):
        return json.loads(self.exactly(struct.unpack("<I", self.exactly(4))[0]))

    def call(self, method, data=None):
        body = json.dumps({"method": method, "data": data or {}}).encode()
        self.sock.sendall(struct.pack("<I", len(body)) + body)
        reply = self.receive()
        if isinstance(reply, dict) and reply.get("error"):
            raise AssertionError(f"{method}: {reply}")
        return reply


ipc = Ipc()


def diagnostics():
    out = subprocess.check_output(["gdbus", "call", "--session", "--dest", "org.scottland.Widgets",
                                   "--object-path", "/org/scottland/Widgets", "--method",
                                   "org.scottland.Diagnostics.Snapshot"], text=True)
    return json.loads(ast.literal_eval(out)[0])


def check(name, condition=True):
    global passes
    assert condition, name
    passes += 1
    print(f"PASS  {name}", flush=True)


def audit(label):
    # Let compositor transactions, animations and the renderer's report settle. Tests observe;
    # no state is repaired and production has no audit loop.
    deadline = time.monotonic() + 5
    last = None
    while time.monotonic() < deadline:
        last = ipc.call("scottland/audit-model", {"service": diagnostics()})
        if last["ok"]:
            check(f"audit {label}")
            return
        time.sleep(0.1)
    observations = {"label": label, "audit": last, "service": diagnostics(),
                    "scene": ipc.call("scottland/layout-state"), "desktop": ipc.call("scottland/desktop-model")}
    (artifacts / f"seed-{seed}-failure.json").write_text(json.dumps(observations, indent=2))
    print("failure observations=" + json.dumps(observations), file=sys.stderr, flush=True)
    raise AssertionError(f"audit {label}: {last}")


def key(code, state):
    ipc.call("stipc/feed_key", {"key": code, "state": state})


def combo(modifier, code):
    key(modifier, True)
    key(code, True)
    key(code, False)
    key(modifier, False)


def shown(app_id):
    model = ipc.call("scottland/desktop-model")
    link = next((w for w in model["widgets"] if w["window"] == app_id), None)
    represented = link["widget_view"] if link and link["lifecycle"] == "docked" else app_id
    return next(v for v in ipc.call("scottland/layout-state")["views"] if v["id"] == represented), link


def drag(app_id, x, y=None, cancel=False, finger=False, audit_held=False):
    view, _ = shown(app_id)
    watch = Ipc() if audit_held else None
    initial = watch.call("scottland/subscribe", {"slice": "desktop"}) if watch else None
    f = view["frame"]
    sx, sy = f["x"] + f["width"] / 2, f["y"] + f["height"] / 2
    y = sy if y is None else y
    if finger:
        ipc.call("stipc/touch", {"finger": 0, "x": round(sx), "y": round(sy)})
        if not view["widget"]:
            time.sleep(0.5)  # window lift, as a real finger holding still
    else:
        ipc.call("stipc/move_cursor", {"x": round(sx), "y": round(sy)})
        time.sleep(0.1)
        key("KEY_LEFTMETA", True)
        ipc.call("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "press"})
    for i in range(1, 13):
        point = {"x": round(sx + (x - sx) * i / 12), "y": round(sy + (y - sy) * i / 12)}
        ipc.call("stipc/touch" if finger else "stipc/move_cursor", {**point, **({"finger": 0} if finger else {})})
        time.sleep(0.025)
    time.sleep(0.4)
    if audit_held:
        state = ipc.call("scottland/desktop-model")
        check("drag origin and morph are in the desktop snapshot",
              state["drag"]["started"] and state["drag"]["window"] == view["id"]
              and state["drag"]["morph"]["window"] == view["id"])
        delivered = initial
        while select.select([watch.sock], [], [], 0.1)[0]:
            delivered = watch.receive()
        check("desktop subscription delivers the held morph's logical state",
              delivered["version"] > initial["version"] and delivered["drag"]["morph"] == state["drag"]["morph"])
        if view["widget"]:
            check("widget morph direction follows the pointer independently of scene agreement",
                  delivered["drag"]["morph"]["from_widget"]
                  and delivered["drag"]["morph"]["toward"] == (x == width / 2))
        watch.sock.close()
        audit("held drag morph")
    if cancel:
        key("KEY_ESC", True)
        key("KEY_ESC", False)
    if finger:
        ipc.call("stipc/touch_release", {"finger": 0})
    else:
        ipc.call("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "release"})
        key("KEY_LEFTMETA", False)
    time.sleep(0.7)


clients = {}
work = tempfile.TemporaryDirectory(prefix="scottland-model-test-")


def open_app():
    number = len(clients) + 1
    path = Path(work.name) / f"title-{number}"
    path.write_text(f"Model {number}")
    # The real application updates its own title. No compositor setter is involved.
    program = """import pathlib,sys,time,subprocess
p=pathlib.Path(sys.argv[1]); last=None
while True:
    text=p.read_text()
    if text!=last:
        print('\\033]2;'+text+'\\007',end='',flush=True); last=text
    mailbox=p.with_suffix('.mailbox')
    if mailbox.exists():
        subprocess.run(['busctl','--user','call','org.scottland.Widgets','/org/scottland/Widgets',
                        'org.scottland.WidgetData','Publish','s',mailbox.read_text()],check=True)
        mailbox.unlink()
    time.sleep(.05)
"""
    client = subprocess.Popen(["foot", "--app-id", f"scottland-model-test-{number}", "-T", f"Model {number}",
                               "-W", "35x8", "python3", "-c", program, str(path)],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.8)
    window = next(v["id"] for v in ipc.call("scottland/desktop-model")["windows"]
                  if v["app_id"] == f"scottland-model-test-{number}")
    clients[window] = (client, path)
    return window


def reload_plugin():
    global ipc
    runtime = Path(os.environ["XDG_RUNTIME_DIR"]) / "scottland"
    mark = runtime / (os.environ["WAYLAND_DISPLAY"] + ".reloading")
    fresh = Path(work.name) / f"libscottland-{time.monotonic_ns()}.so"
    shutil.copyfile("build/libscottland.so", fresh)
    plugins = ipc.call("wayfire/get-config-option", {"option": "core/plugins"})["value"]
    changed = " ".join(str(fresh) if p == "scottland" or "/libscottland-" in p else p for p in plugins.split())
    mark.touch()
    try:
        ipc.call("wayfire/set-config-options", {"core/plugins": changed})
        time.sleep(0.8)
    finally:
        mark.unlink(missing_ok=True)
    # Reconnect and read current state, exactly as a late external subscriber does.
    ipc.sock.close()
    ipc = Ipc()


try:
    ipc.call("scottland/audit-model", {"service": diagnostics()})  # verify the test-only endpoint before input
    ipc.call("wayfire/set-config-options", {"scottland/sounds": False})
    width = ipc.call("window-rules/list-outputs")[0]["geometry"]["width"]
    a = open_app()
    audit("window mapped")
    drag(a, width - 6, audit_held=True)
    audit("docked")
    check("docking used real pointer input", shown(a)[1] is not None)
    properties = subprocess.check_output(["busctl", "--user", "get-property", "org.scottland.Widgets",
                                         f"/org/scottland/widget/{a}", "org.scottland.Widget", "Version", "Revision"], text=True)
    check("D-Bus presentation exposes model version and file revision", len(properties.splitlines()) == 2
          and all(line.startswith("t ") for line in properties.splitlines()))
    wrong = diagnostics()
    wrong["widgets"][str(a)]["Title"] = "an incorrect replica"
    check("checker rejects an incorrect service replica", not ipc.call("scottland/audit-model", {"service": wrong})["ok"])
    wrong = diagnostics()
    unit = shown(a)[1]["widget_unit"]
    wrong["rendered"][unit]["width"] += 100
    check("checker rejects an incorrect card render report", not ipc.call("scottland/audit-model", {"service": wrong})["ok"])
    combo("KEY_LEFTMETA", "KEY_M")
    time.sleep(0.6)
    audit("collapsed")
    b = open_app()
    drag(b, 6)
    audit("new card starts collapsed")
    check("card started collapsed", shown(b)[1]["minimized"])
    combo("KEY_LEFTMETA", "KEY_M")
    time.sleep(0.6)
    audit("cards expanded, including the one started collapsed")
    subprocess.run(["grim", str(artifacts / f"seed-{seed}-expanded.png")], check=True)

    # Stay in one held widget drag: off the rail, then back onto it. Check delivered
    # logical direction and center against input intent, independently of the audit.
    time.sleep(2.6)
    roundtrip_view, _ = shown(b)
    rf = roundtrip_view["frame"]
    rx, ry = rf["x"] + rf["width"] / 2, rf["y"] + rf["height"] / 2
    watch = Ipc()
    initial = watch.call("scottland/subscribe", {"slice": "desktop"})
    ipc.call("stipc/move_cursor", {"x": round(rx), "y": round(ry)})
    time.sleep(.1)
    key("KEY_LEFTMETA", True)
    ipc.call("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "press"})
    delivered = initial
    for destination, toward in ((width / 2, True), (6, False)):
        ipc.call("stipc/move_cursor", {"x": round(destination), "y": round(ry)})
        time.sleep(.4)
        previous = delivered
        while select.select([watch.sock], [], [], .1)[0]:
            delivered = watch.receive()
        morph = delivered["drag"].get("morph", {})
        check("subscription delivers widget morph " + ("off rail" if toward else "back on rail"),
              delivered["version"] > previous["version"] and morph.get("from_widget")
              and morph.get("toward") == toward
              and morph.get("center_x") != previous["drag"].get("morph", {}).get("center_x"))
        direct = ipc.call("scottland/desktop-model")
        check("delivered and direct logical morph share the same version",
              direct["version"] == delivered["version"] and direct["drag"]["morph"] == morph)
    ipc.call("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "release"})
    key("KEY_LEFTMETA", False)
    watch.sock.close()
    time.sleep(.7)
    audit("widget round trip in one held drag")

    # A widget moved, resized by Super+M, then re-grabbed: Esc restores the original rail
    # anchor, and a later resize must not jump back to the cancelled drop point.
    time.sleep(2.6)  # begin a fresh move in widget form
    before, _ = shown(b)
    f = before["frame"]
    old_y = f["y"]
    drag(b, 6, f["y"] + f["height"] / 2 + 45)
    audit("widget moved before collapsed re-grab")
    combo("KEY_LEFTMETA", "KEY_M")
    time.sleep(0.5)
    audit("collapsed during re-grab chain")
    drag(b, width / 2, cancel=True)
    audit("Esc restores the widget anchor after collapse")
    restored, link = shown(b)
    check("Esc restored the original rail and vertical anchor", link["rail"] == "left"
          and abs(restored["frame"]["y"] - old_y) < 1.5)
    combo("KEY_LEFTMETA", "KEY_M")
    time.sleep(0.5)
    audit("expanding after Esc retains the restored anchor")

    events = Ipc()
    current = events.call("scottland/subscribe", {"slice": "widgets"})
    check("late subscription reads complete current widgets", len(current["widgets"]) == 2)
    events.sock.close()
    events = Ipc()
    check("reconnect immediately reads current state", events.call("scottland/subscribe", {"slice": "widgets"}) == current)
    events.sock.close()
    audit("late subscriber and reconnect")

    c = open_app()
    audit("ordinary window for geometry subscription test")
    events = Ipc()
    events.call("scottland/subscribe", {"slice": "widgets"})
    version = ipc.call("scottland/desktop-model")["version"]
    view, _ = shown(c)
    f = view["frame"]
    drag(c, f["x"] + f["width"] / 2 + 25)
    check("pure geometry motion advances the model without external traffic",
          ipc.call("scottland/desktop-model")["version"] > version
          and not select.select([events.sock], [], [], 0)[0])
    events.sock.close()
    audit("geometry filtered from external slice")
    # Re-grab during the ordinary drop hold, then Esc: it must rejoin the normal layer.
    scene = next(v for v in ipc.call("window-rules/list-views") if v["id"] == c)
    check("ordinary drop fixture is temporarily above widgets", scene["always-on-top"]
          and ipc.call("scottland/desktop-model")["drag"]["held_above"] == c)
    view, _ = shown(c)
    f = view["frame"]
    drag(c, f["x"] + f["width"] / 2 + 20, cancel=True)
    scene = next(v for v in ipc.call("window-rules/list-views") if v["id"] == c)
    check("Esc after re-grab releases the ordinary window above widgets", not scene["always-on-top"])
    check("Esc ends model hold ownership", ipc.call("scottland/desktop-model")["drag"]["held_above"] == -1)

    for source in ("reload-a", "reload-b"):
        ipc.call("scottland/attention", {"window": a, "source": source, "attention": True})
    audit("two background attention sources")

    # A helper restart must reconstruct its complete copy with already running cards.
    desktop = shown(a)[1]["desktop"]
    subprocess.run(["busctl", "--user", "emit", "/com/canonical/unity/launcherentry/1",
                    "com.canonical.Unity.LauncherEntry", "Update", "sa{sv}",
                    "application://" + desktop + ".desktop", "2", "count", "x", "9", "count-visible", "b", "true"], check=True)
    time.sleep(0.4)
    audit("badge before restart")
    bus_pid_file = Path(os.environ["XDG_RUNTIME_DIR"]) / "scottland" / (os.environ["WAYLAND_DISPLAY"] + ".widget-bus.pid")
    bus_pid = int(bus_pid_file.read_text().splitlines()[0])
    os.kill(bus_pid, 15)
    subprocess.run([str(Path(os.environ["SCOTTLAND_HOOKS"]) / "reload.d/08-widget-bus")], check=True)
    time.sleep(1)
    audit("widget service restart")
    check("service-owned badge survives its restart", diagnostics()["widgets"][str(a)]["Badge"] == 9)

    payload = '{"unread": 431}'
    clients[b][1].with_suffix('.mailbox').write_text(payload)
    time.sleep(0.6)
    check("second widget owns mailbox data before reload", diagnostics()["widgets"][str(b)]["Data"] == payload)
    before = ipc.call("scottland/desktop-model")
    reload_plugin()
    check("complete handover preserves the second widget mailbox", diagnostics()["widgets"][str(b)]["Data"] == payload)
    audit("model reload")
    after = ipc.call("scottland/desktop-model")
    check("reload preserves widgets and increases version", after["version"] > before["version"]
          and [w["widget_view"] for w in after["widgets"]] == [w["widget_view"] for w in before["widgets"]])
    check("reload preserves each attention source and collapsed mode",
          next(v["attention"] for v in after["windows"] if v["id"] == a)
          == next(v["attention"] for v in before["windows"] if v["id"] == a)
          and after["collapsed"] == before["collapsed"])

    # Guarantee every requested family appears, then shuffle it with the seed and add random steps.
    operations = ["dock", "undock", "collapse", "title", "reload", "esc", "close", "finger", "attention"]
    sequence = operations + [rng.choice(operations) for _ in range(max(0, steps - len(operations)))]
    rng.shuffle(sequence)
    for step, op in enumerate(sequence):
        active = [w for w in clients if any(v["id"] == w for v in ipc.call("scottland/desktop-model")["windows"])]
        if not active:
            active = [open_app()]
            audit(f"{step} replacement window")
        window = rng.choice(active)
        trace.append({"step": step, "operation": op, "window": window})
        _, link = shown(window)
        if op == "dock":
            drag(window, rng.choice([6, width - 6]), rng.randint(130, 560))
        elif op == "undock":
            drag(window, width / 2, rng.randint(160, 480))
        elif op == "collapse":
            combo("KEY_LEFTMETA", "KEY_M")
            time.sleep(0.5)
        elif op == "title":
            clients[window][1].write_text(rng.choice(["", "Short", "A title that gets much longer"]) + f" {step}")
            time.sleep(0.6)
        elif op == "reload":
            reload_plugin()
        elif op == "esc":
            drag(window, 6 if not link else width / 2, cancel=True)
        elif op == "finger":
            # Card single-finger movement; a normal window first docks using real pointer input.
            if not link:
                drag(window, width - 6)
                audit(f"{step} before finger drag")
            view, _ = shown(window)
            f = view["frame"]
            drag(window, f["x"] + f["width"] / 2, rng.randint(140, 540), finger=True)
        elif op == "attention":
            for source in ("random-a", "random-b"):
                ipc.call("scottland/attention", {"window": window, "source": source, "attention": True})
            ipc.call("scottland/attention", {"window": window, "source": "random-a", "attention": False})
        elif op == "close":
            view, _ = shown(window)
            f = view["frame"]
            # Click the halo to focus without opening a card; Alt+F4 is Scottland's real close binding.
            ipc.call("stipc/move_cursor", {"x": round(f["x"] + f["width"] / 2), "y": round(f["y"] - 3)})
            ipc.call("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "press"})
            ipc.call("stipc/feed_button", {"combo": "BTN_LEFT", "mode": "release"})
            combo("KEY_LEFTALT", "KEY_F4")
            time.sleep(0.7)
        audit(f"seed {seed} step {step} {op}")
    subprocess.run(["grim", str(artifacts / f"seed-{seed}-final.png")], check=True)
    check("headless scene renders after randomized inputs")
    print(f"{passes} checks passed; seed={seed}, steps={len(sequence)}", flush=True)
except Exception:
    print(f"FAIL seed={seed} replay: tests/state-model-test.sh {seed} {steps}\ntrace={json.dumps(trace)}", file=sys.stderr)
    raise
finally:
    for process, _ in clients.values():
        if process.poll() is None:
            process.terminate()
    work.cleanup()
