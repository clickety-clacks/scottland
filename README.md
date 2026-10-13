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
| `omarchy/bin/` | `scottland-omarchy` | `scottland-switch` and setup for per-user menu entries, default themes and the screen-sharing portal |
| `omarchy/themes/` | `scottland-omarchy` | Watercolor Dream Light and Dark Omarchy themes, installed per user when their names are available |
| `omarchy/switch-dialog/` | `scottland-omarchy` | The Switch Desktop dialog (Quickshell, Omarchy theme) |
| `omarchy/helper/` | `scottland-omarchy` | Root helper + polkit rule: sets the autologin session, ends one desktop and starts the other |
| `omarchy/hooks/` | `scottland-omarchy` | Crash guard: a Scottland session that dies in seconds boots Hyprland next time |
| `packaging/` | | Arch PKGBUILDs now; other distros later |

Rule: nothing under `core/` knows Omarchy exists. Omarchy integration lives only in `omarchy/`.

## Install and upgrade (Arch Linux)

The [published `v0.3.0` release](https://github.com/clickety-clacks/scottland/releases/tag/v0.3.0)
has two matched Arch package assets: `scottland` is the Wayfire desktop;
`scottland-omarchy` adds the Omarchy switcher and requires that exact core
package version. On Omarchy, download both assets and install them together:

```sh
curl -fLO https://github.com/clickety-clacks/scottland/releases/download/v0.3.0/scottland-0.3.0-1-x86_64.pkg.tar.zst
curl -fLO https://github.com/clickety-clacks/scottland/releases/download/v0.3.0/scottland-omarchy-0.3.0-1-any.pkg.tar.zst
sudo pacman -U ./scottland-0.3.0-1-x86_64.pkg.tar.zst ./scottland-omarchy-0.3.0-1-any.pkg.tar.zst
pacman -Q scottland scottland-omarchy
```

Inspect the release assets and pacman transaction before installing. On a
non-Omarchy Arch desktop, install only the `scottland` asset. To build the
matched pair from source instead, run the following as your normal user on an
Arch system with `base-devel` and the PKGBUILD's build dependencies available:

```sh
git clone https://github.com/clickety-clacks/scottland.git
cd scottland
git switch --detach v0.3.0
cd packaging/arch
makepkg -si
pacman -Q scottland scottland-omarchy
```

`makepkg -si` builds and installs both split packages in one invocation. For a
source build on a non-Omarchy Arch desktop, use `makepkg -s` and install only
the generated `scottland` archive. Review the PKGBUILD and package transaction.

To upgrade, obtain both assets of the same newer published release and install
them together with `sudo pacman -U`, substituting that release's exact asset
filenames for the `v0.3.0` example. If you build from source, fetch and select
the desired published tag in the same checkout, then rebuild the matched pair:

```sh
cd /path/to/scottland
git fetch --tags origin
git switch --detach <new-tag>
cd packaging/arch
makepkg -si
pacman -Q scottland scottland-omarchy
```

Replace `/path/to/scottland` with your checkout. Use a real published tag in place of `<new-tag>`;
do not mix core and adapter archives from different builds. A package upgrade
does not replace code already loaded by a running Wayfire session. Enter a new
Scottland session to use the new package, or use `scottland-reload` from that
session when an in-place reload is appropriate. A development plugin linked
under `~/.local/share/scottland/dev` can take precedence over `/usr/lib` on
reload; remove or deliberately update that development selection before
attributing a running result to the newly installed package. Keep your user
config files when upgrading.

On Omarchy, rerun `scottland-omarchy-setup` after a package upgrade so its
per-user menu, themes, shortcut report and portal setup match the installed
adapter. It keeps existing theme directories and menu entries, but can build
and replace the screen-sharing portal package; read its output and package
transaction. For a packaged upgrade, prefer a fresh Scottland login before
checking the new plugin and helpers.

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
`~/.codex/skills`. The published
`v0.3.0` Scottland assets contain only the Core skill. Use package versions
that actually contain the other skills before following their installed paths.

## Running it

On Omarchy, after installing the matched packages, run `scottland-omarchy-setup` once, then use
Omarchy menu > System > Switch Desktop (Super+Escape inside Scottland). Setup also builds
Gooarchy's xdg-desktop-portal-wlr (from [its fork](https://github.com/clickety-clacks/xdg-desktop-portal-wlr))
and installs it in place of Arch's, saying so: with Arch's, screen sharing in Scottland fails in
most attempts. Gooarchy has no package repository yet; once it has, setup installs the package
from there instead of building it. Switching closes the current session and all its windows;
the dialog warns about this. The machine boots into whichever session was used last.

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
