#!/usr/bin/env python3
"""Real stipc/drag checks for WK13's always-avoid preference, including a bounded stress run."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time

if len(sys.argv) != 3:
    raise SystemExit("usage: hint-avoidance-always-test.py ARTIFACT_DIR HEADLESS_DIR")
artifacts = Path(sys.argv[1])
headless_dir = Path(sys.argv[2])
artifacts.mkdir(parents=True, exist_ok=True)
spec = importlib.util.spec_from_file_location("widget_input", Path(__file__).with_name("widget-input-test.py"))
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)
passes = failures = 0
children = []


def check(name, okay, details=""):
    global passes, failures
    print(("PASS " if okay else "FAIL ") + name + (f": {details}" if details and not okay else ""), flush=True)
    passes += bool(okay)
    failures += not okay


def offsets():
    return {row["window"]: (row["dx"], row["dy"], row["visible"])
            for row in t.ipc.call("scottland/hints")["hints"]}


def geometry():
    return {v["id"]: v["geometry"] for v in t.ipc.call("window-rules/list-views")}


def settled_zero(timeout=4):
    t.wait_for(lambda: all(abs(dx) + abs(dy) < .15 for dx, dy, _ in offsets().values()), timeout=timeout)


def cli(*args):
    ctl = Path(__file__).resolve().parents[1] / "core/libexec/scottland-ctl"
    return subprocess.check_output([str(ctl), *args], text=True, timeout=8)


def restore_with_slow_hint(window_id, title):
    t.key("LEFTALT", True)
    t.wait_for(lambda: t.ipc.call("scottland/hints")["active"])
    label = next(row["hint"] for row in t.ipc.call("scottland/hints")["hints"]
                 if row["window"] == window_id)
    for _ in range(3):
        for letter in label:
            t.key(letter.upper(), True)
            t.key(letter.upper(), False)
        time.sleep(.36)  # Slow presses use the widget-start cycle, not WK15 double-tap.
        if all(int(w["id"]) != window_id for w in t.ipc.call("scottland/widgets")["widgets"]):
            break
    t.key("LEFTALT", False)
    t.wait_for(lambda: not t.ipc.call("scottland/hints")["active"])
    t.wait_for(lambda: t.app(title) is not None and not t.app(title)["hidden"] and
               all(int(w["id"]) != window_id for w in t.ipc.call("scottland/widgets")["widgets"]),
               timeout=8)


def cpu_sample(seconds=3):
    wayfire_config = str(headless_dir / "wayfire.ini").encode()
    pid = None
    for cmdline in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            data = cmdline.read_bytes()
            if b"wayfire" in data.split(b"\0", 1)[0] and wayfire_config in data:
                pid = int(cmdline.parent.name)
                break
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            pass
    if pid is None:
        raise RuntimeError(f"could not find this session's Wayfire process ({wayfire_config!r})")
    ticks = __import__("os").sysconf("SC_CLK_TCK")
    def ticks_now():
        fields = Path(f"/proc/{pid}/stat").read_text().split()
        return int(fields[13]) + int(fields[14])
    before = ticks_now(); started = time.monotonic()
    time.sleep(seconds)
    elapsed = time.monotonic() - started
    return (ticks_now() - before) / ticks / elapsed


try:
    values = json.loads(cli("get"))
    check("scottland-ctl reports the shipped default off", values.get("hint_avoidance_always") is False, values)
    outputs = t.ipc.call("window-rules/list-outputs")
    output = outputs[0]["geometry"]
    titles = [f"avoidance-large-{i}" for i in range(6)]
    for title in titles:
        proc = subprocess.Popen(["foot", "-c", "/dev/null", "-T", title, "-W", "100x32",
                                 "sh", "-c", "exec sleep 600"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        children.append((title, proc))
        t.owned.append((title, proc))
        t.wait_for(lambda title=title: t.app(title))
    time.sleep(.5)

    # Use identical, large fixture rectangles to make the hint-circle exposure solve work hard.
    # These are fixture setup only; all setting toggles and widget transitions below use real input.
    width, height = round(output["width"] * .72), round(output["height"] * .76)
    rect = {"x": output["x"] + (output["width"] - width) // 2,
            "y": output["y"] + (output["height"] - height) // 2,
            "width": width, "height": height}
    ids = {title: t.app(title)["id"] for title in titles}
    for window in ids.values():
        t.ipc.call("window-rules/configure-view", {"id": window, "geometry": rect})
    time.sleep(.5)
    t.ipc.call("window-rules/focus-view", {"id": ids[titles[-1]]})
    time.sleep(.25)
    original = geometry()
    settled_zero()
    check("default off leaves no visual offset outside Window mode",
          not any(abs(dx) + abs(dy) >= .15 for dx, dy, _ in offsets().values()))

    cli("set", "hint_avoidance_always", "true")
    t.wait_for(lambda: json.loads(cli("get")).get("hint_avoidance_always") is True)
    t.wait_for(lambda: sum(abs(dx) + abs(dy) > 10 for dx, dy, _ in offsets().values()) >= 3)
    displaced = offsets()
    stable_geometry = geometry()
    output_center_x = output["x"] + output["width"] / 2
    toward_rail = sum(
        abs((original[window]["x"] + original[window]["width"] / 2 + displaced[window][0]) - output_center_x) >
        abs((original[window]["x"] + original[window]["width"] / 2) - output_center_x) + 1
        for window in ids.values())
    check("always-on avoids overlapping hint circles outside Window mode",
          not t.ipc.call("scottland/hints")["active"] and
          sum(abs(dx) + abs(dy) > 10 for dx, dy, _ in displaced.values()) >= 3 and
          all(not visible for _, _, visible in displaced.values()), displaced)
    check("large-window exposure moves several surfaces outward toward the widget rails",
          toward_rail >= 2, f"{toward_rail} windows shifted outward")
    check("always-on offsets leave model geometry and widget state untouched",
          stable_geometry == original and not t.ipc.call("scottland/widgets")["widgets"])
    subprocess.run(["grim", str(artifacts / "always-avoid-large-overlap.png")], check=True, timeout=8)

    # Attention remains live on the large pile while one separate window is repeatedly docked
    # and restored with Super+drag. The recorded window is never focused, so its pulse persists.
    pulse_id = ids[titles[0]]
    attention = t.ipc.call("scottland/attention", {"window": pulse_id, "attention": True,
                                                      "source": "always-avoid-stress"})
    check("attention breathing is active during the stress run",
          any(row["id"] == pulse_id and "always-avoid-stress" in row["attention"]
              for row in attention.get("windows", [])))
    target = titles[-1]
    sample = cpu_sample()
    check("settled always-on compositor stays below a full-core spin", sample < .95, f"{sample:.1%} of one core")
    (artifacts / "stress-metrics.json").write_text(json.dumps({
        "settled_compositor_cpu_one_core_fraction": sample,
        "attention_window": pulse_id,
        "windows_shifted_outward_toward_rails": toward_rail,
        "offsets_before_widget_cycles": displaced,
        "geometry_before_widget_cycles": stable_geometry,
    }, indent=2) + "\n")
    subprocess.run(["grim", str(artifacts / "attention-breathing.png")], check=True, timeout=8)

    for round_number in range(10):
        window = t.app(target)
        if not window:
            window = t.wait_for(lambda: t.app(target))
        y = output["y"] + output["height"] // 2
        t.drag_begin(window, output["x"] + output["width"] - 5, y)
        t.drag_end()
        t.wait_for(lambda: t.card(target) and not t.card(target)["preview"], timeout=8)
        if round_number in (0, 3, 9):
            subprocess.run(["grim", str(artifacts / f"widgetized-{round_number + 1}.png")],
                           check=True, timeout=8)
        check(f"always-on real-input widgetize {round_number + 1}",
              any(w["title"] == target for w in t.ipc.call("scottland/widgets")["widgets"]) and
              t.card(target) is not None)
        restore_with_slow_hint(ids[target], target)
        def restored():
            app = t.app(target)
            return app is not None and not app["hidden"] and all(
                int(w["id"]) != ids[target] for w in t.ipc.call("scottland/widgets")["widgets"])
        try:
            t.wait_for(restored, timeout=8)
        except (AssertionError, TimeoutError, OSError) as error:
            diagnostic = {
                "round": round_number + 1,
                "error": repr(error),
                "app": t.app(target),
                "card": t.card(target),
                "widgets": t.ipc.call("scottland/widgets"),
                "layout": t.ipc.call("scottland/layout-state"),
                "views": t.ipc.call("window-rules/list-views"),
                "hints": t.ipc.call("scottland/hints"),
            }
            (artifacts / f"restore-failure-{round_number + 1}.json").write_text(
                json.dumps(diagnostic, indent=2, default=str) + "\n")
            subprocess.run(["grim", str(artifacts / f"restore-failure-{round_number + 1}.png")],
                           check=False, timeout=8)
            raise
        t.wait_for(lambda: t.ipc.call("scottland/layout-state")["widget_transition_count"] == 0, timeout=8)
        check(f"always-on real-input restore {round_number + 1}",
              not t.ipc.call("scottland/widgets")["widgets"] or
              all(int(w["id"]) != ids[target] for w in t.ipc.call("scottland/widgets")["widgets"]))
        if round_number in (0, 4, 9):
            subprocess.run(["grim", str(artifacts / f"widget-cycle-{round_number + 1}.png")],
                           check=True, timeout=8)
        check("attention source survives unrelated widget lifecycle",
              any(row["id"] == pulse_id and "always-avoid-stress" in row["attention"]
                  for row in t.ipc.call("scottland/desktop-model", {"slice": "attention"}).get("windows", [])))

    after_cycles = geometry()
    check("stress moves only the window being explicitly dragged",
          all(after_cycles[window] == stable_geometry[window] for window in ids.values() if window != ids[target]))
    cli("set", "hint_avoidance_always", "false")
    t.wait_for(lambda: json.loads(cli("get")).get("hint_avoidance_always") is False)
    settled_zero()
    check("turning always-avoid off eases every scene offset home",
          all(abs(dx) + abs(dy) < .15 for dx, dy, _ in offsets().values()))
    check("turning avoidance off changes no true window geometry", geometry() == after_cycles)
    print(f"always-avoid stress: {passes} passed, {failures} failed; settled compositor CPU {sample:.1%} of one core",
          flush=True)
finally:
    for _, proc in children:
        if proc.poll() is None:
            proc.terminate()
    for _, proc in children:
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)

sys.exit(bool(failures))
