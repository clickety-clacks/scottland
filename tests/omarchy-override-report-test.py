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
        fake_editor = fake_bin / "omarchy-launch-editor"
        fake_editor.write_text("#!/bin/sh\nprintf '%s\\n' \"$1\" >>\"$SCOTTLAND_REPORT_LAUNCH_LOG\"\n")
        fake_editor.chmod(0o755)
        hypr = home / ".config/hypr/hyprland.lua"
        hypr.parent.mkdir(parents=True)
        hypr.write_text('''
hl.bind("ALT+TAB", hl.dsp.exec_cmd("ask"), {description = "Open Ask"})
hl.bind("ALT", hl.dsp.exec_cmd("ask-hold"), {description = "Open Ask on Alt hold"})
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
hl.bind("SUPER+K", hl.dsp.window.tag({tag = "work"}), {description = "Tag window"})
hl.bind("SHIFT+F4", hl.dsp.exec_cmd("release-action"),
        {description = "Release action with modifier", release = true})
hl.bind("SUPER+NoSuchKey", hl.dsp.exec_cmd("unsupported-key"),
        {description = "Unsupported key name"})
hl.bind("SUPER+Y", hl.dsp.exec_cmd("hyprctl dispatch unsupported"),
        {description = "Unsupported Hyprland action"})
''')
        env = test_env(home, hooks, launch_log, fake_bin)

        old_env = os.environ.copy()
        try:
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
            check("O20 report records the displaced center-window shortcut and its reason",
                  "## Used by Scottland Window mode" in report and
                  "Alt+Tab — Was: Open Ask. Now:" in report and
                  "Scottland's Window mode previews the next center window" in report and
                  "these keys to focus center windows while keeping the others visible" in report,
                  report)
            check("O20 report explains an Omarchy Alt-only action becoming Window mode",
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
            check("O20 unavailable editor launcher leaves the report pending",
                  not seen_path.exists() and not launch_log.exists())
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
                  "Super+Shift+3 — Was: Switch or move windows between workspaces." in
                  report_group(report, "Scottland has no workspaces") and
                  report.count("## Scottland has no workspaces") == 1 and
                  "Scottland arranges windows spatially by moving and scaling them" in
                  report_group(report, "Scottland does its own window layout (no tiling)") and
                  "Super+L — Was: Cycle tiling layout." in
                  report_group(report, "Scottland does its own window layout (no tiling)") and
                  "Super+J — Was: Focus next window." in
                  report_group(report, "Scottland uses its own window navigation") and
                  "Super+F — Was: Navigate between windows." in
                  report_group(report, "Scottland uses its own window navigation") and
                  "Super+G — Was: Toggle window group." in
                  report_group(report, "Scottland has no window groups") and
                  "Super+V — Was: Change the window layout." in
                  report_group(report, "Scottland does its own window layout (no tiling)") and
                  "Super+K — Was: Tag window." in report_group(report, "Unsupported in Scottland") and
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
            check("O20 startup can show a pending report once",
                  generate(("--show-pending",)) == "" and launcher_count(launch_log, 1),
                  launch_log.read_text() if launch_log.exists() else "no launcher call")
            generate(("--show-pending",))
            check("O20 does not reopen content already shown", len(launch_log.read_text().splitlines()) == 1)
            check("O20 records the report content that was shown",
                  seen_path.read_text().strip() == hashlib.sha256(report_path.read_bytes()).hexdigest(),
                  seen_path.read_text() if seen_path.exists() else "missing seen digest")

            # A changed live shortcut set is shown by a session-time config regeneration.
            hypr.write_text(hypr.read_text() +
                            'hl.bind("ALT+SHIFT+TAB", hl.dsp.exec_cmd("prev"), {description = "Previous Ask"})\n')
            os.environ["WAYFIRE_SOCKET"] = "/test/session.sock"
            generate((str(base_source),))
            check("O20 changed shortcut content opens once during a session",
                  launcher_count(launch_log, 2), launch_log.read_text())
            generate((str(base_source),))
            check("O20 unchanged session rebuild does not reopen the report",
                  len(launch_log.read_text().splitlines()) == 2, launch_log.read_text())

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
                  report.count("## Gooarchy flavorings") == 1 and
                  launcher_count(launch_log, 3), report)

            # Setup calls the same generator and editor launcher in an isolated home.
            install_home = temp / "install-home"
            install_home.mkdir()
            install_log = temp / "install-launches.txt"
            install_env = test_env(install_home, hooks, install_log, fake_bin)
            install_env["SCOTTLAND_OMARCHY_THEMES_DIR"] = str(temp / "themes")
            for theme in ("watercolor-dream-light", "watercolor-dream-dark"):
                (temp / "themes" / theme).mkdir(parents=True)
            completed = subprocess.run([str(setup)], env=install_env, capture_output=True, text=True, timeout=10)
            install_report = Path(install_env["XDG_STATE_HOME"]) / "scottland/omarchy-overrides.txt"
            check("O20 setup generates and opens the report at install",
                  completed.returncode == 0 and install_report.is_file() and
                  "This report lists Omarchy shortcuts and mappings" in install_report.read_text() and
                  launcher_count(install_log, 1), completed.stdout + completed.stderr)
            subprocess.run([str(setup)], env=install_env, capture_output=True, text=True, timeout=10)
            check("O20 repeated setup does not reopen the same report",
                  len(install_log.read_text().splitlines()) == 1,
                  install_log.read_text() if install_log.exists() else "no launcher call")
            print("\n--- sample O20 report ---", flush=True)
            print(report_path.read_text(), end="", flush=True)
        finally:
            os.environ.clear()
            os.environ.update(old_env)
    print(f"omarchy override report: {passes} passed, {failures} failed", flush=True)
    return failures


if __name__ == "__main__":
    raise SystemExit(main())
