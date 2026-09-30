# Scottland core invariants

Behaviors of the desktop itself, on any distro. Nothing here may depend on Omarchy, Hyprland or
uwsm; those live in [../omarchy/INVARIANTS.md](../omarchy/INVARIANTS.md). Project-wide scope and
rules are in [../AGENTS.md](../AGENTS.md).

Status: **verified** = exercised with real input on a real session (plumbus); **implemented** =
built and tested headless or by IPC only; **not built** = agreed but not implemented yet.

## Layout and scaling

| ID | Invariant | Status |
|---|---|---|
| L1 | Each screen has five vertical zones: widget rail, continuous zone, center zone, continuous zone, widget rail. | verified |
| L2 | Center zone: default one third of the width (33.333%); windows there are at 100%. | verified |
| L3 | Widget rails: thin strips at the far left and right, default 2% of the width (~50 pt on a 2560-wide screen). | verified |
| L4 | Continuous zones: scale follows the scale curve from the largest scale (next to the center, default 100%) to the smallest (next to the rails, default 20%); without a curve it's a straight line between them. | verified |
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
| S1 | Sliders for center width (%) and widget rail width (%). | verified |
| S9 | A 2D curve editor sets the scale across the side zones: two endpoints (largest scale at the center edge, smallest at the rail) move vertically only; clicking adds a point, dragging shapes the curve, double-click or right-click removes a point. Points are joined by a smooth curve that doesn't overshoot them (monotone cubic), and the plugin uses the same curve. | verified |
| S2 | While the panel is open, a click-through overlay shows the zones on every screen. | verified |
| S3 | Moving a slider rescales windows on screen live. | verified |
| S4 | Save persists the values (`~/.config/scottland/layout.ini`); reopening shows the saved values. | verified |
| S5 | Cancel or Escape restores the values from when the panel opened and saves nothing. | verified |
| S6 | If the running session's plugin predates a setting, the panel says "Restart Scottland to use: …" and keeps the saved value instead of resetting it. | implemented |

## Session

| ID | Invariant | Status |
|---|---|---|
| E1 | When Scottland is the user's only graphical session, it acts as the graphical session for systemd (scottland-session.target), so user services tied to graphical-session.target start with it and stop with it, however the session ends. | verified |
| E2 | Quit keys inside Scottland: Ctrl+Alt+Backspace, Super+Shift+Escape. | implemented |
| E4 | `scottland-reload` (Super+Ctrl+Alt+R) loads the current plugin build and config into the running session in place: windows stay open, new settings are registered and take their saved values. | verified |
| E3 | Personal overrides in `~/.config/scottland/overrides.ini` are applied last and win over shipped defaults; Scottland never writes that file. | implemented |
