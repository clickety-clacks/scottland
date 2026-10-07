#!/usr/bin/env python3
"""Core sunrise/mode policy and Omarchy theme choice, isolated in build/."""
from datetime import datetime, timezone
from importlib.machinery import SourceFileLoader
import json
import os
from pathlib import Path
from subprocess import CompletedProcess
import subprocess
from unittest.mock import patch

root = Path(__file__).resolve().parents[1]
art = root/"build"/f"solar-test-evidence-{os.getpid()}"
art.mkdir(parents=True, exist_ok=True)
os.environ["XDG_CONFIG_HOME"] = str(art/"config")
os.environ["XDG_STATE_HOME"] = str(art/"state")
os.environ["WAYLAND_DISPLAY"] = "wayland-solar-test"
os.environ["SCOTTLAND_SOLAR_MODE_FILE"] = str(art/"state/scottland/wayland-solar-test.solar-mode")
os.environ["SCOTTLAND_SOLAR_TRANSITION_FILE"] = str(art/"state/scottland/solar-transition.json")
os.environ["SCOTTLAND_OMARCHY_CURRENT"] = str(art/"state/omarchy/current")
core = SourceFileLoader("scottland_solar", str(root/"core/libexec/scottland-solar-theme")).load_module()
adapter = SourceFileLoader("scottland_omarchy_solar", str(root/"omarchy/libexec/scottland-omarchy-solar-theme")).load_module()
adapter.config_file.unlink(missing_ok=True)
passed = 0

def check(name, okay):
    global passed
    assert okay, name
    passed += 1
    print("PASS", name)

check("San Francisco daytime is light", core.solar_mode(datetime(2026, 6, 21, 20, tzinfo=timezone.utc),37.77,-122.42)=="light")
check("San Francisco nighttime is dark", core.solar_mode(datetime(2026, 6, 21, 9, tzinfo=timezone.utc),37.77,-122.42)=="dark")
check("winter noon is light", core.solar_mode(datetime(2026, 12, 21, 20, tzinfo=timezone.utc),37.77,-122.42)=="light")
check("polar summer and winter follow sun", core.solar_mode(datetime(2026, 6, 21, 12, tzinfo=timezone.utc),80,0)=="light" and core.solar_mode(datetime(2026, 12, 21, 12, tzinfo=timezone.utc),80,0)=="dark")
check("invalid coordinates rejected", core.coordinates(91,0) is None and core.coordinates(0,float("nan")) is None)
day_event = core.solar_transition(datetime(2026, 6, 21, 20, tzinfo=timezone.utc),37.77,-122.42)
night_event = core.solar_transition(datetime(2026, 6, 21, 9, tzinfo=timezone.utc),37.77,-122.42)
check("solar transition identity is stable within a period", day_event == core.solar_transition(datetime(2026, 6, 21, 20, 10, tzinfo=timezone.utc),37.77,-122.42))
check("sunrise and sunset produce distinct period identities", day_event[0] == "light" and night_event[0] == "dark" and day_event[1] != night_event[1])
with patch.object(core.subprocess,"run",side_effect=[CompletedProcess([],1,""),CompletedProcess([],0,"'default'\n")]):
    check("default color preference means light",core.current_mode()=="light")
with patch.object(core.subprocess,"run",side_effect=[CompletedProcess([],1,""),CompletedProcess([],0,"'prefer-dark'\n")]):
    check("dark color preference is recognized",core.current_mode()=="dark")
core.CONFIG.parent.mkdir(parents=True, exist_ok=True)
core.CONFIG.write_text("[solar]\nenabled = false\nallow_ip = false\n")
with patch.object(core,"geoclue_location",side_effect=AssertionError("disabled called Geoclue")), patch.object(core,"ip_location",side_effect=AssertionError("disabled used network")):
    check("disabled schedule does not locate", core.run_once(datetime(2026,6,21,20,tzinfo=timezone.utc)) is None)
core.CONFIG.write_text("[solar]\nenabled = true\nlocation_set = true\nlatitude = 37.77\nlongitude = -122.42\nallow_ip = false\n")
day = datetime(2026,6,21,20,tzinfo=timezone.utc)
event1 = "2026-06-21T12:47Z"
with patch.object(core,"geoclue_location",return_value=None), patch.object(core,"ip_location",side_effect=AssertionError("network without consent")), patch.object(core,"solar_transition",return_value=("light",event1)), patch.object(core,"current_mode",return_value="light"), patch.object(core.subprocess,"run",side_effect=AssertionError("matching mode changed")):
    core.run_once(day)
check("first enabled run publishes and records its period",core.MODE.read_text().strip()=="light" and core.read_transition()["transition_id"]==event1)
record_before_manual_pick=core.TRANSITION.read_text()
with patch.object(core,"geoclue_location",return_value=None), patch.object(core,"solar_transition",return_value=("light",event1)), patch.object(core,"current_mode",return_value="dark"), patch.object(core.subprocess,"run",side_effect=AssertionError("manual pick was overwritten before a transition")):
    core.run_once(day.replace(hour=20,minute=10))
check("manual light/dark pick survives repeated checks",core.TRANSITION.read_text()==record_before_manual_pick)
core.CONFIG.write_text("[solar]\nenabled = true\nlocation_set = true\nlatitude = 40.71\nlongitude = -74.00\nallow_ip = false\n")
with patch.object(core,"solar_transition",return_value=("light","saved-location-event")), patch.object(core,"current_mode",side_effect=AssertionError("Settings Save read or changed the theme")), patch.object(core.subprocess,"run",side_effect=AssertionError("Settings Save reapplied the mode")):
    core.run_once(day.replace(hour=20,minute=20), location_override=(40.71,-74.00))
check("Settings Save changing the saved location leaves the transition record untouched",core.TRANSITION.read_text()==record_before_manual_pick)
core.CONFIG.write_text("[solar]\nenabled = true\nlocation_set = true\nlatitude = 37.77\nlongitude = -122.42\nallow_ip = false\n")
event2="2026-06-21T19:38Z"
with patch.object(core,"geoclue_location",return_value=None), patch.object(core,"solar_transition",return_value=("dark",event2)), patch.object(core,"current_mode",return_value="light") as current, patch.object(core.subprocess,"run") as run:
    run.return_value=CompletedProcess([],0,"","")
    core.run_once(day.replace(hour=19,minute=40))
    check("the next transition applies dark once",run.call_count==1 and run.call_args.args[0][-1]=="prefer-dark")
with patch.object(core,"geoclue_location",return_value=None), patch.object(core,"solar_transition",return_value=("dark",event2)), patch.object(core,"current_mode",return_value="light"), patch.object(core.subprocess,"run",side_effect=AssertionError("same transition reapplied")):
    core.run_once(day.replace(hour=19,minute=41))
check("a repeated check does not reapply the same transition",core.read_transition()["transition_id"]==event2)
event_after_sleep="2026-06-23T19:38Z"
with patch.object(core,"geoclue_location",return_value=None), patch.object(core,"solar_transition",return_value=("dark",event_after_sleep)), patch.object(core,"current_mode",return_value="light"), patch.object(core.subprocess,"run") as run:
    run.return_value=CompletedProcess([],0,"","")
    core.run_once(datetime(2026,6,23,20,tzinfo=timezone.utc))
    check("a missed pair of transitions applies the current period once",run.call_count==1 and run.call_args.args[0][-1]=="prefer-dark")
with patch.object(core,"geoclue_location",side_effect=AssertionError("disabled called Geoclue")), patch.object(core,"ip_location",side_effect=AssertionError("disabled used network")):
    core.CONFIG.write_text("[solar]\nenabled = false\nallow_ip = false\n")
    core.run_once(datetime(2026,6,23,20,tzinfo=timezone.utc))
check("disabling Sunlight clears its published mode",not core.MODE.exists() and not core.read_transition()["enabled"])
core.CONFIG.write_text("[solar]\nenabled = true\nlocation_set = true\nlatitude = 37.77\nlongitude = -122.42\nallow_ip = false\n")
with patch.object(core,"geoclue_location",return_value=None), patch.object(core,"solar_transition",return_value=("dark",event_after_sleep)), patch.object(core,"current_mode",return_value="light"), patch.object(core.subprocess,"run") as run:
    run.return_value=CompletedProcess([],0,"","")
    core.run_once(datetime(2026,6,23,20,10,tzinfo=timezone.utc))
    check("re-enabling Sunlight applies the current period",run.call_count==1 and core.read_transition()["enabled"])
core.CONFIG.write_text("[solar]\nenabled = true\nlocation_set = false\nallow_ip = true\n")
with patch.object(core,"geoclue_location",return_value=None), patch.object(core,"ip_location",return_value=(37.77,-122.42)) as network, patch.object(core,"solar_transition",return_value=("dark",event_after_sleep)):
    core.run_once(datetime(2026,6,23,20,20,tzinfo=timezone.utc))
    check("network lookup occurs after explicit opt-in",network.call_count==1)
with patch.object(core,"geoclue_location",return_value=(37.77,-122.42)), patch.object(core,"ip_location",side_effect=AssertionError("Geoclue available")), patch.object(core,"solar_transition",return_value=("dark",event_after_sleep)):
    core.run_once(datetime(2026,6,23,20,30,tzinfo=timezone.utc))  # IP lookup must not run when Geoclue answers.
core.MODE.write_text("light\n")
record=core.read_transition()
record.update(mode="light",transition_id="adapter-event-light",consumer_ack=None)
core.write_transition(record)
with patch.object(adapter.subprocess,"run") as run:
    run.return_value=CompletedProcess([],0,"","")
    check("a new light transition selects the configured light theme",adapter.run_once()=="watercolor-dream-light")
    check("adapter applies the configured theme on a transition",run.call_args.args[0]==["omarchy","theme","set","watercolor-dream-light"])
with patch.object(adapter.subprocess,"run",side_effect=AssertionError("manual theme was overwritten between transitions")):
    check("manual Omarchy choice is left alone until a new transition",adapter.run_once()=="unchanged")
record=core.read_transition()
record.update(mode="dark",transition_id="adapter-event-dark",consumer_ack=None)
core.write_transition(record)
core.MODE.write_text("dark\n")
with patch.object(adapter.subprocess,"run") as run:
    run.return_value=CompletedProcess([],0,"","")
    check("the next transition selects the configured dark theme",adapter.run_once()=="watercolor-dream-dark")
    check("the adapter consumes that transition only once",core.read_transition()["consumer_ack"]=="adapter-event-dark")
check("Sunlight defaults to the shipped light/dark pair",adapter.themes()=={
    "light":"watercolor-dream-light","dark":"watercolor-dream-dark"})
adapter.config_file.parent.mkdir(parents=True,exist_ok=True)
adapter.config_file.write_text("[themes]\nday = CustomDay\nnight = CustomNight\n")
check("adapter reads configured themes",adapter.themes()=={"light":"CustomDay","dark":"CustomNight"})
adapter.config_file.write_text("[themes]\nday = CustomDay\n")
check("a partial user theme choice preserves the shipped night default",
      adapter.themes()=={"light":"CustomDay","dark":"watercolor-dream-dark"})

current=Path(os.environ["SCOTTLAND_OMARCHY_CURRENT"])
colors_file=current/"theme/colors.toml"
colors_file.parent.mkdir(parents=True,exist_ok=True)
palette_provider=root/"omarchy/accent.d/10-omarchy-theme"
colors_file.write_text('yellow = "#aabbcc"\nattention = "#123456"\n')
palette=json.loads(subprocess.check_output([str(palette_provider),"--palette"],text=True,env=os.environ))
check("Omarchy attention key overrides the yellow fallback",palette["attention"]=="#123456")
colors_file.write_text('yellow = "#aabbcc"\n')
palette=json.loads(subprocess.check_output([str(palette_provider),"--palette"],text=True,env=os.environ))
check("Omarchy attention falls back to theme yellow",palette["attention"]=="#aabbcc")

setup_config=art/f"setup-config-{os.getpid()}"
setup_home=art/f"setup-home-{os.getpid()}"
setup_config.mkdir(parents=True,exist_ok=True)
setup_home.mkdir(parents=True,exist_ok=True)
setup_env=dict(os.environ,XDG_CONFIG_HOME=str(setup_config),HOME=str(setup_home))
setup=root/"omarchy/bin/scottland-omarchy-setup"
subprocess.run([str(setup)],check=True,env=setup_env,capture_output=True,text=True)
installed=setup_config/"omarchy/themes"
check("setup installs both shipped watercolor themes",
      all((installed/name/"colors.toml").is_file() for name in
          ("watercolor-dream-light","watercolor-dream-dark")))
check("setup preserves Aether-managed theme markers",
      all((installed/name/".aether-managed").is_file() for name in
          ("watercolor-dream-light","watercolor-dream-dark")))
check("setup installs Omarchy-compatible WebP backgrounds and previews",
      all((installed/name/"preview.webp").is_symlink() and
          (installed/name/"preview.webp").is_file() and
          list((installed/name/"backgrounds").glob("*.webp")) for name in
          ("watercolor-dream-light","watercolor-dream-dark")))
user_theme=installed/"watercolor-dream-light"
sentinel=user_theme/"user-choice.txt"
sentinel.write_text("keep this theme\n")
(user_theme/"colors.toml").write_text("# user's chosen colors\n")
(user_theme/".aether-managed").write_text("user marker\n")
subprocess.run([str(setup)],check=True,env=setup_env,capture_output=True,text=True)
check("setup leaves a user's same-named theme untouched",
      sentinel.read_text()=="keep this theme\n" and
      (user_theme/"colors.toml").read_text()=="# user's chosen colors\n" and
      (user_theme/".aether-managed").read_text()=="user marker\n")
print(f"{passed} solar checks passed")
