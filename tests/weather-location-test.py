#!/usr/bin/env python3
"""Weather location outcomes that drive the app's current/stale/error states."""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest import mock


RUNTIME_PATH = Path(__file__).resolve().parents[1] / "core/apps/weather/runtime.py"
SPEC = importlib.util.spec_from_file_location("weather_runtime", RUNTIME_PATH)
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)


class WeatherLocationTest(unittest.TestCase):
    def location_result(self, *, allow_ip, saved=None, system=None, ip=None):
        solar = mock.Mock()
        solar.read_config.return_value = {"location": saved, "allow_ip": allow_ip}
        solar.ip_location.return_value = ip
        output = io.StringIO()
        with mock.patch.object(runtime, "solar_helper", return_value=solar), mock.patch.object(
            runtime, "geoclue_location", return_value=system
        ), contextlib.redirect_stdout(output):
            self.assertEqual(runtime.location(), 0)
        return json.loads(output.getvalue()), solar

    def test_enabled_ip_lookup_failure_is_fetch_failure(self):
        result, solar = self.location_result(allow_ip=True)
        self.assertEqual(result, {"status": "unavailable"})
        solar.ip_location.assert_called_once_with()

    def test_disabled_ip_without_other_location_is_no_location(self):
        result, solar = self.location_result(allow_ip=False)
        self.assertEqual(result, {"status": "no-location"})
        solar.ip_location.assert_not_called()

    def test_saved_location_survives_failed_system_lookup(self):
        result, solar = self.location_result(allow_ip=True, saved=(12.5, -77.25))
        self.assertEqual(result, {"status": "located", "latitude": 12.5, "longitude": -77.25})
        solar.ip_location.assert_not_called()


if __name__ == "__main__":
    unittest.main()
