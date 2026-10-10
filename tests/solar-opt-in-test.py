#!/usr/bin/env python3
"""Isolated config/file/command-boundary checks; no desktop, network or install."""
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
        self.bin = self.home/"bin"
        self.bin.mkdir()
        self.log = self.home/"commands"
        env = dict(HOME=str(self.home), XDG_CONFIG_HOME=str(self.home/"config"),
                   XDG_STATE_HOME=str(self.home/"state"), SCOTTLAND_SOLAR_OPT_IN="1",
                   SCOTTLAND_SOLAR_FILE=str(self.home/"solar.ini"),
                   SCOTTLAND_SOLAR_MODE_FILE=str(self.home/"state/scottland/test.solar-mode"),
                   SCOTTLAND_OMARCHY_SOLAR_FILE=str(self.home/"themes.ini"),
                   SCOTTLAND_OMARCHY_CURRENT=str(self.home/"current"),
                   WAYLAND_DISPLAY="test", PATH=str(self.bin)+":"+os.environ["PATH"],
                   SOLAR_COMMAND_LOG=str(self.log), SOLAR_TEST_MODE="dark")
        self.environment = patch.dict(os.environ, env)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        for name, body in (
            ("omarchy-theme-color", 'printf "probe\\n" >>"$SOLAR_COMMAND_LOG"\nprintf "%s\\n" "$SOLAR_TEST_MODE"\n'),
            ("omarchy", 'printf "%s\\n" "$*" >>"$SOLAR_COMMAND_LOG"\n')):
            p = self.bin/name
            p.write_text("#!/bin/sh\n"+body)
            p.chmod(0o755)
        self.core = load("solar_core_test", Path("core/libexec/scottland-solar-theme"))
        self.adapter = load("solar_adapter_test", Path("omarchy/libexec/scottland-omarchy-solar-theme"))
        self.core.MODE.parent.mkdir(parents=True)

    def config(self, text):
        self.core.CONFIG.write_text(text)
        os.utime(self.core.CONFIG, (100, 100))

    def mode(self, text="light", stamp=110):
        self.core.MODE.write_text(text+"\n")
        os.utime(self.core.MODE, (stamp, stamp))

    def once(self, now=120):
        with patch.object(self.adapter.time, "time", return_value=now):
            return self.adapter.run_once()

    def test_missing_or_partial_config_does_not_locate_or_change_theme(self):
        for contents in (None, "[solar]\n", "[solar]\nenabled = false\n", "[solar]\nenabled = invalid\n"):
            with self.subTest(contents=contents):
                self.core.CONFIG.unlink(missing_ok=True)
                if contents is not None:
                    self.config(contents)
                self.mode()
                with patch.object(self.core, "geoclue_location", side_effect=AssertionError("location")), \
                     patch.object(self.core, "ip_location", side_effect=AssertionError("network")), \
                     patch.object(self.core, "apply", side_effect=AssertionError("preference")):
                    self.assertIsNone(self.core.run_once())
                self.assertFalse(self.core.MODE.exists())
                self.mode()  # A stale/older producer can still leave a mode after disable.
                self.assertEqual(self.once(), "off")
                self.assertFalse(self.log.exists())

    def test_saved_opt_in_and_network_choice_are_preserved(self):
        self.config("[solar]\nenabled = true\nallow_ip = true\n")
        self.assertTrue(self.core.read_config()["enabled"])
        self.assertTrue(self.core.read_config()["allow_ip"])
        self.config("[solar]\nenabled = true\n")
        self.assertTrue(self.core.read_config()["enabled"])
        self.assertFalse(self.core.read_config()["allow_ip"])

    def test_standalone_core_defaults_stay_on(self):
        with patch.dict(os.environ, {"SCOTTLAND_SOLAR_OPT_IN": "0"}):
            self.assertTrue(self.core.read_config()["enabled"])
            self.assertTrue(self.core.read_config()["allow_ip"])

    def test_opt_in_uses_saved_location_without_network_or_matching_preference_change(self):
        self.config("[solar]\nenabled = true\nlocation_set = true\nlatitude = 37.77\nlongitude = -122.42\n")
        with patch.object(self.core, "geoclue_location", return_value=None), \
             patch.object(self.core, "ip_location", side_effect=AssertionError("network")), \
             patch.object(self.core, "solar_mode", return_value="light"), \
             patch.object(self.core, "current_mode", return_value="light"), \
             patch.object(self.core.subprocess, "run", side_effect=AssertionError("preference changed")):
            self.assertEqual(self.core.run_once(), "light")
        self.assertEqual(self.core.MODE.read_text(), "light\n")

    def test_missing_invalid_stale_future_or_pre_config_mode_is_ignored(self):
        self.config("[solar]\nenabled = true\n")
        self.assertEqual(self.once(), "off")
        for value, stamp in (("invalid", 110), ("light", 89), ("light", 121), ("light", 99)):
            with self.subTest(value=value, stamp=stamp):
                self.mode(value, stamp)
                self.assertEqual(self.once(), "off")
                self.assertFalse(self.log.exists())

    def test_malformed_configuration_is_ignored(self):
        self.config("not an ini file\n")
        self.mode()
        self.assertEqual(self.once(), "off")
        self.assertFalse(self.log.exists())

    def test_fresh_mode_changes_only_wrong_theme(self):
        self.config("[solar]\nenabled = true\n")
        self.mode()
        self.assertEqual(self.once(), "watercolor-dream-light")
        self.assertEqual(self.log.read_text().splitlines(), ["probe", "theme set watercolor-dream-light"])

    def test_matching_theme_is_kept(self):
        self.config("[solar]\nenabled = true\n")
        self.mode()
        with patch.dict(os.environ, {"SOLAR_TEST_MODE": "light"}):
            self.assertEqual(self.once(), "kept")
        self.assertEqual(self.log.read_text(), "probe\n")

    def test_disable_blocks_existing_producer_without_mutating_configuration(self):
        self.config("[solar]\nenabled = false\n")
        before = self.core.CONFIG.read_bytes()
        self.mode()
        self.assertEqual(self.once(), "off")
        self.assertEqual(self.core.CONFIG.read_bytes(), before)
        self.assertEqual(self.core.MODE.read_text(), "light\n")
        self.assertFalse(self.log.exists())

    def test_unchanged_publication_refreshes_liveness(self):
        self.mode(stamp=1)
        self.core.publish("light")
        self.assertEqual(self.core.MODE.read_text(), "light\n")
        self.assertGreater(self.core.MODE.stat().st_mtime, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
