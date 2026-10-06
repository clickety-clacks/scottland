#!/usr/bin/env python3
"""Core display control for integrations (E14): scottland-output list/set/reset.

Isolated headless session of core alone (no integration's hooks) with two virtual outputs. Each
change is made once with scottland-output, the way an integration calls it, and judged by what the
code under test does not report about itself: Wayfire's own output list (stock ipc-rules: which
outputs are on and their layout geometry, where scale and transform show as logical size) and a
screencopy of the output (grim -o: its pixel size, or no frame while it is off).

  tests/output-control-test.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from omarchy_fixture import REPO, Checks, Fixture, Session, read_ppm  # noqa: E402

check = Checks()
fixture = Fixture(REPO / "build/output-control-fixture")
tool = str(REPO / "build/hooks/libexec/scottland-output")

with Session(fixture, "hl-output-control", omarchy=False, env={"SCOTTLAND_TEST_OUTPUTS": "2"}) as session:
    def outputs():
        reply = session.ipc("window-rules/list-outputs")
        return {o["name"]: {k: int(v) for k, v in o["geometry"].items()} for o in reply} \
            if isinstance(reply, list) else {}

    def capture(output, name):
        path = session.dir.parent / f"{session.dir.name}-{name}.ppm"
        path.unlink(missing_ok=True)
        result = session.run("timeout", "5", "grim", "-t", "ppm", "-o", output, str(path))
        return read_ppm(path)[:2] if result.returncode == 0 and path.exists() else None

    def output(*args):
        result = session.run(tool, *args, timeout=60)
        try:
            reported = json.loads(result.stdout) if result.stdout.strip() else None
        except ValueError:
            reported = result.stdout
        return result.returncode, reported, result.stderr.strip()

    listed = output("list")[1] or []
    geometry = outputs()
    check("list reports every output with its mode, position and scale, as Wayfire lays them out",
          sorted(h["name"] for h in listed) == sorted(geometry) and all(
              h["enabled"] and (h["mode"]["width"], h["mode"]["height"]) == (1280, 720) and h["scale"] == 1
              and (h["x"], h["y"]) == (geometry[h["name"]]["x"], geometry[h["name"]]["y"]) for h in listed),
          (listed, geometry))

    status, reported, error = output("set", "HEADLESS-2", "--scale", "2", "--position", "1280,0")
    check("scale and position: applied when the command returns",
          status == 0 and outputs().get("HEADLESS-2") == {"x": 1280, "y": 0, "width": 640, "height": 360},
          (status, error, outputs()))
    check("the output still renders its full mode (screencopy 1280x720)", capture("HEADLESS-2", "scale2") == (1280, 720))

    status, reported, error = output("set", "HEADLESS-2", "--transform", "90")
    check("transform 90: the output is laid out on its side",
          status == 0 and outputs().get("HEADLESS-2") == {"x": 1280, "y": 0, "width": 360, "height": 640},
          (status, error, outputs()))
    output("set", "HEADLESS-2", "--transform", "normal")

    status, reported, error = output("set", "HEADLESS-2", "--mode", "1024x768")
    check("mode 1024x768: applied (layout 512x384 at scale 2, screencopy 1024x768)",
          status == 0 and outputs().get("HEADLESS-2", {}).get("width") == 512
          and capture("HEADLESS-2", "mode") == (1024, 768), (status, error, outputs()))

    status, reported, error = output("set", "HEADLESS-2", "--off")
    check("off: the output is gone from the layout and renders nothing",
          status == 0 and "HEADLESS-2" not in outputs() and capture("HEADLESS-2", "off") is None,
          (status, error, outputs()))
    check("list still reports it, as disabled", any(
        h["name"] == "HEADLESS-2" and h["enabled"] is False for h in output("list")[1] or []))
    def reloads():
        return (session.ipc("scottland/session-state") or {}).get("config-reloads", -1)
    before = reloads()
    rebuild = session.run(str(REPO / "build/hooks/libexec/scottland-build-config"))
    ok, _ = session.wait(lambda: reloads() > before, timeout=10)  # Wayfire loaded the rebuild
    check("a config rebuild keeps it off", rebuild.returncode == 0 and ok and "HEADLESS-2" not in outputs(),
          (rebuild.stderr, outputs()))

    status, reported, error = output("set", "HEADLESS-2", "--on")
    check("on again: back with the mode, position and scale it had",
          status == 0 and outputs().get("HEADLESS-2") == {"x": 1280, "y": 0, "width": 512, "height": 384}
          and capture("HEADLESS-2", "on") == (1024, 768), (status, error, outputs()))

    # The user's config keeps it off: "on" can't override that, and says so.
    (session.dir / "config/scottland/overrides.ini").write_text("[output:HEADLESS-2]\nmode = off\n")
    output("reset")
    ok, _ = session.wait(lambda: "HEADLESS-2" not in outputs(), timeout=10)
    check("setup: overrides.ini turns HEADLESS-2 off", ok, outputs())
    status, reported, error = output("set", "HEADLESS-2", "--on")
    check("on when the config keeps it off: exit 1, saying why, and it stays off",
          status == 1 and "keeps it off" in error and "HEADLESS-2" not in outputs(), (status, error))
    (session.dir / "config/scottland/overrides.ini").unlink()

    # A virtual output has no preferred mode: Wayfire's default (auto) keeps its current size,
    # 1024x768 here. Back at scale 1 (layout as wide as the mode) shows the settings dropped.
    status, reported, error = output("reset")
    ok, _ = session.wait(lambda: outputs().get("HEADLESS-2", {}).get("width") == 1024, timeout=10)
    check("reset: the session's settings are dropped; the configured default scale 1 again",
          status == 0 and ok and capture("HEADLESS-2", "reset") == (1024, 768), (status, error, outputs()))

    status, _, error = output("set", "HEADLESS-2", "--scale", "fast")
    check("an invalid value is refused before anything changes (exit 64)",
          status == 64 and outputs().get("HEADLESS-2", {}).get("width") == 1024, (status, error))

sys.exit(check.summary())
