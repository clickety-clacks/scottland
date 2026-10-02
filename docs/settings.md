# Settings preview and help

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
