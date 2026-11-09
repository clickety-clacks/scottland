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
| `core/autostart.d/07-solar-theme`, `core/libexec/scottland-solar-theme` | `scottland-sunlight` | Standalone sunrise/sunset scheduler for the standard light/dark preference |
| `omarchy/shim/` | `scottland-omarchy` | `scottland-hyprshim`: serves Hyprland's IPC sockets from Wayfire's IPC so the stock Omarchy shell, `hyprctl` and Quickshell run unmodified. Unsupported requests are logged to `~/.local/state/scottland/hyprshim.log` |
| `omarchy/shell-plugins/` | `scottland-omarchy` | Replacements for Hyprland-only shell plugins (workspaces, displays) |
| `omarchy/bin/` | `scottland-omarchy` | `scottland-switch` and setup for per-user menu entries, default themes and the screen-sharing portal |
| `omarchy/themes/` | `scottland-omarchy` | Watercolor Dream Light and Dark Omarchy themes, installed per user when their names are available |
| `omarchy/switch-dialog/` | `scottland-omarchy` | The Switch Desktop dialog (Quickshell, Omarchy theme) |
| `omarchy/helper/` | `scottland-omarchy` | Root helper + polkit rule: sets the autologin session, ends one desktop and starts the other |
| `omarchy/hooks/` | `scottland-omarchy` | Crash guard: a Scottland session that dies in seconds boots Hyprland next time |
| `packaging/` | | Arch PKGBUILDs now; other distros later |

Rule: nothing under `core/` knows Omarchy exists. Omarchy integration lives only in `omarchy/`.

## Install and upgrade on Omarchy

The [Latest signed Gooarchy repository release](https://github.com/clickety-clacks/gooarchy/releases/latest)
includes the matched packages
`scottland` 0.3.7-1 and `scottland-omarchy` 0.3.7-1. The adapter requires the
matching core version. The associated `gooarchy-flavorings` package is 0.2.2-1.

1. Follow Gooarchy's canonical instructions for enrolling its signed
   repository and verifying its signing-key fingerprint in the
   [Gooarchy README](https://github.com/clickety-clacks/gooarchy/blob/main/README.md#enroll-and-trust-gooarchy-on-stock-omarchy).
   Gooarchy maintains the repository URL and key trust procedure; use its
   instructions rather than duplicating them here.
2. After enrolling Gooarchy and following its [supported Omarchy system update
   guidance](https://github.com/clickety-clacks/gooarchy/blob/main/README.md#refresh-the-system-through-omarchy),
   install the signed Scottland core, Omarchy adapter, and Gooarchy Flavorings
   together in one package-only transaction. Inspect the complete proposed
   transaction before accepting it:

   ```sh
   sudo pacman -S scottland scottland-omarchy gooarchy-flavorings
   ```
3. Verify the installed package versions:

   ```sh
   pacman -Q scottland scottland-omarchy gooarchy-flavorings
   ```

For later full-system upgrades, follow Gooarchy's [supported Omarchy system
update guidance](https://github.com/clickety-clacks/gooarchy/blob/main/README.md#refresh-the-system-through-omarchy).
To add these packages later, use the package-only transaction above.

A package upgrade does not replace code already loaded by a running Wayfire
session. Enter a new Scottland session to use the new package, or use
`scottland-reload` from that session when an in-place reload is appropriate. A
development plugin linked under `~/.local/share/scottland/dev` can take
precedence over `/usr/lib` on reload; remove or deliberately update that
development selection before attributing a running result to the newly
installed package. Keep your user config files when upgrading.

After installing or upgrading `scottland-omarchy`, rerun
`scottland-omarchy-setup` so the per-user menu, themes, shortcut report and
screen-sharing setup match the installed adapter. Read its output for the
result of the portal build and replacement described under Running it.

On a non-Omarchy Arch desktop, install only the `scottland` package from the
[Scottland 0.3.7 release](https://github.com/clickety-clacks/scottland/releases/tag/v0.3.7).
That release is separate from Gooarchy's signed repository.

If a prior `make dev-install` selected a development snapshot, inspect the
links under `~/.local/share/scottland/dev` and preserve any personal files
before changing them. From that checkout, `make dev-uninstall` removes its
development selection and several linked command/config paths, including
`~/.config/scottland/scottland.ini`; run it only when those paths belong to the
development install and you have saved anything personal. It does not uninstall
the Arch packages. Start a fresh Scottland session afterward to select the
packaged plugin.

After installing the packages, agents working on Scottland should install and read the
[Core Scottland skill](core/agents/skills/scottland/SKILL.md). On Omarchy, also
install and read the separate
[Scottland Omarchy adapter skill](omarchy/agents/skills/scottland-omarchy/SKILL.md).
For Gooarchy distribution or configuration work, also install and read the
[Gooarchy skill](https://github.com/clickety-clacks/gooarchy/blob/main/agents/skills/gooarchy/SKILL.md).
Their package directories are, respectively,
`/usr/share/scottland/agents/skills/scottland/`,
`/usr/share/scottland/agents/skills/scottland-omarchy/`, and
`/usr/share/gooarchy/agents/skills/gooarchy/`. When a package provides the
adapter skill in Scottland's shared skills directory, Scottland's login hook
links it into each installed agent harness alongside the Core skill at the
next fresh Scottland login. Confirm each applicable skill is available in
your agent's skills directory before using it; for Codex that directory is
`~/.codex/skills`.

## Running it

On Omarchy, after installing the packages, run `scottland-omarchy-setup` once.
To enable the generic Sunlight scheduler, also opt in to the separate
`scottland-sunlight` package; it is not included in the combined repository
transaction above.
Setup builds Gooarchy's `xdg-desktop-portal-wlr` from
[its fork](https://github.com/clickety-clacks/xdg-desktop-portal-wlr) with
`makepkg`, then replaces Arch's portal package through pacman. This is a
separate setup step after the repository package transaction; it may synchronize
build dependencies itself. Do not preinstall them separately.

Then choose **Omarchy menu > System > Switch Desktop** and select Scottland.
The dialog warns that switching closes the current desktop and all its windows,
so save your work first. The selected desktop becomes the next login session.

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
