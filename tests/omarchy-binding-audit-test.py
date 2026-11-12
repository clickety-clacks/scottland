#!/usr/bin/env python3
"""Exercise every binding from the supported Omarchy source through Scottland's importer.

The CI checkout is pinned to OMARCHY_COMMIT. The Lua fixture loads Omarchy's real binding
aggregator and all conditional bindings; no compositor or installed Omarchy session is needed.
The TSV output is also the per-binding audit artifact for this test run.
"""

import argparse
import contextlib
import csv
import importlib.util
import io
import os
import re
import shlex
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path


OMARCHY_VERSION = "v4.5.0"
OMARCHY_COMMIT = "ac2690cd7be4f47e4a255f5e2e78d904d436b068"
REPO_ROOT = Path(__file__).resolve().parents[1]
IMPORTER_PATH = REPO_ROOT / "omarchy/config.d/50-omarchy-shortcuts"
SOURCE_SCAN_PATH = REPO_ROOT / "tests/fixtures/omarchy-bindings-scan.lua"
BASE_CONFIG = REPO_ROOT / "core/config/scottland.ini"
METADATA_DIR = REPO_ROOT / "core/plugin/metadata"
ALLOWED_DISPLACEMENT_HEADINGS = {
    "Scottland has no workspaces",
    "Scottland does its own window layout (no tiling)",
    "Scottland uses its own window navigation",
    "Scottland has no window groups",
    "Used by Scottland Window mode",
    "Used by Scottland widget controls",
    "Used by Scottland window controls",
    "Used by Scottland Settings",
    "Used by Scottland's reload action",
    "Used by a Scottland feature",
    "App-specific remaps",
    "Scottland leaves Super+W unbound",
}


def parse_ini(text):
    section = ""
    assignments = {}
    for line in text.splitlines():
        value = line.strip()
        if not value or value.startswith(("#", ";")):
            continue
        if value.startswith("[") and value.endswith("]"):
            section = value[1:-1].strip()
        elif "=" in value:
            option, setting = (part.strip() for part in value.split("=", 1))
            assignments[(section, option)] = setting
    return assignments


def scan_rows(output):
    rows = []
    for line in output.splitlines():
        if not line or line.startswith("@"):
            continue
        fields = (line.split("\t") + [""] * 13)[:13]
        mods, key, description, kind, action, repeat, release, locked, mouse, signature, options, plugin, ignore_mods = fields
        label = f"{mods.replace(' ', '+')}+{key}".lstrip("+")
        rows.append({
            "mods": mods,
            "key": key,
            "label": label,
            "description": description,
            "kind": kind,
            "action": action,
            "repeat": repeat,
            "release": release,
            "locked": locked,
            "mouse": mouse,
            "signature": signature,
            "options": options,
            "plugin": plugin,
            "ignore_mods": ignore_mods,
        })
    return rows


def semantic_scan_rows(rows):
    # The plugin bit describes where a live binding came from; it is not part of Omarchy's
    # default action. Everything else must match between the real config and the baseline scan.
    return Counter(tuple(row[key] for key in (
        "mods", "key", "description", "kind", "action", "repeat", "release", "locked",
        "mouse", "signature", "options", "ignore_mods")) for row in rows)


def expected_global_command(action):
    match = re.fullmatch(
        r'''hl\.dsp\.global\(\s*["']omarchy:([a-z]+)\.([A-Za-z0-9_.-]+)["']\s*\)''', action)
    if not match:
        return None
    kind, target = match.groups()
    if kind == "menu" and re.fullmatch(r"[A-Za-z0-9_-]+", target):
        return f"omarchy-menu toggle {shlex.quote(target)}"
    if kind == "panel" and re.fullmatch(r"[A-Za-z0-9_.-]+", target):
        return f"omarchy-shell shell toggle {shlex.quote(target)}"
    if kind == "audio" and target in {"raise", "lower", "mute-toggle"}:
        return f"omarchy-audio-output-volume {target}"
    if kind == "brightness":
        step = {"raise": "+5%", "lower": "5%-"}.get(target)
        return f"omarchy-brightness-display {step}" if step else None
    if kind == "ipc":
        target_name, separator, method = target.partition(".")
        if (separator and re.fullmatch(r"[A-Za-z0-9_-]+", target_name) and
                re.fullmatch(r"[A-Za-z0-9_.-]+", method)):
            return f"omarchy-shell {shlex.quote(target_name)} {shlex.quote(method)}"
    return None


def expected_route(row, importer):
    kind, action = row["kind"], row["action"]
    if row["release"] == "1" and kind not in ("exec", "function"):
        return "unsupported", "release binding action is not translated by the adapter"
    if kind == "exec":
        return ("unsupported", "Hyprland-only command") if importer.hyprland_only(action) else ("command", action)
    if kind == "function":
        suffix = ":release" if row["release"] == "1" else ""
        invocation = ('"${SCOTTLAND_HOOKS:-/usr/lib/scottland}"/libexec/scottland-lua-call '
                      + shlex.quote(f"{row['mods']}+{row['key']}{suffix}"))
        return "command", invocation
    if kind == "lua" and action.startswith("hl.dsp.global("):
        command = expected_global_command(action)
        return ("command", command) if command else ("unsupported", "untranslated Omarchy global action")
    if kind == "lua":
        for prefix, (section, option) in importer.DISPATCHERS.items():
            if action.startswith(prefix):
                return "dispatcher", section, option
        return "unsupported", "untranslated Lua action"
    return "unsupported", f"unrecognized action kind {kind or '(empty)'}"


def wanted_identity(row, importer):
    mods = row["mods"]
    if row["release"] == "1" and row["kind"] in ("exec", "function") and row["ignore_mods"] == "1":
        mods = ""
    return importer.normalized_combo(importer.combo(mods, row["key"]))


def command_result(row, expected, base_assignments, generated_assignments, importer):
    effective = dict(base_assignments)
    effective.update(generated_assignments)
    identity = wanted_identity(row, importer)

    switch = re.fullmatch(r"switch:(?:(on|off):)?(.+)", row["key"], re.IGNORECASE)
    if switch and not row["mods"]:
        wanted_state = (switch.group(1) or "toggle").lower()
        wanted_device = switch.group(2).strip()
        for (section, name), device in generated_assignments.items():
            match = re.fullmatch(r"switch_device_omarchy_(\d+)", name) if section == "scottland" else None
            if not match or device != wanted_device:
                continue
            index = match.group(1)
            state = generated_assignments.get(("scottland", f"switch_state_omarchy_{index}"))
            command = generated_assignments.get(("scottland", f"switch_command_omarchy_{index}"))
            if state == wanted_state and command == expected:
                return f"[scottland] switch_device_omarchy_{index}={device} ({state}) -> {command}"
        return None

    if row["release"] == "1" and row["kind"] in ("exec", "function"):
        for (section, name), keys in generated_assignments.items():
            match = re.fullmatch(r"shortcut_keys_omarchy_(\d+)", name) if section == "scottland" else None
            if not match or importer.normalized_combo(keys) != identity:
                continue
            index = match.group(1)
            if generated_assignments.get(("scottland", f"shortcut_release_omarchy_{index}")) != "true":
                continue
            command = generated_assignments.get(("scottland", f"shortcut_command_omarchy_{index}"))
            if command == expected:
                return f"[scottland] shortcut_keys_omarchy_{index}={keys} -> {command}"
        return None

    for (section, name), keys in effective.items():
        if section != "command" or not name.startswith(("binding_", "repeatable_binding_", "always_binding_")):
            continue
        suffix = name.split("binding_", 1)[1]
        command = effective.get(("command", f"command_{suffix}"))
        if command != expected:
            continue
        for key in keys.split("|"):
            if importer.normalized_combo(key.strip()) == identity:
                return f"[command] {name}={key.strip()} -> command_{suffix}={command}"

    return None


def dispatcher_result(row, section, option, generated_assignments, importer):
    wanted = wanted_identity(row, importer)
    value = generated_assignments.get((section, option))
    if not value:
        return None
    for key in value.split("|"):
        if importer.normalized_combo(key.strip()) == wanted:
            return f"[{section}] {option}={key.strip()}"
    return None


def skipped_entries(output):
    entries = []
    active = False
    pattern = re.compile(r"^(.+?)  (.*?)  \((.*)\)$")
    for line in output.splitlines():
        if line == "# Skipped:":
            active = True
            continue
        if not active or not line.startswith("#   "):
            continue
        match = pattern.fullmatch(line[4:])
        if match:
            entries.append({"label": match.group(1), "description": match.group(2), "reason": match.group(3)})
    return entries


def normalize_text(value):
    return " ".join(value.split()).strip().rstrip(".!? ").casefold()


def report_headings(report, importer):
    current = ""
    result = defaultdict(list)
    for line in report.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
        elif line.startswith("- ") and " — Was:" in line and " Now:" in line:
            keys, details = line[2:].split(" — Was:", 1)
            was = details.split(" Now:", 1)[0]
            result[(importer.report_identity(keys), normalize_text(was))].append(current)
    return result


def find_report_headings(row, importer, headings):
    identity = importer.report_identity(row["label"])
    exact = headings.get((identity, normalize_text(row["description"])), [])
    if exact:
        return exact
    # Some bindings have no description; use their key, but retain all headings so an
    # unsupported entry at the same key cannot be hidden by a different report row.
    return [heading for (key_identity, _was), values in headings.items()
            if key_identity == identity for heading in values]


def classify_rows(rows, importer, base_assignments, generated_assignments, output, report):
    skipped = defaultdict(list)
    for entry in skipped_entries(output):
        skipped[(entry["label"], entry["description"])].append(entry)
    headings = report_headings(report, importer)
    audit = []
    for row in rows:
        route = expected_route(row, importer)
        result = None
        if route[0] == "command":
            result = command_result(row, route[1], base_assignments, generated_assignments, importer)
        elif route[0] == "dispatcher":
            result = dispatcher_result(row, route[1], route[2], generated_assignments, importer)
        if result:
            audit.append({**row, "status": "works", "result": result, "reason": "Action is present on this binding."})
            continue

        candidates = skipped.get((row["label"], row["description"]), [])
        skip = candidates.pop(0) if candidates else None
        if not skip:
            audit.append({**row, "status": "broken", "result": "",
                          "reason": f"No generated action or Skipped entry; expected {route!r}."})
            continue

        report_groups = find_report_headings(row, importer, headings)
        allowed = [heading for heading in report_groups if heading in ALLOWED_DISPLACEMENT_HEADINGS]
        unsupported = [heading for heading in report_groups if heading == "Unsupported in Scottland"]
        if allowed and not unsupported:
            audit.append({**row, "status": "deliberately displaced", "result": "",
                          "reason": f"{skip['reason']}; O20: {', '.join(sorted(set(allowed)))}."})
        else:
            explanation = (f"{skip['reason']}; O20: {', '.join(sorted(set(report_groups)))}"
                           if report_groups else f"{skip['reason']}; no matching O20 entry")
            audit.append({**row, "status": "broken", "result": "", "reason": explanation})

    # Every skipped config row must correspond to a captured source binding. This catches any
    # unexpected importer output that cannot be represented in the per-binding report.
    leftovers = [entry for entries in skipped.values() for entry in entries]
    for entry in leftovers:
        audit.append({"mods": "", "key": entry["label"], "label": entry["label"],
                      "description": entry["description"], "kind": "", "action": "",
                      "repeat": "", "release": "", "locked": "", "status": "broken",
                      "result": "", "reason": f"Unmatched Skipped entry: {entry['reason']}"})
    return audit


def write_audit(path, rows, diagnostics, scottland_revision="unknown"):
    path.parent.mkdir(parents=True, exist_ok=True)
    counts = Counter(row.get("status", "broken") for row in rows)
    with path.open("w", encoding="utf-8", newline="") as output:
        output.write(f"# Supported Omarchy config: {OMARCHY_VERSION}\n")
        output.write(f"# Omarchy source commit: {OMARCHY_COMMIT}\n")
        output.write(f"# Scottland importer commit: {scottland_revision}\n")
        output.write(f"# Binding rows: {len(rows)}\n")
        output.write(f"# Works: {counts['works']}\n")
        output.write(f"# Deliberately displaced: {counts['deliberately displaced']}\n")
        output.write(f"# Broken: {counts['broken']}\n")
        for diagnostic in diagnostics:
            output.write(f"# ERROR: {diagnostic.replace(chr(10), ' ')}\n")
        writer = csv.writer(output, delimiter="\t", lineterminator="\n")
        writer.writerow(("keys", "description", "source", "action_kind", "action", "repeat",
                         "release", "locked", "mouse", "ignore_mods", "function_signature",
                         "options", "status", "imported_result", "classification"))
        for row in rows:
            writer.writerow((row.get("label", ""), row.get("description", ""), "Omarchy default",
                             row.get("kind", ""), row.get("action", ""), row.get("repeat", ""),
                             row.get("release", ""), row.get("locked", ""), row.get("mouse", ""),
                             row.get("ignore_mods", ""), row.get("signature", ""), row.get("options", ""),
                             row.get("status", "broken"), row.get("result", ""), row.get("reason", "")))
    return counts


def importer_module(home, omarchy_path):
    os.environ["HOME"] = str(home)
    os.environ["XDG_CONFIG_HOME"] = str(home / "config")
    os.environ["XDG_STATE_HOME"] = str(home / "state")
    os.environ["OMARCHY_PATH"] = str(omarchy_path)
    os.environ["SCOTTLAND_HOOKS"] = str(home / "hooks")
    os.environ["SCOTTLAND_TEST_MODEL"] = "1"
    os.environ["WAYFIRE_PLUGIN_XML_PATH"] = str(METADATA_DIR)
    os.environ.pop("WAYFIRE_SOCKET", None)
    spec = importlib.util.spec_from_file_location("scottland_omarchy_shortcuts", IMPORTER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load importer from {IMPORTER_PATH}")
    importer = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = importer
    spec.loader.exec_module(importer)
    importer.HYPR_CONFIG = str(home / ".config/hypr/hyprland.lua")
    return importer


def run(args):
    diagnostics = []
    audit = []
    omarchy_path = Path(args.omarchy_path).resolve()
    if not omarchy_path.is_dir():
        diagnostics.append(f"OMARCHY_PATH does not exist: {omarchy_path}")
        counts = write_audit(args.audit_output, audit, diagnostics)
        return 1, counts

    revision = subprocess.run(["git", "-C", str(omarchy_path), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=False)
    actual_revision = revision.stdout.strip() if revision.returncode == 0 else ""
    if actual_revision != OMARCHY_COMMIT:
        diagnostics.append(f"expected Omarchy {OMARCHY_VERSION} commit {OMARCHY_COMMIT}; got {actual_revision or revision.stderr.strip()}")
    scottland_result = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
                                      capture_output=True, text=True, check=False)
    scottland_revision = scottland_result.stdout.strip() if scottland_result.returncode == 0 else "unknown"
    if scottland_revision == "unknown":
        diagnostics.append(f"could not identify the Scottland revision: {scottland_result.stderr.strip()}")

    with tempfile.TemporaryDirectory(prefix="scottland-omarchy-binding-audit-") as scratch:
        home = Path(scratch)
        config = home / ".config/hypr/hyprland.lua"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(f'''local root = {str(omarchy_path)!r}
package.path = root .. "/?.lua;" .. root .. "/?/init.lua;" .. package.path
_G.omarchy_preinstalled_bindings = true
_G.omarchy_default_bindings = true
require("default.hypr.helpers")
-- Include optional upstream bindings in the audit even though CI does not install Omarchy apps.
o.cmd_present = function() return true end
require("default.hypr.bindings")
''', encoding="utf-8")
        importer = importer_module(home, omarchy_path)
        live = importer.run_lua_scan()
        baseline = importer.run_lua_scan(baseline=True)
        for label, result in (("real Omarchy config scan", live), ("Omarchy default scan", baseline)):
            if result.returncode != 0 or result.stderr.strip():
                diagnostics.append(f"{label} failed (exit {result.returncode}): {result.stderr.strip() or '(no stderr)'}")
        rows = scan_rows(live.stdout)
        default_rows = scan_rows(baseline.stdout)
        source_scan = subprocess.run(["lua", str(SOURCE_SCAN_PATH)], capture_output=True,
                                     text=True, check=False, env=os.environ.copy())
        if source_scan.returncode != 0 or source_scan.stderr.strip():
            diagnostics.append("independent Omarchy binding inventory failed "
                               f"(exit {source_scan.returncode}): {source_scan.stderr.strip() or '(no stderr)'}")
        source_rows = scan_rows(source_scan.stdout)
        if not rows:
            diagnostics.append("the real Omarchy binding aggregator produced no bindings")
        if semantic_scan_rows(rows) != semantic_scan_rows(default_rows):
            diagnostics.append(f"live and baseline binding inventories differ ({len(rows)} live, {len(default_rows)} baseline)")
        if semantic_scan_rows(rows) != semantic_scan_rows(source_rows):
            diagnostics.append(f"Scottland's live scan differs from the independent Omarchy inventory "
                               f"({len(rows)} imported, {len(source_rows)} upstream)")

        stdout = io.StringIO()
        original_argv = sys.argv
        try:
            sys.argv = [str(IMPORTER_PATH), str(BASE_CONFIG)]
            with contextlib.redirect_stdout(stdout):
                importer.main()
        except SystemExit as error:
            diagnostics.append(f"importer exited {error.code}")
        finally:
            sys.argv = original_argv

        generated = stdout.getvalue()
        report_path = Path(os.environ["XDG_STATE_HOME"]) / "scottland/omarchy-overrides.txt"
        if not report_path.is_file():
            diagnostics.append("the importer did not create its Omarchy override report")
            report = ""
        else:
            report = report_path.read_text(encoding="utf-8")
        base_assignments = parse_ini(BASE_CONFIG.read_text(encoding="utf-8"))
        generated_assignments = parse_ini(generated)
        audit = classify_rows(rows, importer, base_assignments, generated_assignments, generated, report)
        for diagnostic in diagnostics:
            print(f"ERROR: {diagnostic}", file=sys.stderr)

    counts = write_audit(args.audit_output, audit, diagnostics, scottland_revision)
    print(f"Omarchy {OMARCHY_VERSION}: {len(audit)} bindings audited; "
          f"{counts['works']} work, {counts['deliberately displaced']} deliberately displaced, "
          f"{counts['broken']} broken. Report: {args.audit_output}")
    return (1 if diagnostics or counts["broken"] else 0), counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--omarchy-path", default=os.environ.get("OMARCHY_PATH"),
                        help="checkout of the pinned Omarchy source (defaults to OMARCHY_PATH)")
    parser.add_argument("--audit-output", type=Path,
                        default=Path("build/omarchy-binding-audit-v4.5.0.tsv"),
                        help="write the complete per-binding audit TSV here")
    args = parser.parse_args()
    if not args.omarchy_path:
        parser.error("--omarchy-path or OMARCHY_PATH is required")
    status, _counts = run(args)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
