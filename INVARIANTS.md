# Scottland invariants

Every behavior Mike has asked for. Each has an ID, a status, and how to verify it. Before
reporting a change as done, re-check every invariant it could affect (see AGENTS.md, "Testing").

Status: **verified** = exercised with real input on a real session (plumbus); **implemented** =
built and tested headless or by IPC only; **not built** = agreed but not implemented yet.

## Concept and scope

| ID | Invariant | Status |
|---|---|---|
| C1 | Scottland implements Scott Jenson's spatial "working memory" desktop concept (KDE Akademy 2026 talk), with his go-ahead. The README credits him and says it is independent, not his. | implemented |
| C2 | No workspaces. Window navigation and layout are Scottland's own; Hyprland's workspace/tiling behavior is not reproduced. | implemented |
| C3 | Stock Wayfire, not a fork. Scottland is Wayfire + the scottland plugin + config + integration. | implemented |
| C4 | Two packages: `scottland` (core, any distro) and `scottland-omarchy` (adapter). Nothing under `core/` references Omarchy, Hyprland or uwsm. | implemented |
| C5 | Omarchy users install one package and keep their environment; only window placement/scaling and workspaces change. A "native" install on non-Omarchy distros (e.g. Ubuntu) uses the core alone. | implemented (Arch only; other distros not built) |
| C6 | Hyprland, uwsm and the stock Omarchy shell are not modified. | implemented |
| C7 | Scottland never edits users' app configs. Session-specific behavior goes in how Scottland launches things. | implemented |
| C8 | Scottland adapts to apps and plugins, never the reverse: no per-app clones. Anything that talks to Hyprland goes through the generic shim; anything that lives in Hyprland's config runs in the generic Lua host; compositor abilities live in the plugin. | implemented |

## Layout and scaling

| ID | Invariant | Status |
|---|---|---|
| L1 | Each screen has five vertical zones: widget rail, continuous zone, center zone, continuous zone, widget rail. | verified |
| L2 | Center zone: default one third of the width (33.333%); windows there are at 100%. | verified |
| L3 | Widget rails: thin strips at the far left and right, default 2% of the width (~50 pt on a 2560-wide screen). | verified |
| L4 | Continuous zones: scale falls smoothly and linearly from the largest scale (next to the center, default 100%) to the smallest scale (next to the rails, default 20%). | verified |
| L5 | A window's zone and scale are set by its center. It scales around its center. | verified |
| L6 | True scaling: the real window is transformed, not a thumbnail, and stays fully interactive (click, type, scroll) at any scale. | verified (scale); interaction at small scale: implemented |
| L7 | Scaling is the only transform: no rotation or other distortion. | implemented |
| L8 | Windows rescale live while being dragged, with the grabbed point staying under the pointer. | verified |
| L9 | Dropping a window causes no jump: the scale after release equals the scale just before it. | verified |
| L10 | Scale jumps (e.g. leaving the 100% center for a zone that starts lower) animate smoothly (~180 ms) instead of snapping. Small changes during a drag apply immediately; a moving target re-aims a running animation. | verified |
| L11 | Dropping a window near an edge or corner does not snap or resize it (Wayfire edge snapping is off). | verified |
| L12 | No wobbly windows, in shipped and installed configs. | verified |
| L13 | A window entering a widget rail is told to render as a widget; apps that can't stay at the smallest scale. | not built (currently: stays at smallest scale) |
| L14 | Tiling rules apply to windows. | not built (rules not yet specified) |
| L15 | Scottland copies the display scale the user's Hyprland uses (e.g. 4K panels are not tiny). | not built |
| L16 | How widgets sit on a rail (currently a rail window hangs half off-screen). | not built (needs a decision) |

## Layout configurator (`scottland-settings`)

| ID | Invariant | Status |
|---|---|---|
| S1 | Sliders for center width (%), widget rail width (%), largest scale (next to the center) and smallest scale (next to the rails). | verified |
| S2 | While the panel is open, a click-through overlay shows the zones on every screen. | verified |
| S3 | Moving a slider rescales windows on screen live. | verified |
| S4 | Save persists the values (`~/.config/scottland/layout.ini`); reopening shows the saved values. | verified |
| S5 | Cancel or Escape restores the values from when the panel opened and saves nothing. | verified |
| S6 | If the running session's plugin predates a setting, the panel says "Restart Scottland to use: …" and keeps the saved value instead of resetting it. | implemented |
| S7 | Omarchy menu > System has "Scottland Layout" to open it (added by `scottland-omarchy-setup`). | implemented |

## Switching between Scottland and Hyprland (Omarchy adapter)

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
| W12 | Quit keys inside Scottland: Ctrl+Alt+Backspace, Super+Shift+Escape. | implemented |

## Omarchy environment inside Scottland (adapter)

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
| O9 | A double-tap close shortcut (Super+W) is not turned into a single-press close. | implemented (left unmapped) |
| O10 | Apps launched through Omarchy's launcher (uwsm-app) open in Scottland. | verified |
| O11 | The shim reports the real session-lock state. | not built |
| O12 | Keyboard layout switching, night light and Ask's runtime shortcuts work under the shim. | not built |

## Development rules

| ID | Invariant | Status |
|---|---|---|
| D1 | Nothing is hand-edited on a machine: files come from a package or from the repo in dev mode (`make dev-install`). | implemented |
| D2 | A change is not reported as done until it has been exercised with real input on a real session (plumbus), and any already-running session was checked for predating the build. | rule |
