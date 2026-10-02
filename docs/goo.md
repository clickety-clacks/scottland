# The goo

The halo around windows (core/INVARIANTS.md, A3-A12) becomes **one goo for the whole screen**
instead of a halo per window patched together where they meet. Mike's design (2026-10-01): it should
feel truly organic, goo reaching for goo the way liquids do, waves running through it, color
spreading in it like food coloring in water. The interactive prototype used to explore the feel:
https://claude.ai/artifact/VHTqdn4TqvSN8kZ8CoRV64 (Scottland Goo Lab; its source is in
[prototypes/goo-lab.html](prototypes/goo-lab.html)).

This doc is the design and implementation record. `scottland/goo` selects it live; the shipped
default is **false**, retaining the existing halo. A3, A4, A6, A9, A10 and A11's goo behavior is
restated below; the original halo remains available.

## Model

- **One field per screen.** Every window adds a goo density that peaks at its edge and falls off
  within a short reach; the goo is where the total passes a threshold. Windows are islands lying in
  it: the goo outlines the union of the window shapes and is never drawn over any window, so there
  is no stacking question. It is drawn as one layer beneath all windows.
- **Clinging, fixed volume.** Each window owns an amount of goo held to its edge (surface tension),
  so goo exists only near windows, never everywhere windows aren't. A bridge between two windows
  draws from both borders, which thin where it attaches; stretched, it necks down and snaps.
- **Pooling.** Liquid collects in inside corners, so concave corners (where windows meet or
  overlap) fill because the goo pools there, with no special corner code.
- **Mess.** The amount of goo along each edge wanders slowly (low-frequency noise): lumps drift, no
  two borders look the same, nothing looks machine-perfect.
- **Waves.** A shared wave surface pushes the goo's boundary in and out. A grab, a drop, a swell or
  an attention pulse starts a ripple there; it travels only through connected goo, around the whole
  merged outline, and fades.
- **Dye.** Color is dye carried by the goo, separate from the goo itself. Each window keeps releasing
  its current color into the goo it owns (neutral, focus, attention). Dye spreads and slowly swirls,
  only within goo: where goo connects two windows their colors bleed across the bridge; apart, they
  stay separate. A new state blooms from the window's edge outward; an answered one fades as the
  window's neutral color replaces it.
- **States are dye, not decorations.** Everything that marks a window's state on the halo is dye
  dropped into the goo (and, where it needs presence, a little more goo): focus, attention, the
  resize corner the pointer is near (a bright dye and a thickening at that corner, which blends into
  the surrounding goo with no seams), the close dot's glow. Nothing is painted on top of the goo
  with its own edges.
- **Input reads the same field.** Resize corners, the close dot and grab areas are found by
  evaluating the field at the pointer; hit-testing doesn't depend on drawing. A window corner tucked
  inside another window has no goo, so it has no resize handle there; the exposed corners do.
- **Cost.** A low-resolution grid per screen on the GPU (goo, waves, dye), idle when everything is
  settled.

## Tuning

Every goo constant is a Scottland setting with a live control in the settings app (beside the zone
sliders and scale curve): reach, border thickness, bridge draw, mess, lump size, drift, wave speed,
wave persistence, wave height, dye spread, dye swirl, dye release, shine, relief, and the falloff
curve (how density drops away from an edge) in the curve editor. Changes apply live, as the zone
settings do. Anyone can tune it. The initial defaults are the prototype’s Scottland preset.

## Invariants

| ID | Invariant | Status |
|---|---|---|
| GO1 | One goo per screen: one field from all windows, drawn as one layer beneath all windows, outlining the union of the window shapes and never drawn over a window. | implemented; headless union/content, two-output drag and screenshot checks |
| GO2 | The goo clings: each window's goo stays within a reach of its edge; between windows close enough, it bridges, drawing from both borders, and a stretched bridge thins and snaps. | implemented; prototype volume approximation, bridge/snap input checks |
| GO3 | Inside corners (where windows meet or overlap) fill smoothly because goo pools there; no corner-specific code. | implemented; overlap pooling screenshot inspected |
| GO4 | The goo isn't uniform: its amount along each edge wanders slowly, configurable (mess, lump size, drift). | implemented; prototype noise port, inspected; drift freezes to settle |
| GO5 | Waves start at grabs, drops, swells and attention pulses, travel only through connected goo along the whole merged outline, and fade. | implemented; grab propagation across bridge and isolation across gap sampled |
| GO6 | Color is dye in the goo: each window releases its state's color into its own goo; dye spreads and swirls only within goo, bleeding across bridges between connected windows. | implemented; bridge/gap dye sample checks |
| GO7 | Halo state markers are dye (plus goo where they need presence), never separately drawn shapes: focus, attention, the hovered resize corner (no hard edges where it meets the rest of the halo), the close dot's glow. | implemented; palette, corner and close screenshots/input checks |
| GO8 | Resize corners, the close dot and grab areas are hit-tested against the same field; a corner hidden inside another window has no handle. | implemented; pointer/touch move, resize, close and hidden-corner checks |
| GO9 | Every goo constant, and the falloff curve, is a setting with a live control in the settings app. | implemented; live slider/curve, Save/Cancel/Defaults checks |
| GO10 | The goo costs nothing while the desktop is still: its simulation sleeps when settled. | implemented; GPU energy sleep and unchanged step count checked |

## Halo jobs with goo enabled

- **A3/A9:** a single shaded, translucent liquid field outlines all visible windows and rail widgets.
  Resting thickness scales with the window; the Scottland preset starts at 13 pt at full scale.
  Refraction samples the real wallpaper, with relief, rim light and specular highlights.
- **A4/A8:** neutral dye follows light/dark, focused dye uses the live palette accent, and attention
  dye uses the palette attention color. Existing focus transitions and attention breathing feed
  the liquid. Theme integration continues through the existing palette file; core adds no adapter
  dependency. Tenets 1 and 5: asking for attention colors/pulses the goo without moving or raising
  the window.
- **A5/A6:** the field owns move handles and exposed resize corners; resizing still keeps the center.
  Corners gain a Gaussian deposit of dye and density, with no separate corner patch. For A5's 12 pt
  minimum target, the same field is dilated only as far as needed. A shared bridge's strongest
  contributing window owns its input; existing stable source order breaks exact ties. This resolves
  the old front-window rule for one shared liquid without adding a second visible border.
- **A7:** the existing dwell, proximity, drag and linger rules remain. Full reveal is twice the
  full-size goo thickness (26 pt at the preset), independent of window scale; the Swell control
  scales that response. Swells and grabs excite waves.
- **A10 → GO2/GO3:** summed density naturally pools at concave joins and forms bridges. The old
  separately drawn meniscus is replaced by pooling; no corner-specific pooling code is used.
- **A11/A12:** the close target remains at the bottom midpoint and appears on proximity or touch.
  Its mark is a soft dye bloom and density deposit, not a separately drawn circle. Pointer and
  touch move/resize/close retain their existing behavior. Three-finger and Super drags use the
  same desktop input paths and continue to scale live.
- **Fullscreen:** model-owned FS1 focus places a fullscreen window is a non-emitting island clipping the entire output, including
  transparent fullscreen clients. Its surface is suspended, including hidden attention, until
  fullscreen ends. This follows tenet 6: nothing interrupts fullscreen focus.

## Implementation and defaults

`goo-model.*` holds source snapshots, the CPU field and falloff; `goo-hit.cpp` owns field input;
`goo-renderer.*` and `goo-shaders.hpp` own GPU resources and the prototype shader port; `goo.cpp`
owns output nodes, option updates, impulses and sleep. The desktop model supplies its window list, widget lifecycle/away state, attention, drag and
FS1 fullscreen focus; goo samples geometry and state cross-fades from the frame presentation,
including collapsed and morphing sizes. It does not duplicate widget or drag state. Model-owned
`goo_outputs` records which screens have an available surface (including live disable and GPU
fallback); the desktop snapshot publishes these screen names as `goo`. Simulation counters and
source samples are renderer diagnostics, excluded from model snapshots. One background scene node per output
sits above the wallpaper and below all windows. During a cross-output move it uses the move tool's
current transformed geometry on each intersected output.

The prototype's volume rule is preserved: nearest-neighbor gap reduces each border's source amount
as a bridge draws from it. It is an approximation of fixed volume, not a conserved-fluid solver.
Noise scales down with small windows and a small clinging reserve prevents mess from erasing their
borders. Pooling comes solely from the sum and threshold.

Density is half resolution; height/velocity and dye are quarter resolution. There are two wave
steps and one masked curl/advection/diffusion dye step per active update. Union clipping excludes
window islands from both flow and rendering. Unsupported half-float render targets use packed
RGBA8 (log density and quantization-aware wave damping); missing float source textures, failed
targets or shaders retain the halo with a log message.
GLES 2 limits the source list to 1024; GLES 3 loops use the actual source count.

Drift and curl time freeze two seconds after the last geometry/state change (except ongoing
attention). GPU max reduction of wave energy and dye change every 30 updates decides sleep after
at least three seconds. Sleeping disconnects the timer, stops simulation and source uploads, and
reuses the settled image when other desktop damage needs painting. This interprets GO10 as no
simulation work at rest; ordinary compositor repainting still costs a draw. Tenet 1 favors stillness
after the liquid response over endless unattended motion.

The Goo tab has a live switch, fifteen tall grab-anywhere `ParameterStack` rows and the shared
curve editor. These are the same component as the Layout rows (whose first row is Center edge
softness), with the same Left/Right and Shift steps, Up/Down/Tab navigation, typed values,
Backspace and double-click opening-value reset, and modified-value color. Keyboard navigation
scrolls the selected Goo row into view; the scrollbar reaches the falloff editor without dragging
a parameter. The opening Goo object is published atomically for row reset bindings. Defaults resets the
Scottland preset and the shipped off switch. Save writes the values with the zone settings to
`layout.ini`; Cancel/Escape restores the opening values, including an empty/default curve, without
writing. Live updates are batched; Save/Cancel wait for the control process to acknowledge
them, and Save finishes an atomic file write before closing. The falloff curve is monotone cubic,
spans zero to four reaches, and continues exponentially beyond the editor. Empty means the prototype
exponential. Invalid curves keep the last valid LUT.

| Panel control | Option suffix (`scottland/goo_…`) | Default |
|---|---|---|
| Border thickness | thickness | 13 |
| Reach | reach | 24 |
| Bridge draw | thinning | 0.45 |
| Swell | swell | 0.7 |
| Mess | noise | 0.32 |
| Lump size | lump | 190 |
| Drift | drift | 0.12 |
| Wave speed | wave_speed | 0.28 |
| Wave persistence | wave_damp | 0.985 |
| Wave height | wave_height | 0.55 |
| Dye spread | spread | 0.45 |
| Dye swirl | swirl | 0.9 |
| Dye release | release | 0.06 |
| Shine | shine | 0.75 |
| Relief | relief | 5 |
| Density falloff | falloff | empty (exponential) |

## Original branch verification (2026-10-01)

Testing uses the isolated `Projects/scottland-goo` checkout and
`scottland-headless-goo` session on plumbus, with real stipc pointer, key, touch and drag input.
No live osanwe session or plumbus real screen was installed, reloaded or used. Accordingly these
rows say **implemented/headless checked**, not verified on a real login/display (AGENTS.md D2).
`tests/goo-test.py` writes inspected screenshots and timings to `build/goo-evidence`.
`tests/goo-model-test.cpp` checks finite input, curves, union exclusion, bridge/snap and thinning.

| Check | Result |
|---|---|
| Existing widget suite, goo off / on | 102 / 102 passed, including Escape drag cancellation and reload |
| Existing present suite, goo off / on | 6 / 6 passed |
| Goo interactions, palette, panel, switch and sleep (RGBA16F) | 30 passed |
| Same goo checks with GLES 2 and packed RGBA8 | 30 passed |
| Connected/gapped waves, wide bridge input, fullscreen and two-output drag | 10 passed |
| Missing float source textures: halo fallback and real halo drag | 2 passed |
| Widget launcher / widget bus | 17 / 8 passed |
| Concurrent config builder / focus hooks | 5 rounds of 20 builds / 3 passed |
| CPU field model | all assertions passed |
| Sandboxed stock shell, goo off / on | rendered both; same four sandbox mise trust warnings, no QML load errors |

On the RX 580 at 1280×720 with two sources, the last completed normal-path GPU query measured
**0.508 ms** for simulation plus drawing. The corresponding CPU submission/reduction sample was
**0.906 ms**; ordinary submissions in the two-output test were about 0.08 ms. These are individual
samples, not a sustained frame-time benchmark. Both texture paths reached sleep and kept their
simulation step count unchanged. The forced GLES 2 context has no GPU timer query, so its reported
zero is unavailable timing, not zero cost.

Inspected evidence includes bridged/separated dye, exposed corner and close dye, overlap pooling,
the live panel/curve, light palette, restored halo, fullscreen, and a held/dropped cross-output
window. Artifacts and logs are under `build/goo-evidence`, `build/goo-flow-evidence` and
`build/goo-*-test*.log`; the packed-path screenshots are copied locally to
`build/goo-packed-evidence`. The compatibility tests constrain the RX 580's Mesa context;
they do not substitute for testing older physical GPUs.

Reproduce in the private plumbus checkout (never its real screen):

```sh
export TMPDIR=$HOME/.cache/scottland-test-tmp
export SCOTTLAND_HEADLESS_DIR=$XDG_RUNTIME_DIR/scottland-headless-goo
mkdir -p "$TMPDIR"
tests/headless.sh start --widgets
tests/headless.sh run python3 tests/goo-test.py
tests/headless.sh stop
# Start with SCOTTLAND_TEST_GOO_GLES=2 for the packed path, or
# SCOTTLAND_TEST_GOO_GLES=unsupported for goo-fallback-test.py.
# goo-flow-test.py needs SCOTTLAND_TEST_OUTPUTS=2 at start.
SCOTTLAND_TEST_GOO=1 tests/widgets-test.sh
tests/widgets-test.sh
```

Remaining coverage: physical screen/login, mixed DPI and rotated outputs, very large window counts,
and theme-file producer changes. The live palette consumer is checked, but no adapter code changes
were made. Hardware other than plumbus's RX 580 family remains untested.


## Main merge validation (2026-10-01)

The goo branch now includes main `a198ede` (the initial code merge was `16286df`), including the desktop model, Super+M fixes,
L29/L31, FS1 and the shared zone parameter rows. The model supplies goo's logical windows,
widget lifecycle/away state, attention and drag; the existing frame supplies their rendered
geometry, focus/attention transitions and collapsed/morph shape. Goo screen availability is
published in the versioned desktop snapshot. Live disable and unsupported-GPU fallback leave
no goo screens there; widget/attention slices contain no goo field or simulation samples.
The old halo is still the shipped default, with `scottland/goo = false`.

Both settings tabs use `ParameterStack`; Layout starts with Center edge softness. The Goo
stack follows keyboard selection through its scroll area (tenet 2: recognition keeps the
selected setting visible). The falloff curve still uses the shared curve editor. Real input
checks cover live row movement, Left/Right, Shift steps, Backspace opening-value reset,
typed values, navigation to Relief, curve editing, Save, Cancel/Escape and Defaults.

Deployment used `SCOTTLAND_DEPLOY_DIR=Projects/scottland-goo-merge tests/deploy.sh plumbus
--tests-only`. All checks ran in that isolated checkout, with scratch files beneath
`~/.cache/scottland-test-tmp`. Widget and seeded-model runs use the requested
`$XDG_RUNTIME_DIR/scottland-headless-goo-merge`; goo/panel and focused state regressions
also used private headless runtimes. Each compositor started on the current build; reload
checks were confined to those test sessions. No live session on osanwe, other checkout,
or physical screen on plumbus was installed, reloaded or used. Status remains
**implemented/headless checked**, not physical-display verification under D2.

| Check | Result |
|---|---|
| Widget suite, goo off / on | 146 / 146 passed (including nested input/process checks) |
| Seeded desktop-model audit, `271828 50`, goo on | 95 passed; audits after all 50 real-input operations |
| Goo input, palette, panel, screen publication and sleep, RGBA16F / packed GLES 2 | 38 / 38 passed |
| Wave isolation, wide bridge input, model FS1 and two-output drag | 12 passed |
| Explicit goo request on unsupported float-texture context, model availability and real halo drag | 3 passed |
| Focused state regressions, goo on | 6 passed |
| Attention / widget launcher / widget service unit checks | 5 / 17 / 16 passed |
| CPU goo field unit executable | all assertions passed |
| Config builder / focus hooks | 5 rounds of 20 concurrent builds / 3 passed |

The first headless start exposed a merge-resolution newline that detached the backend
environment; `605264f` fixes it. The first widget run used a private runtime without the
user systemd socket: six scope/adoption checks failed, including a later fixture-cleanup
check. Its log is retained as `widgets-off.log`; the final widget matrix uses the requested
host runtime so the scope checks exercise the real user manager. These failures were not
worked around by weakening assertions.

Inspected screenshots include the goo bridge and dye, the rebuilt Layout/Goo panel, keyboard
navigation to the last row, the falloff editor, fullscreen exclusion, cross-output held/drop,
and halo restoration. Both GPU paths load the panel without QML errors. Local evidence is in `build/merge-goo-evidence`,
`build/merge-goo-flow-evidence` and `build/merge-evidence`; the plumbus checkout retains the
same logs and source screenshots. Coverage still excludes physical login/display, mixed DPI,
rotation, large window counts and hardware beyond plumbus, as required by this task's isolation.
