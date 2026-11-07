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
    bindings = omarchy_root / "default/hypr/bindings"
    config.parent.mkdir(parents=True)
    bindings.mkdir(parents=True)

    config.write_text('''
local loaded_plugins = 0
for _ in ipairs(hl.get_loaded_plugins()) do
  loaded_plugins = loaded_plugins + 1
end

hl.unsupported_api.deep_call()
hl.unsupported_api.deep_call().another_call()
''')
    (omarchy_root / "default/hypr/helpers.lua").write_text('''
o = o or {}
function o.bind(keys, description, action, options)
  options = options or {}
  options.description = description
  if type(action) == "string" then action = hl.dsp.exec_cmd(action) end
  hl.bind(keys, action, options)
end
''')
    (bindings / "tiling.lua").write_text('''
o.bind("SUPER+1", "Finite list regression", "echo finite-list-regression")
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
    print(f"{'PASS' if completed else 'FAIL'}: shortcut generator completes for an unsupported list API "
          f"(exit={result.returncode})", flush=True)
    imported = completed and "echo finite-list-regression" in result.stdout
    print(f"{'PASS' if imported else 'FAIL'}: generator processes the following supported binding",
          flush=True)
    if not completed or not imported:
        print(f"stdout={result.stdout!r}\nstderr={result.stderr!r}", file=sys.stderr)
    sys.exit(0 if completed and imported else 1)
