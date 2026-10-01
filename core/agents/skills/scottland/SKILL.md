---
name: scottland
description: Configure Scottland, the spatial desktop (windows scale toward the screen edges; no workspaces). Use for any change to how Scottland behaves for the user — per-app touchscreen scrolling, input and layout settings, per-app key remaps — and to find an app's app-id. Triggers: Scottland, touchscreen scrolling in an app, "this app doesn't scroll with my finger", window scaling zones, halos, rail widgets.
---

# Scottland

Scottland is a spatial desktop on Wayfire: a full-size center zone, windows shrinking toward
the screen edges, widget rails at the edges, and no workspaces. Use this skill to change how it
behaves for the user.

## Where settings live

| File | Who writes it | Notes |
|---|---|---|
| `~/.config/scottland/overrides.ini` | The user (and you, on their behalf) | Wayfire-ini format. Applied last, so it wins over everything. Scottland never writes it. |
| `~/.config/scottland/layout.ini` | The Scottland settings app | Zone widths and the scale curve. Prefer the app (`scottland-settings`). |
| Scottland's shipped config | The package | Defaults. Never edit it; override in `overrides.ini`. |

Changes to `overrides.ini` apply to the running session automatically within a second or two
(Scottland rebuilds its config when the file changes). No restart or reload needed.

Put settings under the section they belong to, e.g. `[scottland]` or `[input]`. Only add the
keys you're changing; keep the user's existing lines.

## Find an app's app-id

Per-app settings match the app-id. List what's open:

```bash
scottland-ctl windows
```

The first column is the app-id (e.g. `com.mitchellh.ghostty`, `foot`, `chromium`).

## Touchscreen scrolling for apps that ignore touch

Many apps scroll with a finger on their own (browsers, most GTK and Qt apps). Some don't, notably
terminals: a finger does nothing in them. For apps listed here, Scottland turns a one-finger drag
into smooth scrolling (with momentum after a flick) and a quick tap into a click. A long press
still lifts the window to move it.

Entries are `touch_scroll_<name> = <regex>` in `[scottland]`, matched case-insensitively against
the app-id. Scottland ships one entry:

```ini
touch_scroll_terminals = ^(com\.mitchellh\.ghostty|foot|footclient|Alacritty|kitty|org\.wezfurlong\.wezterm)$
```

In `~/.config/scottland/overrides.ini`:

```ini
[scottland]
# Add an app (any name after touch_scroll_; one entry per app or group):
touch_scroll_editors = ^(dev\.zed\.Zed|org\.gnome\.TextEditor)$

# Change a shipped entry: give the same name a new value.
touch_scroll_terminals = ^(com\.mitchellh\.ghostty|foot)$

# Switch a shipped entry off: give the same name an empty value.
touch_scroll_terminals =
```

Don't add apps that already handle touch (Chromium, Firefox, GTK4/Qt apps): they would scroll
twice. Test by dragging a finger in the app; if it already scrolls without an entry, leave it out.

Check the live value:

```bash
scottland-ctl option scottland/touch_scroll
```

## Other common settings (`[scottland]` unless noted)

| Setting | What it does |
|---|---|
| `lift_delay = 350` | Milliseconds a finger must rest still on a window before it lifts to move. |
| `sounds = true` | Scottland's interface sounds (the lift "bloop"). |
| `touchpad_gestures = true` | Three-finger drag moves a window; three-finger click-drag resizes it. |
| `remap_apps_<n>` / `remap_from_<n>` / `remap_to_<n>` | Per-app key remaps, e.g. Ctrl+W → Ctrl+BackSpace in browsers. |
| `[input] natural_scroll`, `touchpad_scroll_speed`, `click_method`, `drag_lock` | Touchpad behavior (Wayfire input options). |

## Rail widgets

Not available yet. Windows dragged to the screen-edge rails currently stay as small windows.
A configurable widget system is being designed; don't invent settings for it.

## Don't

- Don't edit Scottland's shipped files or anything under `/usr/share/scottland` or `/usr/lib/scottland`.
- Don't restart the user's session to apply settings; `overrides.ini` applies live.
