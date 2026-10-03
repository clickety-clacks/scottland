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
assert 'setGoo("goo"' in qml
assert 'goo_falloff: curveText(points)' in qml and 'onEdited: points => root.setEditorPoints(points)' in qml
expected_goo = {name for name in options if name == "goo" or name.startswith("goo_")}
assert goo_rows | goo_special == expected_goo, (
    f"Goo Settings mismatch: missing {expected_goo - goo_rows - goo_special}; "
    f"unknown {goo_rows - expected_goo}")

inertia = {name for name in options if name in {
    "key_impulse", "key_friction", "resize_impulse", "resize_friction", "key_max_velocity", "cycle_overshoot",
    "alt_hold_delay", "window_double_tap_delay"}}
motion_section = qml.split("id:motionSettings", 1)[1].split("id:windowOpacitySettings", 1)[0]
motion_rows = set(re.findall(r'id:\s*"([^"]+)"', motion_section))
motion_special = set(re.findall(r'setMotion\("([^"]+)"', qml))
motion_special.update(re.findall(r'setMotionPair\("([^"]+)"', qml))
motion_special.update(re.findall(r'setMotionPair\("[^"]+","([^"]+)"', qml))
assert inertia <= motion_rows | motion_special, f"Missing inertial Settings rows: {inertia - motion_rows - motion_special}"
assert "key_restitution" not in options and "key_restitution" not in qml
assert "move_friction_curve" not in options and "resize_friction_curve" not in options
assert qml.count("CoastGraph {") == 2

for name, label, step in (("goo_depth", "Liquid depth", 0.1),
                          ("goo_profile", "Wall wetting", 0.01),
                          ("goo_soak", "Wallpaper soak", 0.01)):
    match = re.search(r'\{\s*name:\s*"' + name + r'"[^}]+\}', goo_section)
    assert match, f"No row for {name}"
    row = match.group()
    metadata = options[name]
    for field, qml_field in (("_short", "title"), ("_long", "hint"),
                             ("default", "initial"), ("min", "low"), ("max", "high")):
        value = metadata.findtext(field)
        literal = f'{qml_field}: "{value}"' if field.startswith("_") else f"{qml_field}: {value}"
        assert literal in row, f"{name} {field} differs from metadata"
    assert f"step: {step}" in row

assert 'id:"cycle_overshoot"' in motion_section
assert 'id:"widget_bounce"' in qml
print(f"PASS Settings covers {len(expected_goo)} Goo and {len(inertia)} inertial options; GO14/GO15 hints and ranges match metadata")
