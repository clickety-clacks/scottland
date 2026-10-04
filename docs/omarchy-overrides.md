# Omarchy override report

The Omarchy adapter writes a plain-text report to
`${XDG_STATE_HOME:-~/.local/state}/scottland/omarchy-overrides.txt`. It is a stable, diffable
snapshot of the shortcuts and mappings that Scottland changes or cannot import. The file remains
available to reread after the report window is closed.

## When the report is generated and shown

`scottland-omarchy-setup` generates and opens the report at install. The live shortcut generator
refreshes it at session start and after Hyprland configuration changes. At session start, an
autostart hook waits for the Hyprland shim and opens any unseen report once Scottland is running.
When the report changes, the adapter asks `omarchy-default-agent` which coding agent the user chose
and passes a short prompt and the report path to `omarchy-agent-prompt`. It opens asynchronously in
Omarchy's terminal. If no default agent is selected or either agent tool is missing or cannot be
started, the adapter falls back to `omarchy-launch-editor`, which opens terminal editors through
Omarchy's TUI terminal launcher. Starting the desktop never waits for the window. The prompt template
is `omarchy/prompts/omarchy-overrides-agent.txt` in the source tree and
`/usr/lib/scottland/prompts/omarchy-overrides-agent.txt` after package installation.

The adapter stores the SHA-256 digest of the last report it opened at
`${XDG_STATE_HOME:-~/.local/state}/scottland/omarchy-overrides.seen`. A session start with identical
content does not open another window. If neither launcher can be started, the report stays on disk
and its digest is not marked as shown, so a later install or session can retry it.

## What it reports

The report groups entries by reason. Each group has one heading and one plain-language explanation;
each key line says what the shortcut did in the live Omarchy configuration and what happens in
Scottland. It includes feature-key conflicts, active app-specific `key_remaps` (including
reservations that do not collide with an imported shortcut), and omissions (O4): window navigation, tiling/layout, workspace and
window-group shortcuts, plus any unsupported key or action. Super+W is included because it remains
unbound so a press cannot immediately close a window (O9). Core metadata and comments provide short,
generic reasons for Scottland features; the adapter groups them and adds the imported shortcut
description. Core contains no Omarchy-specific names (C4).

Each key line also gives its source. `[Omarchy default]` means the live shortcut's key, action, and
relevant options match Omarchy's shipped defaults. `[Your custom/changed shortcut]` means the user
added that key or changed the action/options. Bindings loaded from the user's
`~/.config/omarchy/plugins/` are always labeled `[Your custom/changed shortcut]`, even if their
binding signature matches a shipped default. The adapter compares the live Hyprland Lua scan with
the binding modules under `$OMARCHY_PATH/default/hypr` (normally
`/usr/share/omarchy/default/hypr`) and tracks whether a live binding came from the user plugin
directory. Descriptions do not determine the match; plugin action descriptions are reported as
written, without presenting plugin features as Omarchy features. If the shipped-default scan is
unavailable, ordinary shortcuts say `[Source not verified]`, while user-plugin bindings remain
identified as custom. A key with no current Omarchy shortcut is labeled
`[No Omarchy shortcut on this key]`, or `[Gooarchy flavoring]` for a flavoring-only entry. A standalone
Scottland `key_remaps` entry is labeled `[Scottland app remap]` because it is an app mapping rather
than an Omarchy shortcut.

Scottland-side personal shortcut overrides live in
`~/.config/scottland/overrides.ini`, in Wayfire INI format. Scottland reads these last and never
writes them. Before suggesting a new key, check the shipped Scottland bindings and this file to make
sure the key is free. The prompted agent must ask before changing anything and must not edit Omarchy
or Hyprland files without the user's explicit OK.

## Adding flavoring overrides

Gooarchy flavorings is empty today. When it adds an Omarchy override, it can add a `.txt` fragment
under `$SCOTTLAND_HOOKS/override-report.d/` (for a package this is
`/usr/lib/scottland/override-report.d/`) or the user's
`$XDG_CONFIG_HOME/scottland/override-report.d/`. The adapter reads fragments in sorted path order.
Each fragment contains one or more reason groups. The adapter merges entries with the same heading
and explanation, so each reason appears only once in the report:

```text
## Gooarchy widget controls
Reason: Gooarchy uses this shortcut to open its widget controls.

- Keys: Alt+Space
  Was: Open Ask
  Now: Opens Gooarchy's widget controls.
  Origin: Gooarchy flavoring
```

`Was` is the action recorded for that key in the live configuration; `Now` is its current Scottland
or flavoring action. Keep group explanations short and generic. Fragments are stable text without
timestamps and participate in the same report digest and open-once behavior. The earlier four-field form with a per-entry `Why`
is still accepted; those entries are grouped by their `Why` text. `Origin` is optional; when omitted,
the adapter compares the key with the live Hyprland shortcut and labels a flavoring-only key as
`[Gooarchy flavoring]`.
