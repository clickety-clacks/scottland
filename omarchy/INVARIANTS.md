# Scottland Omarchy adapter invariants

Behaviors that exist because Scottland runs on Omarchy beside Hyprland and uwsm (package
`scottland-omarchy`). Core behaviors are in [../core/INVARIANTS.md](../core/INVARIANTS.md);
project-wide scope and rules in [../AGENTS.md](../AGENTS.md).

Status: **verified** = exercised with real input on a real session (plumbus); **implemented** =
built and tested headless or by IPC only; **not built** = agreed but not implemented yet.

## Menu

| ID | Invariant | Status |
|---|---|---|
| S8 | Omarchy menu > System has "Reload Scottland" (shown only inside Scottland). | implemented |
| S7 | Omarchy menu > System has "Scottland Settings" to open it (added by `scottland-omarchy-setup`). | implemented |

## Switching between Scottland and Hyprland

| ID | Invariant | Status |
|---|---|---|
| W1 | Omarchy menu > System > "Switch Desktop" opens the switch dialog; Super+Escape in Scottland opens the Omarchy System menu, which has it. | implemented |
| W2 | The dialog has a checkbox "Close <current desktop> when switching", checked by default, with a note below it that some apps may not work correctly when both run at once. The choice is remembered. | implemented |
| W3 | Switching needs no password. | verified |
| W4 | The machine boots into whichever desktop was used last. | implemented |
| W5 | Close mode, Hyprland to Scottland: Hyprland's apps get to close cleanly first; Scottland starts as the only desktop with the user's graphical-session services (Yoohoo, Voxtype, portals, 1Password, ClipMesh, …) running in it. | verified |
| W6 | Close mode, Scottland to Hyprland: Hyprland starts cleanly (no stale session state blocks uwsm) and its services come back attached to it. | verified |
| W7 | Keep mode: both desktops keep running on separate VTs; switching flips between them without closing anything. | verified |
| W8 | Keep mode: the graphical-session services follow whichever desktop is on screen, including plain Ctrl+Alt+F-key switches, restarting as needed. Switches may be slow; a fully capable desktop matters more than speed. | verified |
| W9 | Quitting Scottland in keep mode hands everything back to Hyprland. | verified |
| W10 | If Scottland dies within seconds of starting, the next boot goes to Hyprland (crash guard). | implemented |
| W11 | Session services exit cleanly on a switch (no crash reports from apps losing their display). | implemented (1Password still crashes on keep-mode handover restarts; proposed: exclude it from the handover) |

## Omarchy environment inside Scottland

| ID | Invariant | Status |
|---|---|---|
| O1 | The stock Omarchy shell (bar, menus, theme, wallpaper, plugins such as Ask and Yoohoo) runs unmodified in Scottland. | verified |
| O2 | The Hyprland shim answers Hyprland IPC generically (monitors, clients, workspaces, devices, options, events, dispatch exec, send-key); unsupported requests are logged to the session state directory. On Wayfire IPC closure (including reload), the shim reconnects while retaining its Hyprland sockets; reload starts a missing shim. Headless `--omarchy` logs stay in its isolated state directory. | verified |
| O3 | Scottland's shortcuts are generated from the user's live Hyprland config at each session start and regenerated when that config changes; no copy is made at install. | implemented |
| O4 | Window navigation, layout, workspace and group shortcuts are not imported. | implemented |
| O5 | Scottland keeps the user's Omarchy shortcuts as far as it can and overrides them only where its own features need the keys: an imported shortcut on the same keys as a Scottland feature binding (the `[scottland]` section, including both center-window Alt+Tab directions, or a `scottland_*` command such as Super+, for Scottland Settings) or any `key_remaps` `from` combo steps aside, listed as displaced in the generated config. Remap combos are reserved globally because imported bindings apply to every app, while remaps apply only to matching apps. On keys held by any other default (Wayfire's, or Scottland's generic ones such as the terminal) the user's shortcut wins. Collision detection includes Scottland metadata defaults after explicit base INI overrides, normalizes modifier order/case and xkb key names, and removes every conflicting owner while preserving unclaimed alternatives. | implemented (headless) |
| O20 | Overrides are announced, with reasons. Whenever the adapter (or gooarchy-flavorings, which it installs) replaces or displaces an Omarchy shortcut or other Omarchy mapping, it writes a plain-text report of every override, each with what it was, what it is now, and why (e.g. "Alt hold opened Omarchy Ask; it now enters Scottland's Window mode, which is central to how Scottland works"), and opens it in a window for the user (an editor such as Neovim in their terminal) at install and whenever the set of overrides changes. The report stays on disk to reread. (Mike, 2026-10-03) | not built |
| O21 | gooarchy-flavorings is installed by the adapter: Omarchy has no widget concept, so its widgets and defaults are additions. It may also override an Omarchy choice, but only where that gives a better core Scottland/Gooarchy experience than Omarchy's binding, and every such override falls under O20. (Mike, 2026-10-03) | not built |
| O6 | Lua-function shortcuts (Yoohoo Super+Tab, universal copy/cut/paste) run through the Lua host with their state intact. | implemented |
| O7 | Key-release shortcuts work (Yoohoo accepts on Super release; Voxtype push-to-talk stops on release). | implemented |
| O8 | Injected shortcuts reach the focused window with only their own modifiers; a physically held Super does not leak in (universal copy sends plain Ctrl+C / Ctrl+Insert). | implemented (headless) |
| O13 | Shortcuts a Hyprland config switches on and off (`:set_enabled()`, e.g. Ctrl+W remapped only while Chromium is focused) follow the focused window; while off, their keys pass through to the app unchanged (terminals keep Ctrl+W = delete word). | implemented |
| O14 | Apps in Scottland get the environment Omarchy's Hyprland config sets with `hl.env` (e.g. `QT_QPA_PLATFORMTHEME=gtk3` for native file dialogs in Qt apps, Electron/Chromium Wayland hints, cursor size, compose file, theme colors, the user's own variables), read from the same config by the Lua host, and handed to user services as uwsm does; the variables naming the desktop stay Scottland's. | implemented |
| O15 | Scottland's palette (widgets such as the default card) follows the Omarchy theme: background, foreground, muted, accent, alert and attention colors from the current theme, switching live with `omarchy theme set` (the `accent.d` provider's `--palette`). Attention reads the optional `attention = "#rrggbb"` key from `colors.toml` and falls back to `yellow`; the shipped Watercolor themes omit this key. | implemented (Plumbus isolated palette checks, 2026-10-03) |
| O18 | When core Sunlight (S21) requests a day/night mode, the adapter checks the current Omarchy theme with `omarchy-theme-color --file <current>/theme/colors.toml mode` and runs `omarchy theme set` only if that mode is wrong. Adapter defaults are Watercolor Dream Light by day and Watercolor Dream Dark by night; `~/.config/scottland/omarchy-solar.ini` can override either choice, and an omitted value uses that shipped default. A matching user-picked theme stays selected. | implemented (Plumbus isolated test, 2026-10-03) |
| O19 | `scottland-omarchy-setup` installs Watercolor Dream Light and Dark into the user's Omarchy theme directory only when each destination name is absent; an existing same-named theme is kept intact. The adapter package and dev-install snapshot ship the themes, including their `.aether-managed` markers; dev-install links the matching setup command from its snapshot. | implemented (Plumbus isolated config check, 2026-10-03) |
| O17 | Full screen holds Omarchy's notifications (FS1): the `focus.d` hook turns the shell's do-not-disturb on while a fullscreen window is in front, and back off after; a do-not-disturb the user already had on stays on. | implemented (tests/omarchy-focus-test.sh, with a stand-in shell) |
| O9 | A double-tap close shortcut (Super+W) is not turned into a single-press close. | implemented (left unmapped) |
| O10 | Apps launched through Omarchy's launcher (uwsm-app) open in Scottland. | verified; Files via imported Super+Shift+F and a real uwsm scope also completes native file drops on plumbus ([evidence](../docs/native-dnd.md)) |
| O11 | The shim reports the real session-lock state. | not built |
| O12 | Keyboard layout switching and night light work under the shim. Runtime surface shortcuts use core key layers (K1; [../docs/key-layers.md](../docs/key-layers.md)), rather than adapter submaps. | layout/night light: not built; key layers: core K1 |

The optional `dispatch hl.dsp.window.tag(...)` translation remains logged as unsupported.
Wayfire has no equivalent dynamic tag state; implementing it needs a per-window tag lifecycle
that the shim can also report consistently in `clients` and events. A dispatch-only reply would
make tags appear to change without updating those readers.

## Switching from Mike's personal solar timer

Mike's `~/.local/bin/omarchy-solar-theme` and its user timer stay in place until he chooses to
switch. To switch, disable that user timer, enable Sunlight in Scottland Settings, enter fallback
coordinates if Geoclue is unavailable, and optionally create
`~/.config/scottland/omarchy-solar.ini` with `[themes]`, `day = watercolor-dream-light`,
`night = watercolor-dream-dark` (or another theme pair). The adapter reads these values on each
check. Do not run both schedulers.

## Watercolor theme assets and verification

Each original 2752×1728 sRGB PNG was identical between `preview.png` and its background file.
The adapter stores one quality-95 WebP per theme and makes `preview.webp` a relative link to that
background; Omarchy's theme picker and background selection enumerate WebP files. The light
painting is 1,712,560 bytes (42.6 dB PSNR from the 8,769,559-byte PNG); the dark painting is
1,722,764 bytes (41.2 dB from the 8,385,881-byte PNG). This avoids a second copy of each preview
and reduces the pair from 34.3 MB of duplicated PNGs to 3.4 MB of image data. Side-by-side
comparisons are under `build/part6-theme-candidates/`.

On Plumbus, `tests/solar-test.py` passed **24 checks** for solar defaults and overrides, the optional
attention-color fallback, theme setup, marker preservation and collision safety in a temporary
config. `makepkg --nodeps` built both Arch packages; the 3,484,027-byte `scottland-omarchy` package
contains both themes, `.aether-managed` markers and WebP previews. Package and test evidence is
under `build/part6-plumbus/`.
