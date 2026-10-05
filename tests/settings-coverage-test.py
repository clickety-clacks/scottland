#!/usr/bin/env python3
"""Keep every Goo and inertial plugin option reachable from Scottland Settings."""
from pathlib import Path
import re
import xml.etree.ElementTree as ET

root = Path(__file__).resolve().parents[1]
qml = (root / "core/settings/shell.qml").read_text()
options = {item.attrib["name"]: item for item in ET.parse(
    root / "core/plugin/metadata/scottland.xml").iter("option")}

goo_section = qml.split("readonly property var gooControls:", 1)[1].split(
    "function gooDefaults", 1)[0]
goo_rows = set(re.findall(r'name:\s*"(goo_[^"]+)"', goo_section))
goo_special = {"goo", "goo_falloff"}
expected_goo = {name for name in options if name == "goo" or
                (name.startswith("goo_") and name != "goo_breath_keys")}
assert goo_rows | goo_special == expected_goo, (
    f"Goo Settings mismatch: missing {expected_goo - goo_rows - goo_special}; "
    f"unknown {goo_rows - expected_goo}")

edge_section = qml.split("readonly property var edgeControls:", 1)[1].split(
    "function gooDefaults", 1)[0]
edge_options = {name for name in options if name.startswith("unfocused_edge_")}
edge_rows = set(re.findall(r'"(unfocused_edge_[a-z_]+)"', edge_section))
assert edge_rows == edge_options, (
    f"Unfocused edge Settings mismatch: missing {edge_options - edge_rows}; "
    f"unknown {edge_rows - edge_options}")
assert options["attention_color_family"].attrib["type"] == "string"
assert options["attention_color_family"].findtext("default") == "theme"
assert options["goo_dye_strength"].findtext("default") == "1"
assert options["goo_dye_strength"].findtext("min") == "0"
assert options["goo_dye_strength"].findtext("max") == "1.5"
ctl = (root / "core/libexec/scottland-ctl").read_text()
assert all(f'"{name}"' in ctl for name in edge_options)
for name in edge_options:
    metadata = options[name]
    assert metadata.findtext("min") == "0" and metadata.findtext("max") == "1"

inertia = {name for name in options if name in {
    "key_impulse", "key_friction", "resize_impulse", "resize_friction", "key_max_velocity", "cycle_overshoot",
    "alt_hold_delay", "window_double_tap_delay", "window_hold_delay"}}
motion_section = qml.split("id:motionSettings", 1)[1].split("id:windowOpacitySettings", 1)[0]
motion_rows = set(re.findall(r'id:\s*"([^"]+)"', motion_section))
motion_special = set(re.findall(r'setMotion\("([^"]+)"', qml))
motion_special.update(re.findall(r'setMotionPair\("([^"]+)"', qml))
motion_special.update(re.findall(r'setMotionPair\("[^"]+","([^"]+)"', qml))
assert inertia <= motion_rows | motion_special, f"Missing inertial Settings rows: {inertia - motion_rows - motion_special}"
assert "key_restitution" not in options
assert "move_friction_curve" not in options and "resize_friction_curve" not in options

assert options["window_mode_tint"].findtext("default") == "7"
assert options["window_mode_tint"].findtext("min") == "0"
assert '"window_mode_tint"' in (root / "core/libexec/scottland-ctl").read_text()
print(f"PASS Settings covers {len(goo_rows)} numeric Goo, {len(edge_options)} unfocused-edge and "
      f"{len(inertia)} inertial options")
