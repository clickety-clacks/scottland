#!/usr/bin/env python3
"""Isolated opt-in, stale-config and fail-closed checks for Sunlight."""
import importlib.util
from importlib.machinery import SourceFileLoader
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    loader = SourceFileLoader(name, str(ROOT/path))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    loader.exec_module(module)
    return module


class SolarOptIn(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="solar-opt-in-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.home/"config"),
            "XDG_STATE_HOME": str(self.home/"state"),
            "SCOTTLAND_SOLAR_FILE": str(self.home/"config/scottland/solar.ini"),
            "SCOTTLAND_SOLAR_MODE_FILE": str(self.home/"state/scottland/test.solar-mode"),
            "SCOTTLAND_SOLAR_TRANSITION_FILE": str(self.home/"state/scottland/solar-transition.json"),
            "SCOTTLAND_OMARCHY_SOLAR_FILE": str(self.home/"themes.ini"),
            "WAYLAND_DISPLAY": "solar-opt-in-test",
            "SCOTTLAND_SOLAR_OPT_IN": "1",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.core = load("solar_core_opt_in", Path("core/libexec/scottland-solar-theme"))
        self.adapter = load("solar_adapter_opt_in", Path("omarchy/libexec/scottland-omarchy-solar-theme"))

    def config(self, contents):
        self.core.CONFIG.parent.mkdir(parents=True, exist_ok=True)
        self.core.CONFIG.write_text(contents)

    def test_missing_and_partial_config_default_off_for_integration(self):
        self.assertFalse(self.core.read_config()["enabled"])
        self.assertFalse(self.core.read_config()["allow_ip"])
        self.config("[solar]\n")
        self.assertFalse(self.core.read_config()["enabled"])
        self.assertFalse(self.core.read_config()["allow_ip"])
        with patch.object(self.core, "geoclue_location", side_effect=AssertionError("disabled located")), \
             patch.object(self.core, "ip_location", side_effect=AssertionError("disabled used network")), \
             patch.object(self.adapter.subprocess, "run", side_effect=AssertionError("disabled set theme")):
            self.assertIsNone(self.core.run_once())
            self.assertEqual(self.adapter.run_once(), "off")

    def test_explicit_enable_and_network_choices_survive_settings_save(self):
        self.config("[solar]\nenabled = true\nallow_ip = true\n")
        first = self.core.read_config()
        self.assertTrue(first["enabled"])
        self.assertTrue(first["allow_ip"])
        self.config("[solar]\nenabled = true\n")
        second = self.core.read_config()
        self.assertTrue(second["enabled"])
        self.assertFalse(second["allow_ip"])

    def test_standalone_defaults_remain_enabled_without_adapter_opt_in(self):
        os.environ.pop("SCOTTLAND_SOLAR_OPT_IN", None)
        self.assertTrue(self.core.read_config()["enabled"])
        self.assertTrue(self.core.read_config()["allow_ip"])
        self.config("[solar]\nenabled = false\nallow_ip = false\n")
        self.assertFalse(self.core.read_config()["enabled"])
        self.assertFalse(self.core.read_config()["allow_ip"])

    def test_saved_coordinates_do_not_trigger_network_lookup(self):
        self.config("[solar]\nenabled = true\nallow_ip = false\nlocation_set = true\nlatitude = 37.77\nlongitude = -122.42\n")
        event = ("light", "2026-06-21T12:47:34Z", None)
        with patch.object(self.core, "geoclue_location", return_value=None), \
             patch.object(self.core, "ip_location", side_effect=AssertionError("network not enabled")), \
             patch.object(self.core, "solar_transition", return_value=event), \
             patch.object(self.core, "current_mode", return_value="light"):
            self.assertEqual(self.core.run_once(), "light")

    def test_network_location_requires_both_opted_in_settings(self):
        self.config("[solar]\nenabled = true\nallow_ip = true\n")
        event = ("light", "2026-06-21T12:47:34Z", None)
        with patch.object(self.core, "geoclue_location", return_value=None), \
             patch.object(self.core, "ip_location", return_value=(37.77, -122.42)) as network, \
             patch.object(self.core, "solar_transition", return_value=event), \
             patch.object(self.core, "current_mode", return_value="light"):
            self.assertEqual(self.core.run_once(), "light")
            network.assert_called_once_with()

    def test_settings_save_during_location_lookup_discards_old_result(self):
        self.config("[solar]\nenabled = true\nallow_ip = true\n")

        def save_during_lookup():
            self.config("[solar]\nenabled = true\nlocation_set = true\nlatitude = 0\nlongitude = 0\n")
            return (37.77, -122.42)

        with patch.object(self.core, "geoclue_location", side_effect=save_during_lookup), \
             patch.object(self.core, "ip_location", side_effect=AssertionError("Geoclue result exists")), \
             patch.object(self.core, "current_mode", side_effect=AssertionError("stale result applied")), \
             patch.object(self.core, "set_mode") as set_mode:
            self.assertIsNone(self.core.run_once())
        set_mode.assert_not_called()

    def test_missing_location_and_bad_transition_fail_closed(self):
        self.config("[solar]\nenabled = true\nallow_ip = false\n")
        with patch.object(self.core, "geoclue_location", return_value=None), \
             patch.object(self.core, "set_mode") as set_mode:
            self.assertIsNone(self.core.run_once())
            set_mode.assert_not_called()
        self.config("[solar]\nenabled = true\n")
        self.core.write_transition({"schema": 91, "enabled": True})
        with patch.object(self.adapter.subprocess, "run",
                          side_effect=AssertionError("invalid state changed the current theme")):
            with self.assertRaises(ValueError):
                self.adapter.run_once()

    def test_disabled_record_never_changes_theme(self):
        self.config("[solar]\nenabled = true\n")
        self.core.write_transition({"schema": 1, "enabled": False, "mode": "dark",
                                    "transition_id": "old", "consumer_ack": None})
        with patch.object(self.adapter.subprocess, "run", side_effect=AssertionError("disabled record applied")):
            self.assertEqual(self.adapter.run_once(), "off")


if __name__ == "__main__":
    unittest.main(verbosity=2)
