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
| L18 | Just outside the center zone, a blend band (default 40 pt, "Center edge softness" slider, up to 300 pt) eases the scale from 100% into the scale curve: flat where it meets the center, matching the curve's starting slope where it meets the curve, so there is no jump or corner at the edge. | implemented |
| L5 | A window's zone and scale are set by its center. It scales around its center. | verified |
| L6 | True scaling: the real window is transformed, not a thumbnail, and stays fully interactive (click, type, scroll) at any scale. | verified (scale); interaction at small scale: implemented |
| L7 | Scaling is the only transform: no rotation or other distortion. Wayfire's window rotation (flat and 3D) and desktop cube are not enabled. | implemented |
| L20 | Super + right-drag, or Super + Alt + drag, resizes around the window's center, like visionOS: it grows or shrinks symmetrically, the center stays put (also when the app snaps to its own size, or won't grow past a limit such as the screen's size: no flicker), so the window keeps its zone and scale; the edges track the cursor at the window's current scale. Direction is absolute wherever you grab: dragging right makes it wider, left narrower, up taller, down shorter. | verified |
| L8 | Windows rescale live while being dragged, with the grabbed point staying under the pointer. Crossing a jump in scale (e.g. the center zone's edge) changes size once, never flickering between sizes: a size is chosen only if it agrees with the center it produces, so the switch has a little hysteresis wherever the window is grabbed. What you see while dragging is what you get: the size doesn't change on release (a drop inside a jump keeps the size shown and nudges the window by the least distance that agrees). Same for every drag: Super+drag, the halo, three-finger and touch. | verified |
| L9 | Dropping a window causes no jump: the scale after release equals the scale just before it. | verified |
| L10 | Scale jumps (e.g. leaving the 100% center for a zone that starts lower) animate smoothly (~180 ms) instead of snapping. Small changes during a drag apply immediately; a moving target re-aims a running animation. | verified |
| L11 | Dropping a window near an edge or corner does not snap or resize it (Wayfire edge snapping is off). | verified |
| L12 | No wobbly windows, in shipped and installed configs. | verified |
| L21 | Touchpad clicks use clickfinger: a two-finger press anywhere is a right click, three fingers a middle click (shipped default). | implemented |
| L22 | Touchpad double-tap-and-drag has a grace period: lifting the finger mid-drag keeps the drag for about 300 ms (libinput drag lock, timeout mode), so putting it back down continues the same drag; past the grace period the drag drops. Resizes don't get the grace period: lifting the finger ends a resize at once (drag lock is off while one runs), as a resize magnifies motion and a resumed one surprises. Shipped default. | implemented |
| L23 | A three-finger drag on a touchpad moves the window under the pointer (no click, no modifier), with the same live scaling as Super+drag; the pointer follows the fingers. Lifting the fingers ends the drag at once (no grace period; picking the window up again within 2.5 s only counts as the same move for where Esc sends it back, L27). | implemented |
| L24 | A three-finger click-drag on a touchpad (clickfinger's middle button) resizes the window under the pointer around its center, like Super+right-drag. A three-finger click without moving is still a middle click, delivered to the app on release. Mice keep their normal middle button. | implemented |
| L25 | Touchscreen: holding one finger still (within ~10 pt) on a window for ~350 ms lifts it: the app's touch is cancelled, the window bulges out elastically and pulls back to its normal size, its halo swells as on hover, a synthesized bloop plays, and the window follows the finger with live scaling until the finger lifts. Touches that move sooner, and touches with two or more fingers, belong to the app with no added delay. | implemented |
| L26 | Touchscreen for apps that ignore touch: for apps listed in `[scottland] touch_scroll_<name> = <app-id regex>` (shipped: common terminals, Ghostty first), the app gets none of the touch (Scottland takes it before delivery): a one-finger drag scrolls with the content following the finger (momentum after a flick) and a quick tap clicks; long press still lifts. Scrolling is smooth, or a high-resolution wheel for apps in `touch_scroll_wheel` (shipped: Ghostty, which ignores smooth scrolling from Scottland's pointer). Users add, change or empty entries in overrides.ini. | implemented |
| L27 | Esc cancels any window drag: the window glides back to where it was picked up (a re-grab within 2.5 s continues the same move), in its original form (docs/widgets.md, WG14); the halos it glides over are whole again behind it. | implemented |
| L28 | Apps don't take focus by asking for it: an xdg-activation request takes focus only when it comes from input in the app the user is using (a link opened from it); otherwise it's attention (docs/widgets.md, WG15). Scottland implements the protocol; Wayfire's xdg-activation plugin isn't loaded. | implemented |
| L29 | A window being dragged is above the widgets, and stays above them after it's let go for as long as a re-grab would continue the move (2.5 s, L27: fingers lifted to reset on the touchpad); then the widgets float above it again. Esc cancellation ends the hold, including after a re-grab. Going to another window ends the hold at once, and that window comes (and stays) in front: the hold never leaves a dropped window in front of the one the user is on. A window the user set always-on-top keeps that. | implemented (headless) |
| FS1 | Full screen is for focus (tenet 6): while a fullscreen window is in front on a screen, that screen's widgets slide off its edges (each toward its own rail's edge) and are hidden; when no fullscreen window is in front anymore (left full screen, closed, or another window came in front), they slide back to their places. Explicitly entering Alt hints brings them back while asking, and ending hints hides them again if full screen remains in front ([windowing-keys.md](../docs/windowing-keys.md), WK12). While any screen is in focus, Scottland runs the `focus.d` hooks with `on`, and with `off` after (`scottland-focus-mode`: the integrations' hooks, then the user's in ~/.config/scottland/focus.d); integrations use them to hold notifications. A test session runs only its own hooks (`SCOTTLAND_FOCUS_HOOKS`). Widgets mapping late arrive hidden while their output is in fullscreen focus (unless hints explicitly reveal them). Reload reconstructs promotion on every output, including one without keyboard focus. | implemented (headless) |
| L30 | Presenting a window ("I want to see this now", e.g. picking it or its agent in a launcher): any process can ask for it over IPC (`scottland/present {window}`, the window's id or its widget's). A widget opens as if clicked (WG17); a window in a side zone flies to its remembered center spot, else the nearest least-overlapping full-size center spot, and grows to 100% ([windowing-keys.md](../docs/windowing-keys.md), WP1–WP5); a window already in the center zone stays put. Each ends up raised and focused. A plain focus request (a switcher, xdg-activation) does not move anything. | implemented (headless) |
| L31 | Holding Alt while dragging a window keeps the scale it has, wherever it goes (into the periphery at 100%, say), and dropping it with Alt still held leaves it at that scale there; letting go of Alt mid-drag returns it to the zones, and dragging it again without Alt makes it follow the zones again. (With Super+drag, press Alt after the drag starts: Super+Alt+drag resizes, L20.) Holding Alt during a drag never opens the Alt window hints. | implemented (headless) |
| L19 | Ctrl+W deletes the previous word in every app, browsers included: the plugin remaps it to Ctrl+Backspace for browsers and Chromium web apps (shipped `key_remaps` rules), and Ctrl+Alt+W closes the browser tab (Ctrl+F4). Holding the key repeats. The remap mechanism is generic: per-app rules of app-id regex, from-combo, to-combo. | implemented (headless) |
| L17 | Trackpad (two-finger) scrolling honors `input/touchpad_scroll_speed`, applied live; the shipped default is 0.2. (Wayfire 0.11 ignores it for touchpads, WayfireWM/wayfire#3148; the plugin applies it until the fix ships.) | verified (on Mike's trackpad, osanwe) |
| L13 | A window moved onto a widget rail becomes a widget (any program, chosen per app, default card); see [docs/widgets.md](../docs/widgets.md) (WG1-WG12). | implemented (headless) |
| L14 | Tiling rules apply to windows. | not built (rules not yet specified) |
| L15 | Scottland copies the display scale the user's Hyprland uses (e.g. 4K panels are not tiny). | not built |
| L16 | How widgets sit on a rail: free-floating where dropped, at the widget's own size ([docs/widgets.md](../docs/widgets.md), WG4). | implemented (headless) |

## Window appearance and handles

The original halo remains the default (`scottland/goo = false`). With goo enabled, one liquid
field per screen replaces its drawing and field input; the restated appearance and handle behavior
is in [docs/goo.md](../docs/goo.md#halo-jobs-with-goo-enabled). Input, proximity and palette jobs remain.

| ID | Invariant | Status |
|---|---|---|
| A1 | No window chrome: Scottland asks every app that allows it for server-side decorations and draws none (no title bars, no borders). GTK4/libadwaita apps keep the header bars they draw themselves; Chromium is switched to server-side decorations through additive app tuning (C7). | implemented (Chromium tuning not built) |
| A2 | Every window is a rounded rectangle; the corner radius is double Omarchy's (10 pt), scaling with the window. | verified |
| A3 | Every window has a halo, always visible: a translucent rounded band around it, as if a larger rounded rectangle hung behind it, with corners concentric to the window's. It visually separates overlapping windows. Its thickness scales with the window (about 10.7 pt at 100%).  Goo mode: see [docs/goo.md](../docs/goo.md#halo-jobs-with-goo-enabled). | implemented |
| A4 | The focused window's halo is less translucent, in the theme's highlight color; other windows' halos are more translucent, in a neutral tone that follows light/dark (A8). Focus changes cross-fade, and the newly focused window's halo is disturbed: it bulges briefly and settles in slow waves.  Goo mode: see [docs/goo.md](../docs/goo.md#halo-jobs-with-goo-enabled). | implemented |
| A5 | The halo is the move handle: dragging it anywhere outside the corners moves the window, with the same live scaling as Super+drag. Its grab area is never thinner than 12 pt on screen, even when the drawn halo is. | implemented |
| A6 | The halo's corners are resize handles: dragging one resizes around the window's center, outward growing. When the cursor comes near a corner, that part of the halo turns cloudy (the goo thickens, more opaque) and its glints strengthen, as if the light source brightened.  Goo mode: see [docs/goo.md](../docs/goo.md#halo-jobs-with-goo-enabled). | implemented |
| A7 | When the cursor pauses for 0.5 s within 50 pt of the halo (inside or outside the window, including over another window's content; windows behind the one the cursor is over don't respond), the halo swells to a fixed on-screen thickness of twice the full-size halo (~21 pt), however small the window (only the resting halo scales; the 50 pt doesn't scale either); any motion restarts the wait. The swell moves like goo: it bulges, overshoots and settles with slow, irregular, low-frequency waves along the edge. It stays while the cursor is within the 50 pt, and sinks back the same way about 0.5 s after the cursor leaves (never while dragging). Dragging doesn't count as pausing: the wait starts when the window is let go, so the swell always animates. | implemented |
| A9 | The halo looks like liquid: shaded as a rounded surface with a bright rim and glints, with soft organic variation, not a flat tint.  Goo mode: see [docs/goo.md](../docs/goo.md#halo-jobs-with-goo-enabled). | implemented |
| A10 | Halos of different windows behave as one liquid: where they cross, the inside corners fill with a rounded meniscus (surface tension), and when two halos come within about 5 pt they reach for each other and bridge. The joining liquid belongs to the window in front, for drawing and grabbing. (Reaching will gain a meaning later.) Halos swelling or breathing in place keep merging with their neighbors as they change.  Goo mode: see [docs/goo.md](../docs/goo.md#halo-jobs-with-goo-enabled). | implemented |
| A11 | A close dot sits at the middle of the halo's bottom edge, appearing as the cursor comes near; clicking it closes the window. There is no minimize.  Goo mode: see [docs/goo.md](../docs/goo.md#halo-jobs-with-goo-enabled). | implemented |
| A12 | The halo works by finger with no wait: dragging it moves the window (grabbed exactly at the finger), dragging a corner resizes around the center, and touching the halo shows the close dot for a few seconds so it can be tapped. | implemented |
| A13 | Interface sounds are synthesized at runtime (no sample files), follow the system volume and mute, and can be turned off (`scottland/sounds`). | implemented |
| A8 | The halo's neutral tone and the close dot follow the desktop's light or dark color scheme (the desktop portal's appearance setting, or GNOME's color-scheme), switching live. The highlight color comes from the desktop portal's accent color, or from an integration (the Omarchy adapter supplies its theme's accent), and also switches live. | implemented |

## Layout configurator (`scottland-settings`)

| ID | Invariant | Status |
|---|---|---|
| S1 | The zone settings (center edge softness, center zone width, widget rail width) are one stack of tall rows, each row a slider grabbed anywhere along it (label left, value right, thin separators, one rounded block), after the shared parameter-slider design: Left/Right adjust (Shift: larger steps), Up/Down or Tab move between rows, digits type a value, Backspace resets a row, double-click resets a row to its value when the panel opened; changed values show in the accent color. Super+, opens the panel. | implemented |
| S9 | A 2D curve editor sets the scale across the side zones: two endpoints (largest scale at the center edge, smallest at the rail) move vertically only; clicking adds a point, dragging shapes the curve, double-click or right-click removes a point. Points are joined by a smooth curve that doesn't overshoot them (monotone cubic), and the plugin uses the same curve. | verified |
| S2 | While the panel is open, a click-through overlay shows the zones on every screen. | verified |
| S3 | Moving a zone slider rescales windows on screen live; Goo rows change the field live. | zones verified; Goo plumbus headless input checked |
| S4 | Save persists zone and Goo values (`~/.config/scottland/layout.ini`); reopening shows the saved values. | zones verified; Goo plumbus headless Save/config-consumer checked |
| S5 | Cancel or Escape restores zone and Goo values from when the panel opened and saves nothing, including the live Goo switch and empty/default falloff. | zones verified; Goo plumbus headless input checked |
| S6 | If the running session's plugin predates a setting, the panel says "Restart Scottland to use: …" and keeps the saved value instead of resetting it. | implemented |
| S10 | A Goo section beside Layout exposes the live goo switch and all fifteen settings in the same tall grab-anywhere ParameterStack rows as the zone settings (S1), including keyboard steps/navigation, typed values, opening-value reset and modified color. Keyboard navigation scrolls to the selected row; the scrollbar reaches the falloff curve in the shared editor. Save, Cancel and Defaults include goo. See [GO9](../docs/goo.md). | implemented; plumbus headless pointer/keyboard input and screenshots checked |

## Desktop state

The single desktop model and its reactive subscription/launch/audit contracts are in
[../docs/desktop-model.md](../docs/desktop-model.md) (DM1-DM8).

## Key layers

| ID | Invariant | Status |
|---|---|---|
| K1 | A focused surface can add a temporary shortcut layer: claimed keys reach that surface as ordinary press/release events with modifiers intact, while every unclaimed key uses the user's current shortcuts (including live changes, imported functions, release bindings and remaps). Native toplevels and layer-shell surfaces work independently even within one client. Unmap, close or client disconnect removes the layer; set replaces it. Full rules and IPC: [../docs/key-layers.md](../docs/key-layers.md), KL1–KL8. | implemented (plumbus headless, including combined Alt hints); physical verification pending |

## Session

| ID | Invariant | Status |
|---|---|---|
| E1 | When Scottland is the user's only graphical session, it acts as the graphical session for systemd (scottland-session.target), so user services tied to graphical-session.target start with it and stop with it, however the session ends. | verified |
| E2 | Quit keys inside Scottland: Ctrl+Alt+Backspace, Super+Shift+Escape. | implemented |
| E4 | `scottland-reload` (Super+Ctrl+Alt+R) loads the current plugin build and config into the running session in place: windows stay open, new settings are registered and take their saved values. | verified |
| E5 | Each session records its environment at startup; `scottland-exec` runs commands in a chosen session with that environment and `scottland-reload` goes through it, so reloading from any shell (another desktop, ssh, an agent) behaves exactly like Super+Ctrl+Alt+R and never leaks the caller's variables into the session's helpers. | verified |
| E6 | Scottland ships an agent skill (`scottland`) and links it into each installed coding-agent harness's skills folder at login (Claude Code, Codex, ~/.agents, pi, Hermes), as Omarchy does with its own: how to configure Scottland, find app-ids (`scottland-ctl windows`), set touchscreen scrolling, and later the widget system. | implemented |
| E3 | Personal overrides in `~/.config/scottland/overrides.ini` are applied last and win over shipped defaults; Scottland never writes that file. | implemented |
| E7 | The session's config is never left empty or half-written by Scottland: config builds (the config watcher, `scottland-reload`, session start) run one at a time, each from its own temporary file, and an empty build never replaces the config. (An interleaved pair once emptied it, and Wayfire loaded every setting at its default: snapping on, natural scrolling off.) | implemented (tests/build-config-test.sh) |

## Window keys and contention-aware placement

See [docs/windowing-keys.md](../docs/windowing-keys.md): WK1–WK16 (window mode: Alt-alone hold, theme-derived Vimarchy hints,
start-relative cycles, double-tap to rail, input ownership, full screen and visual declutter)
and WP1–WP7 (zone memory, side choice and shared rectangle placement). Statuses and verification are recorded there.

L29 regression: `state-model-test.py` re-grabs an ordinary dropped window during the hold
and cancels with real Esc input; the scene must return to its ordinary layer and the hold ends.

FS1 regression: late-mapped widgets respect fullscreen before their first visible frame.
`state-regressions-test.sh` covers late adoption and reload with keyboard focus on another
headless output. The model audit checks visibility against Wayfire fullscreen promotion.
