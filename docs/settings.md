# Settings preview and help

Layout and Goo use the same `ParameterStack` rows. Each numeric row explains its effect and
what a higher or lower value looks like in at most two lines inside the slider. The hint uses
existing foreground and accent colors, accepts no input, and leaves the whole row draggable.
Hover shows the row under the pointer; keyboard selection shows the selected row. The most
recent input wins, so a stationary pointer does not obscure a newly selected keyboard hint.
Keyboard traversal still scrolls Goo rows into view. The curve editors retain their visible
editing instructions.

This follows tenet 2, recognition rather than recall: the explanation appears at the setting,
without a separate help mode. Goo wording follows `goo-model.cpp`, `goo-shaders.hpp` and the
source state in `scottland.cpp`: Bridge draw reduces border density near a join; Reach changes
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
and keyboard input. Pixel checks inspect actual hint text, both band boundaries and shading;
IPC only reads preview values and arranges the app fixture. The suite covers all numeric hints,
both sides of all three borders on both outputs, continuous drag updates, hover highlighting,
Save/reopen, Cancel, Escape during a held drag, effective softness capping, zero-width handle
access, and clicking/typing through the overlay to an ordinary app. Screenshots, panel logs,
test results and the compositor log stay in `build/settings-help-evidence`.

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
