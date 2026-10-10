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
art = root/"build/solar-test-evidence"
art.mkdir(parents=True, exist_ok=True)
os.environ["XDG_CONFIG_HOME"] = str(art/"config")
os.environ["XDG_STATE_HOME"] = str(art/"state")
os.environ["WAYLAND_DISPLAY"] = "wayland-solar-test"
os.environ["SCOTTLAND_SOLAR_MODE_FILE"] = str(art/"state/scottland/wayland-solar-test.solar-mode")
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
with patch.object(core.subprocess,"run",side_effect=[CompletedProcess([],1,""),CompletedProcess([],0,"'default'\n")]):
    check("default color preference means light",core.current_mode()=="light")
with patch.object(core.subprocess,"run",side_effect=[CompletedProcess([],1,""),CompletedProcess([],0,"'prefer-dark'\n")]):
    check("dark color preference is recognized",core.current_mode()=="dark")
core.CONFIG.parent.mkdir(parents=True, exist_ok=True)
core.CONFIG.write_text("[solar]\nenabled = false\nallow_ip = false\n")
with patch.object(core,"geoclue_location",side_effect=AssertionError("disabled called Geoclue")), patch.object(core,"ip_location",side_effect=AssertionError("disabled used network")):
    check("disabled schedule does not locate", core.run_once() is None)
core.CONFIG.write_text("[solar]\nenabled = true\nlocation_set = true\nlatitude = 37.77\nlongitude = -122.42\nallow_ip = false\n")
with patch.object(core,"geoclue_location",return_value=None), patch.object(core,"ip_location",side_effect=AssertionError("network without consent")), patch.object(core,"solar_mode",return_value="light"), patch.object(core,"current_mode",return_value="light"), patch.object(core.subprocess,"run",side_effect=AssertionError("matching mode changed")):
    core.run_once()
check("matching mode retained and desired mode published",core.MODE.read_text().strip() in ("light","dark"))
core.CONFIG.write_text("[solar]\nenabled = true\nlocation_set = false\nallow_ip = true\n")
with patch.object(core,"geoclue_location",return_value=None), patch.object(core,"ip_location",return_value=(37.77,-122.42)) as network, patch.object(core,"apply"):
    core.run_once()
    check("network lookup occurs after explicit opt-in",network.call_count==1)
with patch.object(core,"geoclue_location",return_value=(37.77,-122.42)), patch.object(core,"ip_location",side_effect=AssertionError("Geoclue available")), patch.object(core,"apply"):
    core.run_once()  # the IP lookup raises if Geoclue's location is not preferred
core.publish("light", core.read_config()["config_mtime_ns"])
with patch.object(adapter.subprocess,"run") as run:
    run.return_value.returncode=0; run.return_value.stdout="light\n"
    check("matching Omarchy theme is preserved",adapter.run_once()=="kept")
    check("mode check uses requested colors file",run.call_args.args[0][:3]==["omarchy-theme-color","--file",str(adapter.current/"theme/colors.toml")])
with patch.object(adapter.subprocess,"run") as run:
    run.side_effect=[type("R",(),dict(returncode=0,stdout="dark\n"))(),type("R",(),dict(returncode=0,stdout=""))()]
    check("wrong mode selects default day theme",adapter.run_once()=="watercolor-dream-light")
    check("adapter calls omarchy theme set",run.call_args.args[0]==["omarchy","theme","set","watercolor-dream-light"])
check("Sunlight defaults to the shipped light/dark pair",adapter.themes()=={
    "light":"watercolor-dream-light","dark":"watercolor-dream-dark"})
adapter.config_file.parent.mkdir(parents=True,exist_ok=True)
adapter.config_file.write_text("[themes]\nday = CustomDay\nnight = CustomNight\n")
check("adapter reads configured themes",adapter.themes()=={"light":"CustomDay","dark":"CustomNight"})
adapter.config_file.write_text("[themes]\nday = CustomDay\n")
check("a partial user theme choice preserves the shipped night default",
      adapter.themes()=={"light":"CustomDay","dark":"watercolor-dream-dark"})

colors_file=adapter.current/"theme/colors.toml"
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
