# Settings (Scottland Layout)

Super+, opens Scottland Layout: tabs for Layout (zones, scale curve), Goo (GO9) and, planned, Window mode (S14). While it is open, a zone overlay shows and lets you drag the zone borders.

## Invariants

| ID | Invariant | Status |
|---|---|---|
| S1 | The zone settings (center edge softness, center zone width, widget rail width) are one stack of tall rows, each row a slider grabbed anywhere along it (label left, value right, thin separators, one rounded block), after the shared parameter-slider design: Left/Right adjust (Shift: larger steps), Up/Down or Tab move between rows, digits type a value, Backspace resets a row, double-click resets a row to its value when the panel opened; changed values show in the accent color. Super+, opens the panel. | implemented; isolated osanwe pointer/keyboard rows and hint screenshots checked |
| S2 | While the panel is open, an overlay shows the zones on every screen; it is click-through except for the border handles (S13). | zones verified; handle-only input and ordinary app click-through checked on two isolated osanwe outputs |
| S3 | Moving a zone slider rescales windows on screen live; Goo rows change the field live. | zones verified; Goo field and continuous border preview checked with isolated osanwe input |
| S4 | Save persists zone and Goo values (`~/.config/scottland/layout.ini`); reopening shows the saved values. | zones verified; Goo Save/config-consumer and border Save/reopen checked with isolated osanwe input |
| S5 | Cancel or Escape restores zone and Goo values from when the panel opened and saves nothing, including the live Goo switch and empty/default falloff. | zones verified; Goo and border Cancel/Escape, including a held drag, checked with isolated osanwe input |
| S6 | If the running session's plugin predates a setting, the panel says "Restart Scottland to use: …" and keeps the saved value instead of resetting it. | implemented |
| S9 | A 2D curve editor sets the scale across the side zones: two endpoints (largest scale at the center edge, smallest at the rail) move vertically only; clicking adds a point, dragging shapes the curve, double-click or right-click removes a point. Points are joined by a smooth curve that doesn't overshoot them (monotone cubic), and the plugin uses the same curve. | verified |
| S10 | A Goo section beside Layout exposes the live goo switch and all nineteen numeric settings in the same tall grab-anywhere ParameterStack rows as the zone settings (S1), including keyboard steps/navigation, typed values, opening-value reset and modified color. Keyboard navigation scrolls to the selected row; the scrollbar reaches the falloff curve in the shared editor. Save, Cancel and Defaults include goo. See [GO9](../docs/goo.md). | implemented; plumbus and osanwe headless pointer/keyboard input and screenshots checked |
| S11 | Every Layout and Goo numeric row explains itself: hover or keyboard selection shows a separate floating popout beside the row, flipping sides when needed on its screen. Its plain-language hint explains what raising/lowering the value looks like, with a padded theme-colored bubble, the interface font at 15 px times the desktop text scale, and the row label for clear association. It never covers the dragged slider or takes input/focus, hides on pointer leave (keyboard selection persists), when the row scrolls out of view, or on close; rows contain only label and value. (Mike, 2026-10-02) | implemented; isolated headless hover/keyboard OCR, edge flip, theme/text scale, held drag, dismissal and input pass-through checked; see [settings design](../docs/settings.md) |
| S12 | The zone overlay shows the center edge softness: the blend band on each side of the center zone where windows ease from full size into the side-zone scale is visible as its own shaded region with its outer border drawn as a line like the zone edges, updating live. (Mike, 2026-10-02) | implemented; isolated osanwe two-output stipc input and screenshots checked; see [settings design](../docs/settings.md) |
| S13 | While the panel is open, the zone borders (center zone edges, rail edges, softness band edges) can be dragged directly on screen; dragging updates the same values as the sliders, live, with the same Save/Cancel semantics. The overlay stays click-through everywhere except the border handles; while a border is held the whole overlay takes input, so a long or fast drag stays with the pointer until release. (Mike, 2026-10-02) | implemented; isolated osanwe two-output stipc input and screenshots checked; see [settings design](../docs/settings.md) |
| S14 | Scottland Layout has a Window mode tab beside Layout and Goo for everything that shapes the inertial transformations (keyboard pushes, inertial resize, the drag coast), plus hold and double-tap timing. Each control is the most direct interactive visualization that fits, with plain sliders only where no better visual exists: the friction law for movement and for scaling (resize) as draggable curve editors (deceleration as a function of current speed, S9 style; the default reproduces today's constant deceleration, so motion stays physical and one model drives keys and drag coasts); a live playground in the tab where you flick or arrow-push a sample window and see its trajectory, stopping distance and edge bounce drawn as it happens, with handles to drag directly (e.g. the push impulse as a velocity arrow, bounciness on the bounce trace); timings as visual timelines where that reads better. Hints (S11), Save/Cancel/Defaults as in the other tabs. (Mike, 2026-10-02) | planned, after the drag coast lands |
| S15 | The app is called Scottland Settings (window title, panel heading, launcher entries, docs); Super+, opens it. (Mike, 2026-10-02) | planned |
| S16 | One design system for the whole panel: tabs, Defaults/Cancel/Save and the curve editors are designed to match the tall grab-anywhere slider rows (same block shapes, separators, type, accent and modified colors, hover/pressed states), not stock controls. (Mike, 2026-10-02: the current tabs and buttons are "uglier than sin") | planned |
| S17 | Scrolling the panel is inertial (flick to coast, smooth deceleration), for touchpad, wheel and touch. (Mike, 2026-10-02) | planned |
| S18 | Curve knobs are easy to grab: generous hit targets (at least ~24 pt) with a visible hover/selected state. A clicked knob is selected; Delete or Backspace removes the selected knob (endpoints can't be removed); double-click/right-click removal may remain. (Mike, 2026-10-02) | planned |
| S19 | Scottland Settings has a Widgets tab for widget behavior, starting with the elastic bounce of expand/contract (WG23); peek timing and similar widget settings belong there too. Same design system (S16). (Mike, 2026-10-02) | planned, after the settings redesign |

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

Each screen uses its own logical width. The center boundaries are symmetric about its middle,
and rail widths are percentages of that screen. Softness is in logical points, outside the
center on each side. As in the compositor's `place()`, its visible width is capped at half the
available center-to-rail span. The shaded blend band is separate from the curve shading; the
curve shading starts at the outer edge of the band. No band is drawn where rails overlap the
center. Changing one value previews it on every screen.

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
the current shared values; Cancel/Escape restores all opening values and writes nothing,
including when Escape interrupts a held border. Borders take keyboard focus on demand to
retain those shortcuts after a drag. Reopening reads the saved/current settings.

## Validation

`tests/settings-help-test.sh` requires a fresh `SCOTTLAND_HEADLESS_DIR`, starts two headless
outputs, and stops only that session. `tests/settings-help-test.py` uses stipc pointer, button
and keyboard input. Tesseract checks the actual row label in the popout outside the panel;
pixel checks inspect row layout, theme colors, enlarged glyphs, both band boundaries and shading;
IPC only reads preview values and arranges the app fixture. The suite covers all numeric hints,
both sides of all three borders on both outputs, continuous drag updates, hover highlighting,
Save/reopen, Cancel, Escape during a held drag, effective softness capping, zero-width handle
access, and clicking/typing through the overlay and popout to an ordinary app. A small fixture
using the real ParameterStack checks left-side flipping at an output edge. Palette changes are scoped
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

The `hint-popouts` change was built and exercised entirely in this checkout. Final headless
sessions used a unique `SCOTTLAND_HEADLESS_DIR` and a private `XDG_RUNTIME_DIR` per run;
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
