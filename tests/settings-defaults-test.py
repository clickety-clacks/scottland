#!/usr/bin/env python3
"""The shipped compositor defaults and Settings reset match the approved values."""

from configparser import ConfigParser
from decimal import Decimal
from pathlib import Path
import re
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/data/settings-defaults-20261013.ini"
META = ROOT / "core/plugin/metadata/scottland.xml"
CONFIG = ROOT / "core/config/scottland.ini"
QML = ROOT / "core/settings/shell.qml"


def same_value(actual: str, expected: str) -> bool:
    if actual == expected:
        return True
    try:
        return Decimal(actual) == Decimal(expected)
    except Exception:
        return False


def values_in_object(text: str) -> dict[str, str]:
    return dict(re.findall(r'(\w+)\s*:\s*("[^"]*"|true|false|-?\d+(?:\.\d+)?)', text))


class ShippedSettingsDefaults(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        approved = ConfigParser()
        approved.read(FIXTURE)
        cls.approved = dict(approved["scottland"])
        cls.qml = QML.read_text()

    def test_plugin_and_shipped_config_use_approved_values(self) -> None:
        options = {option.attrib["name"]: option.findtext("default")
                   for option in ET.parse(META).getroot().findall(".//option")}
        self.assertEqual(set(self.approved) - set(options), set())
        for key, value in self.approved.items():
            with self.subTest(key=key):
                self.assertTrue(same_value(options[key], value), (key, options[key], value))

        config = ConfigParser()
        config.read(CONFIG)
        for key, value in self.approved.items():
            if config.has_option("scottland", key):
                with self.subTest(shipped_override=key):
                    self.assertTrue(same_value(config["scottland"][key], value))

    def test_settings_defaults_reset_to_approved_values(self) -> None:
        qml = self.qml
        seen: dict[str, str] = {}
        layout = re.search(r'readonly property var defaults: \(\{(.*?)\}\)', qml, re.S)
        self.assertIsNotNone(layout)
        seen.update(values_in_object(layout[1]))
        seen.pop("x", None)
        seen.pop("y", None)

        curve = re.findall(r'\{ x: ([\d.]+), y: ([\d.]+) \}', layout[1])
        self.assertEqual(len(curve), 3)
        seen["scale_curve"] = " ".join(f"{Decimal(x):.3f}:{Decimal(y):.3f}" for x, y in curve)
        seen["max_scale"] = curve[0][1]
        seen["min_scale"] = curve[-1][1]

        for key, value in re.findall(r'name: "(goo_\w+)"[^\n]*?initial: ([\d.]+)', qml):
            seen[key] = value
        goo = re.search(r'const values = \{(.*?)\}\s*for \(const c of gooControls\)', qml, re.S)
        self.assertIsNotNone(goo)
        seen.update(values_in_object(goo[1]))

        edge = re.search(r'initial: root\.lightScheme \? ([\d.]+) : ([\d.]+)', qml)
        self.assertIsNotNone(edge)
        seen["unfocused_edge_tone_light"], seen["unfocused_edge_tone_dark"] = edge.groups()

        for group in ("motionDefaults", "opacityDefaults", "widgetDefaults"):
            match = re.search(rf'readonly property var {group}: \(\{{(.*?)\}}\)', qml, re.S)
            self.assertIsNotNone(match, group)
            seen.update(values_in_object(match[1]))
        seen["screen_zones"] = "[]" if "property var screenZones: []" in qml else "missing"

        self.assertEqual(set(seen), set(self.approved))
        for key, value in self.approved.items():
            actual = seen[key].strip('"')
            with self.subTest(key=key):
                self.assertTrue(same_value(actual, value), (key, actual, value))

        self.assertIn("defaultGooPoints: parseCurve(gooDefaults().goo_falloff, 0)", qml)
        self.assertIn("root.gooPoints = root.defaultGooPoints", qml)
        for name in ("motionDefaults", "opacityDefaults", "widgetDefaults"):
            self.assertIn(f"root.{name}", qml)


if __name__ == "__main__":
    unittest.main()
