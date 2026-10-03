# Scottland

A spatial desktop for Linux, built on [Wayfire](https://wayfire.org/). It implements
Scott Jenson's "working memory" desktop concept from his KDE Akademy 2026 talk,
[Are we really going to use the same Desktop UX forever?](https://media.ccc.de/v/kde2026-7-are_we_really_going_to_use_the_same_desktop_ux_forever),
with his blessing. Scottland is an independent project, not his.

## The idea

- The middle third of the screen is a dead zone where windows appear at full scale.
- Drag a window left or right of it and it scales down smoothly, smaller the farther it goes.
  Scaled windows stay fully interactive.
- Snap a window to a side rail and it becomes a widget: the app renders a widget form if it
  supports one, otherwise it stays as a small scaled window.
- Tiling rules apply. No workspaces, no rotation.

## Layout

| Path | Package | What |
|---|---|---|
| `core/plugin/` | `scottland` | Wayfire layout plugin (C++, meson) |
| `core/config/` | `scottland` | Default Wayfire config |
| `core/session/` | `scottland` | `start-scottland` launcher and login-session entry |
| `omarchy/shim/` | `scottland-omarchy` | `scottland-hyprshim`: serves Hyprland's IPC sockets from Wayfire's IPC so the stock Omarchy shell, `hyprctl` and Quickshell run unmodified. Unsupported requests are logged to `~/.local/state/scottland/hyprshim.log` |
| `omarchy/shell-plugins/` | `scottland-omarchy` | Replacements for Hyprland-only shell plugins (workspaces, displays) |
| `omarchy/bin/` | `scottland-omarchy` | `scottland-switch` and setup for per-user menu entries and default themes |
| `omarchy/themes/` | `scottland-omarchy` | Watercolor Dream Light and Dark Omarchy themes, installed per user when their names are available |
| `omarchy/switch-dialog/` | `scottland-omarchy` | The Switch Desktop dialog (Quickshell, Omarchy theme) |
| `omarchy/helper/` | `scottland-omarchy` | Root helper + polkit rule: sets the autologin session, starts sessions on their own VT |
| `omarchy/hooks/` | `scottland-omarchy` | Crash guard: a Scottland session that dies in seconds boots Hyprland next time |
| `packaging/` | | Arch PKGBUILDs now; other distros later |

Rule: nothing under `core/` knows Omarchy exists. Omarchy integration lives only in `omarchy/`.

## Running it

On Omarchy, install `scottland-omarchy`, run `scottland-omarchy-setup` once, then use
Omarchy menu > System > Switch Desktop (Super+Escape inside Scottland). The dialog's
"Close … when switching" checkbox (on by default) ends the current session; unchecked, both
sessions keep running on separate VTs, and some apps may not work correctly. The machine
boots into whichever session was used last.

The adapter opens a plain-text report when it changes or cannot import shortcuts. By default it
stays at `~/.local/state/scottland/omarchy-overrides.txt` (under `$XDG_STATE_HOME` when set); see
[the report format and flavoring hook](docs/omarchy-overrides.md).

By hand, from a text console (for example Ctrl+Alt+F3): `start-scottland`.

Quit with Ctrl+Alt+Backspace or Super+Shift+Escape. Super+Enter opens a terminal.

## Testing

    tests/shell-probe.sh 25                                         # stock shell, no shim: logs every Hyprland call
    PROBE_SHIM=omarchy/shim/scottland-hyprshim tests/shell-probe.sh 25   # same, answered by the shim

The probe runs headless and sandboxed (own D-Bus, sandbox HOME, service commands stubbed out).

## Development

    make dev-install    # build the plugin; symlink plugin, config and launcher from this repo
    make package        # build and install the Arch package

Wayfire plugins only load into the Wayfire version they were built against, so rebuild after
Wayfire updates.
