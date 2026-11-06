#!/usr/bin/env python3
"""Unsupported list APIs stay finite while normal Lua lists still iterate."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

repo = Path(__file__).resolve().parents[1]
lua_host = repo / "omarchy/libexec/scottland-luahost"
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

sys.exit(0 if all(checks) else 1)
