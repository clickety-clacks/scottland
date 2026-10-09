# Settings (Scottland Settings)

Super+, opens Scottland Settings: Layout, Goo, Window mode, Translucency, Widgets and Sunlight tabs. While it is open, a zone overlay shows and lets you drag the zone borders.

## Invariants

| ID | Invariant | Status |
|---|---|---|
| S1 | The zone settings (center edge softness, center zone width, widget rail width) are one stack of tall rows, each row a slider grabbed anywhere along it (label left, value right, thin separators, one rounded block), after the shared parameter-slider design: Left/Right adjust (Shift: larger steps), Up/Down or Tab move between rows, digits type a value, Backspace resets a row, double-click resets a row to its value when the panel opened; changed values show in the accent color. Super+, opens the panel. | implemented; isolated osanwe pointer/keyboard rows and hint screenshots checked |
| S2 | While the panel is open, an overlay shows the zones on every screen; it is click-through except for the border handles (S13). | zones verified; handle-only input and ordinary app click-through checked on two isolated osanwe outputs |
| S3 | Moving a zone slider rescales windows on screen live; Goo rows change the field live. | zones verified; Goo field and continuous border preview checked with isolated osanwe input |
| S4 | Save persists zone, Goo and Window mode values (`~/.config/scottland/layout.ini`); reopening shows the saved values. | zones verified; Goo Save/config-consumer and border Save/reopen checked with isolated osanwe input |
| S5 | Cancel or Escape restores zone, Goo and Window mode values from when the panel opened and saves nothing, including the live Goo switch and empty/default falloff. | zones verified; Goo and border Cancel/Escape, including a held drag, checked with isolated osanwe input |
| S6 | If the running session's plugin predates a setting, the panel says "Restart Scottland to use: …" and keeps the saved value instead of resetting it. | implemented |
| S9 | A 2D curve editor sets the scale across the side zones: two endpoints (largest scale at the center edge, smallest at the rail) move vertically only; clicking adds a point, dragging shapes the curve, double-click or right-click removes a point. Points are joined by a smooth curve that doesn't overshoot them (monotone cubic), and the plugin uses the same curve. | verified |
| S10 | A Goo section beside Layout exposes the live goo switch, Theme/Warm/Cool attention-color choice (GO22), all twenty-four numeric settings (including GO23 Dye density, formerly Dye strength, and GO28 Pickup balance) and the two unfocused-edge appearance sliders in the same tall grab-anywhere ParameterStack rows as the zone settings (S1), including keyboard steps/navigation, typed values, opening-value reset and modified color. The tone slider edits the active light or dark scheme's value. Keyboard navigation scrolls to the selected row; the scrollbar reaches the falloff curve in the shared editor. The GO14/GO15 Liquid depth, Wall wetting and Wallpaper pickup (formerly Wallpaper soak) rows and GO23 Dye density use their metadata hints verbatim. Save, Cancel and Defaults include goo, attention family, dye density and edge appearance. See [GO9](../docs/goo.md). | implemented; headless live slider input on the x86 test machine, preview, Save/Cancel/Defaults and light/dark Goo/fallback color screenshots (2026-10-03) |
| S11 | Every numeric row in Layout, Goo, Window mode, Translucency, Widgets and Sunlight and visual control explains itself: hover or keyboard selection shows a separate floating popout beside the row, flipping sides when needed on its screen. Its plain-language hint explains what raising/lowering the value looks like, with a padded theme-colored bubble, the interface font at 15 px times the desktop text scale, and the row label for clear association. It never covers the dragged slider or takes input/focus, hides on pointer leave (keyboard selection persists), when the row scrolls out of view, or on close; rows contain only label and value. (Mike, 2026-10-02) | implemented; isolated headless QML identity and rectangle pixel checks, edge flip, theme/text scale, held drag, dismissal and input pass-through checked; see [settings design](../docs/settings.md) |
| S12 | The zone overlay shows the center edge softness: the blend band on each side of the center zone where windows ease from full size into the side-zone scale is visible as its own shaded region with its outer border drawn as a line like the zone edges, updating live. (Mike, 2026-10-02) | implemented; isolated osanwe two-output stipc input and screenshots checked; see [settings design](../docs/settings.md) |
| S13 | While the panel is open, the zone borders (center zone edges, rail edges, softness band edges) can be dragged directly on screen; dragging updates the same values as the sliders, live, with the same Save/Cancel semantics. The overlay stays click-through everywhere except the border handles; while a border is held the whole overlay takes input, so a long or fast drag stays with the pointer until release. (Mike, 2026-10-02) | implemented; isolated osanwe two-output stipc input and screenshots checked; see [settings design](../docs/settings.md) |
| S14 | Scottland Settings has a Window mode tab beside Layout and Goo for keyboard pushes, inertial resize, drag coasts, hint-cycle overshoot, the hint-color overlay strength (`scottland/window_mode_tint`, WK38), Alt hold/double-tap/hint hold timing (WK39), the hold hotspot (`scottland/solo_audition_hotspot`: moving the pointer beyond it from where a pointer hold fired counts as starting to drag and cancels the hold's audition; ruling 10-05; no effect until pointer-hold auditions are built, and its help says so), and `scottland/window_avoidance_always`. Its “Window avoidance” toggle defaults off and controls whether scene-only reservations continue outside Window mode (WK13); it never changes real layout state. The old `scottland/hint_avoidance_always` key remains honored as a compatibility alias. Movement and resize each have a position-over-time graph after one impulse: drag the endpoint right for a longer coast and up for more travel. Each graph uses constant deceleration, `distance = v²/(2a)` and `duration = v/a`, and controls its own impulse/deceleration pair; movement deceleration also governs drag coasts. The old friction-law curves are removed. A live playground shows a flick or arrow push, stopping distance, 100 pt vertical stops and side-rail widget morph; its velocity arrow also edits movement impulse. Timings use visual timelines. Hints (S11) and Save/Cancel/Defaults cover every control. (Mike, 2026-10-02) | implemented; the x86 test machine headless, 177 Settings checks, 2026-10-03 |
| S15 | The app is called Scottland Settings (window title, panel heading, launcher entries, docs); Super+, opens it. (Mike, 2026-10-02) | implemented; isolated headless real-input checks and screenshots; see redesign validation below |
| S16 | One design system for the whole panel: tabs, Defaults/Cancel/Save and the curve editors match the tall grab-anywhere slider rows (same block shapes, separators, type, accent and modified colors, hover/pressed states). The panel grows with the output to about 63% of its logical width and 88% of its height, capped for comfortable reading and always within the output width, with generous outer/inner padding and section spacing. At enlarged text sizes or narrow widths, tabs wrap to a 3-by-2 grid; on shorter outputs the panel can use 94% of the logical height to keep its actions visible. Curves and the playground have room to be manipulated; help text lives in the S11 popout rather than repeating in the panel. (Mike, 2026-10-02: the current tabs and buttons are "uglier than sin") | implemented; isolated headless real-input checks and screenshots at 1280×720 and 2560×1600, scale 1.6; see redesign validation below |
| S17 | Touchpad pixel deltas scroll content about one logical point per point of finger travel with the shipped input speed; wheel notches scroll about 96 pt. Each input moves the panel immediately, and release starts a smoothly decelerating coast. Touch flicks use the native Flickable coast. (Mike, 2026-10-02) | implemented; isolated headless real-input checks and screenshots; see redesign validation below |
| S18 | Curve knobs are easy to grab: generous hit targets (at least ~24 pt) with a visible hover/selected state. A clicked knob is selected; Delete or Backspace removes the selected knob (endpoints can't be removed); double-click/right-click removal may remain. (Mike, 2026-10-02) | implemented; isolated headless real-input checks and screenshots; see redesign validation below |
| S19 | Scottland Settings has a Widgets tab for widget behavior: elastic expand/contract bounce (WG23), hover and attention peek timing (WG19), the live `widget_make_room_dwell` rail-pause setting (WG26; default 350 ms, range 100–1500 ms) and the Super+M hold delay (WG16). The controls share the S16 design system and live preview with Save/Cancel/Defaults. | implemented; Plumbus headless settings and rail-pause input checks (2026-10-04); Super+M hold delay headless |
| S20 | The Translucency tab has focused/unfocused opacity pairs for center windows, side-zone windows and widgets (all default fully opaque); Window mode has its own focused/unfocused pair, applied while active. Values preview live and ease over 180 ms as focus or zone changes. Fullscreen stays opaque. S11 hints and Save/Cancel/Defaults cover every pair. | implemented (headless) |
| S21 | Sunlight follows local sunrise and sunset when enabled, changing the standard light/dark preference only if its current mode is wrong, leaving a matching user-picked theme alone. System Geoclue city-level location comes first, followed by coordinates saved once in Settings; IP location is the last fallback. On by default, location included (Mike, 2026-10-02: he didn't ask for this panel but keeps it); both can be turned off; Save/Cancel/Defaults apply to `solar.ini`. The distro's cross-app theme engine and named day/night defaults stay outside core. | implemented (headless) |
| S22 | The Goo settings appearance rows tune the unfocused edge on both render paths: the active scheme's neutral gray level (with separate saved light/dark values) and its continuous tint strength from clear refraction to the existing look. Changes preview live; this A16 control leaves focus, attention and Window mode hint colors at full strength, while the separate GO23 Dye strength control scales them. Defaults, Save and Cancel include both tone values and strength. | implemented; plumbus headless two-output slider, save/reset coverage (2026-10-03) |

| S24 | Layout can edit all screens' global zone sizes or the screen Settings opened on. Its six sizes are keyed by the exact reported make/model/serial triple; outputs with the same triple share the override, and outputs with all three fields empty use global sizes. All-screens edits leave overrides intact. This-screen edits start from the override or current globals, apply to matching identities, and can reset an existing override to globals. Overlays draw each output's resolved sizes and show handles only where the current choice applies. Save stores every override in `layout.ini`; Cancel restores globals and overrides from opening. | implemented; verified by S24 headless acceptance (241/0) and added-output placement |

## Preview and help

Layout and Goo use the same `ParameterStack` rows. Each numeric row explains its effect and
what a higher or lower value looks like in a separate Quickshell `PopupWindow` beside the row.
The bubble has 14-point padding, wrapped 15-pixel text multiplied by the desktop text scale,
and a repeated row label in the accent color for clear association. The popout’s colors,
interface font and text scale come from the session palette, with defaults when no palette
is available.
The row itself stays label + value, baseline-aligned and vertically centered.

The anchor spans the row plus a gap outside the panel. Quickshell's `FlipX` adjustment moves
it to the other side when the preferred side would leave its output; `SlideY` keeps it vertically
on screen. Horizontal sliding is deliberately disabled so the hint cannot cover the slider.
An empty input region and `grabFocus: false` leave pointer input and keyboard focus with the
controls or app beneath it. Hover shows the row under the pointer; keyboard selection shows
the selected row. The most recent input wins, so a stationary pointer does not obscure a
newly selected keyboard hint. Hover tracks screen coordinates, so scrolling content under a
stationary pointer also preserves the keyboard selection.
Leaving a hovered row hides its bubble; keyboard help stays until pointer interaction or loss
of focus. Popouts also hide with their tab/panel or when the row is clipped by scrolling.
The viewport offset refreshes the row anchor as Goo scrolls. Keyboard traversal still scrolls
Goo rows into view. The curve editors retain their visible editing instructions.

This follows tenet 2, recognition rather than recall: the explanation appears at the setting,
without a separate help mode. Tenet 5 also keeps the explanation outside the control being
adjusted and prevents it from taking input or focus. Goo wording follows `goo-model.cpp`,
`goo-shaders.hpp` and the source state in `scottland.cpp`: Bridge draw reduces border density near a join; Reach changes
falloff and joining distance rather than isolated resting thickness; Swell controls revealed
thickness; Drift wanders only while the simulation is awake; Relief affects lighting and
wallpaper refraction, rather than changing the goo's hit area.

## Overlay geometry and input

Each output uses its own logical width. The center boundaries are symmetric about its middle,
and rail widths are percentages of that output. Softness is in logical points, outside the
center on each side. As in the compositor's `place()`, its visible width is capped at half the
available center-to-rail span. The shaded blend band is separate from the curve shading; the
curve shading starts at the outer edge of the band. No band is drawn where rails overlap the
center.

The six zone sizes (center width, rail width, softness, scale curve, minimum scale and maximum
scale) can be global or saved for one screen identity: the exact make, model and serial reported
by the compositor. Outputs with the same triple share one override. An output with no reported
identity follows the global sizes. The connector name is used only to match the Settings window
to its output while the app is running; it is never saved as an identity.

The Layout choice opens on “This screen” when that screen already has an override, otherwise on
“All screens”. The target stays the screen Settings opened on, even if the window moves. “All
screens” changes global sizes while screens with overrides keep theirs. “This screen” starts from
its override or the current globals and edits every connected output with the same identity; an
existing override can be reset to the global sizes. The overlay draws each output's resolved
sizes and exposes border handles only on outputs affected by the current choice. Preview is live.
Save writes globals and every override, including disconnected screens, to `layout.ini`; Cancel
restores both from when Settings opened.

Each border has a narrow 12-point input strip, a visible grip, and a horizontal resize cursor;
hover brightens the border. The layer-shell input region is the union of these six strips.
Shading and labels pass input to the desktop. The zone overlay is on the Top layer, with the
settings panel on Overlay, so interacting with a border cannot raise labels above the controls.
At coincident boundaries (including zero softness), center, rail and softness handles occupy
separate vertical thirds, in that order, keeping all settings reachable.

Dragging either center edge changes `center_width` symmetrically; either rail edge changes
`rail_width`; either outer band edge changes `blend_width`. Motion uses coordinates in the
owning output, with an opening pointer position and value, so moving the handle under the
pointer does not feed its own motion back into the drag. All edits pass through the same range
and step logic as the sliders (center 10–90%, step 0.5%; rails 0.5–10%, step 0.1%; softness
0–300 pt, step 1 pt). A softness drag starts from the effective band width on that output when
the configured width is capped, keeping the visible edge attached to the pointer.

Preview updates are coalesced every 30 ms, including continuous motion. Save/Return persists
the current global values and all screen overrides; Cancel/Escape restores all opening values and writes nothing,
including when Escape interrupts a held border. Borders take keyboard focus on demand to
retain those shortcuts after a drag. Reopening reads the saved/current settings.

## Validation

`tests/settings-help-test.sh` requires a fresh `SCOTTLAND_HEADLESS_DIR`, starts two headless
outputs, and stops only that session. `tests/settings-help-test.py` uses stipc pointer, button
and keyboard input. The current suite reads opt-in, read-only QML geometry and visible-hint snapshots (no OCR);
pixel checks inspect row layout, theme colors, enlarged glyphs, both band boundaries and shading;
IPC only reads preview values and arranges the app fixture. The suite covers all numeric hints,
both sides of all three borders on both outputs, continuous drag updates, hover highlighting,
Save/reopen, Cancel, Escape during a held drag, effective softness capping, zero-width handle
access, and clicking/typing through the overlay and popout to an ordinary app. The earlier hint fixture documents output-edge flipping; the redesign retains the same popup anchor policy. Palette changes are scoped
to the test session and cover a light theme, a serif interface font and 150% text size.
Screenshots, panel logs, test results and the compositor log stay in `build/settings-help-evidence`.

The existing Goo suite additionally covers live field changes, row steps, numeric entry,
reset, scrolling, curve editing, Defaults, Save and Cancel. Headless input and screenshots
establish implementation coverage; physical display, touch input, mixed DPI and rotated
outputs remain outside this task's explicitly isolated test scope.

## Settings help validation (2026-10-02)

On osanwe, the plugin and test helpers were built in `scottland-settings-help`. All sessions
started after that build, each with a unique `SCOTTLAND_HEADLESS_DIR`; none reloaded a session.
The final settings help suite passed **119 checks** on two 1280×720 outputs. The existing Goo
suite passed **40 checks** in a separate single-output session, including live field edits,
curve, Defaults, Save/config-consumer, Cancel and settlement. QML loaded without syntax,
binding or reference errors. Python and shell syntax checks and `git diff --check` passed.
The initial harness failures (row/footer coordinates, fractional step rounding, active-output
placement and a border obscured by the panel) are retained in numbered results logs alongside
the passing run. The live checkout and `wayland-1` were neither changed nor used.

Inspected images include `02-layout-hover.png`, `05-layout-keyboard-pointer-stationary.png`,
`goo-keyboard-goo_release.png`, `07-border-drags.png`, `07a-handle-hover.png`,
`08-capped-softness.png` and `09-click-through.png` under `build/settings-help-evidence`.
Goo field/panel regression images are under `build/goo-evidence`. All task sessions were stopped.

## Readable popout validation (2026-10-02)

The `hint-popouts` change was built and exercised entirely in this checkout. This historical
validation used a unique `SCOTTLAND_HEADLESS_DIR` and a private `XDG_RUNTIME_DIR` per run;
current testing rule 7 forbids redirecting `XDG_RUNTIME_DIR`, and the batch validation below
used the ordinary runtime with uniquely named sessions and artifacts under `build/`.
the installed checkout and live session were untouched. The final settings help suite passed
**143 checks** on two outputs, including every Layout/Goo row, pointer/keyboard precedence,
label-and-value-only rows, held slider input, scrolling/dismissal, clicking and typing through
a popout, output-edge flipping, and live theme/font changes at 150% text size. The existing
Goo suite passed **46 checks**, preserving row editing, scrolling, curve editing, Defaults,
Save and Cancel. These runs rechecked S1–S5 and S10–S13. Python/shell syntax checks and
`git diff --check` passed; QML logs contain no configuration, binding or reference errors.

Screenshot inspection covered `02-layout-hover.png`, `06a-held-slider-popout.png`,
`06b-themed-large-popout.png`, `06c-popout-flipped-left.png`,
`goo-keyboard-goo_release.png`, `goo-keyboard-scroll-pointer-stationary.png` and
`09a-popout-over-app.png` in
`build/settings-help-evidence`, plus the panel/curve images in `build/goo-evidence`.
The live-theme test caught an old popup buffer surviving a font/size change on Wayfire;
remapping only the passive hint on style changes refreshes both its pixels and geometry.
All isolated sessions were stopped after testing. Physical displays and mixed DPI remain
outside the explicitly headless scope of this change.

## One settings design system and Window mode (S14–S18)

The application name and heading are **Scottland Settings**, including the desktop launcher
and adapter menu label. Super+, retains its existing binding. The panel is a layer-shell
surface, which has no toplevel window-title protocol; its application name and visible heading
carry the name.

`Design.qml` supplies the session palette, interface family, text scale, 12-point corner radius,
subdued separators and accent tints. The tall parameter rows, joined tab strip, action buttons,
curve cards, timing rows and passive hint bubbles use that vocabulary. Pointer hover, presses,
keyboard focus and modified values have distinct states. Return saves even from a focused action
button; Escape cancels. Defaults affects the current tab, while Save and Cancel cover every tab.

The shared Layout/Goo curve editor gives each knob a 36-point hit disk. Clicking selects; Delete or
Backspace removes an interior point. Endpoints remain, and move vertically. Left/Right selects
a knob; Up/Down adjusts its height. Curves, including Goo's descending falloff, retain the
existing monotone Hermite interpolation.

The movement and resize coast graphs show position in logical points against seconds after one
arrow impulse. The draggable endpoint is where velocity reaches zero. From its duration `t`
and distance `d`, each graph sets `impulse = 2d/t` and `deceleration = 2d/t²`, within the plugin
option bounds. Movement uses `key_impulse` and `key_friction`; resize uses independent
`resize_impulse` and `resize_friction`, with the same 335 pt/s and 608 pt/s² defaults.
Constant deceleration gives `position(t) = impulse*t - deceleration*t²/2` until stopping,
so the default impulse travels 92.29 pt. Movement arrows and drag releases share movement
deceleration; Ctrl+arrow resize uses the resize pair. The axis integrator includes the exact
partial stopping frame. The speed cap and hint timings remain Settings controls, and WK29's
`cycle_overshoot` has its own Window mode row. WK38's `window_mode_tint` (0–100%, default 7%) has its own row below the Window mode opacity pair; 0 turns the overlay off. WK42's `hint_background_opacity` has a separate row beside it in the same stack; its 0–100% value is the absolute opacity of every hint's backing, window and widget alike (default 21%).

The playground uses those same equations: flick its sample or press arrows, with Ctrl for
resize. It draws the travelled path, vertical stops and the side-contact widget morph, and reports
travelled and stopping distance. Drag the velocity arrow to set movement push strength. Timelines show Alt-down to hint appearance and hint release to the start of the repeated
hint; their markers change the existing hold and double-tap intervals. All changes preview
live and participate in the opening-value transaction. The speed limit uses a row.

The scroll viewport applies touchpad pixel deltas directly after compensating the shipped 0.2 compositor speed, and wheel notches move 96 pt. Both then coast at constant deceleration; touch uses native Flickable inertia. A vertical finger gesture on a parameter row belongs to scrolling;
a horizontal gesture belongs to that slider. Keyboard row traversal stops the coast and reveals
the selected row. Hints remain passive and hide when their control leaves the viewport.

These choices follow tenet 2: people recognize the velocity, braking and timing they are
changing, without remembering option names. Tenet 3 keeps Cancel as a return to the opening
state. The playground never moves another real window on the user's behalf (tenet 5).

### Redesign validation (2026-10-02)

All sessions were started from this checkout after its plugin build, using unique
`SCOTTLAND_HEADLESS_DIR` directories under `build/`. The existing runtime was retained, so the
backend allocated distinct display names; no test redirected `XDG_RUNTIME_DIR`, read personal
layout/overrides, installed files, reloaded the live session or changed the live widget service.
Each runner stopped its own session. Physical-display validation is intentionally outside the
user-authorized headless scope.

`settings-help-test` extends S1–S13 coverage with rename, tab/action/curve interactions,
36-point knob selection, protected endpoints, Delete/Backspace, wheel/touchpad/touch coast and
settlement, playground impulses, side morph and vertical stops, timing edits, persistence, Defaults and Cancel.
The QML probe is enabled only by `SCOTTLAND_SETTINGS_TEST=1`, read through that process’s Quickshell IPC and exposes no mutation methods.
Inputs are stipc pointer/keyboard/touch events; because this installed stipc lacks wheel input,
a headless-only `test-input` extension emits real wlroots wheel/finger axis events through the
compositor. Position/velocity samples are retained as JSON. No OCR is used.

The Settings test drags each position-over-time endpoint, checks its live impulse and
deceleration, then measures the resulting real-window movement and resize travel.
The existing inertia and windowing regressions ran alongside Goo metadata coverage. Early failures
and the incomplete test-palette assertion remain in numbered logs; the test fixture now supplies
a complete palette. Batch results:

| Suite | Result |
|---|---|
| `settings-help-test` | 171 passed |
| `inertia-test` | 65 single-output + 8 two-output passed |
| `windowing-test` | 84 passed |
| `settings-coverage-test` | 24 Goo + 8 inertia options covered |
| `inertia-unit` | 35 passed |

The final settings observer uses on-demand IPC rather than periodic disk writes, so measuring
scrolling cannot stall the UI on filesystem writes. Test processes disable QML file watching to
keep each run on the sources it launched with. Python/shell syntax checks and `git diff --check`
also passed.

Visual review used `build/settings-layout-evidence/1280-layout-wrapped.png`,
`1280-window-mode-wrapped.png`, `2560x1600-scale1.6-layout.png`,
`2560x1600-scale1.6-window-mode.png`, `2560x1600-scale1.6-coast-graph.png` and
`2560x1600-scale1.6-goo.png`. The larger output ran at scale 1.6, yielding 1600×1000
logical points; both sizes used Mike's 1.6364 interface text scale. The enlarged tabs wrap
without overlap, and the graph, playground, rows, popouts and actions remain usable.
`build/settings-help-evidence` holds the interaction screenshots, `wheel-samples.json` and
`touchpad-samples.json`. Goo evidence remains in `build/goo-evidence`.

## Widget elasticity integration (WG23 / S19)

`scottland-ctl get`, `set` and `stdin` expose `widget_bounce`: a value from 0 to 0.1, default 0.04 (4% size overshoot). Zero keeps the existing monotonic easing; nonzero uses a 360 ms single bounce with bounded overshoot on large contractions. The Widgets tab now controls it with Save/Cancel/Defaults, alongside hover intent, hover leave and attention-peek timing (WG19) and the Super+M hold delay (`minimize_hold_delay`, 100–2000 ms, default 300: a press held this long is momentary instead of a tap, WG16).

## Translucency, widgets and sunlight (2026-10-02)

The compositor exposes the eight opacity options and four widget controls through metadata and
`scottland-ctl`. The opacity of a frame is independent of the temporary alpha used to hide a
widget during a morph; fullscreen has no frame. Window mode's pair takes precedence only while
Window mode is active. The six normal-zone pairs return when it ends. Defaults are all 1.0.

WG23's `widget_bounce` changes only widget expansion/contraction. Rail motion has no rebound
control. Widget peek delays default to 150 ms on hover, 100 ms on leave and 5000 ms for attention.

Sunlight writes `~/.config/scottland/solar.ini`. Latitude and longitude are used only if the
system location service is unavailable; entering both coordinates enables the saved pair.
The network lookup switch is off by default and calls ipapi.co only after explicit permission.
A matching current light/dark preference is left alone. The per-session desired mode is published
in `<XDG_STATE_HOME>/scottland/<WAYLAND_DISPLAY>.solar-mode` for adapters.
An isolated headless `once` run used the keyfile GSettings backend and saved coordinates when
Geoclue was unavailable: it changed `prefer-dark` to `prefer-light` for daylight and published
`light` in that session's state directory. No live preference or personal solar config was changed.

## Hint background opacity (S23 / WK42, 2026-10-06)

The Window mode slider is separate from window opacity and hint-color overlay strength.
It is one absolute opacity for the background behind hint letters, applied to window hints
and exterior widget hints alike: 100% is a solid backing, 0% no backing, default 21%.
Every hint's backing is the same color, the theme background under a 21% hint-color tint,
the color hint letters are chosen to contrast with. At low values what lies beneath shows
through, so over wallpaper a widget hint's letter contrast depends on the setting.

The first version (same day) scaled each kind's own fill instead, so 100% meant a 21%
hint-color fill on window hints and an opaque themed fill on widget hints. Mike: "why
does 100% mean 21% that makes no sense" and "it should set both. having them different
is a bug." The PO confirmed the shared absolute 0–100% range and 21% default in ruling 10-07;
there is no contrast floor or automatic adjustment.

`tests/hint-background-opacity-test.sh` uses an owned isolated headless session. A light
window fixture and a real Super-dragged rail widget carry one hint each. Real pointer and
keyboard input opens the tab and edits the row live while Alt is held. Pixel oracles,
independent of the plugin's reported state: at 0% the fill shows exactly the surface
beneath (the window's own surface, or the wallpaper beside the widget hint); at 100% every
fill pixel is the themed backing color computed from the palette and hint color; at 50% and
at the default, a least-squares fit of the fill pixels gives the backing's opacity, which
must be 0.5 and 0.21 and equal for both kinds. Opaque letter ink, fallback rim ink and
window content are unchanged. A lone `scottland-ctl set hint_background_opacity` while
hints show must redraw them (this fails without the option's live callback; the Settings
batch write alone would refresh hints through another option). Save persists a separate
value, the config generator consumes it, reopening reads it, and Cancel and Defaults
(the plugin's own default) exercise the existing transaction without unintended writes.

Results (Plumbus headless, 2026-10-06, prior integration tree): 25/0 with fallback halos
and 23/0 with Goo. Fitted backing opacity, window / widget: default 0.211 / 0.206, 50%
0.506 / 0.496 (Goo 0.495), 100% 1.003 / 0.997. With the option's live callback removed the
test fails exactly the scottland-ctl check (22/1). `tests/hint-style-test.sh` (53 checks)
passed with Goo on that tree; it measured widget letter contrast over dark headless wallpaper
at the 21% default: 6.37:1 with the dark theme, 1.32:1 with the light one. These are historical
results: the current main-based candidate must rerun because main changed the headless wrapper
and Goo framebuffer path. Captures inspected.
