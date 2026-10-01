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

## Bring a window to the user

`scottland-ctl present <id>` (IPC `scottland/present {window}`) shows a window now: a widget opens
back into its window, a window at the side flies to the middle at 100%, and either way it's raised
and focused. A window already in the center zone stays put. Use it when the user picks a window
(from a launcher, a list of agents); a plain focus request moves nothing.

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

Dragging a window onto a screen-edge rail turns it into a **widget**: the window hides and a
widget program stands in for it, at 100%, where it was dropped. Dragging the widget off the rail
(so no part of it is on the rail) brings the window back; closing either closes both. Full design: Scottland's `docs/widgets.md`.

**Choose a widget for an app** in `~/.config/scottland/widgets.ini` (create it if missing):

```ini
[widgets]
# app-id (or .desktop id) = widget id
org.gnome.Nautilus = card
```

Otherwise the app's own widget is used (its `.desktop` entry's `X-Scottland-Widget=`, or a
package claiming its app-id), else the default **card** (icon, title, alert badge).

**Make a widget**: a folder in `~/.local/share/scottland/widgets/<name>/` with `widget.toml`:

```toml
id = "my-widget"
name = "My widget"
apps = ['^org\.example\.App$']      # app-id regexes it's for (optional)
exec = "quickshell -p %d/shell.qml"  # any program; %d = this folder
touch_drag = true   # optional: a finger drag anywhere moves it (only if it drags nothing itself)
```

A widget is any program (QML via Quickshell, GTK, a web view, a TUI...), fully interactive, with
the user's normal access (files, network, D-Bus). It gets the window's identity in its
environment: `SCOTTLAND_WIDGET_APP_ID`, `_ICON`, `_NAME`, `_DESKTOP`, `_PID` (the app's
process), `_WINDOW`, `_ID`, `_STATE` (a complete JSON presentation snapshot, written atomically
before the widget starts and replaced live: identity, title, rail, minimized, badge, focus,
urgency, data, model version and presentation revision), and `SCOTTLAND_PALETTE` (a JSON file with the desktop's colors: `scheme`,
`background`, `foreground`, `muted`, `accent`, `alert`, kept current). Placeholders in `exec`: `%a` app-id, `%t` title, `%i` icon, `%p` pid, `%w`
window, `%r` rail, `%d` folder. Live properties and `Restore()`/`Close()`/`Focus()` are on D-Bus at
`org.scottland.Widgets /org/scottland/widget/<id>` (interface `org.scottland.Widget`).

The window turns into its widget while it's dragged onto the rail (and back when dragged off);
Esc cancels a drag. A widget whose app needs attention (bell, notification) gets a breathing halo
in the theme's attention color (the Omarchy theme's yellow; it follows theme changes).

For reactive IPC, `scottland/subscribe {"slice":"widgets"}` returns the complete current slice
immediately and then full `scottland-widgets#` events. Replace your copy for each newer version;
never merge fields. Slices `desktop` and `attention` provide the full model or attention source
sets. `session` identifies the compositor; a new session replaces everything. `builtin:` attention
source names are reserved for plugin inputs.

Troubleshooting: `~/.local/state/scottland/widgets.log` says which widget was chosen and why.

## Attention

A window (or its widget) whose app needs the user gets a breathing halo in the attention color,
until the user goes to it. Built in: an app's bell or focus request, its urgency hint, a desktop
notification from its own process. **Add a source** (another program that knows which windows need
the user) with a file `~/.config/scottland/attention.d/<name>.ini`, then run `scottland-reload`:

```ini
[source]
list = some-command --json      # prints JSON listing the windows that need attention now
windows = items                 # where the list is in it ("a.b" for nested; empty = top level)
window = id                     # the field of each entry that names its window
format = id                     # id (Scottland window id) | pid | hex-offset:<base>
interval = 2                    # seconds between listings
watch = ~/.cache/x/state.json   # optional: re-read at once when this file changes
answered = some-command dismiss {id}   # optional: run when the user goes to it ({field})
```

Find window ids with `scottland-ctl windows`. A source only takes back its own attention.
Log: `~/.local/state/scottland/attention.log`.

## Don't

- Don't edit Scottland's shipped files or anything under `/usr/share/scottland` or `/usr/lib/scottland`.
- Don't restart the user's session to apply settings; `overrides.ini` applies live.
