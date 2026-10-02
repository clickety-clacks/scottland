# The goo

The halo around windows (core/INVARIANTS.md, A3-A12) becomes **one goo for the whole screen**
instead of a halo per window patched together where they meet. Mike's design (2026-10-01): it should
feel truly organic, goo reaching for goo the way liquids do, waves running through it, color
spreading in it like food coloring in water. The interactive prototype used to explore the feel:
https://claude.ai/artifact/VHTqdn4TqvSN8kZ8CoRV64 (Scottland Goo Lab; its source is in
[prototypes/goo-lab.html](prototypes/goo-lab.html)).

This doc is the design and implementation record. `scottland/goo` selects it live; the shipped
default is **true**. Switching it off, or a GPU unable to run it, retains separate per-window
halo bands. A3, A4, A6, A9, A10 and A11's goo behavior is restated below; the original halo
remains available.

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
settings do. Every numeric Goo row now shows a short explanation on hover or keyboard selection,
inside the shared slider; see [settings help and preview](settings.md). Anyone can tune it.
The initial defaults are the prototype’s Scottland preset.

## Invariants

| ID | Invariant | Status |
|---|---|---|
| GO1 | One goo per screen: one field from all windows, drawn as one layer beneath all windows, outlining the union of the window and widget shapes and never drawn over their content. Widget expand/collapse follows the animated frame rectangle, including reversals and rail anchoring. | implemented; headless union/content, two-output drag and screenshot checks; per-widget presentation morph: plumbus headless geometry and screenshots checked |
| GO2 | The goo clings: each window's goo stays within a reach of its edge; between windows close enough, it bridges, drawing from both borders, and a stretched bridge thins and snaps. | implemented; prototype volume approximation, bridge/snap input checks |
| GO3 | Inside corners (where windows meet or overlap) fill smoothly because goo pools there; no corner-specific code. | implemented; overlap pooling screenshot inspected |
| GO4 | The goo isn't uniform: its amount along each edge wanders slowly, configurable (mess, lump size, drift). | implemented; prototype noise port, inspected; drift freezes to settle |
| GO5 | Waves start at grabs, drops, swells and attention pulses, travel only through connected goo along the whole merged outline, and fade. | implemented; grab propagation across bridge and isolation across gap sampled on normal and packed GPU paths |
| GO6 | Color is dye in the goo: each window or widget releases its state's color at its presented edge, including while expanding/collapsing; dye spreads and swirls only within goo, bleeding across bridges between connected windows. | implemented; bridge/gap dye sample checks; widget presentation morph retains attention dye |
| GO7 | Halo state markers are dye (plus goo where they need presence), never separately drawn shapes: focus, attention, the hovered resize corner (no hard edges where it meets the rest of the halo), the close dot's glow. | implemented; palette, corner and close screenshots/input checks |
| GO8 | Resize corners, the close dot and grab areas are hit-tested against the same field; a corner hidden inside another window has no handle. | implemented; pointer/touch move, resize, close and hidden-corner checks |
| GO9 | Every goo constant, and the falloff curve, is a setting with a live control in the settings app. | implemented; live slider/curve, Save/Cancel/Defaults checks; all fifteen hover/keyboard hints and screenshots checked on isolated osanwe outputs |
| GO10 | The goo costs nothing while the desktop is still: its simulation sleeps when settled. | implemented; GPU energy sleep and unchanged step count checked |
| GO11 | Overlapping windows stay readable through the goo, not a border: each window's goo lies on top of whatever is behind that window, so a front window's edge shows its goo over the back window's content (a film whose width over windows behind is a setting with a Goo Panel row, `goo_overlap_film`, default a thin 4 pt, thickening to the full goo where it reaches open desktop). It is still one liquid: where that film meets other windows' goo it merges, and waves and dye cross the join. Hidden only by windows in front of it. (Mike, 2026-10-02; core) | planned, after the GO10 cost work |
| GO12 | Nearing a window's goo clouds it where the pointer is, clearly visible: at a corner (as before) and anywhere along the sides, the goo nearest the pointer turns cloudy (denser, milkier dye with swirl), fading with distance and following the pointer along the edge. Visual only: it does not change what the sides or corners do. Strength and spread are Goo Panel settings with sensible defaults. (Mike, 2026-10-02: corner clouding is barely visible in the goo today; the dye mark is released at only `release` strength.) | planned, after the GO10 cost work |

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
  contributing window owns its input; existing stable source order breaks exact ties.
- **Window-mode hints (WK14):** the transient frame color feeds the same goo source dye.
  The goo simply takes the hint color, at once while hints show (blended by contribution, so
  connected goo stays smooth); there is no separate rim. It adds no per-window joining layer and
  clears with the existing hint lifecycle, without changing focus or attention state.
- **A7:** the existing dwell, proximity, drag and linger rules remain. Full reveal is twice the
  full-size goo thickness (26 pt at the preset), independent of window scale; the Swell control
  scales that response. Swells and grabs excite waves.
- **A10 → GO2/GO3:** summed density naturally pools at concave joins and forms bridges.
  The fallback halo draws only its own band: it has no meniscus, bridge or shared-liquid grab rule.
  No corner-specific pooling code is used.
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
including per-widget expand/collapse presentation snapshots, rail-edge anchoring, reversals,
and the separate window/widget drag morph. The outline and dye source follow the animated
rectangle, rather than the client's already-applied final size. It does not duplicate widget or
drag state. Model-owned
`goo_outputs` records which screens have an available surface (including live disable and GPU
fallback); the desktop snapshot publishes these screen names as `goo`. Simulation counters and
source samples are renderer diagnostics, excluded from model snapshots. One background scene node per output
sits above the wallpaper and below all windows. During a cross-output move it uses the move tool's
current transformed geometry on each intersected output.

The prototype's volume rule is preserved: nearest-neighbor gap reduces each border's source amount
as a bridge draws from it. It is an approximation of fixed volume, not a conserved-fluid solver.
Noise scales down with small windows and a small clinging reserve prevents mess from erasing their
borders. Pooling comes solely from the sum and threshold.

Density is half resolution; height/velocity and dye are quarter resolution. The packed RGBA8
path stores each signed wave component in two bytes (16 bits), so small
velocities propagate along thin borders while rounding toward zero lets residual waves settle.
There are two wave steps and one masked curl/advection/diffusion dye step per active update. Union clipping excludes
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
Scottland preset and the shipped on switch. Save writes the values with the zone settings to
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
tests/widgets-test.sh                     # shipped default: goo on
SCOTTLAND_TEST_GOO=0 tests/widgets-test.sh  # explicit fallback halo
```

Remaining coverage: physical screen/login, mixed DPI and rotated outputs, very large window counts,
and theme-file producer changes. The live palette consumer is checked, but no adapter code changes
were made. Hardware other than plumbus's RX 580 family remains untested.


## Main merge validation (2026-10-01)

The goo branch now includes main `10774a2` (the initial code merge was `16286df`), including the desktop model, Super+M fixes,
L29/L31, FS1 and the shared zone parameter rows. The model supplies goo's logical windows,
widget lifecycle/away state, attention and drag; the existing frame supplies their rendered
geometry, focus/attention transitions and collapsed/morph shape. Goo screen availability is
published in the versioned desktop snapshot. Live disable and unsupported-GPU fallback leave
no goo screens there; widget/attention slices contain no goo field or simulation samples.
At this validation the old halo was the shipped default, with `scottland/goo = false`;
the goo-default change below supersedes that default.

Main advanced during validation: after the first complete green matrix it merged Alt hints,
focused-surface key layers and strict center placement. `a6cb985` brings that main into goo;
`ae6d2f4` composes parent presentation transforms (including Alt declutter) into the islands,
and field input uses those same source rectangles/radii/dots. Model geometry and zone memories
remain unchanged by declutter. The final matrix below was repeated on this current-main build;
real Alt input additionally checks displaced clipping and return to the original islands.

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
| Goo input, palette, panel, screen publication, Alt declutter and sleep, RGBA16F / packed GLES 2 | 40 / 40 passed |
| Wave isolation, wide bridge input, model FS1 and two-output drag | 12 passed |
| Explicit goo request on unsupported float-texture context, model availability and real halo drag | 3 passed |
| Focused state regressions, goo on | 7 passed |
| Main windowing / focused-surface key-layer real input, goo on | 73 / 59 passed |
| Main placement/declutter/Alt-controller units | 41 passed |
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
Alt-declutter outlines, and halo restoration. Both GPU paths load the panel without QML errors. Local evidence is in `build/merge-goo-evidence`,
`build/merge-goo-flow-evidence`, `build/merge-evidence` and `build/current-main-evidence`; the plumbus checkout retains the
same logs and source screenshots. Coverage still excludes physical login/display, mixed DPI,
rotation, large window counts and hardware beyond plumbus, as required by this task's isolation.


## Widget presentation merge validation (2026-10-01)

The branch includes main `50e563e` through merge `88595e6`: per-widget presentation snapshots,
rail anchoring, premultiplied content blending, WP7 screen padding and the WG21 lifecycle ID.
The sole source conflict retained both `goo_sources()` and `widget-presentation.hpp`. The frame's
animated rectangle supplies the goo island and its dye source, including interrupted transitions;
the disabled goo path retains main's halo renderer. At this validation the shipped switch was false and changed
live; the goo-default change below supersedes that default. GO1, GO5, GO6 and WG16 were checked together.

All tests ran on plumbus in `Projects/scottland-goo-merge`, deployed with
`SCOTTLAND_DEPLOY_DIR=Projects/scottland-goo-merge tests/deploy.sh plumbus --tests-only`.
Scratch files used `TMPDIR=$HOME/.cache/scottland-test-tmp` and sessions used
`SCOTTLAND_HEADLESS_DIR=$XDG_RUNTIME_DIR/scottland-headless-goo-merge`. Every session started
after its plugin build. No live session, other checkout or physical screen was changed or used;
local verification was limited to building and reading screenshot evidence. This remains headless
verification, with physical-display, mixed-DPI and rotated-output coverage outstanding.

| Check | Result |
|---|---|
| Widgets, goo off / on | 146 / 146 passed |
| Widget morph, goo off / on (RGBA16F) | 76 / 86 passed |
| Widget morph, packed GLES 2 with goo on | 86 passed |
| Goo input, dye, palette, panel, live switch and sleep, normal / packed | 40 / 40 passed |
| Goo propagation, gap isolation, FS1 and two-output drag, normal / packed | 12 / 12 passed |
| Unsupported texture context: halo fallback | 3 passed |
| Window navigation and placement (including WP7), goo on | 74 passed |
| Focused-surface key layers, goo on | 59 passed |
| Seeded model audit `271828 50`, goo on | 95 passed |
| Focused state regressions, goo on | 7 passed |
| Placement, declutter and Alt-controller units | 41 passed |
| Widget service / launcher / attention-source units | 16 / 17 / 5 passed |
| CPU goo field unit | 19 assertions passed |
| Config builder / focus hooks | 5 rounds of 20 concurrent builds / 3 passed |

The full requested matrix passed on the merge, after correcting an attention fixture that asked
for attention on the focused widget (correctly answered as `in_front`). A repeated field probe
also exposed an invalid assumption that IPC timer geometry and rendered field geometry were
synchronous. The probe now checks the recent frame history, bounds render lag, requires
intermediate motion and checks exact final geometry; assertions on the compositor were retained.

An additional packed-path propagation run exposed single-byte truncation of small velocities.
`254c720` stores each signed wave component in two bytes within the same RGBA8 allocation.
After this fix, the affected goo and widget-morph suites were repeated on both GPU paths,
along with normal and packed propagation, including settlement and stopped simulation steps.
The normal rendering values and disabled-goo path are unchanged by the packing fix.

Mid-collapse, expansion and reversal screenshots with goo enabled were inspected, including
attention dye around the shortened card and natural-size icons. Logs, geometry samples and
screenshots are retained on plumbus and locally under `build/widget-goo-merge-evidence`;
initial fixture, timing and packed-propagation failures are retained alongside final results.
Plugin/test source hashes matched across 71 files. Both osanwe and plumbus builds passed;
all tests were on plumbus. The isolated sessions were stopped afterward.


## Goo-default validation (2026-10-01)

Mike's decisions now ship: `scottland/goo = true` in metadata, the config and Goo Panel
Defaults. Switching it off, or a GPU unable to run it, retains a separate band for each window.
The old halo's neighbor list, recomputation callbacks, neighbor diagnostics, smooth-minimum
meniscus/bridges, shader ownership masks and shared joining-liquid grab rules are removed.
A10 now describes independent bands and points to GO2/GO3 for goo pooling and bridging.
The other appearance, input, attention, proximity and close controls remain.

Default-on testing exposed WK14's previously unconnected goo hint dye. The frame's transient
hint color now feeds the shared source, the visible bridge blends those source contributions,
and palette recoloring is immediate. (A two-pixel goo rim was built here, then removed: Mike's
direction is that window mode just tints the goo.) Focus/attention state and the hint lifecycle
retain their existing owners.

The test harness inherits shipped defaults unless `SCOTTLAND_TEST_GOO=0` explicitly requests
the fallback. Internal neighbor assertions are replaced by rendered independent-band checks.
Morph screenshots are bracketed with recent geometry because IPC and grim are asynchronous;
the initial reversal sample includes its pre-input frame in the existing 50 ms history allowance.
The first present invocation omitted its required caller-owned session; both modes were repeated
inside fresh private sessions and passed. These sampling/harness corrections do not change product
input behavior.

All runs used `Projects/scottland-goodefault` on plumbus, with
`SCOTTLAND_DEPLOY_DIR=Projects/scottland-goodefault tests/deploy.sh plumbus --tests-only`,
`TMPDIR=$HOME/.cache/scottland-test-tmp` and
`SCOTTLAND_HEADLESS_DIR=$XDG_RUNTIME_DIR/scottland-headless-goodefault`.
Sessions started after their relevant builds. Halo removal was checked in both modes;
the final hint integration and sampling corrections were additionally checked against
`dde68af` (including the extended fallback test from `874d1dd`). No live session, other
checkout or physical screen was changed or used. The task runtime was stopped afterward.
Local work was limited to source edits/builds, reading results and inspecting screenshots.
This remains isolated headless verification under AGENTS.md D2; physical display/login,
mixed DPI, rotation and other GPU families remain outside the checked scope.

| Check | Goo on (shipped default) | Explicit goo off |
|---|---|---|
| Widgets (including nested input/process checks) | 146 passed | 146 passed |
| Widget morph | 86 passed | 76 passed |
| Windowing | 84 passed | 84 passed |
| Inertia, single / two outputs | 61 / 8 passed | 61 / 8 passed |
| Hint style | 51 passed | 51 passed |
| Key layers | 59 passed | 59 passed |
| State model, seed 271828 / 50 steps | 95 passed | 95 passed |
| State regressions | 7 passed | 7 passed |
| Present | 6 passed | 6 passed |

| Additional check | Result |
|---|---|
| Halo separation, empty-corner/gap input, centered corner resize, finger move/reveal/close and goo overlap pooling | 11 passed |
| Goo interactions, palette, panel Defaults/Save/Cancel, live switch and sleep | 40 passed on RGBA16F; 40 passed on GLES 2/packed RGBA8 |
| Goo connected/gapped waves, bridge input, fullscreen and two-output drag | 12 passed per GPU path |
| Unsupported float textures, requested-on default, halo fallback and real halo drag | 4 passed |
| Hint style with GLES 2/packed goo | 51 passed |
| Windowing / inertia units | 75 / 39 passed |
| Widget launcher / widget bus / attention-source units | 17 / 16 / 5 passed |
| CPU goo model, including hinted 5% field reserve | 21 assertions passed |

All final checks passed. Logs, including retained first-run failures, are in
`build/goodefault-results`. Inspected screenshots in `build/halo-separation-evidence` show
the same overlapping fixtures with goo off (independent bands, empty inside corner) and on
(smooth pooling), plus a narrow unbridged halo gap. `build/hint-style-evidence/dark-hints.png`
showed the since-removed goo rims and smoothly blended goo.
