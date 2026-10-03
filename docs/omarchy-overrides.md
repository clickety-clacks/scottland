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

Each entry states the keys, what the imported shortcut did, what happens now, and why. This covers
feature-key conflicts and active app-specific `key_remaps` (including reservations that do not
collide with an imported shortcut), Omarchy shortcuts the adapter cannot translate (O4), and
Super+W close, which remains unbound so it cannot become an immediate single-press close (O9). Core
metadata and comments provide short, generic feature reasons; the adapter combines them with the
imported shortcut description. Core contains no Omarchy-specific names (C4).

## Adding flavoring overrides

Gooarchy flavorings is empty today. When it adds an Omarchy override, it can add a `.txt` fragment
under `$SCOTTLAND_HOOKS/override-report.d/` (for a package this is
`/usr/lib/scottland/override-report.d/`) or the user's
`$XDG_CONFIG_HOME/scottland/override-report.d/`. The adapter appends fragments in sorted path order.
Each fragment contains one or more entries in this plain-text format:

```text
- Keys: Alt+Space
  Was: Open Ask
  Now: Open Scottland's Window mode hints.
  Why: Window mode keeps windows visible while you choose what to focus.
```

Fragments should be stable text without timestamps. Their contents are included in the same report
and change detection as adapter-generated entries, so a changed fragment is shown once.
