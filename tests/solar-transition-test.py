#!/usr/bin/env python3
"""Core Sunlight transition policy in a private XDG state tree."""
from datetime import datetime, timezone
from importlib.machinery import SourceFileLoader
import os
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import patch

root = Path(__file__).resolve().parents[1]
art = root / "build" / f"solar-transition-test-{os.getpid()}"
art.mkdir(parents=True, exist_ok=False)
home = art / "home"
config_home = art / "config"
state_home = art / "state"
home.mkdir()
config_home.mkdir()
state_home.mkdir()
os.environ.update({
    "HOME": str(home),
    "XDG_CONFIG_HOME": str(config_home),
    "XDG_STATE_HOME": str(state_home),
    "WAYLAND_DISPLAY": "wayland-solar-transition-test",
    "SCOTTLAND_SOLAR_FILE": str(config_home / "scottland/solar.ini"),
    "SCOTTLAND_SOLAR_MODE_FILE": str(state_home / "scottland/wayland.solar-mode"),
    "SCOTTLAND_SOLAR_TRANSITION_FILE": str(state_home / "scottland/solar-transition.json"),
})
core = SourceFileLoader("scottland_solar_transition_test", str(root / "core/libexec/scottland-solar-theme")).load_module()
passed = 0


def check(name, okay):
    global passed
    if not okay:
        raise AssertionError(name)
    passed += 1
    print("PASS", name, flush=True)


def write_config(enabled=True, latitude=37.77, longitude=-122.42):
    core.CONFIG.parent.mkdir(parents=True, exist_ok=True)
    core.CONFIG.write_text(
        "[solar]\n"
        f"enabled = {'true' if enabled else 'false'}\n"
        f"location_set = {'true' if enabled else 'false'}\n"
        f"latitude = {latitude}\n"
        f"longitude = {longitude}\n"
        "allow_ip = false\n"
    )


def run(event, now, location=(37.77, -122.42), current="light", forbid_apply=False):
    with patch.object(core, "solar_transition", return_value=event), \
         patch.object(core, "current_mode", return_value=current), \
         patch.object(core.subprocess, "run", side_effect=AssertionError("unexpected preference write") if forbid_apply else None) as command:
        if not forbid_apply:
            command.return_value = subprocess.CompletedProcess([], 0, "", "")
        result = core.run_once(now, location_override=location)
    return result, command


try:
    location = (37.77, -122.42)
    day = ("light", "2026-06-21T12:00Z")
    night = ("dark", "2026-06-21T19:38Z")
    next_day = ("light", "2026-06-23T12:01Z")
    write_config()

    result, command = run(day, datetime(2026, 6, 21, 20, tzinfo=timezone.utc), current="dark")
    check("first enable applies the current period once", result == "light" and command.call_count == 1 and
          command.call_args.args[0] == ["gsettings", "set", "org.gnome.desktop.interface", "color-scheme", "prefer-light"])
    first_record = core.read_transition()
    check("first transition identity and last-applied state are persisted", first_record["enabled"] and
          first_record["transition_id"] == day[1] and first_record["last_applied"] ==
          {"mode": "light", "transition_id": day[1]})

    before_manual_pick = core.TRANSITION.read_text()
    result, command = run(day, datetime(2026, 6, 21, 20, 10, tzinfo=timezone.utc), current="dark", forbid_apply=True)
    check("manual dark choice survives same-period checks", result == "light" and command.call_count == 0 and
          core.TRANSITION.read_text() == before_manual_pick)

    restart_probe = art / "restart-probe.py"
    restart_probe.write_text(f'''\
from datetime import datetime, timezone
from importlib.machinery import SourceFileLoader
from unittest.mock import patch
core = SourceFileLoader("scottland_solar_restart_probe", {str(root / "core/libexec/scottland-solar-theme")!r}).load_module()
with patch.object(core, "solar_transition", return_value=("light", {day[1]!r})), \\
     patch.object(core, "current_mode", side_effect=AssertionError("restart inspected the manual preference")), \\
     patch.object(core.subprocess, "run", side_effect=AssertionError("restart reapplied the period")):
    core.run_once(datetime(2026, 6, 21, 20, 20, tzinfo=timezone.utc), location_override={location!r})
print("PASS fresh-process restart keeps the acknowledged period")
''')
    restart = subprocess.run([sys.executable, str(restart_probe)], env=os.environ.copy(),
                             check=True, capture_output=True, text=True)
    check("fresh-process restart preserves the manual choice", "PASS fresh-process restart" in restart.stdout)

    saved_state = core.TRANSITION.read_text()
    write_config(latitude=40.71, longitude=-74.00)
    result, command = run(day, datetime(2026, 6, 21, 20, 30, tzinfo=timezone.utc),
                          location=(40.71, -74.00), current="dark", forbid_apply=True)
    check("Settings Save changing location does not apply or replace the active transition",
          result == "light" and command.call_count == 0 and core.TRANSITION.read_text() == saved_state)

    write_config()
    result, command = run(night, datetime(2026, 6, 22, 5, tzinfo=timezone.utc), current="light")
    check("the next day/night boundary applies the new period once", result == "dark" and command.call_count == 1 and
          command.call_args.args[0][-1] == "prefer-dark" and core.read_transition()["transition_id"] == night[1])

    before_manual_light = core.TRANSITION.read_text()
    result, command = run(night, datetime(2026, 6, 22, 5, 10, tzinfo=timezone.utc),
                          current="light", forbid_apply=True)
    check("manual light choice survives repeated checks during night", result == "dark" and
          command.call_count == 0 and core.TRANSITION.read_text() == before_manual_light)

    result, command = run(next_day, datetime(2026, 6, 23, 20, tzinfo=timezone.utc), current="dark")
    check("wake after missed transitions applies only the current period once",
          result == "light" and command.call_count == 1 and
          core.read_transition()["transition_id"] == next_day[1])

    write_config(enabled=False)
    core.run_once(datetime(2026, 6, 23, 20, 10, tzinfo=timezone.utc))
    disabled = core.read_transition()
    check("disabling Sunlight records the disabled state", not disabled["enabled"] and
          disabled["consumer_ack"] == next_day[1])

    write_config()
    result, command = run(next_day, datetime(2026, 6, 23, 20, 20, tzinfo=timezone.utc), current="dark")
    check("re-enable applies the current period once", result == "light" and command.call_count == 1 and
          core.read_transition()["enabled"])

    day_identity = core.solar_transition(datetime(2026, 6, 21, 20, tzinfo=timezone.utc), *location)
    same_day_identity = core.solar_transition(datetime(2026, 6, 21, 20, 10, tzinfo=timezone.utc), *location)
    night_identity = core.solar_transition(datetime(2026, 6, 21, 9, tzinfo=timezone.utc), *location)
    check("solar identity stays stable within a period and changes at its boundary",
          day_identity == same_day_identity and day_identity[1] != night_identity[1])
    print(f"{passed} core Sunlight transition checks passed")
finally:
    shutil.rmtree(art, ignore_errors=True)
