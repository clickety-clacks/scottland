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
| S7 | Omarchy menu > System has "Scottland Layout" to open it (added by `scottland-omarchy-setup`). | implemented |

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
| O2 | The Hyprland shim answers Hyprland IPC generically (monitors, clients, workspaces, devices, options, events, dispatch exec, send-key); unsupported requests are logged to `~/.local/state/scottland/hyprshim.log`. | verified |
| O3 | Scottland's shortcuts are generated from the user's live Hyprland config at each session start and regenerated when that config changes; no copy is made at install. | implemented |
| O4 | Window navigation, layout, workspace and group shortcuts are not imported. | implemented |
| O5 | Where an imported shortcut collides with a Scottland default, the user's shortcut wins. | implemented |
| O6 | Lua-function shortcuts (Yoohoo Super+Tab, universal copy/cut/paste) run through the Lua host with their state intact. | implemented |
| O7 | Key-release shortcuts work (Yoohoo accepts on Super release; Voxtype push-to-talk stops on release). | implemented |
| O8 | Injected shortcuts reach the focused window with only their own modifiers; a physically held Super does not leak in (universal copy sends plain Ctrl+C / Ctrl+Insert). | implemented (headless) |
| O13 | Shortcuts a Hyprland config switches on and off (`:set_enabled()`, e.g. Ctrl+W remapped only while Chromium is focused) follow the focused window; while off, their keys pass through to the app unchanged (terminals keep Ctrl+W = delete word). | implemented |
| O14 | Apps in Scottland get the environment Omarchy's Hyprland config sets with `hl.env` (e.g. `QT_QPA_PLATFORMTHEME=gtk3` for native file dialogs in Qt apps, Electron/Chromium Wayland hints, cursor size, compose file, theme colors, the user's own variables), read from the same config by the Lua host, and handed to user services as uwsm does; the variables naming the desktop stay Scottland's. | implemented |
| O15 | Scottland's palette (widgets such as the default card) follows the Omarchy theme: background, foreground, muted, accent and alert colors from the current theme, switching live with `omarchy theme set` (the `accent.d` provider's `--palette`). | implemented |
| O17 | Full screen holds Omarchy's notifications (FS1): the `focus.d` hook turns the shell's do-not-disturb on while a fullscreen window is in front, and back off after; a do-not-disturb the user already had on stays on. | implemented (tests/omarchy-focus-test.sh, with a stand-in shell) |
| O9 | A double-tap close shortcut (Super+W) is not turned into a single-press close. | implemented (left unmapped) |
| O10 | Apps launched through Omarchy's launcher (uwsm-app) open in Scottland. | verified |
| O11 | The shim reports the real session-lock state. | not built |
| O12 | Keyboard layout switching, night light and Ask's runtime shortcuts work under the shim. | not built |
