#!/usr/bin/env python3
"""Unsupported list APIs stay finite during Omarchy shortcut generation."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

repo = Path(__file__).resolve().parents[1]
shortcut_generator = repo / "omarchy/config.d/50-omarchy-shortcuts"
header = "# Generated from ~/.config/hypr/hyprland.lua by 50-omarchy-shortcuts:"

with tempfile.TemporaryDirectory(prefix="scottland-shortcut-list-") as temporary:
    root = Path(temporary)
    home = root / "home"
    config = home / ".config/hypr/hyprland.lua"
    omarchy_root = root / "omarchy"
    config.parent.mkdir(parents=True)

    config.write_text('''
for _ in ipairs(hl.get_loaded_plugins()) do
  error("unsupported plugin list must be empty")
end

hl.unsupported_api.deep_call()
hl.unsupported_api.deep_call().another_call()
''')

    env = {
        **os.environ,
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_STATE_HOME": str(home / ".local/state"),
        "SCOTTLAND_HOOKS": str(repo / "omarchy"),
        "SCOTTLAND_TEST_MODEL": "1",
        "OMARCHY_PATH": str(omarchy_root),
        "WAYFIRE_PLUGIN_XML_PATH": str(root / "missing-metadata"),
    }
    try:
        result = subprocess.run(
            [str(shortcut_generator), str(repo / "core/config/scottland.ini")],
            env=env, text=True, capture_output=True, timeout=20, check=False)
    except subprocess.TimeoutExpired as error:
        print("FAIL: shortcut generator completes for an unsupported list API "
              f"(timed out; stdout={error.stdout!r}; stderr={error.stderr!r})", flush=True)
        sys.exit(1)

    completed = result.returncode == 0 and header in result.stdout
    print(f"{'PASS' if completed else 'FAIL'}: shortcut generator completes with an unsupported list API "
          f"(exit={result.returncode})", flush=True)
    if not completed:
        print(f"stdout={result.stdout!r}\nstderr={result.stderr!r}", file=sys.stderr)
    sys.exit(0 if completed else 1)
