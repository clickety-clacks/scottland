#!/usr/bin/env python3
"""Focused transition scheduler/state tests; no desktop, bus, or host required."""
from datetime import datetime, timedelta, timezone
from importlib.machinery import SourceFileLoader
import atexit
import configparser
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch

root = Path(__file__).resolve().parents[1]
build_dir = root/"build"
build_dir.mkdir(parents=True, exist_ok=True)
scratch = Path(tempfile.mkdtemp(prefix="solar-scheduler-", dir=build_dir))
atexit.register(shutil.rmtree, scratch, ignore_errors=True)
os.environ["XDG_CONFIG_HOME"] = str(scratch / "config")
os.environ["XDG_STATE_HOME"] = str(scratch / "state")
os.environ["WAYLAND_DISPLAY"] = "wayland-scheduler-test"
os.environ["SCOTTLAND_SOLAR_MODE_FILE"] = str(scratch / "state/scottland/test.solar-mode")
os.environ["SCOTTLAND_SOLAR_TRANSITION_FILE"] = str(scratch / "state/scottland/solar-transition.json")
os.environ["SCOTTLAND_SOLAR_FILE"] = str(scratch / "config/scottland/solar.ini")
scratch.mkdir(parents=True, exist_ok=True)
core = SourceFileLoader("scottland_solar_scheduler", str(root / "core/libexec/scottland-solar-theme")).load_module()
adapter = SourceFileLoader("scottland_solar_adapter_scheduler", str(root / "omarchy/libexec/scottland-omarchy-solar-theme")).load_module()
passed = 0


def check(name, condition):
    global passed
    assert condition, name
    passed += 1
    print("PASS", name)


def event_for(when, mode):
    return mode, when.strftime("%Y-%m-%dT%H:%MZ"), when


def solar_config(enabled=True, lat=37.77, lon=-122.42):
    core.CONFIG.parent.mkdir(parents=True, exist_ok=True)
    core.CONFIG.write_text(
        "[solar]\n"
        f"enabled = {'true' if enabled else 'false'}\n"
        "allow_ip = false\n"
        f"location_set = {'true' if lat is not None else 'false'}\n"
        f"latitude = {lat if lat is not None else 0}\n"
        f"longitude = {lon if lon is not None else 0}\n"
    )
    return core.read_config()


# Fixed instants make this independent of the machine clock. TZ must not affect
# the UTC solar boundary; in particular a local DST fold must not create one.
before_dawn = datetime(2026, 6, 21, 10, tzinfo=timezone.utc)
sunrise = core.next_solar_transition(before_dawn, 37.77, -122.42)
check("next event is an upcoming sunrise with an exact UTC identity",
      sunrise[0] == "light" and sunrise[2] > before_dawn and sunrise[2].hour in (12, 13)
      and sunrise[1] == sunrise[2].strftime("%Y-%m-%dT%H:%M:%SZ"))
before_sunset = datetime(2026, 6, 21, 18, tzinfo=timezone.utc)
sunset = core.next_solar_transition(before_sunset, 37.77, -122.42)
check("next event after sunrise is sunset", sunset[0] == "dark" and sunset[2] > before_sunset)
old_tz = os.environ.get("TZ")
try:
    os.environ["TZ"] = "America/Los_Angeles"
    time.tzset()
    la_event = core.next_solar_transition(before_dawn, 37.77, -122.42)
    os.environ["TZ"] = "Pacific/Kiritimati"
    time.tzset()
    kiribati_event = core.next_solar_transition(before_dawn, 37.77, -122.42)
finally:
    if old_tz is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = old_tz
    time.tzset()
check("DST and host timezone changes do not move the UTC transition", la_event == kiribati_event)

polar_now = datetime(2026, 6, 21, 12, tzinfo=timezone.utc)
polar_next = core.next_solar_transition(polar_now, 80, 0)
check("polar day still schedules the next real seasonal boundary", polar_next[2] > polar_now)

# First enable applies once. A manual choice and repeated config notifications
# in the same period never write the preference again.
config = solar_config()
event1 = event_for(datetime(2026, 6, 21, 12, 47, tzinfo=timezone.utc), "light")
with patch.object(core, "current_mode", return_value="dark"), patch.object(core, "set_mode") as set_mode:
    core.reconcile(config, (37.77, -122.42), datetime(2026, 6, 21, 13, tzinfo=timezone.utc), event1)
    check("first enable applies the current period once", set_mode.call_count == 1)
record_after_enable = core.TRANSITION.read_text()
old_fingerprint = core.read_config()["config_fingerprint"]

def save_settings():
    time.sleep(.03)
    core.CONFIG.write_text(core.CONFIG.read_text()+"# settings saved\n")

save_thread = threading.Thread(target=save_settings, daemon=True)
save_thread.start()
config_wake = core.wait_for_config_or_deadline(datetime.now(timezone.utc)+timedelta(seconds=10), old_fingerprint)
save_thread.join(timeout=1)
check("config notification wakes the existing scheduler without polling", config_wake == "config")
saved_config = core.read_config()
with patch.object(core, "current_mode", side_effect=AssertionError("same-period check inspected preference")), patch.object(core, "set_mode", side_effect=AssertionError("manual choice overwritten")):
    core.reconcile(saved_config, (37.77, -122.42), datetime(2026, 6, 21, 14, tzinfo=timezone.utc), event1)
check("manual light/dark choice survives checks and Settings Save", core.TRANSITION.read_text() == record_after_enable)

timer_start = time.monotonic()
timer_wake = core.wait_for_config_or_deadline(datetime.now(timezone.utc)+timedelta(seconds=.08),
                                               core.read_config()["config_fingerprint"])
check("absolute realtime timer wakes at its scheduled boundary", timer_wake == "deadline"
      and time.monotonic()-timer_start >= .04)

event2 = event_for(datetime(2026, 6, 21, 19, 38, tzinfo=timezone.utc), "dark")
with patch.object(core, "current_mode", return_value="light"), patch.object(core, "set_mode") as set_mode:
    core.reconcile(config, (37.77, -122.42), datetime(2026, 6, 21, 20, tzinfo=timezone.utc), event2)
    core.reconcile(config, (37.77, -122.42), datetime(2026, 6, 21, 20, 1, tzinfo=timezone.utc), event2)
    check("a new transition applies once and duplicate wake is a no-op", set_mode.call_count == 1)

# A fresh scheduler process after missed sleep/reboot observes the latest event
# identity and applies that current period once, not each missed boundary.
missed = event_for(datetime(2026, 6, 23, 19, 38, tzinfo=timezone.utc), "dark")
with patch.object(core, "current_mode", return_value="light"), patch.object(core, "set_mode") as set_mode:
    core.reconcile(config, (37.77, -122.42), datetime(2026, 6, 23, 20, tzinfo=timezone.utc), missed)
    check("reboot after missed transitions applies the current period once", set_mode.call_count == 1)
with patch.object(core, "set_mode", side_effect=AssertionError("same persisted period replayed")):
    core.reconcile(config, (37.77, -122.42), datetime(2026, 6, 23, 20, 5, tzinfo=timezone.utc), missed)
check("reboot in the same solar period does not replay the last applied event",
      core.read_transition()["last_applied"]["transition_id"] == missed[1])

core.disable()
with patch.object(core, "set_mode") as set_mode:
    core.reconcile(config, (37.77, -122.42), datetime(2026, 6, 23, 20, 10, tzinfo=timezone.utc), missed)
    check("re-enable applies the current period once", set_mode.call_count == 1
          and core.read_transition()["enabled"])

# Saving a new location re-bases future scheduling but does not treat the new
# calculation's already-past event as a sunrise/sunset.
new_config = solar_config(lat=40.71, lon=-74.0)
location_event = event_for(datetime(2026, 6, 23, 9, 25, tzinfo=timezone.utc), "light")
before_rebase = core.read_transition()["last_applied"]
before_event_id = core.read_transition()["transition_id"]
with patch.object(core, "set_mode", side_effect=AssertionError("location save caused apply")):
    core.reconcile(new_config, (40.71, -74.0), datetime(2026, 6, 23, 20, tzinfo=timezone.utc), location_event)
check("location/config save does not count as a solar transition",
      core.read_transition()["last_applied"] == before_rebase
      and core.read_transition()["transition_id"] == before_event_id
      and core.read_transition()["schedule_transition_id"] == location_event[1])
future_location_event = event_for(datetime(2026, 6, 23, 19, 40, tzinfo=timezone.utc), "dark")
with patch.object(core, "set_mode") as set_mode:
    core.reconcile(new_config, (40.71, -74.0), datetime(2026, 6, 23, 20, tzinfo=timezone.utc), future_location_event)
    check("new saved-location sunrise/sunset applies at its next boundary",
          set_mode.call_count == 1
          and core.read_transition()["last_applied"]["transition_id"] == future_location_event[1])

# Adapter consumes only a new unacknowledged transition. Restarting after ack
# must not rewrite a user's manual theme, even if its current mode differs.
solar_config()
omarchy_cfg = scratch / "config/scottland/omarchy-solar.ini"
omarchy_cfg.write_text("[themes]\nday = Test Light\nnight = Test Dark\n")
record = core.read_transition()
record.update(enabled=True, mode="dark", transition_id="adapter-reboot-event",
              consumer_delivery_id="adapter-reboot-delivery", consumer_ack=None)
core.write_transition(record)
with patch.object(adapter.subprocess, "run") as run:
    run.return_value.returncode = 0
    check("adapter applies a new transition even when the theme's mode already matches",
          adapter.run_once() == "Test Dark")
    check("adapter acknowledges only after theme set succeeds",
          core.read_transition()["consumer_ack"] == "adapter-reboot-delivery")
with patch.object(adapter.subprocess, "run", side_effect=AssertionError("fresh adapter replayed acknowledged transition")):
    check("fresh adapter process after ack preserves manual theme", adapter.run_once() == "unchanged")

# Exercise the real CLI in a fresh process against the persisted state. This is
# the reboot boundary: the executable must not replay an acknowledged event.
fixture_bin = scratch / "bin"
fixture_bin.mkdir(parents=True, exist_ok=True)
invocations = scratch / "omarchy-invocations"
fake_omarchy = fixture_bin / "omarchy"
fake_omarchy.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$SOLAR_TEST_INVOCATIONS\"\n")
fake_omarchy.chmod(0o755)
restart_env = dict(os.environ, PATH=str(fixture_bin)+":"+os.environ.get("PATH", ""),
                   SOLAR_TEST_INVOCATIONS=str(invocations), SCOTTLAND_SOLAR_FILE=str(core.CONFIG),
                   SCOTTLAND_SOLAR_TRANSITION_FILE=str(core.TRANSITION),
                   SCOTTLAND_OMARCHY_SOLAR_FILE=str(adapter.config_file))
restart = subprocess.run([sys.executable, str(root/"omarchy/libexec/scottland-omarchy-solar-theme"), "once"],
                         env=restart_env, capture_output=True, text=True, check=True)
check("new adapter process after restart reads durable ack without theme command",
      restart.stdout.strip() == "unchanged" and not invocations.exists())

observed_digest = adapter._watch_digest()
record = core.read_transition()
record.update(mode="light", transition_id="adapter-watch-race",
              consumer_delivery_id="adapter-watch-delivery", consumer_ack=None)
core.write_transition(record)
adapter.wait_for_transition_change(observed_digest)
with patch.object(adapter.subprocess, "run") as run:
    run.return_value.returncode = 0
    check("adapter watch catches a transition published before inotify wait begins",
          adapter.run_once() == "Test Light")
    check("adapter watcher event is acknowledged after consume",
          core.read_transition()["consumer_ack"] == "adapter-watch-delivery")

# Two session autostarts may run the adapter at once. The dedicated consumer
# lock must serialize the theme command and durable acknowledgement.
record = core.read_transition()
record.update(mode="dark", transition_id="adapter-concurrent-event",
              consumer_delivery_id="adapter-concurrent-delivery", consumer_ack=None)
core.write_transition(record)
invocations.unlink(missing_ok=True)
fake_omarchy.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$SOLAR_TEST_INVOCATIONS\"\nsleep 0.2\n")
fake_omarchy.chmod(0o755)
concurrent_env = dict(restart_env, SOLAR_TEST_INVOCATIONS=str(invocations))
consumers = [subprocess.Popen([sys.executable, str(root/"omarchy/libexec/scottland-omarchy-solar-theme"), "once"],
                              env=concurrent_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
             for _ in range(2)]
consumer_outputs = [consumer.communicate(timeout=5) for consumer in consumers]
check("concurrent session adapters apply one transition only once",
      all(consumer.returncode == 0 for consumer in consumers)
      and invocations.read_text().splitlines() == ["theme set Test Dark"]
      and sorted(output[0].strip() for output in consumer_outputs) == ["Test Dark", "unchanged"])

# A theme map is a separate settings file. The same inotify wait must recover
# promptly when a malformed mapping is corrected, without restarting session.
record = core.read_transition()
record.update(mode="light", transition_id="adapter-malformed-theme",
              consumer_delivery_id="adapter-malformed-delivery", consumer_ack=None)
core.write_transition(record)
omarchy_cfg.write_text("bad theme mapping\n")
observed_digest = adapter._watch_digest()
try:
    adapter.run_once()
except configparser.Error:
    malformed_theme_rejected = True
else:
    malformed_theme_rejected = False
check("malformed theme mapping fails closed without acknowledging transition",
      malformed_theme_rejected and core.read_transition()["consumer_ack"] is None)
config_thread = threading.Thread(target=lambda: (time.sleep(.03), omarchy_cfg.write_text("[themes]\nday = Fixed\n")),
                                 daemon=True)
config_thread.start()
adapter.wait_for_transition_change(observed_digest)
config_thread.join(timeout=1)
check("theme mapping save wakes adapter without polling", adapter.themes()["light"] == "Fixed")

observed_digest = adapter._watch_digest()
solar_thread = threading.Thread(target=lambda: (time.sleep(.03), core.CONFIG.write_text("[solar]\nenabled = false\n")),
                                daemon=True)
solar_thread.start()
adapter.wait_for_transition_change(observed_digest)
solar_thread.join(timeout=1)
check("solar.ini save wakes adapter to observe disable", not adapter.solar_enabled())

# Verify parser failures are caught by the long-lived watcher and that its
# recovery wait is armed before the next session iteration.
with patch.object(adapter, "run_once", side_effect=[
        configparser.MissingSectionHeaderError("omarchy-solar.ini", 1, "bad line"), KeyboardInterrupt()]), \
     patch.object(adapter, "wait_for_transition_change") as wait_for_change:
    try:
        adapter.watch()
    except KeyboardInterrupt:
        pass
check("malformed theme config waits for a save instead of killing the watcher",
      wait_for_change.call_count == 1)

# Failure isolation: absent location, disabled settings, malformed state and
# a failed theme command each fail closed without falsely acknowledging a change.
before_missing_location = core.TRANSITION.read_text()
with patch.object(core, "set_mode", side_effect=AssertionError("no location must not apply")):
    check("missing location performs no transition write",
          core.reconcile(config, None, datetime(2026, 6, 24, tzinfo=timezone.utc)) is None
          and core.TRANSITION.read_text() == before_missing_location)

solar_config(enabled=False)
with patch.object(adapter.subprocess, "run", side_effect=AssertionError("disabled Sunlight applied a theme")):
    check("disabled Sunlight prevents adapter theme commands", adapter.run_once() == "off")

solar_config()
record = core.read_transition()
record.update(enabled=True, mode="light", transition_id="adapter-failed-command",
              consumer_delivery_id="adapter-failed-delivery", consumer_ack=None)
core.write_transition(record)
with patch.object(adapter.subprocess, "run", side_effect=subprocess.CalledProcessError(1, ["omarchy"])):
    try:
        adapter.run_once()
    except subprocess.CalledProcessError:
        pass
check("failed theme command leaves transition unacknowledged",
      core.read_transition()["consumer_ack"] is None)

# Disable and re-enable can retain the same solar-boundary identity. A unique
# consumer delivery ID fences an ack from the command already in flight.
old_delivery = "adapter-aba-old-delivery"
record = core.read_transition()
record.update(enabled=True, mode="dark", transition_id="same-solar-boundary",
              schedule_transition_id="same-solar-boundary",
              consumer_delivery_id=old_delivery, consumer_ack=None)
core.write_transition(record)

def disable_and_reenable_during_theme_set(*_args, **_kwargs):
    core.disable()
    config = solar_config(enabled=True)
    with patch.object(core, "set_mode"):
        core.reconcile(config, (37.77, -122.42), datetime(2026, 6, 24, 20, tzinfo=timezone.utc),
                       ("dark", "same-solar-boundary", datetime(2026, 6, 24, 19, 38, tzinfo=timezone.utc)))
    return subprocess.CompletedProcess([], 0, "")

with patch.object(adapter.subprocess, "run", side_effect=disable_and_reenable_during_theme_set):
    check("stale in-flight adapter ack cannot consume a re-enabled same-period apply",
          adapter.run_once() == "superseded")
current_delivery = core.read_transition()
check("re-enable creates a fresh consumer delivery ID for the same solar boundary",
      current_delivery["transition_id"] == "same-solar-boundary"
      and current_delivery["consumer_delivery_id"] != old_delivery
      and current_delivery["consumer_ack"] is None)

bad = dict(core.read_transition(), schema=99)
core.write_transition(bad)
try:
    adapter.run_once()
except ValueError:
    malformed_fails_closed = True
else:
    malformed_fails_closed = False
check("unsupported state schema is rejected instead of replayed", malformed_fails_closed)

print(f"{passed} solar scheduler checks passed")
