#!/usr/bin/env python3
"""Theme-memory behavior with temporary state and a stand-in theme command.

File publications stand in for Omarchy theme selection; no compositor, user
configuration, installed theme setter or timer is exercised by these fixtures.
"""
import importlib.util
from importlib.machinery import SourceFileLoader
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1]/"omarchy/libexec/scottland-omarchy-solar-theme"


class ThemeMemory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="theme-memory-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        env = patch.dict(os.environ, {
            "HOME": str(self.root), "XDG_STATE_HOME": str(self.root/"state"),
            "XDG_CONFIG_HOME": str(self.root/"config"), "SCOTTLAND_SOLAR_OPT_IN": "1",
            "SCOTTLAND_SOLAR_FILE": str(self.root/"solar.ini"),
            "SCOTTLAND_SOLAR_TRANSITION_FILE": str(self.root/"state/scottland/solar-transition.json"),
            "SCOTTLAND_OMARCHY_SOLAR_FILE": str(self.root/"themes.ini"),
            "OMARCHY_PATH": str(self.root/"omarchy"),
        })
        env.start()
        self.addCleanup(env.stop)
        self.adapter = self.load()
        self.adapter.solar_file.write_text("[solar]\nenabled=true\n")
        self.calls = []
        self.publish("initial-dark")
        self.event("dark", "initial", acknowledged=True)
        self.assertEqual(self.adapter.run_once(), "unchanged")

    def load(self):
        loader = SourceFileLoader("theme_memory_fixture", str(SOURCE))
        module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(module)
        return module

    def publish(self, name):
        (self.root/".config/omarchy/themes"/name).mkdir(parents=True, exist_ok=True)
        path = self.adapter.current_theme_file
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".next")
        temporary.write_text(name+"\n")
        temporary.replace(path)

    def event(self, mode, identity, acknowledged=False):
        self.adapter._atomic_write({"schema": 1, "enabled": True, "mode": mode,
                                    "transition_id": identity,
                                    "consumer_ack": identity if acknowledged else None})

    def memory(self):
        return json.loads(self.adapter.memory_file.read_text())

    def setter(self, argv, **kwargs):
        self.assertEqual(argv[:3], ["omarchy", "theme", "set"])
        self.calls.append(argv[3])
        self.publish(argv[3].lower().replace(" ", "-"))
        return subprocess.CompletedProcess(argv, 0)

    def run_once(self):
        with patch.object(self.adapter.subprocess, "run", side_effect=self.setter):
            return self.adapter.run_once()

    def test_independent_memories_restore_dark_light_dark_once(self):
        self.publish("personal-dark")
        self.assertEqual(self.run_once(), "unchanged")
        self.event("light", "sunrise")
        self.assertEqual(self.run_once(), "watercolor-dream-light")
        self.publish("personal-light")
        self.assertEqual(self.run_once(), "unchanged")
        self.event("dark", "sunset")
        self.assertEqual(self.run_once(), "personal-dark")
        self.assertEqual(self.run_once(), "unchanged")
        self.event("light", "next-sunrise")
        self.assertEqual(self.run_once(), "personal-light")
        self.assertEqual(self.calls, ["watercolor-dream-light", "personal-dark", "personal-light"])
        self.assertEqual(self.memory()["choices"], {"dark": "personal-dark", "light": "personal-light"})

    def test_restart_and_polls_preserve_manual_choice(self):
        self.publish("personal-dark")
        self.run_once()
        self.adapter = self.load()
        for _ in range(3):
            self.assertEqual(self.run_once(), "unchanged")
        self.assertEqual(self.adapter.current_theme_file.read_text(), "personal-dark\n")
        self.assertEqual(self.memory()["choices"], {"dark": "personal-dark"})
        self.assertEqual(self.calls, [])

    def test_unobserved_manual_change_at_boundary_belongs_to_outgoing_mode(self):
        self.publish("late-dark-choice")
        self.event("light", "sunrise")
        self.run_once()
        self.assertEqual(self.memory()["choices"], {"dark": "late-dark-choice"})

    def test_both_empty_modes_use_fallback_without_learning_it(self):
        for mode, identity in [("light", "sunrise"), ("dark", "sunset")]:
            self.event(mode, identity)
            self.assertEqual(self.run_once(), "watercolor-dream-"+mode)
            self.adapter = self.load()
            self.assertEqual(self.run_once(), "unchanged")
        self.assertEqual(self.memory()["choices"], {})
        self.assertEqual(self.calls, ["watercolor-dream-light", "watercolor-dream-dark"])

    def test_manual_reselection_of_automatic_name_is_a_new_choice(self):
        self.event("light", "sunrise")
        self.run_once()
        self.publish("watercolor-dream-light")
        self.assertEqual(self.run_once(), "unchanged")
        self.assertEqual(self.memory()["choices"], {"light": "watercolor-dream-light"})
        self.assertEqual(len(self.calls), 1)

    def test_disabled_missing_and_unset_config_do_not_observe_or_apply(self):
        before = self.adapter.memory_file.read_bytes()
        self.publish("while-disabled")
        self.event("light", "sunrise")
        for config in (None, "[solar]\n", "[solar]\nenabled=false\n"):
            if config is None:
                self.adapter.solar_file.unlink(missing_ok=True)
            else:
                self.adapter.solar_file.write_text(config)
            self.assertEqual(self.run_once(), "off")
            self.assertEqual(self.adapter.memory_file.read_bytes(), before)
        self.assertEqual(self.calls, [])

    def test_missing_disabled_and_invalid_records_do_not_learn(self):
        before = self.adapter.memory_file.read_bytes()
        self.publish("unqualified-choice")
        for record in (None, {"schema": 1, "enabled": False},
                       {"schema": 1, "enabled": True, "mode": "dark", "transition_id": ""}):
            if record is None:
                self.adapter.transition_file.unlink()
            else:
                self.adapter._atomic_write(record)
            self.assertEqual(self.run_once(), "off")
            self.assertEqual(self.adapter.memory_file.read_bytes(), before)
        self.assertEqual(self.calls, [])

    def test_config_mapping_remains_a_fallback_until_manual_memory_exists(self):
        self.adapter.config_file.write_text("[themes]\nday=Configured Day\n")
        self.assertEqual(self.run_once(), "unchanged")
        self.event("light", "sunrise")
        self.assertEqual(self.run_once(), "Configured Day")
        self.assertEqual(self.memory()["choices"], {})

    def test_partial_failed_auto_publication_is_not_a_manual_choice(self):
        self.event("light", "sunrise")
        def fail(argv, **kwargs):
            self.setter(argv, **kwargs)
            raise subprocess.CalledProcessError(7, argv)
        with patch.object(self.adapter.subprocess, "run", side_effect=fail):
            with self.assertRaises(subprocess.CalledProcessError):
                self.adapter.run_once()
        self.assertIsNone(self.adapter.read_transition()["consumer_ack"])
        self.adapter = self.load()
        self.run_once()
        self.assertEqual(self.memory()["choices"], {})
        self.assertEqual(self.run_once(), "unchanged")

    def test_corrupt_memory_refuses_without_theme_or_ack(self):
        self.adapter.memory_file.write_text('{"schema": 99}')
        self.event("light", "sunrise")
        with self.assertRaises(ValueError):
            self.run_once()
        self.assertEqual(self.calls, [])
        self.assertIsNone(self.adapter.read_transition()["consumer_ack"])

    def remember_pair(self):
        self.publish("personal-dark")
        self.run_once()
        self.event("light", "sunrise")
        self.run_once()
        self.publish("personal-light")
        self.run_once()

    def test_removed_user_or_builtin_memory_uses_fallback_once(self):
        self.remember_pair()
        user = self.root/".config/omarchy/themes/personal-dark"
        builtin = self.root/"omarchy/themes/personal-dark"
        builtin.parent.mkdir(parents=True)
        user.rename(builtin)
        self.event("dark", "builtin-sunset")
        self.assertEqual(self.run_once(), "personal-dark")
        # The setter fixture publishes in the user root; both lookup locations
        # must be absent, as after removal or an update dropping a builtin.
        user.rmdir()
        builtin.rmdir()
        self.event("light", "second-sunrise")
        self.run_once()
        self.event("dark", "removed-sunset")
        self.assertEqual(self.run_once(), "watercolor-dream-dark")
        self.assertEqual(self.adapter.read_transition()["consumer_ack"], "removed-sunset")
        self.adapter = self.load()
        count = len(self.calls)
        self.assertEqual(self.run_once(), "unchanged")
        self.assertEqual(len(self.calls), count)
        self.assertEqual(self.memory()["choices"], {"light": "personal-light"})

    def test_removed_memory_fallback_failure_keeps_manual_pick_in_destination(self):
        self.remember_pair()
        (self.root/".config/omarchy/themes/personal-dark").rmdir()
        self.event("dark", "sunset")
        def fail(argv, **kwargs):
            self.calls.append(argv[3])
            raise subprocess.CalledProcessError(7, argv)
        with patch.object(self.adapter.subprocess, "run", side_effect=fail):
            with self.assertRaises(subprocess.CalledProcessError):
                self.adapter.run_once()
        self.assertEqual(self.calls[-1], "watercolor-dream-dark")
        self.assertEqual(self.memory()["mode"], "dark")
        self.assertIsNone(self.adapter.read_transition()["consumer_ack"])
        self.publish("replacement-dark")
        self.adapter = self.load()
        self.assertEqual(self.run_once(), "replacement-dark")
        self.assertEqual(self.memory()["choices"],
                         {"dark": "replacement-dark", "light": "personal-light"})

    def test_removal_during_set_falls_back_without_learning_failed_publication(self):
        self.remember_pair()
        self.event("dark", "sunset")
        def remove_during_set(argv, **kwargs):
            result = self.setter(argv, **kwargs)
            if argv[3] == "personal-dark":
                (self.root/".config/omarchy/themes/personal-dark").rmdir()
                raise subprocess.CalledProcessError(1, argv)
            return result
        with patch.object(self.adapter.subprocess, "run", side_effect=remove_during_set):
            self.assertEqual(self.adapter.run_once(), "watercolor-dream-dark")
        self.assertEqual(self.calls[-2:], ["personal-dark", "watercolor-dream-dark"])
        self.assertEqual(self.memory()["choices"], {"light": "personal-light"})
        self.assertEqual(self.adapter.read_transition()["consumer_ack"], "sunset")

    def test_existing_theme_command_failure_does_not_trigger_fallback(self):
        self.remember_pair()
        self.event("dark", "sunset")
        def fail(argv, **kwargs):
            self.calls.append(argv[3])
            raise subprocess.CalledProcessError(7, argv)
        count = len(self.calls)
        with patch.object(self.adapter.subprocess, "run", side_effect=fail):
            with self.assertRaises(subprocess.CalledProcessError):
                self.adapter.run_once()
        self.assertEqual(self.calls[count:], ["personal-dark"])
        self.assertEqual(self.memory()["mode"], "dark")
        self.assertEqual(self.memory()["choices"],
                         {"dark": "personal-dark", "light": "personal-light"})
        self.assertIsNone(self.adapter.read_transition()["consumer_ack"])

    def test_watch_observes_manual_publication_without_theme_command(self):
        class Done(Exception):
            pass
        waits = []
        def wait(digest):
            waits.append(digest)
            if len(waits) == 1:
                self.publish("watch-selected-dark")
                self.assertNotEqual(digest, self.adapter._watch_digest())
            else:
                raise Done()
        with patch.object(self.adapter, "wait_for_transition_change", side_effect=wait), \
             patch.object(self.adapter.subprocess, "run", side_effect=self.setter):
            with self.assertRaises(Done):
                self.adapter.watch()
        self.assertEqual(self.memory()["choices"], {"dark": "watch-selected-dark"})
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
