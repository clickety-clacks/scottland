#!/usr/bin/env python3
"""Check flavoring-owned shortcuts displace Omarchy imports only while configured."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile


repo = Path(__file__).resolve().parents[1]
builder = repo / "core/session/scottland-build-config"
importer = repo / "omarchy/config.d/50-omarchy-shortcuts"
base = repo / "core/config/scottland.ini"
metadata = repo / "core/plugin/metadata"


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f": {detail}" if not ok else ""), flush=True)
    if not ok:
        raise AssertionError(name)


def scan_row(mods, key, description, action):
    fields = [mods, key, description, "exec", action, "0", "0", "0", "0", "", "", "0", "0"]
    return "\t".join(fields)


def run_case(root, active, report):
    case = root / f"{'active' if active else 'inactive'}-{'report' if report else 'no-report'}"
    home = case / "home"
    config = home / ".config"
    hooks = case / "hooks"
    runtime = case / "runtime"
    fake_bin = case / "bin"
    for path in (config / "hypr", config / "scottland", hooks / "config.d",
                 hooks / "override-report.d", runtime, fake_bin):
        path.mkdir(parents=True, exist_ok=True)
    (config / "scottland/scottland.ini").write_text(base.read_text())
    (config / "hypr/hyprland.lua").write_text("-- captured fixture bindings\n")

    flavoring = hooks / "config.d/40-gooarchy-flavorings"
    fragment = """[command]
binding_flavoring_cheatsheet = <super> KEY_K
command_flavoring_cheatsheet = flavoring-cheatsheet-action
binding_flavoring_emoji = <super> <ctrl> KEY_E
command_flavoring_emoji = flavoring-emoji-action
""" if active else "# no flavoring shortcut is enabled\n"
    flavoring.write_text("#!/bin/sh\ncat <<'SCOTTLAND_FLAVORINGS'\n" + fragment +
                        "SCOTTLAND_FLAVORINGS\n")
    flavoring.chmod(0o755)
    (hooks / "config.d/50-omarchy-shortcuts").symlink_to(importer)

    # O20 is explanatory text only; the generated config.d/40 output above determines ownership.
    if report:
        (hooks / "override-report.d/gooarchy-shortcuts.txt").write_text(
            "## Gooarchy flavoring shortcuts\n"
            "Reason: Gooarchy actions work better in Scottland.\n\n"
            "- Keys: Super+K\n"
            "  Was: Open the Omarchy keybinding menu\n"
            "  Now: Open the Scottland keybinding sheet\n"
            "  Origin: Gooarchy flavoring\n\n"
            "- Keys: Super+Ctrl+E\n"
            "  Was: Open the Omarchy emoji menu\n"
            "  Now: Open the Scottland emoji picker\n"
            "  Origin: Gooarchy flavoring\n\n"
            "## Gooarchy desktop defaults\n"
            "Reason: Gooarchy keeps desktop behavior consistent.\n\n"
            "- Keys: Touchpad tap-to-click\n"
            "  Was: Tap-to-click is disabled\n"
            "  Now: Tap-to-click is enabled\n"
            "  Origin: Gooarchy flavoring\n\n"
            "- Keys: Terminal bell\n"
            "  Was: Bell is enabled\n"
            "  Now: Bell is muted\n"
            "  Origin: Gooarchy flavoring\n\n"
            "- Keys: Terminal title\n"
            "  Was: Title follows the shell\n"
            "  Now: Title follows the active directory\n"
            "  Origin: Gooarchy flavoring\n\n"
            "- Keys: Default app\n"
            "  Was: Use the Omarchy default\n"
            "  Now: Use the Gooarchy default\n"
            "  Origin: Gooarchy flavoring\n\n"
            "## Gooarchy widget controls\n"
            "Reason: Gooarchy supplies its own widget controls for the desktop.\n\n"
            "- Keys: Super+G\n"
            "  Was: Open Gooarchy widget list\n"
            "  Now: Opens Gooarchy's widget controls.\n")
        if not active:
            user_fragment = config / "scottland/override-report.d/user-shortcuts.txt"
            user_fragment.parent.mkdir(parents=True, exist_ok=True)
            user_fragment.write_text(
                "## User shortcut notes\n"
                "Reason: This shortcut belongs to the user's configuration.\n\n"
                "- Keys: Super+K\n"
                "  Was: User's previous action\n"
                "  Now: User's configured action\n"
                "  Origin: Your custom/changed shortcut\n")

    rows = "\n".join((
        scan_row("SUPER", "K", "Open Omarchy keybinding menu", "omarchy-menu-keybindings"),
        scan_row("CTRL SUPER", "E", "Open Omarchy emoji menu", "omarchy-shell shell toggle omarchy.emojis"),
        scan_row("SUPER", "G", "Toggle window group", "window-group-toggle"),
    )) + "\n"
    fake_lua = fake_bin / "lua"
    fake_lua.write_text(
        f"#!{sys.executable}\nimport os, sys\nsys.stdin.read()\n"
        "sys.stdout.write(os.environ['SCOTTLAND_TEST_SCAN_ROWS'])\n")
    fake_lua.chmod(0o755)

    output = runtime / "wayfire.ini"
    env = os.environ.copy()
    env.update({
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(config),
        "XDG_STATE_HOME": str(case / "state"),
        "SCOTTLAND_HOOKS": str(hooks),
        "SCOTTLAND_SESSION_DIR": str(runtime),
        "SCOTTLAND_CONFIG_OUTPUT": str(output),
        "SCOTTLAND_TEST_SCAN_ROWS": rows,
        "SCOTTLAND_TEST_MODEL": "1",
        "WAYFIRE_PLUGIN_XML_PATH": str(metadata),
        "PATH": str(fake_bin) + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"),
    })
    env.pop("WAYFIRE_SOCKET", None)
    result = subprocess.run([str(builder)], env=env, capture_output=True, text=True, timeout=20)
    if result.returncode != 0:
        raise RuntimeError(f"config build exited {result.returncode}: {result.stderr}")
    return output.read_text(), (case / "state/scottland/omarchy-overrides.txt").read_text()


def main():
    with tempfile.TemporaryDirectory(prefix="scottland-flavoring-owner-") as temp_name:
        root = Path(temp_name)
        active_config, active_report = run_case(root, active=True, report=True)
        check("active flavoring actions remain and Omarchy actions step aside",
              active_config.count("binding_flavoring_cheatsheet = <super> KEY_K") == 1 and
              active_config.count("command_flavoring_cheatsheet = flavoring-cheatsheet-action") == 1 and
              active_config.count("binding_flavoring_emoji = <super> <ctrl> KEY_E") == 1 and
              active_config.count("command_flavoring_emoji = flavoring-emoji-action") == 1 and
              "omarchy-menu-keybindings" not in active_config and
              "omarchy-shell shell toggle omarchy.emojis" not in active_config,
              active_config)
        check("each effective overlap appears once in the O20 report",
              active_report.count("Super+K —") == 1 and
              active_report.count("Super+Ctrl+E —") == 1 and
              "Now: Open the Scottland keybinding sheet" in active_report and
              "Now: Open the Scottland emoji picker" in active_report,
              active_report)

        inactive_config, inactive_report = run_case(root, active=False, report=False)
        check("without the flavoring binding, both Omarchy actions are imported",
              inactive_config.count("binding_omarchy_0 = <super> KEY_K") == 1 and
              inactive_config.count("command_omarchy_0 = omarchy-menu-keybindings") == 1 and
              inactive_config.count("binding_omarchy_1 = <ctrl> <super> KEY_E") == 1 and
              inactive_config.count("command_omarchy_1 = omarchy-shell shell toggle omarchy.emojis") == 1 and
              "flavoring-cheatsheet-action" not in inactive_config and
              "flavoring-emoji-action" not in inactive_config,
              inactive_config)
        check("inactive keys have no flavoring override report entry",
              "Super+K —" not in inactive_report and
              "Super+Ctrl+E —" not in inactive_report,
              inactive_report)

        stale_config, stale_report = run_case(root, active=False, report=True)
        check("O20 text without an effective flavoring binding does not suppress Omarchy",
              stale_config.count("command_omarchy_0 = omarchy-menu-keybindings") == 1 and
              stale_config.count("command_omarchy_1 = omarchy-shell shell toggle omarchy.emojis") == 1,
              stale_config)
        check("O20 text without an effective binding is not reported as an override",
              "Now: Open the Scottland keybinding sheet" not in stale_report and
              "Now: Open the Scottland emoji picker" not in stale_report,
              stale_report)
        check("unrelated and user O20 fragment rows survive stale shortcut filtering",
              all(label in stale_report for label in (
                  "Touchpad tap-to-click —", "Terminal bell —", "Terminal title —",
                  "Default app —", "Super+G — Was: Open Gooarchy widget list.",
                  "Super+K — Was: User's previous action.")) and
              "Now: User's configured action." in stale_report,
              stale_report)

        active_without_report, fallback_report = run_case(root, active=True, report=False)
        check("effective flavoring bindings suppress Omarchy without O20 text",
              "omarchy-menu-keybindings" not in active_without_report and
              "omarchy-shell shell toggle omarchy.emojis" not in active_without_report and
              "flavoring-cheatsheet-action" in active_without_report and
              "flavoring-emoji-action" in active_without_report,
              active_without_report)
        check("effective bindings without O20 text still report each override once",
              fallback_report.count("Super+K —") == 1 and
              fallback_report.count("Super+Ctrl+E —") == 1 and
              "Runs the active Gooarchy flavoring action" in fallback_report,
              fallback_report)
    print("9 focused ownership checks passed")


if __name__ == "__main__":
    main()
