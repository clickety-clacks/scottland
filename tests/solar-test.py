#!/usr/bin/env python3
"""Solar policy and command-boundary checks; no desktop, network, or install."""
from datetime import datetime, timezone
from importlib.machinery import SourceFileLoader
import atexit
import json
import os
from pathlib import Path
import shutil
from subprocess import CompletedProcess
import tempfile
from unittest.mock import patch

root = Path(__file__).resolve().parents[1]
build = root/"build"
build.mkdir(parents=True, exist_ok=True)
scratch = Path(tempfile.mkdtemp(prefix="solar-policy-", dir=build))
atexit.register(shutil.rmtree, scratch, ignore_errors=True)
os.environ.update(HOME=str(scratch), XDG_CONFIG_HOME=str(scratch/"config"), XDG_STATE_HOME=str(scratch/"state"),
                  WAYLAND_DISPLAY="wayland-policy-test",
                  SCOTTLAND_SOLAR_MODE_FILE=str(scratch/"state/scottland/policy.solar-mode"),
                  SCOTTLAND_SOLAR_TRANSITION_FILE=str(scratch/"state/scottland/solar-transition.json"),
                  SCOTTLAND_SOLAR_FILE=str(scratch/"config/scottland/solar.ini"))
core = SourceFileLoader("scottland_solar_policy", str(root/"core/libexec/scottland-solar-theme")).load_module()
adapter = SourceFileLoader("scottland_solar_adapter_policy", str(root/"omarchy/libexec/scottland-omarchy-solar-theme")).load_module()
passed = 0


def check(name, okay):
    global passed
    assert okay, name
    passed += 1
    print("PASS", name)


check("San Francisco summer day and night follow the sun",
      core.solar_mode(datetime(2026, 6, 21, 20, tzinfo=timezone.utc), 37.77, -122.42) == "light"
      and core.solar_mode(datetime(2026, 6, 21, 9, tzinfo=timezone.utc), 37.77, -122.42) == "dark")
check("winter daylight and polar seasons follow solar altitude",
      core.solar_mode(datetime(2026, 12, 21, 20, tzinfo=timezone.utc), 37.77, -122.42) == "light"
      and core.solar_mode(datetime(2026, 6, 21, 12, tzinfo=timezone.utc), 80, 0) == "light"
      and core.solar_mode(datetime(2026, 12, 21, 12, tzinfo=timezone.utc), 80, 0) == "dark")
check("invalid coordinates are rejected", core.coordinates(91, 0) is None and core.coordinates(0, float("nan")) is None)
day = datetime(2026, 6, 21, 20, tzinfo=timezone.utc)
event = ("light", "2026-06-21T12:47:34Z", datetime(2026, 6, 21, 12, 47, 34, tzinfo=timezone.utc))
with patch.object(core.subprocess, "run", side_effect=[CompletedProcess([], 1, ""), CompletedProcess([], 0, "'default'\n")]):
    check("default color preference maps to light", core.current_mode() == "light")
with patch.object(core.subprocess, "run", side_effect=[CompletedProcess([], 1, ""), CompletedProcess([], 0, "'prefer-dark'\n")]):
    check("dark color preference is recognized", core.current_mode() == "dark")

core.CONFIG.parent.mkdir(parents=True, exist_ok=True)
core.CONFIG.write_text("[solar]\nenabled = false\nallow_ip = false\n")
with patch.object(core, "geoclue_location", side_effect=AssertionError("disabled called Geoclue")), \
     patch.object(core, "ip_location", side_effect=AssertionError("disabled used network")), \
     patch.object(core, "set_mode") as set_mode:
    disabled_result = core.run_once(day)
check("disabled Sunlight does not locate or change the preference",
      disabled_result is None and set_mode.call_count == 0)

core.CONFIG.write_text("[solar]\nenabled = true\nlocation_set = true\nlatitude = 37.77\nlongitude = -122.42\nallow_ip = false\n")
with patch.object(core, "geoclue_location", return_value=None), \
     patch.object(core, "ip_location", side_effect=AssertionError("network without consent")), \
     patch.object(core, "solar_transition", return_value=event), \
     patch.object(core, "current_mode", return_value="light"), \
     patch.object(core.subprocess, "run", side_effect=AssertionError("matching preference changed")):
    check("saved coordinates work without network and publish transition mode", core.run_once(day) == "light"
          and core.MODE.read_text().strip() == "light")
core.CONFIG.write_text("[solar]\nenabled = true\nlocation_set = false\nallow_ip = true\n")
with patch.object(core, "geoclue_location", return_value=None), \
     patch.object(core, "ip_location", return_value=(37.77, -122.42)) as network, \
     patch.object(core, "solar_transition", return_value=event):
    check("network location is used only after explicit opt-in", core.run_once(day) == "light"
          and network.call_count == 1)
with patch.object(core, "geoclue_location", return_value=(37.77, -122.42)), \
     patch.object(core, "ip_location", side_effect=AssertionError("Geoclue result ignored")), \
     patch.object(core, "solar_transition", return_value=event), \
     patch.object(core, "current_mode", return_value="light"):
    geoclue_result = core.run_once(day)
check("Geoclue location produces the solar mode without an IP lookup",
      geoclue_result == "light")

# Adapter action consumes the event, not the current theme's reported mode.
core.CONFIG.write_text("[solar]\nenabled = true\nallow_ip = false\n")
adapter.config_file.parent.mkdir(parents=True, exist_ok=True)
adapter.config_file.write_text("[themes]\nday = Test Day\nnight = Test Night\n")
record = core.read_transition()
record.update(mode="dark", transition_id="policy-adapter-event", consumer_ack=None)
core.write_transition(record)
with patch.object(adapter.subprocess, "run") as run:
    run.return_value = CompletedProcess([], 0, "")
    check("adapter applies selected theme for an unacknowledged event",
          adapter.run_once() == "Test Night" and run.call_args.args[0] == ["omarchy", "theme", "set", "Test Night"])
with patch.object(adapter.subprocess, "run", side_effect=AssertionError("acknowledged event replayed")):
    check("acknowledged adapter event does not run again", adapter.run_once() == "unchanged")

adapter.config_file.write_text("[themes]\nday = Custom Day\n")
check("omitted custom night theme retains the shipped default",
      adapter.themes() == {"light": "Custom Day", "dark": "watercolor-dream-dark"})

print(f"{passed} solar policy checks passed")
