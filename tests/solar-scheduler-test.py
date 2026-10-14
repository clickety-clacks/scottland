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
check("next event is a future sunrise in the expected UTC period",
      sunrise[0] == "light" and sunrise[2] > before_dawn and sunrise[2].hour in (12, 13))
before_sunset = datetime(2026, 6, 21, 18, tzinfo=timezone.utc)
sunset = core.next_solar_transition(before_sunset, 37.77, -122.42)
check("next event after sunrise is sunset", sunset[0] == "dark" and sunset[2] > before_sunset)
west_after_utc_midnight = datetime(2026, 10, 1, 0, 30, tzinfo=timezone.utc)
west_previous_solar_day = [
    event for event in core.solar_day_transitions(
        west_after_utc_midnight.date()-timedelta(days=1), 37.77, -122.42)
    if event[2] > west_after_utc_midnight
]
west_expected_sunset = min(west_previous_solar_day, key=lambda event: event[2])
west_next = core.next_solar_transition(west_after_utc_midnight, 37.77, -122.42)
check("western UTC-midnight scan keeps the previous solar day's future sunset",
      west_expected_sunset[0] == "dark" and west_next == west_expected_sunset
      and west_next[2] < west_after_utc_midnight+timedelta(hours=4))
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

# The same sunrise must have one identity even when a save, refresh, or relog
# asks for the current period at a different time. These instants reproduce a
# one-second identity drift from a9a40e9's moving upper bisection bound.
same_period_times = [
    datetime(2026, 10, 4, 14, 7, 36, tzinfo=timezone.utc),
    datetime(2026, 10, 4, 14, 13, 55, tzinfo=timezone.utc),
    datetime(2026, 10, 4, 18, 8, 7, tzinfo=timezone.utc),
    datetime(2026, 10, 4, 19, 6, 57, tzinfo=timezone.utc),
    datetime(2026, 10, 5, 0, 7, 5, tzinfo=timezone.utc),
]
same_period_events = [core.solar_transition(when, 37.77, -122.42)
                      for when in same_period_times]
same_period_id = same_period_events[0][1]
check("one sunrise has a stable identity across same-period checks",
      all(event[0] == "light" and event[1] == same_period_id
          for event in same_period_events))
scheduled_sunrise = core.next_solar_transition(
    datetime(2026, 10, 4, 12, tzinfo=timezone.utc), 37.77, -122.42)
timer_wake_event = core.solar_transition(
    scheduled_sunrise[2]+timedelta(seconds=1), 37.77, -122.42)
check("timer wake uses the same identity as the scheduled sunrise",
      timer_wake_event[:2] == scheduled_sunrise[:2]
      and scheduled_sunrise[1] == same_period_id)

# State written by the previous implementation can hold the adjacent rounded
# second for this same crossing. Treat it as the same event during upgrade so
# the repaired identity does not undo a manual theme choice once.
solar_config()
sunrise_stamp = datetime.strptime(same_period_id, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
prior_rounded_id = (sunrise_stamp-timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
location = (37.77, -122.42)
core.write_transition({
    "schema": 1, "enabled": True, "location": core.location_key(location),
    "mode": "light", "transition_id": prior_rounded_id+"#prior",
    "schedule_transition_id": prior_rounded_id,
    "last_applied": {"mode": "light", "transition_id": prior_rounded_id+"#prior",
                     "schedule_transition_id": prior_rounded_id},
    "consumer_ack": prior_rounded_id+"#prior",
})
core.CONFIG.write_text(core.CONFIG.read_text()+"# Settings Save\n")
saved_solar_config = core.read_config()
manual_theme = {"mode": "dark"}
solar_reapplications = []

def apply_solar_mode(mode):
    solar_reapplications.append(mode)
    manual_theme["mode"] = mode

for check_time in same_period_times:
    with patch.object(core, "set_mode", side_effect=apply_solar_mode):
        core.reconcile(saved_solar_config, location, check_time)
check("Settings Save and same-period refresh preserve a manual pick across identity migration",
      manual_theme["mode"] == "dark" and solar_reapplications == [])

# First enable applies once. A manual choice survives same-period config saves.
config = solar_config()
event1 = event_for(datetime(2026, 6, 21, 12, 47, tzinfo=timezone.utc), "light")
with patch.object(core, "current_mode", return_value="dark"), patch.object(core, "set_mode") as set_mode:
    core.reconcile(config, (37.77, -122.42), datetime(2026, 6, 21, 13, tzinfo=timezone.utc), event1)
    check("first enable applies the current period once", set_mode.call_count == 1)
core.CONFIG.write_text(core.CONFIG.read_text()+"# settings saved\n")
saved_config = core.read_config()
manual_theme = {"mode": "dark"}
with patch.object(core, "current_mode", side_effect=AssertionError("same-period check inspected preference")), \
     patch.object(core, "set_mode", side_effect=lambda mode: manual_theme.update(mode=mode)) as set_mode:
    core.reconcile(saved_config, (37.77, -122.42), datetime(2026, 6, 21, 14, tzinfo=timezone.utc), event1)
check("manual light/dark choice survives checks and Settings Save",
      manual_theme["mode"] == "dark" and set_mode.call_count == 0)

# A live scheduler must consume a saved disable and stop the adapter without
# changing the theme the user picked manually. Writing solar.ini represents
# Settings Save; the wait is a test seam, so this tests the product effect and
# deliberately bypasses UI serialization and notification mechanics.
solar_config()
manual_theme = {"mode": "dark"}
waits = 0

def save_disable_then_stop(*_args, **_kwargs):
    global waits
    waits += 1
    if waits == 1:
        solar_config(enabled=False)
        return "config"
    raise KeyboardInterrupt

try:
    with patch.object(core, "locate", return_value=(37.77, -122.42)), \
         patch.object(core, "solar_transition", return_value=event1), \
         patch.object(core, "next_solar_transition",
                      return_value=("dark", "2026-06-21T19:38:00Z",
                                    datetime(2026, 6, 21, 19, 38, tzinfo=timezone.utc))), \
         patch.object(core, "set_mode",
                      side_effect=lambda mode: manual_theme.update(mode=mode)), \
         patch.object(core, "wait_for_config_or_deadline", side_effect=save_disable_then_stop):
        core.watch()
except KeyboardInterrupt:
    pass
with patch.object(adapter.subprocess, "run",
                  side_effect=AssertionError("disabled Sunlight invoked a theme command")):
    saved_disable_result = adapter.run_once()
check("Settings Save disables a running scheduler without replacing a manual theme",
      manual_theme["mode"] == "dark"
      and core.read_transition()["enabled"] is False
      and saved_disable_result == "off")

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
with patch.object(core, "set_mode") as set_mode:
    core.reconcile(config, (37.77, -122.42), datetime(2026, 6, 23, 20, 5, tzinfo=timezone.utc), missed)
check("reboot in the same solar period does not apply the theme again", set_mode.call_count == 0)

core.disable()
with patch.object(core, "set_mode") as set_mode:
    core.reconcile(config, (37.77, -122.42), datetime(2026, 6, 23, 20, 10, tzinfo=timezone.utc), missed)
    check("re-enable applies the current period once", set_mode.call_count == 1)

# Saving a new location re-bases future scheduling but does not treat the new
# calculation's already-past event as a sunrise/sunset.
new_config = solar_config(lat=40.71, lon=-74.0)
location_event = event_for(datetime(2026, 6, 23, 9, 25, tzinfo=timezone.utc), "light")
with patch.object(core, "set_mode") as set_mode:
    rebased_mode = core.reconcile(new_config, (40.71, -74.0), datetime(2026, 6, 23, 20, tzinfo=timezone.utc), location_event)
check("location/config save does not count as a solar transition",
      rebased_mode == "light" and set_mode.call_count == 0)
future_location_event = event_for(datetime(2026, 6, 23, 19, 40, tzinfo=timezone.utc), "dark")
with patch.object(core, "set_mode") as set_mode:
    core.reconcile(new_config, (40.71, -74.0), datetime(2026, 6, 23, 20, tzinfo=timezone.utc), future_location_event)
    check("new saved-location sunrise/sunset applies at its next boundary",
          set_mode.call_count == 1)

# Adapter consumes only a new unacknowledged transition. Restarting after ack
# must not rewrite a user's manual theme, even if its current mode differs.
solar_config()
omarchy_cfg = scratch / "config/scottland/omarchy-solar.ini"
omarchy_cfg.write_text("[themes]\nday = Test Light\nnight = Test Dark\n")
record = core.read_transition()
record.update(enabled=True, mode="dark", transition_id="adapter-reboot-event",
              consumer_ack=None)
core.write_transition(record)
with patch.object(adapter.subprocess, "run") as run:
    run.return_value.returncode = 0
    check("adapter applies a new transition even when the theme's mode already matches",
          adapter.run_once() == "Test Dark")
with patch.object(adapter.subprocess, "run", side_effect=AssertionError("fresh adapter replayed acknowledged transition")):
    check("fresh adapter process after a completed theme change does not replace the current choice",
          adapter.run_once() == "unchanged")

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
check("adapter restart after a completed theme change does not apply it again",
      restart.stdout.strip() == "unchanged" and not invocations.exists())

# A saved theme map is held until the next solar event; it must neither overwrite
# the user's current choice nor be lost when the next boundary is consumed.
# Direct file writes represent Settings Save; UI serialization and watcher
# notification are outside this behavior check.
omarchy_cfg.write_text("[themes]\nday = Saved Light\nnight = Saved Dark\n")
with patch.object(adapter.subprocess, "run",
                  side_effect=AssertionError("theme-map save replaced a manual choice")):
    check("theme-map Settings Save preserves the manual choice until a boundary",
          adapter.run_once() == "unchanged")
record = core.read_transition()
record.update(mode="light", transition_id="theme-map-next-boundary",
              schedule_transition_id="theme-map-next-boundary", consumer_ack=None)
core.write_transition(record)
with patch.object(adapter.subprocess, "run") as run:
    run.return_value.returncode = 0
    mapped_theme = adapter.run_once()
    check("next solar boundary uses the saved day theme",
          mapped_theme == "Saved Light"
          and run.call_args.args[0] == ["omarchy", "theme", "set", "Saved Light"])
omarchy_cfg.write_text("[themes]\nday = Test Light\nnight = Test Dark\n")

# Preserve the applied theme across an upgrade from an earlier state format.
record = core.read_transition()
record.update(mode="dark", transition_id="upgrade-transition",
              schedule_transition_id="upgrade-boundary",
              consumer_delivery_id="upgrade-delivery", consumer_ack="upgrade-delivery")
core.write_transition(record)
with patch.object(adapter.subprocess, "run", side_effect=AssertionError("upgrade replayed acknowledged transition")):
    check("upgrade does not reapply a theme that was already selected",
          adapter.run_once() == "unchanged")
record = core.read_transition()
record["consumer_ack"] = None
core.write_transition(record)
with patch.object(adapter.subprocess, "run") as run:
    run.return_value.returncode = 0
    check("upgrade applies a pending theme from the earlier state format",
          adapter.run_once() == "Test Dark")
with patch.object(adapter.subprocess, "run", side_effect=AssertionError("fresh adapter replayed delivery ack")):
    check("upgraded adapter does not repeat the selected theme after restart",
          adapter.run_once() == "unchanged")

record = core.read_transition()
record.pop("consumer_delivery_id", None)
record["consumer_ack"] = None
core.write_transition(record)

# Two session autostarts may run the adapter at once. The dedicated consumer
# lock must serialize the theme command and durable acknowledgement.
record = core.read_transition()
record.update(mode="dark", transition_id="adapter-concurrent-event",
              consumer_ack=None)
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

# A malformed theme map must not acknowledge or consume the pending transition.
record = core.read_transition()
record.update(mode="light", transition_id="adapter-malformed-theme",
              consumer_ack=None)
core.write_transition(record)
omarchy_cfg.write_text("bad theme mapping\n")
try:
    with patch.object(adapter.subprocess, "run",
                      side_effect=AssertionError("malformed mapping changed the current theme")):
        adapter.run_once()
except configparser.Error:
    malformed_theme_rejected = True
else:
    malformed_theme_rejected = False
check("malformed theme mapping leaves the current theme unchanged", malformed_theme_rejected)
omarchy_cfg.write_text("[themes]\nday = Test Light\nnight = Test Dark\n")
# Failure isolation: absent location, disabled settings, malformed state and
# a failed theme command each fail closed without falsely acknowledging a change.
with patch.object(core, "set_mode") as set_mode:
    no_location_result = core.reconcile(config, None, datetime(2026, 6, 24, tzinfo=timezone.utc))
check("missing location leaves the light/dark preference unchanged",
      no_location_result is None and set_mode.call_count == 0)

solar_config(enabled=False)
with patch.object(adapter.subprocess, "run", side_effect=AssertionError("disabled Sunlight applied a theme")):
    check("disabled Sunlight prevents adapter theme commands", adapter.run_once() == "off")

solar_config()
record = core.read_transition()
record.update(enabled=True, mode="light", transition_id="adapter-failed-command",
              consumer_ack=None)
core.write_transition(record)
with patch.object(adapter.subprocess, "run", side_effect=subprocess.CalledProcessError(1, ["omarchy"])):
    try:
        adapter.run_once()
    except subprocess.CalledProcessError:
        failed_apply = True
    else:
        failed_apply = False
with patch.object(adapter.subprocess, "run") as retry:
    retry.return_value = subprocess.CompletedProcess([], 0, "")
    retry_result = adapter.run_once()
with patch.object(adapter.subprocess, "run",
                  side_effect=AssertionError("successful retry repeated the theme change")):
    no_repeat_result = adapter.run_once()
check("failed theme change remains retryable and succeeds once",
      failed_apply and retry_result == "Test Light" and no_repeat_result == "unchanged")

# Disable and re-enable can retain the same solar-boundary identity. A unique
# consumer-visible transition ID fences an ack from the command already in flight.
old_transition_id = "same-solar-boundary#old-generation"
record = core.read_transition()
record.update(enabled=True, mode="dark", transition_id=old_transition_id,
              schedule_transition_id="same-solar-boundary",
              consumer_ack=None)
core.write_transition(record)

def disable_and_reenable_during_theme_set(*_args, **_kwargs):
    core.disable()
    config = solar_config(enabled=True)
    with patch.object(core, "set_mode"):
        core.reconcile(config, (37.77, -122.42), datetime(2026, 6, 24, 20, tzinfo=timezone.utc),
                       ("dark", "same-solar-boundary", datetime(2026, 6, 24, 19, 38, tzinfo=timezone.utc)))
    return subprocess.CompletedProcess([], 0, "")

with patch.object(adapter.subprocess, "run", side_effect=disable_and_reenable_during_theme_set):
    adapter.run_once()
with patch.object(adapter.subprocess, "run") as reenabled_apply:
    reenabled_apply.return_value = subprocess.CompletedProcess([], 0, "")
    reenabled_result = adapter.run_once()
with patch.object(adapter.subprocess, "run",
                  side_effect=AssertionError("re-enabled boundary applied twice")):
    no_duplicate_result = adapter.run_once()
check("same-period re-enable applies the theme once after an earlier command finishes",
      reenabled_result == "Test Dark" and no_duplicate_result == "unchanged")

bad = dict(core.read_transition(), schema=99)
core.write_transition(bad)
try:
    with patch.object(adapter.subprocess, "run",
                      side_effect=AssertionError("invalid state changed the current theme")):
        adapter.run_once()
except ValueError:
    malformed_fails_closed = True
else:
    malformed_fails_closed = False
check("unsupported state schema is rejected instead of replayed", malformed_fails_closed)

print(f"{passed} solar scheduler checks passed")
