# Omarchy override report

The Omarchy adapter writes a plain-text report to
`${XDG_STATE_HOME:-~/.local/state}/scottland/omarchy-overrides.txt`. It is a stable, diffable
snapshot of the shortcuts and mappings that Scottland changes or cannot import. The file remains
available to reread after the report window is closed.

## When the report is generated and shown

`scottland-omarchy-setup` generates and opens the report at install. The live shortcut generator
refreshes it at session start and after Hyprland configuration changes. At session start, an
autostart hook waits for the Hyprland shim and opens any unseen report once Scottland is running.
When the report changes during a session, the generator asks Omarchy's `omarchy-launch-editor` to
open it asynchronously. That launcher selects the user's Omarchy editor and opens terminal editors
through Omarchy's TUI terminal launcher, so starting the desktop never waits for the report window.

The adapter stores the SHA-256 digest of the last report it opened at
`${XDG_STATE_HOME:-~/.local/state}/scottland/omarchy-overrides.seen`. A session start with identical
content does not open another window. If the editor launcher is unavailable, the report stays on
disk and its digest is not marked as shown, so a later install or session can retry it.

## What it reports

The report groups entries by reason. Each group has one heading and one plain-language explanation;
each key line says what that shortcut did in Omarchy and what happens in Scottland. It includes
feature-key conflicts, active app-specific `key_remaps` (including reservations that do not collide
with an imported shortcut), and omissions (O4): window navigation, tiling/layout, workspace and
window-group shortcuts, plus any unsupported key or action. Super+W is included because it remains
unbound so a press cannot immediately close a window (O9). Core metadata and comments provide short,
generic reasons for Scottland features; the adapter groups them and adds the imported shortcut
description. Core contains no Omarchy-specific names (C4).

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
```

`Was` is the shortcut's Omarchy action; `Now` is its current Scottland or flavoring action. Keep
group explanations short and generic. Fragments are stable text without timestamps and participate
in the same report digest and open-once behavior. The earlier four-field form with a per-entry `Why`
is still accepted; those entries are grouped by their `Why` text.
