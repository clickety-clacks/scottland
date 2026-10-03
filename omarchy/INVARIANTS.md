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
| O6 | Lua-function shortcuts (Yoohoo Super+Tab, universal copy/cut/paste) run through the Lua host with their state intact. | implemented |
| O7 | Key-release shortcuts work (Yoohoo accepts on Super release; Voxtype push-to-talk stops on release). | partial: standalone release supported; stock F9 press/release pair loses its press action during import (reproduced with headless input on plumbus; [adapter gaps](../docs/adapter-gaps.md), AG09/H1). Modified release chords are skipped. |
| O8 | Injected shortcuts reach the focused window with only their own modifiers; a physically held Super does not leak in (universal copy sends plain Ctrl+C / Ctrl+Insert). | implemented (headless) |
| O13 | Shortcuts a Hyprland config switches on and off (`:set_enabled()`, e.g. Ctrl+W remapped only while Chromium is focused) follow the focused window; while off, their keys pass through to the app unchanged (terminals keep Ctrl+W = delete word). | implemented |
| O14 | Apps in Scottland get the environment Omarchy's Hyprland config sets with `hl.env` (e.g. `QT_QPA_PLATFORMTHEME=gtk3` for native file dialogs in Qt apps, Electron/Chromium Wayland hints, cursor size, compose file, theme colors, the user's own variables), read from the same config by the Lua host, and handed to user services as uwsm does; the variables naming the desktop stay Scottland's. | implemented |
| O15 | Scottland's palette (widgets such as the default card) follows the Omarchy theme: background, foreground, muted, accent and alert colors from the current theme, switching live with `omarchy theme set` (the `accent.d` provider's `--palette`). | implemented |
| O18 | When core Sunlight (S21) requests a day/night mode, the adapter checks the current Omarchy theme with `omarchy-theme-color --file <current>/theme/colors.toml mode` and runs `omarchy theme set` only if that mode is wrong. Adapter defaults are Nasa2043 by day and Nord by night; `~/.config/scottland/omarchy-solar.ini` can override them. A matching user-picked theme stays selected. | implemented (headless) |
| O17 | Full screen holds Omarchy's notifications (FS1): the `focus.d` hook turns the shell's do-not-disturb on while a fullscreen window is in front, and back off after; a do-not-disturb the user already had on stays on. | implemented (tests/omarchy-focus-test.sh, with a stand-in shell) |
| O9 | A double-tap close shortcut (Super+W) is not turned into a single-press close. | implemented (left unmapped) |
| O10 | Apps launched through Omarchy's launcher (uwsm-app) open in Scottland. | verified |
| O11 | The shim reports the real session-lock state. | not built |
| O12 | Keyboard layout switching and night light work under the shim. Runtime surface shortcuts use core key layers (K1; [../docs/key-layers.md](../docs/key-layers.md)), rather than adapter submaps. | layout/night light: not built; key layers: core K1 |

The optional `dispatch hl.dsp.window.tag(...)` translation remains logged as unsupported.
Wayfire has no equivalent dynamic tag state; implementing it needs a per-window tag lifecycle
that the shim can also report consistently in `clients` and events. A dispatch-only reply would
make tags appear to change without updating those readers.

[Omarchy compatibility audit](../docs/adapter-gaps.md) records 38 behavior rows (AG01–AG38)
for stock and optional Omarchy choices, with source evidence, observed headless failures and
unverified outcomes kept separate. O1/O2/O10 describe their exercised shell, IPC and launch
paths; they do not establish compatibility for every menu action, application rule, portal,
input layout or hardware configuration. The audit proposes fix layers, not new commitments.

## Switching from Mike's personal solar timer

Mike's `~/.local/bin/omarchy-solar-theme` and its user timer stay in place until he chooses to
switch. To switch, disable that user timer, enable Sunlight in Scottland Settings, enter fallback
coordinates if Geoclue is unavailable, and optionally create
`~/.config/scottland/omarchy-solar.ini` with `[themes]`, `day = Nasa2043`,
`night = Nord`. The adapter reads these values on each check. Do not run both schedulers.
