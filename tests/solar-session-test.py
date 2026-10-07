#!/usr/bin/env python3
"""Exercise Sunlight transitions against GSettings and Omarchy in a private session."""
from datetime import datetime, timezone
from importlib.machinery import SourceFileLoader
import os
from pathlib import Path
import shutil
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
hooks = Path(os.environ.get("SCOTTLAND_HOOKS", "")).resolve()
if not os.environ.get("WAYFIRE_SOCKET") or hooks != (root / "build/hooks").resolve():
    raise SystemExit("run via tests/solar-session-test.sh inside its private Omarchy headless session")

config_home = Path(os.environ["XDG_CONFIG_HOME"])
state_home = Path(os.environ["XDG_STATE_HOME"])
art = state_home / "scottland" / f"solar-session-{os.getpid()}"
config_file = art / "solar.ini"
mode_file = art / "wayland.solar-mode"
transition_file = art / "solar-transition.json"
adapter_config = config_home / "scottland/omarchy-solar.ini"
themes_dir = config_home / "omarchy/themes"
manual_theme_dir = themes_dir / "solar-test-manual"
setting = ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"]
setting_key = ["org.gnome.desktop.interface", "color-scheme"]
original_scheme = subprocess.check_output(setting, text=True).strip()

art.mkdir(parents=True, exist_ok=False)
os.environ["SCOTTLAND_SOLAR_FILE"] = str(config_file)
os.environ["SCOTTLAND_SOLAR_MODE_FILE"] = str(mode_file)
os.environ["SCOTTLAND_SOLAR_TRANSITION_FILE"] = str(transition_file)
os.environ["SCOTTLAND_OMARCHY_SOLAR_FILE"] = str(adapter_config)

core = SourceFileLoader("scottland_solar_session", str(root / "core/libexec/scottland-solar-theme")).load_module()
adapter = SourceFileLoader("scottland_omarchy_solar_session", str(root / "omarchy/libexec/scottland-omarchy-solar-theme")).load_module()
passed = 0


def check(name, okay):
    global passed
    if not okay:
        raise AssertionError(name)
    passed += 1
    print("PASS", name, flush=True)


def active_theme():
    return (state_home / "omarchy/current").resolve(strict=True).name


def set_theme(name):
    subprocess.run(["omarchy", "theme", "set", name], check=True, capture_output=True, text=True)
    check(f"Omarchy selected {name}", active_theme() == name)


try:
    setup = root / "omarchy/bin/scottland-omarchy-setup"
    subprocess.run([str(setup)], check=True, capture_output=True, text=True)
    adapter_config.parent.mkdir(parents=True, exist_ok=True)
    adapter_config.write_text("[themes]\nday = watercolor-dream-light\nnight = watercolor-dream-dark\n")
    shutil.copytree(themes_dir / "watercolor-dream-light", manual_theme_dir)
    core.CONFIG.parent.mkdir(parents=True, exist_ok=True)
    core.CONFIG.write_text("[solar]\nenabled = true\nlocation_set = true\nlatitude = 37.77\nlongitude = -122.42\nallow_ip = false\n")

    location = (37.77, -122.42)
    daytime = datetime(2026, 6, 21, 20, tzinfo=timezone.utc)
    core.run_once(daytime, location_override=location)
    first = core.read_transition()
    check("first enabled period applies and records light", first["enabled"] and first["mode"] == "light" and mode_file.read_text().strip() == "light")
    check("first event identity is persisted", first["transition_id"] is not None)

    check("adapter selects configured daytime theme", adapter.run_once() == "watercolor-dream-light" and active_theme() == "watercolor-dream-light")
    set_theme("solar-test-manual")
    subprocess.run(["gsettings", "set", *setting_key, "'prefer-dark'"], check=True)
    core.run_once(daytime.replace(minute=10), location_override=location)
    check("manual light/dark selection survives another check", core.current_mode() == "dark")
    check("manual Omarchy theme survives the same transition", adapter.run_once() == "unchanged" and active_theme() == "solar-test-manual")

    config_before = transition_file.read_text()
    core.CONFIG.write_text("[solar]\nenabled = true\nlocation_set = true\nlatitude = 40.71\nlongitude = -74.00\nallow_ip = false\n")
    core.run_once(daytime.replace(minute=20), location_override=(40.71, -74.00))
    check("saving a new location does not apply a theme", transition_file.read_text() == config_before and core.current_mode() == "dark" and active_theme() == "solar-test-manual")
    core.CONFIG.write_text("[solar]\nenabled = true\nlocation_set = true\nlatitude = 37.77\nlongitude = -122.42\nallow_ip = false\n")

    sunset = datetime(2026, 6, 22, 5, tzinfo=timezone.utc)
    core.run_once(sunset, location_override=location)
    night = core.read_transition()
    check("the next solar period records its dark transition", night["mode"] == "dark" and night["transition_id"] != first["transition_id"])
    check("next transition restores configured nighttime theme", adapter.run_once() == "watercolor-dream-dark" and active_theme() == "watercolor-dream-dark")
    subprocess.run(["gsettings", "set", *setting_key, "'prefer-dark'"], check=True)

    sunrise = datetime(2026, 6, 22, 14, tzinfo=timezone.utc)
    core.run_once(sunrise, location_override=location)
    morning = core.read_transition()
    check("a later period change applies light after a manual dark pick", morning["mode"] == "light" and morning["transition_id"] != night["transition_id"] and core.current_mode() == "light")
    set_theme("solar-test-manual")
    check("adapter restores configured daytime theme at sunrise", adapter.run_once() == "watercolor-dream-light" and active_theme() == "watercolor-dream-light")
    print(f"{passed} Sunlight session checks passed")
finally:
    restore = subprocess.run(["gsettings", "set", *setting_key, original_scheme], capture_output=True, text=True)
    shutil.rmtree(manual_theme_dir, ignore_errors=True)
    shutil.rmtree(art, ignore_errors=True)
    if restore.returncode:
        print(f"RESTORE FAILED for color-scheme: {restore.stderr.strip()}", file=sys.stderr)
        sys.exit(2)
