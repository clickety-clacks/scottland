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
| L18 | Just outside the center zone, a blend band (default 3 pt, "Center edge blend" slider) eases the scale from 100% into the scale curve: flat where it meets the center, matching the curve's starting slope where it meets the curve, so there is no jump or corner at the edge. | implemented |
| L5 | A window's zone and scale are set by its center. It scales around its center. | verified |
| L6 | True scaling: the real window is transformed, not a thumbnail, and stays fully interactive (click, type, scroll) at any scale. | verified (scale); interaction at small scale: implemented |
| L7 | Scaling is the only transform: no rotation or other distortion. Wayfire's window rotation (flat and 3D) and desktop cube are not enabled. | implemented |
| L20 | Super + right-drag resizes around the window's center, like visionOS: it grows or shrinks symmetrically, the center stays put (also when the app snaps to its own size), so the window keeps its zone and scale; the edges track the cursor at the window's current scale. Direction is absolute wherever you grab: dragging right makes it wider, left narrower, up taller, down shorter. | verified |
| L8 | Windows rescale live while being dragged, with the grabbed point staying under the pointer. | verified |
| L9 | Dropping a window causes no jump: the scale after release equals the scale just before it. | verified |
| L10 | Scale jumps (e.g. leaving the 100% center for a zone that starts lower) animate smoothly (~180 ms) instead of snapping. Small changes during a drag apply immediately; a moving target re-aims a running animation. | verified |
| L11 | Dropping a window near an edge or corner does not snap or resize it (Wayfire edge snapping is off). | verified |
| L12 | No wobbly windows, in shipped and installed configs. | verified |
| L21 | Touchpad clicks use clickfinger: a two-finger press anywhere is a right click, three fingers a middle click (shipped default). | implemented |
| L19 | Ctrl+W deletes the previous word in every app, browsers included: the plugin remaps it to Ctrl+Backspace for browsers and Chromium web apps (shipped `key_remaps` rules), and Ctrl+Alt+W closes the browser tab (Ctrl+F4). Holding the key repeats. The remap mechanism is generic: per-app rules of app-id regex, from-combo, to-combo. | implemented (headless) |
| L17 | Trackpad (two-finger) scrolling honors `input/touchpad_scroll_speed`, applied live; the shipped default is 0.2. (Wayfire 0.11 ignores it for touchpads, WayfireWM/wayfire#3148; the plugin applies it until the fix ships.) | verified (on Mike's trackpad, osanwe) |
| L13 | A window entering a widget rail is told to render as a widget; apps that can't stay at the smallest scale. | not built (currently: stays at smallest scale) |
| L14 | Tiling rules apply to windows. | not built (rules not yet specified) |
| L15 | Scottland copies the display scale the user's Hyprland uses (e.g. 4K panels are not tiny). | not built |
| L16 | How widgets sit on a rail (currently a rail window hangs half off-screen). | not built (needs a decision) |

## Window appearance and handles

| ID | Invariant | Status |
|---|---|---|
| A1 | No window chrome: Scottland asks every app that allows it for server-side decorations and draws none (no title bars, no borders). GTK4/libadwaita apps keep the header bars they draw themselves; Chromium is switched to server-side decorations through additive app tuning (C7). | implemented (Chromium tuning not built) |
| A2 | Every window is a rounded rectangle; the corner radius is double Omarchy's (10 pt), scaling with the window. | verified |
| A3 | Every window has a halo, always visible: a translucent rounded band around it, as if a larger rounded rectangle hung behind it, with corners concentric to the window's. It visually separates overlapping windows. Its thickness scales with the window (about 10.7 pt at 100%). | implemented |
| A4 | The focused window's halo is less translucent, in the theme's highlight color; other windows' halos are more translucent, in a neutral tone that follows light/dark (A8). Focus changes cross-fade. | implemented |
| A5 | The halo is the move handle: dragging it anywhere outside the corners moves the window, with the same live scaling as Super+drag. Its grab area is never thinner than 12 pt on screen, even when the drawn halo is. | implemented |
| A6 | The halo's corners are resize handles: dragging one resizes around the window's center, outward growing. When the cursor comes near a corner, that part of the halo turns cloudy (the goo thickens, more opaque) and its glints strengthen, as if the light source brightened. | implemented |
| A7 | When the cursor pauses for 0.5 s within 50 pt of the halo (inside or outside the window), the halo swells to a fixed on-screen thickness of twice the full-size halo (~21 pt), however small the window (only the resting halo scales; the 50 pt doesn't scale either); any motion restarts the wait. The swell moves like goo: it bulges, overshoots and settles with slow, irregular, low-frequency waves along the edge. It stays while the cursor is within the 50 pt, and sinks back the same way about 0.5 s after the cursor leaves (never while dragging). | implemented |
| A9 | The halo looks like liquid: shaded as a rounded surface with a bright rim and glints, with soft organic variation, not a flat tint. | implemented |
| A10 | Halos of different windows behave as one liquid: where they cross, the inside corners fill with a rounded meniscus (surface tension), and when two halos come within about 5 pt they reach for each other and bridge. The joining liquid belongs to the window in front, for drawing and grabbing. (Reaching will gain a meaning later.) | implemented |
| A11 | A close dot sits at the middle of the halo's bottom edge, appearing as the cursor comes near; clicking it closes the window. There is no minimize. | implemented |
| A8 | The halo's neutral tone and the close dot follow the desktop's light or dark color scheme (the desktop portal's appearance setting, or GNOME's color-scheme), switching live. The highlight color comes from the desktop portal's accent color, or from an integration (the Omarchy adapter supplies its theme's accent), and also switches live. | implemented |

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
| E5 | Each session records its environment at startup; `scottland-exec` runs commands in a chosen session with that environment and `scottland-reload` goes through it, so reloading from any shell (another desktop, ssh, an agent) behaves exactly like Super+Ctrl+Alt+R and never leaks the caller's variables into the session's helpers. | verified |
| E3 | Personal overrides in `~/.config/scottland/overrides.ini` are applied last and win over shipped defaults; Scottland never writes that file. | implemented |
