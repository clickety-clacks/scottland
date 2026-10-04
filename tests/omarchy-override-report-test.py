#!/usr/bin/env python3
"""Exercise O20 report generation, change detection, setup display, and flavoring drop-ins."""
import contextlib
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time


repo = Path(__file__).resolve().parents[1]
script = repo / "omarchy/config.d/50-omarchy-shortcuts"
setup = repo / "omarchy/bin/scottland-omarchy-setup"
metadata = repo / "core/plugin/metadata"
base_source = repo / "core/config/scottland.ini"
passes = failures = 0


def check(name, ok, detail=""):
    global passes, failures
    if ok:
        passes += 1
        print(f"PASS  {name}", flush=True)
    else:
        failures += 1
        print(f"FAIL  {name}: {detail}", flush=True)


def launcher_count(path, count, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file() and len(path.read_text().splitlines()) >= count:
            return True
        time.sleep(0.025)
    return False


def prompt_count(path):
    return path.read_text().count("__PROMPT_END__") if path.is_file() else 0


def wait_prompt_count(path, count, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if prompt_count(path) >= count:
            return True
        time.sleep(0.025)
    return False


def make_default_fixture(root):
    hypr = root / "default/hypr"
    (hypr / "bindings").mkdir(parents=True)
    (hypr / "helpers.lua").write_text('''
o = o or {}
function o.bind(keys, description, action, options)
  options = options or {}
  options.description = description
  if type(action) == "string" then action = hl.dsp.exec_cmd(action) end
  hl.bind(keys, action, options)
end
''')
    (hypr / "bindings/tiling.lua").write_text('''
o.bind("ALT + TAB", "Focus on next window", hl.dsp.window.cycle_next())
o.bind("SUPER + 1", "Workspace 1", hl.dsp.workspace.focus({workspace = "1"}))
o.bind("SUPER + 2", "Workspace 2", hl.dsp.workspace.focus({workspace = "2"}), {repeating = true})
o.bind("SUPER + 3", "Workspace 3", hl.dsp.workspace.focus({workspace = "3"}))
''')


def report_group(report, heading):
    marker = f"## {heading}\n"
    start = report.find(marker)
    if start < 0:
        return ""
    start += len(marker)
    end = report.find("\n## ", start)
    return report[start:] if end < 0 else report[start:end]


def test_env(home, hooks, launch_log, path_prefix):
    config = home / ".config"
    state = home / ".local/state"
    env = os.environ.copy()
    env.update({
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(config),
        "XDG_STATE_HOME": str(state),
        "SCOTTLAND_HOOKS": str(hooks),
        "SCOTTLAND_REPORT_LAUNCH_LOG": str(launch_log),
        "WAYFIRE_PLUGIN_XML_PATH": f"{metadata}:/usr/share/wayfire/metadata",
        "PATH": str(path_prefix) + os.pathsep + env.get("PATH", "/usr/bin:/bin"),
    })
    env.pop("WAYFIRE_SOCKET", None)
    env.pop("SCOTTLAND_TEST_MODEL", None)
    (config / "hypr").mkdir(parents=True, exist_ok=True)
    (config / "scottland").mkdir(parents=True, exist_ok=True)
    (config / "scottland/scottland.ini").write_text(base_source.read_text())
    return env


def main():
    with tempfile.TemporaryDirectory(prefix="scottland-o20-") as temp_name:
        temp = Path(temp_name)
        fake_bin = temp / "bin"
        hooks = temp / "hooks"
        home = temp / "generator-home"
        fake_bin.mkdir()
        (hooks / "config.d").mkdir(parents=True)
        (hooks / "override-report.d").mkdir()
        home.mkdir()
        (hooks / "config.d/50-omarchy-shortcuts").symlink_to(script)
        launch_log = temp / "launches.txt"
        agent_log = temp / "agent-prompts.txt"
        defaults = temp / "omarchy"
        make_default_fixture(defaults)
        fake_editor = fake_bin / "omarchy-launch-editor"
        fake_editor.write_text("#!/bin/sh\nprintf '%s\\n' \"$1\" >>\"$SCOTTLAND_REPORT_LAUNCH_LOG\"\n")
        fake_editor.chmod(0o755)
        fake_selector = fake_bin / "omarchy-default-agent"
        fake_selector.write_text("#!/bin/sh\nprintf '%s\\n' \"${SCOTTLAND_DEFAULT_AGENT:-}\"\n")
        fake_selector.chmod(0o755)
        fake_agent_prompt = fake_bin / "omarchy-agent-prompt"
        fake_agent_prompt.write_text("#!/bin/sh\nprintf '%s\\n' \"$1\" >>\"$SCOTTLAND_AGENT_PROMPT_LOG\"\nprintf '%s\\n' '__PROMPT_END__' >>\"$SCOTTLAND_AGENT_PROMPT_LOG\"\n")
        fake_agent_prompt.chmod(0o755)
        hypr = home / ".config/hypr/hyprland.lua"
        hypr.parent.mkdir(parents=True)
        plugin = home / ".config/omarchy/plugins/ask.lua"
        plugin.parent.mkdir(parents=True)
        plugin.write_text('''
hl.bind("ALT+TAB", hl.dsp.exec_cmd("ask"), {description = "Open Ask"})
hl.bind("ALT", hl.dsp.exec_cmd("ask-hold"), {description = "Open Ask on Alt hold"})
hl.bind("SUPER+3", hl.dsp.workspace.focus({workspace = "3"}), {description = "Workspace 3"})
hl.bind("SUPER+K", function() end, {release = true})
''')
        hypr.write_text('''
package.path = os.getenv("HOME") .. "/.config/?.lua;" .. package.path
hl.bind("SUPER+COMMA", hl.dsp.exec_cmd("ask-settings"), {description = "Open Ask settings"})
hl.bind("CTRL+W", hl.dsp.exec_cmd("close-tab"), {description = "Close tab"})
hl.bind("CTRL+ALT+W", hl.dsp.exec_cmd("close-window"), {description = "Close browser window"})
hl.bind("SUPER+W", hl.dsp.window.close(), {description = "Close window"})
hl.bind("SUPER+1", hl.dsp.workspace.focus({workspace = "1"}), {description = "Workspace 1"})
hl.bind("SUPER+2", hl.dsp.workspace.focus({workspace = "2"}), {description = "Workspace 2"})
hl.bind("SUPER+SHIFT+1", hl.dsp.window.move_to_workspace({workspace = "1"}),
        {description = "Move window to workspace 1"})
hl.bind("SUPER+SHIFT+3", hl.dsp.workspace.focus({workspace = "3"}))
hl.bind("SUPER+L", hl.dsp.layout("cycle"), {description = "Cycle tiling layout"})
hl.bind("SUPER+J", hl.dsp.window.focus({direction = "next"}), {description = "Focus next window"})
hl.bind("SUPER+F", hl.dsp.exec_cmd("hyprctl dispatch movefocus l"))
hl.bind("SUPER+V", hl.dsp.exec_cmd("hyprctl dispatch layoutmsg togglesplit"))
hl.bind("SUPER+G", hl.dsp.group.toggle(), {description = "Toggle window group"})
hl.bind("SHIFT+F4", hl.dsp.exec_cmd("release-action"),
        {description = "Release action with modifier", release = true})
hl.bind("SUPER+NoSuchKey", hl.dsp.exec_cmd("unsupported-key"),
        {description = "Unsupported key name"})
hl.bind("SUPER+Y", hl.dsp.exec_cmd("hyprctl dispatch unsupported"),
        {description = "Unsupported Hyprland action"})
require("omarchy.plugins.ask")
''')
        env = test_env(home, hooks, launch_log, fake_bin)
        env["SCOTTLAND_AGENT_PROMPT_LOG"] = str(agent_log)
        env["SCOTTLAND_DEFAULT_AGENT"] = "codex-test"
        env["OMARCHY_PATH"] = str(defaults)

        old_env = os.environ.copy()
        installed_omarchy_root = Path(old_env.get("OMARCHY_PATH", "/usr/share/omarchy"))
        try:
            os.environ.clear()
            os.environ.update(env)
            loader = importlib.machinery.SourceFileLoader("o20_shortcut_import", str(script))
            importer = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
            loader.exec_module(importer)
            importer.HYPR_CONFIG = str(hypr)
            importer.LUA_SCAN = importer.LUA_SCAN.replace(
                'os.getenv("HOME") .. "/.config/hypr/hyprland.lua"', json.dumps(str(hypr)))
            configured_metadata = os.environ["WAYFIRE_PLUGIN_XML_PATH"]
            os.environ["WAYFIRE_PLUGIN_XML_PATH"] = str(temp / "missing-metadata")
            try:
                check("O20 uses fallback reasons if metadata is unavailable",
                      importer.metadata_reasons() == ({}, "", ""))
            finally:
                os.environ["WAYFIRE_PLUGIN_XML_PATH"] = configured_metadata

            def generate(args=()):
                old_argv = sys.argv
                try:
                    sys.argv = [str(script), *args]
                    with contextlib.redirect_stdout(io.StringIO()) as output:
                        importer.main()
                    return output.getvalue()
                finally:
                    sys.argv = old_argv

            generated = generate((str(base_source),))
            report_path = Path(env["XDG_STATE_HOME"]) / "scottland/omarchy-overrides.txt"
            seen_path = report_path.with_name("omarchy-overrides.seen")
            report = report_path.read_text() if report_path.is_file() else ""
            sample_report = report
            live_rows = importer.parse_scan_rows(importer.run_lua_scan().stdout)
            default_rows = importer.parse_scan_rows(importer.run_lua_scan(baseline=True).stdout)
            plugin_identity = importer.report_identity("Super+3")
            live_plugin_row = next(row for row in live_rows if row["identity"] == plugin_identity)
            default_plugin_row = next(row for row in default_rows if row["identity"] == plugin_identity)
            plugin_workspace_line = next(line for line in report.splitlines()
                                         if "Super+3 — Was: Workspace 3." in line)
            own_heading = "## Your own shortcuts that don't work in Scottland"
            own_start = report.index(own_heading) + len(own_heading)
            own_end = report.index("This report lists shortcuts", own_start)
            own_section = report[own_start:own_end]
            own_lines = [line for line in own_section.splitlines()
                         if line.startswith("- ") and "[Your custom/changed shortcut]" in line]
            group_report = report.split("This report lists shortcuts", 1)[-1]
            all_custom_lines = [line for line in group_report.splitlines()
                                if line.startswith("- ") and "[Your custom/changed shortcut]" in line]
            own_entries = sorted(line.rsplit(" Why:", 1)[0] for line in own_lines)
            all_custom_entries = sorted(all_custom_lines)
            check("O20 puts every custom or plugin shortcut in the report callout, with its reason",
                  report.index("## Your own shortcuts that don't work in Scottland") <
                  report.index("This report lists shortcuts") and own_entries == all_custom_entries and
                  any(line.startswith("- Super+3 —") for line in own_lines) and
                  any(line.startswith("- Alt+Tab —") for line in own_lines) and
                  "[Omarchy default]" not in own_section and
                  "Why: Scottland keeps every window on one spatial desktop" in own_section,
                  f"callout={own_entries!r}; grouped={all_custom_entries!r}; section={own_section}")
            ask_line = next(line for line in report.splitlines()
                            if "Alt+Tab — Was: Open Ask." in line)
            check("O20 identifies a user-plugin binding even when its signature matches a shipped default",
                  live_plugin_row["user_plugin"] and
                  live_plugin_row["signature"] == default_plugin_row["signature"] and
                  "[Your custom/changed shortcut]" in plugin_workspace_line and
                  "[Omarchy default]" not in plugin_workspace_line,
                  f"live={live_plugin_row}, default={default_plugin_row}, line={plugin_workspace_line}")
            installed_version = subprocess.run(["pacman", "-Q", "omarchy"],
                                               capture_output=True, text=True, check=False)
            copied_root = temp / "omarchy4-default-copy"
            actual_hypr_defaults = installed_omarchy_root / "default/hypr"
            is_omarchy4 = (installed_version.returncode == 0 and
                           installed_version.stdout.startswith("omarchy 4.") and
                           (actual_hypr_defaults / "bindings/tiling.lua").is_file())
            real_defaults_scan = None
            if is_omarchy4:
                shutil.copytree(actual_hypr_defaults, copied_root / "default/hypr")
                saved_omarchy_path = os.environ["OMARCHY_PATH"]
                try:
                    os.environ["OMARCHY_PATH"] = str(copied_root)
                    real_defaults_scan = importer.run_lua_scan(baseline=True)
                    real_default_rows = importer.parse_scan_rows(real_defaults_scan.stdout)

                    def real_default_line(label):
                        identity = importer.report_identity(label)
                        return next(line for line in real_defaults_scan.stdout.splitlines()
                                    if importer.parse_scan_rows(line)[0]["identity"] == identity)

                    changed_fields = real_default_line("Super+1").split("\t")
                    changed_fields[2] = "Changed personal action"
                    changed_fields[3] = "exec"
                    changed_fields[4] = "personal-command"
                    plugin_fields = real_default_line("Super+2").split("\t")
                    plugin_fields[11] = "1"
                    partial_live_output = "\n".join((
                        real_defaults_scan.stdout,
                        "\t".join(changed_fields),
                        "\t".join(plugin_fields),
                    ))
                    saved_origins = importer.BINDING_ORIGINS.copy()
                    saved_complete = importer.ORIGIN_SCAN_COMPLETE
                    importer.update_binding_origins(
                        partial_live_output, real_defaults_scan.stdout,
                        defaults_complete=(real_defaults_scan.returncode == 0 and
                                           not real_defaults_scan.stderr.strip()),
                        live_complete=False)  # an unrelated live module was skipped
                    check("O20 compares against a copied Omarchy 4 layout and classifies captured rows despite unrelated live warnings",
                          real_defaults_scan.returncode == 0 and not real_defaults_scan.stderr.strip() and
                          len(real_default_rows) >= 200 and
                          importer.source_for_label("Super+1") == "Your custom/changed shortcut" and
                          importer.source_for_label("Super+2") == "Your custom/changed shortcut" and
                          importer.source_for_label("Super+3") == "Omarchy default" and
                          importer.source_for_label("F12") == "Source not verified" and
                          not importer.ORIGIN_SCAN_COMPLETE,
                          f"version={installed_version.stdout.strip()}, rows={len(real_default_rows)}, "
                          f"scan_stderr={real_defaults_scan.stderr!r}")
                    importer.BINDING_ORIGINS = saved_origins
                    importer.ORIGIN_SCAN_COMPLETE = saved_complete
                finally:
                    os.environ["OMARCHY_PATH"] = saved_omarchy_path
            else:
                check("O20 copied Omarchy 4 fixture is available for the source-label regression test",
                      False, f"version={installed_version.stdout.strip()}, layout={actual_hypr_defaults}")
            check("O20 labels Ask from the user plugin as custom and keeps its action wording neutral",
                  "[Your custom/changed shortcut]" in ask_line and
                  "[Omarchy default]" not in ask_line and
                  "Was: Open Ask." in ask_line and "Omarchy Ask" not in report and
                  "these Omarchy actions" not in report and
                  "Was: Run an Omarchy shortcut function." not in report,
                  report)
            check("O20 report records the displaced center-window shortcut and its reason",
                  "## Used by Scottland Window mode" in report and
                  "Alt+Tab — Was: Open Ask. Now:" in report and
                  "Scottland's Window mode previews the next center window" in report and
                  "these keys to focus center windows while keeping the others visible" in report,
                  report)
            check("O20 labels a changed default shortcut from the live/default comparison",
                  "Alt+Tab — Was: Open Ask." in report and
                  "[Your custom/changed shortcut]" in next(
                      line for line in report.splitlines() if "Alt+Tab — Was: Open Ask." in line),
                  report)
            default_line = next(line for line in report.splitlines()
                                if "Super+1 — Was: Workspace 1." in line)
            check("O20 labels an unchanged Omarchy shortcut as a default",
                  "[Omarchy default]" in default_line and
                  "[Your custom/changed shortcut] came from your settings or a user-installed plugin" in report,
                  report)
            check("O20 treats a changed shortcut option as a user change",
                  "[Your custom/changed shortcut]" in next(
                      line for line in report.splitlines() if "Super+2 — Was: Workspace 2." in line),
                  report)
            default_only_report = importer.make_report([{
                "heading": "Scottland has no workspaces",
                "why": "Scottland has no workspaces, so workspace shortcuts are unavailable.",
                "keys": "Super+1", "was": "Workspace 1", "now": "No shortcut in Scottland",
                "origin": "Omarchy default", "order": 1,
            }])
            default_only_prompt = importer.render_override_prompt(report_path, default_only_report)
            check("O20 says no own shortcuts were found when only defaults are affected",
                  "## Your own shortcuts that don't work in Scottland" in default_only_report and
                  "None found." in default_only_prompt and
                  "Super+1 — Was: Workspace 1." not in default_only_prompt,
                  default_only_prompt)
            configured_omarchy = os.environ["OMARCHY_PATH"]
            os.environ["OMARCHY_PATH"] = str(temp / "missing-omarchy-defaults")
            generate((str(base_source),))
            incomplete_report = report_path.read_text()
            plugin_workspace_line = next(line for line in incomplete_report.splitlines()
                                         if "Super+3 — Was: Workspace 3." in line)
            check("O20 marks ordinary sources unverified but still recognizes user-plugin bindings",
                  "[Source not verified]" in incomplete_report and
                  "[Your custom/changed shortcut]" in plugin_workspace_line and
                  "[Omarchy default]" not in plugin_workspace_line,
                  incomplete_report)
            os.environ["OMARCHY_PATH"] = configured_omarchy
            generate((str(base_source),))
            report = report_path.read_text()
            check("O20 report explains the user-plugin Ask Alt-only action becoming Window mode",
                  "Alt — Was: Open Ask on Alt hold. Now:" in report and
                  "Holding Alt now enters Scottland's Window mode" in report and
                  "Window mode uses Alt alone to show hints and these keys to focus center windows" in report,
                  report)
            check("O20 report composes the core reason for Scottland Settings",
                  "Super+, — Was: Open Ask settings. Now: Opens Scottland Settings." in report and
                  "Now: Opens Scottland Settings." in report and
                  "Opens the desktop controls for spatial layout, Goo, and Window mode." in report,
                  report)
            original_path = os.environ["PATH"]
            os.environ["PATH"] = ""
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    generate(("--show-pending",))
            finally:
                os.environ["PATH"] = original_path
            check("O20 missing launch tools leave the report pending",
                  not seen_path.exists() and not launch_log.exists() and not agent_log.exists())
            check("O20 report records app-specific remap reservations",
                  "## App-specific remaps" in report and
                  "Ctrl+W — Was: Close tab." in report and
                  "Scottland omits this imported shortcut globally; Ctrl+W sends Ctrl+Backspace" in report and
                  "Ctrl+Alt+W — Was: Close browser window." in report and
                  "Ctrl+Alt+W sends Ctrl+F4" in report and
                  "intended editing effect in matching apps" in report, report)
            check("O20 groups workspace, layout, navigation, group, unsupported and close omissions",
                  "Was: Workspace 1." in report_group(report, "Scottland has no workspaces") and
                  "Was: Workspace 2." in report_group(report, "Scottland has no workspaces") and
                  "Was: Move window to workspace 1." in report_group(report, "Scottland has no workspaces") and
                  "Super+Shift+3 — Was: Switch to workspace 3." in
                  report_group(report, "Scottland has no workspaces") and
                  report.count("## Scottland has no workspaces") == 1 and
                  "Scottland arranges windows spatially by moving and scaling them" in
                  report_group(report, "Scottland does its own window layout (no tiling)") and
                  "Super+L — Was: Cycle tiling layout." in
                  report_group(report, "Scottland does its own window layout (no tiling)") and
                  "Super+J — Was: Focus next window." in
                  report_group(report, "Scottland uses its own window navigation") and
                  "Super+F — Was: Focus the window to the left." in
                  report_group(report, "Scottland uses its own window navigation") and
                  "Super+G — Was: Toggle window group." in
                  report_group(report, "Scottland has no window groups") and
                  "Super+V — Was: Toggle the window split layout." in
                  report_group(report, "Scottland does its own window layout (no tiling)") and
                  "Super+K — Was: Run a shortcut function." in report_group(report, "Unsupported in Scottland") and
                  "Shift+F4 — Was: Release action with modifier." in
                  report_group(report, "Unsupported in Scottland") and
                  "Super+Nosuchkey — Was: Unsupported key name." in
                  report_group(report, "Unsupported in Scottland") and
                  "Super+Y — Was: Unsupported Hyprland action." in
                  report_group(report, "Unsupported in Scottland") and
                  "## Scottland leaves Super+W unbound" in report and
                  "Super+W — Was: Close window." in report, report)
            report_headings = [line for line in report.splitlines() if line.startswith("## ")]
            check("O20 emits one heading for each report reason",
                  len(report_headings) == len(set(report_headings)), report)
            check("O9 Super+W is not generated as a single-press close binding",
                  "close_top_view = <super> KEY_W" not in generated and
                  "O9: close remains unbound" in generated, generated)
            first_mtime = report_path.stat().st_mtime_ns if report_path.exists() else 0
            generate((str(base_source),))
            check("O20 identical report generation preserves the stable file",
                  report_path.stat().st_mtime_ns == first_mtime and
                  "Generated:" not in report_path.read_text(), report_path.read_text())
            check("O20 startup chooses the selected coding agent instead of the editor",
                  generate(("--show-pending",)) == "" and wait_prompt_count(agent_log, 1) and
                  not launch_log.exists(),
                  agent_log.read_text() if agent_log.exists() else "no agent prompt")
            prompt = agent_log.read_text().split("__PROMPT_END__", 1)[0]
            rendered_prompt = prompt
            check("O20 prompt names the report and gives the plain-language opening",
                  str(report_path) in prompt and "user may not know what happened and may not be technical" in prompt and
                  "short, plain-words explanation" in prompt and
                  "opened on its own" in prompt and
                  "shortcuts currently set up on this computer" in prompt and
                  "Avoid jargon unless the user asks" in prompt and
                  "Avoid overwhelming the user with multiple groups at once" in prompt,
                  prompt)
            check("O20 prompt embeds custom shortcuts first, explains them, and keeps defaults brief",
                  "The user's own custom or changed shortcuts" in prompt and
                  "lead with these own shortcuts, one at a time" in prompt and
                  "Super+3 — Was: Workspace 3." in prompt and
                  "Alt+Tab — Was: Open Ask." in prompt and
                  "Why: Scottland keeps every window on one spatial desktop" in prompt and
                  "Explain each one's reason in simple words" in prompt and
                  "Briefly summarize affected Omarchy defaults from the rest of the report afterward" in prompt and
                  "Super+1 — Was: Workspace 1." not in prompt,
                  prompt)
            check("O20 prompt covers safe Scottland-only remapping and installed guidance",
                  "[Your custom/changed shortcut]" in prompt and
                  "~/.config/scottland/overrides.ini" in prompt and
                  "verify that key is free" in prompt and
                  "Never edit Hyprland or Omarchy files without the user's explicit OK" in prompt and
                  "/usr/share/scottland/agents/skills/scottland/SKILL.md" in prompt and
                  "linked into your agent's skills" in prompt and
                  "core/autostart.d/" not in prompt and "docs/" not in prompt,
                  prompt)
            generate(("--show-pending",))
            check("O20 does not reopen content already shown", prompt_count(agent_log) == 1)
            check("O20 records the report content that was shown",
                  seen_path.read_text().strip() == hashlib.sha256(report_path.read_bytes()).hexdigest(),
                  seen_path.read_text() if seen_path.exists() else "missing seen digest")

            # A changed live shortcut set is shown by a session-time config regeneration.
            hypr.write_text(hypr.read_text() +
                            'hl.bind("ALT+SHIFT+TAB", hl.dsp.exec_cmd("prev"), {description = "Previous Ask"})\n')
            os.environ["WAYFIRE_SOCKET"] = "/test/session.sock"
            generate((str(base_source),))
            check("O20 changed shortcut content opens once during a session",
                  wait_prompt_count(agent_log, 2), agent_log.read_text())
            generate((str(base_source),))
            check("O20 unchanged session rebuild does not reopen the report",
                  prompt_count(agent_log) == 2, agent_log.read_text())

            # An empty selected-agent value uses Omarchy's editor launcher.
            os.environ["SCOTTLAND_DEFAULT_AGENT"] = ""
            hypr.write_text(hypr.read_text() +
                            'hl.bind("ALT+TAB", hl.dsp.exec_cmd("ask"), {description = "Open Ask changed"})\n')
            generate((str(base_source),))
            check("O20 falls back to the editor when no default agent is selected",
                  launcher_count(launch_log, 1) and prompt_count(agent_log) == 2,
                  launch_log.read_text() if launch_log.exists() else "no editor fallback")
            generate((str(base_source),))
            check("O20 does not reopen fallback content without another report change",
                  len(launch_log.read_text().splitlines()) == 1)

            # Each missing Omarchy agent tool falls back to the editor independently.
            no_selector_bin = temp / "agent-prompt-only-bin"
            no_selector_bin.mkdir()
            (no_selector_bin / "omarchy-agent-prompt").symlink_to(fake_agent_prompt)
            (no_selector_bin / "omarchy-launch-editor").symlink_to(fake_editor)
            report_path.write_text(report_path.read_text() + "\n# missing default-agent tool\n")
            os.environ["SCOTTLAND_DEFAULT_AGENT"] = "codex-test"
            os.environ["PATH"] = str(no_selector_bin)
            generate(("--show-pending",))
            check("O20 falls back when omarchy-default-agent is missing",
                  launcher_count(launch_log, 2) and prompt_count(agent_log) == 2,
                  launch_log.read_text())

            no_prompt_bin = temp / "selector-only-bin"
            no_prompt_bin.mkdir()
            (no_prompt_bin / "omarchy-default-agent").symlink_to(fake_selector)
            (no_prompt_bin / "omarchy-launch-editor").symlink_to(fake_editor)
            report_path.write_text(report_path.read_text() + "\n")
            os.environ["PATH"] = str(no_prompt_bin)
            generate(("--show-pending",))
            check("O20 falls back when omarchy-agent-prompt is missing",
                  launcher_count(launch_log, 3) and prompt_count(agent_log) == 2,
                  launch_log.read_text())

            # With neither agent tool available, a report still uses the editor.
            isolated_bin = temp / "editor-only-bin"
            isolated_bin.mkdir()
            (isolated_bin / "omarchy-launch-editor").symlink_to(fake_editor)
            report_path.write_text(report_path.read_text() + "\n# both agent tools missing\n")
            os.environ["PATH"] = str(isolated_bin)
            generate(("--show-pending",))
            check("O20 falls back to the editor when both agent tools are missing",
                  launcher_count(launch_log, 4) and prompt_count(agent_log) == 2,
                  launch_log.read_text())
            os.environ["PATH"] = original_path
            os.environ["SCOTTLAND_DEFAULT_AGENT"] = ""

            # Flavorings can append stable entries without changing the generator.
            dropin = hooks / "override-report.d/gooarchy.txt"
            dropin.write_text("## Gooarchy flavorings\n"
                              "Reason: Gooarchy supplies its own widget controls for the desktop.\n\n"
                              "- Keys: Alt+Space\n"
                              "  Was: Open Ask\n"
                              "  Now: Opens Gooarchy's widget controls.\n")
            second_dropin = hooks / "override-report.d/gooarchy-extra.txt"
            second_dropin.write_text("## Gooarchy flavorings\n"
                                     "Reason: Gooarchy supplies its own widget controls for the desktop.\n\n"
                                     "- Keys: Super+G\n"
                                     "  Was: Open Gooarchy widget list\n"
                                     "  Now: Opens Gooarchy's widget controls.\n")
            generate((str(base_source),))
            report = report_path.read_text()
            check("O20 flavoring entries with one reason share a heading and trigger one display",
                  "## Gooarchy flavorings" in report and
                  "Alt+Space — Was: Open Ask. Now: Opens Gooarchy's widget controls." in report and
                  "Super+G — Was: Open Gooarchy widget list. Now: Opens Gooarchy's widget controls." in report and
                  "[Gooarchy flavoring]" in next(
                      line for line in report.splitlines() if "Alt+Space — Was: Open Ask." in line) and
                  report.count("## Gooarchy flavorings") == 1 and
                  launcher_count(launch_log, 5), report)

            # Setup calls the same generator and agent prompt in an isolated home.
            install_home = temp / "install-home"
            install_home.mkdir()
            install_log = temp / "install-launches.txt"
            install_agent_log = temp / "install-agent-prompts.txt"
            install_env = test_env(install_home, hooks, install_log, fake_bin)
            install_env["SCOTTLAND_AGENT_PROMPT_LOG"] = str(install_agent_log)
            install_env["SCOTTLAND_DEFAULT_AGENT"] = "codex-test"
            install_env["OMARCHY_PATH"] = str(defaults)
            install_env["SCOTTLAND_OMARCHY_THEMES_DIR"] = str(temp / "themes")
            for theme in ("watercolor-dream-light", "watercolor-dream-dark"):
                (temp / "themes" / theme).mkdir(parents=True)
            completed = subprocess.run([str(setup)], env=install_env, capture_output=True, text=True, timeout=10)
            install_report = Path(install_env["XDG_STATE_HOME"]) / "scottland/omarchy-overrides.txt"
            check("O20 setup generates and opens the report at install",
                  completed.returncode == 0 and install_report.is_file() and
                  "This report lists shortcuts and mappings from the live Omarchy configuration" in install_report.read_text() and
                  wait_prompt_count(install_agent_log, 1) and not install_log.exists(),
                  completed.stdout + completed.stderr)
            subprocess.run([str(setup)], env=install_env, capture_output=True, text=True, timeout=10)
            check("O20 repeated setup does not reopen the same report",
                  prompt_count(install_agent_log) == 1,
                  install_agent_log.read_text() if install_agent_log.exists() else "no agent prompt")
            print("\n--- rendered O20 agent prompt ---", flush=True)
            print(rendered_prompt, end="", flush=True)
            sample_top = sample_report.splitlines()
            next_group = next((index for index, line in enumerate(sample_top)
                               if line.startswith("## ") and
                               line != "## Your own shortcuts that don't work in Scottland" and
                               index > sample_top.index("## Your own shortcuts that don't work in Scottland")),
                              len(sample_top))
            print("\n--- sample O20 report top ---", flush=True)
            print("\n".join(sample_top[:next_group]), flush=True)
        finally:
            os.environ.clear()
            os.environ.update(old_env)
    print(f"omarchy override report: {passes} passed, {failures} failed", flush=True)
    return failures


if __name__ == "__main__":
    raise SystemExit(main())
