#!/usr/bin/env python3
"""Unsupported list APIs stay finite in both Omarchy scanning paths."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

repo = Path(__file__).resolve().parents[1]
lua_host = repo / "omarchy/libexec/scottland-luahost"
shortcut_generator = repo / "omarchy/config.d/50-omarchy-shortcuts"
checks = []


def check(name, passed, detail=""):
    checks.append(bool(passed))
    print(f"{'PASS' if passed else 'FAIL'}: {name}" + (f" ({detail})" if detail else ""),
          flush=True)


with tempfile.TemporaryDirectory(prefix="scottland-luahost-list-") as temporary:
    root = Path(temporary)
    home = root / "home"
    config = home / ".config/hypr/hyprland.lua"
    fake_bin = root / "bin"
    config.parent.mkdir(parents=True)
    fake_bin.mkdir()

    config.write_text('''
local unsupported_items = 0
for _ in ipairs(hl.get_loaded_plugins()) do
  unsupported_items = unsupported_items + 1
  break -- Bound the regression case if the stand-in ever returns a value again.
end

local tag_count = 0
for _, tag in ipairs(hl.get_active_window().tags) do
  tag_count = tag_count + 1
end

hl.unsupported_api.deep_call() -- Named-property chaining remains a safe no-op while loading.
hl.env("SCOTTLAND_TEST_UNSUPPORTED_LIST_ITEMS", unsupported_items)
hl.env("SCOTTLAND_TEST_ACTIVE_WINDOW_TAGS", tag_count)
hl.env("SCOTTLAND_TEST_CHAINING", "works")
''')
    hyprctl = fake_bin / "hyprctl"
    hyprctl.write_text('''#!/bin/sh
[ "$1" = "-j" ] && [ "$2" = "activewindow" ] || exit 2
printf '%s\\n' '{"address":"0x1","tags":["one","two"]}'
''')
    hyprctl.chmod(0o755)

    env = {
        **os.environ,
        "HOME": str(home),
        "XDG_RUNTIME_DIR": str(root),
        "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
    }
    try:
        result = subprocess.run([str(lua_host), "--print-env"], env=env, text=True,
                                capture_output=True, timeout=10, check=False)
    except subprocess.TimeoutExpired as error:
        print("FAIL: Lua host --print-env completes for an unsupported list API "
              f"(timed out after 10s; stdout={error.stdout!r}, stderr={error.stderr!r})",
              flush=True)
        sys.exit(1)

    check("Lua host --print-env completes for an unsupported list API",
          result.returncode == 0, f"exit={result.returncode}; stderr={result.stderr.strip()!r}")
    check("unsupported list API yields no numeric entries",
          "export SCOTTLAND_TEST_UNSUPPORTED_LIST_ITEMS='0'" in result.stdout, result.stdout.strip())
    check("supported active-window tag list still yields both entries",
          "export SCOTTLAND_TEST_ACTIVE_WINDOW_TAGS='2'" in result.stdout, result.stdout.strip())
    check("named unsupported calls remain chainable during config loading",
          "export SCOTTLAND_TEST_CHAINING='works'" in result.stdout, result.stdout.strip())

    generator_root = root / "shortcut-generator"
    generator_home = generator_root / "home"
    generator_config = generator_home / ".config/hypr/hyprland.lua"
    omarchy_root = generator_root / "omarchy"
    (generator_config.parent).mkdir(parents=True)
    (omarchy_root / "default/hypr/bindings").mkdir(parents=True)
    generator_config.write_text('''
local loaded_plugins = 0
for _ in ipairs(hl.get_loaded_plugins()) do
  loaded_plugins = loaded_plugins + 1
end
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
    (omarchy_root / "default/hypr/bindings/tiling.lua").write_text('''
o.bind("SUPER+1", "Workspace 1", hl.dsp.workspace.focus({workspace = "1"}))
''')
    generator_env = {
        **os.environ,
        "HOME": str(generator_home),
        "XDG_CONFIG_HOME": str(generator_home / ".config"),
        "XDG_STATE_HOME": str(generator_home / ".local/state"),
        "SCOTTLAND_HOOKS": str(repo / "omarchy"),
        "OMARCHY_PATH": str(omarchy_root),
        "WAYFIRE_PLUGIN_XML_PATH": str(generator_root / "missing-metadata"),
    }
    try:
        generated = subprocess.run(
            [str(shortcut_generator), str(repo / "core/config/scottland.ini")],
            env=generator_env, text=True, capture_output=True, timeout=20, check=False)
    except subprocess.TimeoutExpired as error:
        print("FAIL: Omarchy shortcut generator completes for an unsupported list API "
              f"(timed out; stdout={error.stdout!r}, stderr={error.stderr!r})", flush=True)
        sys.exit(1)

    check("Omarchy shortcut generator completes for an unsupported list API",
          generated.returncode == 0 and
          "# Generated from ~/.config/hypr/hyprland.lua by 50-omarchy-shortcuts:" in generated.stdout,
          f"exit={generated.returncode}; stdout={generated.stdout.strip()!r}; "
          f"stderr={generated.stderr.strip()!r}")

sys.exit(0 if all(checks) else 1)
