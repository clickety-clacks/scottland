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
  it. On open desktop their densities sum into one outline; over a back window, only the
  windows in front contribute a thin translucent film. The film opens into the full goo at the
  back window's shore. One shared wave/dye grid spans both; stacking clips foreground content.
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
  corner or side the pointer is near (whole-control cloudy dye and internal light, blended into
  the surrounding goo), the close dot's glow. Nothing is painted on top of the goo
  with its own edges.
- **Input reads the same field.** Resize corners, the close dot and grab areas are found by
  evaluating the field at the pointer; hit-testing doesn't depend on drawing. A window corner tucked
  inside another window has no goo, so it has no resize handle there; the exposed corners do.
- **Cost.** A low-resolution grid per screen on the GPU (goo, waves, dye), idle when everything is
  settled.

## Tuning

Every goo constant is a Scottland setting with a live control in the settings app (beside the zone
sliders and scale curve): reach, border thickness, bridge draw, mess, lump size, drift, wave speed,
wave persistence, wave height, dye spread, dye swirl, dye release, shine, relief, overlap film,
control cloudiness, control glow, control proximity, and the falloff
curve (how density drops away from an edge) in the curve editor. Changes apply live, as the zone
settings do. Every numeric Goo row now shows a short explanation on hover or keyboard selection,
inside the shared slider; see [settings help and preview](settings.md). Anyone can tune it.
The initial defaults are the prototype’s Scottland preset.

## Invariants

| ID | Invariant | Status |
|---|---|---|
| GO1 | One goo per screen: one field from all windows, outlining the window and widget shapes; GO11 adds stacking-aware film over content behind a window, while foreground content clips it. Widget expand/collapse follows the animated frame rectangle, including reversals and rail anchoring. | implemented; headless union/content, two-output drag and screenshot checks; per-widget presentation morph: plumbus headless geometry and screenshots checked |
| GO2 | The goo clings: each window's goo stays within a reach of its edge; between windows close enough, it bridges, drawing from both borders, and a stretched bridge thins and snaps. | implemented; prototype volume approximation, bridge/snap input checks |
| GO3 | Where windows meet or overlap, the summed field pools and bridges naturally; there is no concave-corner infill or meniscus (the old halo's was removed 2026-10-01) and no corner-specific code. | implemented; overlap pooling screenshot inspected |
| GO4 | The goo isn't uniform: its amount along each edge wanders slowly, configurable (mess, lump size, drift). | implemented; prototype noise port, inspected; drift freezes to settle |
| GO5 | Waves start at grabs, drops, swells and attention pulses, travel only through connected goo along the whole merged outline, and fade. | implemented; grab propagation across bridge and isolation across gap sampled on normal and packed GPU paths |
| GO6 | Color is dye in the goo: each window or widget releases its state's color at its presented edge, including while expanding/collapsing; dye spreads and swirls only within goo, bleeding across bridges between connected windows. | implemented; bridge/gap dye sample checks; widget presentation morph retains attention dye |
| GO7 | Halo state markers are dye (plus goo where they need presence), never separately drawn shapes: focus, attention, the hovered resize corner (no hard edges where it meets the rest of the halo), the close dot's glow. | implemented; palette, corner and close screenshots/input checks |
| GO8 | Resize corners, the close dot and grab areas are hit-tested against the same field; a corner hidden inside another window has no handle. Widgets and non-resizable windows (resize permission denied, or both dimensions fixed by min/max hints) have no resize handles; their band remains a move handle. A single fixed dimension still permits resizing the other. | implemented; pointer/touch move, resize, close and hidden-corner checks |
| GO9 | Every goo constant, and the falloff curve, is a setting with a live control in the settings app. | implemented; live slider/curve, Save/Cancel/Defaults checks; all nineteen hover/keyboard hints and screenshots checked on isolated headless outputs |
| GO10 | The goo costs nothing while the desktop is still: its simulation sleeps when settled. Active breathing damages only conservative goo bands; expensive field work uses occupied tiles, without changing the falloff or update rate. | implemented/headless checked; see the GPU cost validation below |
| GO11 | Overlapping windows stay readable through the goo, not a border: each window's goo lies on top of whatever is behind that window, so a front window's edge shows its goo over the back window's content (a film whose width over windows behind is a setting with a Goo Panel row, `goo_overlap_film`, default a thin 4 pt, thickening to the full goo where it reaches open desktop). At rest the film has the set width; when that window's outer goo expands for proximity/hover, lift while dragging, or attention breathing, its film swells in the same proportion, governed by `goo_swell`, and eases back with it. It is still one liquid: where that film meets other windows' goo it merges, and waves and dye cross the join. Hidden only by windows in front of it. (Mike, 2026-10-02; core; swell clarification 2026-10-02) | implemented; isolated headless validation recorded below |
| GO12 | The goo highlights its controls the way a UI highlights an interactive control: when the pointer nears or is over one of a window's goo controls (a corner's resize handle, a side's grab area), that control's whole goo surface (not a spot under the pointer) turns cloudy (denser, milkier dye with swirl) and glows as if lit from within (emissive: it brightens on its own, not only by reflecting light), strengthening as the pointer approaches and full while over it, then easing back when the pointer leaves. Only resizable windows have corner cloud/glow: widgets and non-resizable windows (including equal min/max size hints) never show it, in goo or the fallback halo. Their sides still highlight and move normally. Visual only: it does not change what the sides or corners do. Goo Panel settings with sensible defaults: cloudiness, emissivity (0 = no glow), and how near the pointer must be for it to begin. (Mike, 2026-10-02: corner clouding is barely visible in the goo today; the dye mark is released at only `release` strength.) | implemented; isolated headless validation recorded below |
| GO13 | Goo outlines fade over approximately one device pixel using screen-space field derivatives, at every output/window scale. The full-resolution draw reconstructs the coarse field with smooth cubic filtering, restricted to goo bands; GO11 film and GO12 control outlines use the same coverage. Keep the existing window-edge SDF antialiasing and otherwise preserve the look, simulation and input. Added active cost stays well below one millisecond per frame, checked with the paired GO10 benchmark on Xe and RX 580. (Mike, 2026-10-02; core) | implemented; isolated headless validation recorded below |

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
  Corners and sides highlight across their whole goo surface with cloudy dye and emission (GO12),
  with a soft transition at the control ends and no separate patch. For A5's 12 pt
  minimum target, the same field is dilated only as far as needed. A shared bridge's strongest
  contributing window owns its input; stable window IDs retain the original tie break independently of render order.
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
source samples are renderer diagnostics, excluded from model snapshots. One scene node per output
retains the original position below windows when none overlap. When windows overlap, that
same node moves above the window layers, below overlay UI and the lock screen, to composite the
film. A whole-output invalidation on that transition refreshes the backdrop cache; ordinary
animation still uses bounded damage.
The source list follows actual scene order, including raised windows, widgets and move transforms.
Its visibility mask excludes foreground contents; over back contents it admits only the sources in
front. The backdrop cache now contains the underlying composed scene, so the film remains
translucent over actual window content. Refraction does not bend window contents; the film blends the underlying pixel. During a
cross-output move it uses the move tool's
current transformed geometry on each intersected output.

The prototype's volume rule is preserved: nearest-neighbor gap reduces each border's source amount
as a bridge draws from it. It is an approximation of fixed volume, not a conserved-fluid solver.
Noise scales down with small windows and a small clinging reserve prevents mess from erasing their
borders. Pooling comes solely from the sum and threshold.

Density is half resolution; height/velocity and dye are quarter resolution. The packed RGBA8
path stores each signed wave component in two bytes (16 bits), so small
velocities propagate along thin borders while rounding toward zero lets residual waves settle.
There are two wave steps and one masked curl/advection/diffusion dye step per active update.
The same ordered field feeds flow, drawing and input. Over back content, edge distances compress
by the film-width/thickness ratio, returning smoothly to the full reach at its shore; the source
amount is normalized there so volume draw does not erase a thin film. The foremost source is
extended under its own content only for field reconstruction; analytic clipping still excludes
that content from drawing and flow. Scenes without overlapping rectangles keep the union fast path. Unsupported half-float render targets use packed
RGBA8 (log density and quantization-aware wave damping); missing float source textures, failed
targets or shaders retain the halo with a log message.
GLES 2 limits the source list to 1024; GLES 3 loops use the actual source count.

Drift and curl time freeze two seconds after the last geometry/state change (except ongoing
attention). GPU max reduction of wave energy and dye change every 30 updates decides sleep after
at least three seconds. Sleeping disconnects the timer, stops simulation and source uploads, and
reuses the settled image when other desktop damage needs painting. This interprets GO10 as no
simulation work at rest; ordinary compositor repainting still costs a draw. Tenet 1 favors stillness
after the liquid response over endless unattended motion.

The Goo tab has a live switch, nineteen tall grab-anywhere `ParameterStack` rows and the shared
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
| Overlap film | overlap_film | 4 pt |
| Control cloudiness | hover_cloudiness | 0.65 |
| Control glow | hover_emissivity | 0.35 (0 disables emission) |
| Control proximity | hover_distance | 48 pt |
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


## GPU cost validation (2026-10-02)

This work starts from `ship-goo` (`6919af6`), merged into `goo-perf` before revising
`944b24f` and the interrupted tile WIP `a59ceae`. The shipped on switch, independent
fallback halos and window-mode dye are retained. Tenet 1 keeps attention breathing
continuous; neither simulation frequency nor any appearance setting is reduced.

The four-reach cutoff in the WIP was removed. It changes distant contributions to
both the visible field and dye, even near another window, so it cannot silently
become a performance-only change. The original LUT and exponential continuation
remain identical in the CPU and shader. Bounds invert the render threshold with
maximum noise, clinging reserve, all corner/dot deposits, maximum wave height and
texture quantization included. Nearby source contributions are summed; distant
sources still contribute bounded exponential tails. Three logical pixels account
for field reconstruction and the normal's forward sample. Damage covers current
and previous bands to erase old outlines. The output-sized scene bounding box only
intersects incoming damage; it does not generate whole-output repaint each tick.

The half-resolution field uses a batch of quads snapped to 32-logical-pixel tiles,
with one tile of sampling halo. Clearing the field first prevents stale density
when a window moves. A quarter-resolution Boolean union mask avoids repeating the
window walk in both wave stencils. Dye keeps exact SDF evaluations, including its
continuous advection positions: reusing an interpolated mask there changed corner
colors in the pixel comparison. The wave tile map conservatively retains every
tile that could hold a wave until resize, so no arbitrary age freezes a residual
wave. Both ping-pong copies stay valid. Long drags can expand this map to the whole
output; it is bounded by output coverage and never requires a damage readback.

Dry dye still evolves across the whole grid. Its original recurrence approaches
the current nearest-source color even where there is no visible goo; freezing it
would change color on re-entry. Expensive swirl/diffusion executes only where the
mask is nonzero, and all equal-strength dye emitters retain their original blend.
Thus this is deliberately conservative field/wave tiling, not a claim that every
simulation pass is sparse. Further dye tiling needs a way to preserve that history.
The existing sleep-energy readback and input probes remain; neither drives damage.

Breathing frames wake the goo without repainting window contents. Touch-lift scale
animation still damages both old and new content bounds. Empty outputs sleep, and
an occluded attention wake cannot undo fullscreen suspension. The wallpaper cache
copies compositor damage, including pixels outside current goo bands that later
refraction may sample. Fullscreen, option changes and node removal retain necessary
whole-output invalidation.

`tests/headless.sh` now copies this checkout's base config into its private config
directory before assembly. Previously it silently fell back to the installed base,
which on the test machines loaded old decorations and extra plugins. Initial runs
against that config are retained as diagnostic evidence, not the final matrix or
benchmark. Both baseline and optimized builds use the corrected harness, their own
plugin, and their own shipped config. The bridge/snap probe now samples the actual
gap after the drag: its old fixed point could be inside the stationary window's
attention-breathing border. The field/threshold assertion is unchanged.
The morph history check now compares elapsed time with its existing 50 ms allowance
instead of comparing an absolute monotonic timestamp with 0.05. The two-output flow
probe selects the fixture's named output rather than whichever screen happens to be
first in a pointer-keyed map. These corrections preserve the original motion, lag,
wave-propagation and fullscreen assertions.

Reproduce paired measurements with `tests/goo-bench.sh REPO FRESH_HEADLESS_DIR 10`.
It arranges six windows at 2560×1600, moves two to rails with real input, measures
settled sleep, persistent attention on both widgets, and a real held-window drag.
It also measures the fallback with both widgets breathing. Results include process
GPU busy (Xe cycle counters or AMD gfx nanoseconds), CPU usage, median goo GPU query,
and simulation step count. A sleeping GPU query is stale and is reported as null.
Use separate sequential runs; other sessions on the same GPU affect timer latency.
`tests/goo-visual-fixture.py` supplies repeatable bridge, overlap, held/drop and
return-to-position screenshots with stochastic appearance disabled for comparison.


### Intel Xe, osanwe: paired shipped-config measurements

Two sequential before/after pairs at 2560×1600, ten seconds per case. The baseline
is `6919af6`; the optimized renderer is `2c1eed9`. Both start fresh headless
compositors. Mike's live compositor remains untouched and contends for the GPU.
Ranges below are the two runs, not confidence intervals.

| Case | Compositor GPU busy before → after | Median goo GPU query before → after |
|---|---|---|
| Settled | 0.0% → 0.0% | unavailable while asleep; zero simulation steps in each ten-second sample |
| Two attention widgets | 48.2–48.4% → 18.6–19.5% | 9.84–11.33 ms → 7.28–7.55 ms |
| One window dragged | 48.0–48.1% → 18.6–18.7% | 11.36–11.37 ms → 7.24–7.30 ms |
| Goo off, two widgets breathing | 0.5% → 0.6% | no goo simulation |

This is about **61% less compositor GPU busy** in the two active cases. Active
simulation remained about 59 steps/second (587–598 steps per measured interval);
no lower-rate mode was introduced. Compositor CPU rose from 3.8–4.2% to 5.2–5.4%
while breathing and from 6.2–6.5% to 7.6% while dragging. The analytic CPU bounds
trade a small amount of CPU work for less GPU work. Whole-GPU busy during active
cases was 91–92% before and 72–76% after; GPU timer queries are affected by that
contention and must not be interpreted as uncontended shader timings.

With the shipped config and deterministic visual fixture, bridge, overlap and
return screenshots match the baseline pixel-for-pixel. Held/drop comparisons have
31 / 11 changed RGB channels respectively, each by just one 8-bit level, across
1280×720 images. Inspected outlines and old drag locations show no clipping or
stale goo. These checks establish the sampled scenes, not bitwise equivalence for
all animation phases or arbitrary custom settings. The full exponential falloff
is preserved; no compact-cutoff appearance change is being proposed for shipping.

### RX 580, plumbus: uncontended paired measurements

Two further before/after pairs use the same 2560×1600 fixture and shipped configs.
All other task test sessions were stopped first; whole-GPU busy matches the measured
compositor to within 0.2 percentage points.

| Case | Compositor GPU busy before → after | Median goo GPU query before → after |
|---|---|---|
| Settled | 0.0% → 0.0% | zero simulation steps in each ten-second sample |
| Two attention widgets | 21.4–22.2% → 15.0–15.2% | 2.892–2.898 ms → 1.707–1.714 ms |
| One window dragged | 18.6–19.0% → 14.6% | 2.880–2.883 ms → 1.678–1.679 ms |
| Goo off, two widgets breathing | 0.6% → 0.6% | no goo simulation |

That is about **31% less compositor GPU busy while breathing**, **22% less while
dragging**, and **41% less goo GPU time**. Breathing CPU is 5.9% → 7.3%; dragging
is 8.1% → 9.3–9.5%. Active updates remain approximately 62/second in this headless
backend (615–627 steps per measured interval). Results are in `build/bench-amd-*.log`.
These idle/off numbers describe this isolated fixture, not the applications or
background services in Mike's live desktop.

### Final checks and isolation

Final validation uses only private headless sessions on osanwe and plumbus, with
`SCOTTLAND_HEADLESS_DIR` per run. Plumbus deployment is `--tests-only` into
`Projects/scottland-goo-perf-final-tests`; scratch files are under
`~/.cache/scottland-test-tmp`. Every tested compositor starts after its build;
reload/unload exercises are confined to those tests. No live checkout, `wayland-1`,
installed options, physical screen or user session is changed. These remain
**implemented/headless checked**, not physical-display verification under D2.
The ten renderer/frame source hashes match between osanwe and plumbus.

| Final check | Result |
|---|---|
| Goo input, palette, settings and sleep on RX 580, normal / packed GLES 2 | 40 / 40 passed |
| Unsupported-GPU halo fallback | 4 passed |
| Two-output flow, wave connectivity/isolation, fullscreen and cross-output drag on RX 580 | 12 passed |
| Additional Intel Xe packed GLES 2 two-output flow | 12 passed |
| Widgets, including nested input/process checks | 146 passed |
| Widget morph | 86 passed after correcting the history clock comparison |
| Hint style, goo on / explicitly off | 51 / 51 passed |
| Windowing | 84 passed |
| CPU goo model, including conservative-bound sweeps with 12 sources and three curves | all assertions passed |

The first morph run's failure and the successful correction/recheck are both
retained. The additional Xe flow run first exposed the output-order assumption;
the named-output rerun passed all assertions. Other initial installed-config runs
are also retained. Final source/render artifacts are in `build/perf-results`,
`build/visual-shipped-{before,after}`, `build/visual-shipped-comparison.json`,
`build/bench-shipped-*.log`, and `build/xe-packed-flow-fixed.log`. Plumbus retains
its test screenshots beside the isolated runtime result folders and in the private
checkout's build directory. Coverage excludes physical login/display, mixed DPI,
rotation and GPU families beyond Xe and RX 580.


## Overlap film and control highlight (2026-10-02)

GO11/GO12 extend the shared surface without changing the fallback halo. The four new settings
are metadata-backed, exposed through `scottland-ctl`, and use the same Goo Panel rows, hints,
Save, Cancel and Defaults as the existing values. Tenet 2 (recognition, not recall) guides the
visible whole-control response; tenet 4 keeps the overlap film narrow and translucent so the
back window remains readable. Corners still resize and sides still move the window.

Control proximity selects a corner or side and eases its strength with the existing animation.
The field carries that control's whole-surface cloud amount, blended where contributions meet;
the renderer makes it milkier with a low-frequency swirl and adds emission independently of
reflection and dye-release strength. A zero glow value removes that added light. The existing
close-dot dye and proximity behavior remain. Settled highlights freeze their swirl and sleep,
following GO10's existing interpretation of stillness.

Damage includes previous and current perimeter bands, the full highlighted control and film
support over content. Film bounds account for normalized source strength, custom falloff,
wave height and film widths greater than resting thickness. GPU field and wave tiling remain;
no simulation frequency or quality setting was reduced. A compiled shader specialization removes
overlap/hover branches when neither is present; moving into an overlap or a hover selects the full
path on the next update, preserving the same wave and dye textures.

All sessions are isolated
headless sessions with a private `SCOTTLAND_HEADLESS_DIR`, started after their build, on osanwe
or plumbus. The live checkout, installed settings and live session were not used or changed.
This is **implemented/headless checked**, not physical-display verification under D2.


The final regression matrix uses `Projects/scottland-goo-oh-tests` on plumbus, deployed with
`--tests-only`, and this checkout on osanwe. The thirteen changed plugin/settings/control files
have matching SHA-256 hashes on both hosts. Every compositor starts after its build; reloads in
the existing widget/windowing tests operate only on their private sessions. No widget-presentation
implementation was edited.

| Check | Result |
|---|---|
| GO11/GO12: film width/zero, reversed stacking, wave/dye join, whole side/corner highlights, proximity/easing, zero glow, film drag and stale-damage cleanup, normal / packed GLES 2 | 18 / 18 passed |
| Goo input, palette, live settings, new rows through scottland-ctl, Save/Cancel/Defaults, sleep, normal / packed | 46 / 46 passed |
| Unsupported-GPU fallback | 4 passed |
| Two-output flow, connected/gapped waves, wide-bridge input, fullscreen, cross-output drag, normal / packed | 12 / 12 passed |
| Widgets | 146 passed |
| Widget morph | 86 passed |
| Hint style, goo on / off | 51 / 51 passed |
| Windowing | 84 passed |
| Settings help, including hover and keyboard hints on all nineteen Goo rows | 127 passed |
| CPU goo model, including ordered film visibility and whole-control coverage | all assertions passed |

The original GO10 benchmark is unchanged apart from additional diagnostic output: six windows,
two real rail widgets, 2560×1600, ten seconds per case, same shipped settings and input. Baseline
is `1e8e57f`, built in a separate checkout with its own hooks. Neither GPU clocks nor live sessions
are changed. Idle cases report zero GPU busy and zero simulation steps; disabled goo retains the
fallback's cost. Added diagnostics identify the overlap/highlight path and Xe reference-counter
rate (not the GPU core frequency).

The final Xe pair (`bench-xe-clock-{before,after}.log`) gives:

| Case | Compositor GPU busy, baseline → new | Compositor CPU, baseline → new |
|---|---|---|
| Settled | 0.0% → 0.0% | 0.1% → 0.1% |
| Two attention widgets | 19.7% → 19.6% | 5.6% → 6.2% |
| Held window drag | 20.4% → 18.7% | 7.7% → 8.0% |
| Goo off, two widgets breathing | 0.9% → 0.5% | 2.3% → 2.1% |

Active updates remain 592/590 per ten seconds (baseline 596/594). The Xe timer median is
2.976 → 3.090 ms while breathing and 2.986 → 5.859 ms during the drag. Whole-GPU busy changed
from 22.3% to 58.6% in the latter comparison, so the elapsed GPU query includes substantially
different contention; it is not an uncontended shader-cost comparison. Client work falls from
3.9 to 3.6 million reference cycles/second in that drag. Earlier Xe runs vary widely with other
sessions and unpinned clocks, including rejected pre-specialization cost increases; all logs
remain available. These figures establish sampled GPU-busy preservation, with a small CPU cost
for the additional visibility/state work, rather than a universal performance claim.

On RX 580 the two baseline runs and final build are uncontended: whole-GPU busy matches the
compositor's busy percentage. `bench-amd-before{1,2}.log` and `bench-amd-final-matrix.log` give:

| Case | GPU busy, baseline → new | Median goo GPU query, baseline → new | CPU, baseline → new |
|---|---|---|---|
| Two attention widgets | 15.2–15.3% → 14.9% | 1.722–1.732 ms → 1.647 ms | 7.3–7.4% → 7.6% |
| Held window drag | 14.7% → 14.3% | 1.675–1.680 ms → 1.625 ms | 9.3–9.4% → 9.6% |

The new run has 619/614 active steps per interval, comparable with the baseline's 619–620/615.
Settled goo remains at zero steps and 0.0% GPU busy; goo-off breathing remains at 0.6%.
These measurements preserve GO10's GPU budget without lowering the update rate. The small CPU
increase is recorded explicitly rather than treating GPU busy as the compositor's entire cost.

Evidence is in `build/go11-results`: `amd-final` contains the final matrix and its screenshots;
`amd-morph-evidence` and `amd-settings-evidence` contain the additional visual checks. Useful
GO11/GO12 images in `amd-final/overlap-evidence` are `01-overlap-film.png`,
`04-reversed-stacking.png`, `04a-shared-join.png`, `06-side-hover.png`, `10-corner-hover.png`,
`11-cloud-without-emission.png`, `12-film-drag.png`, `13-shipped-feel.png` and
`14-separated-again.png`. Normal and packed shots were inspected, as were the new settings hints.

Earlier failed probes remain alongside final passes. The overlap pixel probe now uses the
reported frame edge. The wave-join fixture puts the rear pulse beside the join rather than
hundreds of pixels away around the perimeter. One Xe wide-bridge/fullscreen run failed; the
original stable window-ID input tie break was restored independently of rendering order and
fresh flow runs pass. No assertion threshold was weakened. The final renderer retains front-source
dye under its own clipped content for interpolation, avoiding black dry texels at the film edge.

## GO11 film swell follow-up (2026-10-02)

The overlap film now uses the same window swell as the outer goo. Its width is
`goo_overlap_film × (swollen outer thickness / resting outer thickness)`, so it is exactly the
configured width at rest; `goo_swell = 0` disables its expansion. The value is calculated once
per source update and sent to the field and dye shaders. The CPU field used for input matches it.
The previous/current damage bands include the film's expanded width, including when a small
window or a larger film setting makes that band wider than the open-desktop goo. The existing
shore transition, wave and dye continuity, and fallback halo remain unchanged. Tenet 4 keeps
the film at the user's narrow setting when settled while permitting the requested temporary
expansion.

Real `stipc` pointer, attention, and held-finger drag checks use a front window overlapping a
back window. In the Xe headless screenshots the visible film is 4 pixels at rest and 9 pixels
during the touch lift and drag, returning to 4 after drop. Hover expands the film and eases it
back; attention breathes it while a third, non-overlapping window has focus. The CPU model also
checks a scaled window and wide film where the old damage bound would miss visible pixels.
Evidence is under `build/go11-swell-results/xe-normal/`: `01a-film-swell-rest.png`,
`01b-film-swell-hover.png`, `01c-film-swell-leaving.png`,
`01d-film-swell-restored.png`, `01e-film-swell-zero.png`,
`01f-film-attention-breathing.png`, `01g-film-lift-drag.png`, and
`01h-film-lift-settled.png`. These images were inspected. The RX 580 normal and
packed screenshots in `build/go11-swell-results/amd-{normal,packed}/` were also inspected.

The isolated plumbus matrix passed: GO11/GO12 overlap and hover 27/27 on both GPU paths,
goo 46/46 on both paths, widgets 146, widget morph 86, settings help 128, and hint style
51/51 with goo on and off. Logs and screenshot copies are in
`build/go11-swell-results/amd-matrix/`. The Xe GO11/GO12 run also passed 27/27; the CPU
goo model assertions passed. All test sessions started after their builds; no live session
was reloaded.

The unchanged GO10 benchmark compares the shipped `1372aaf` build with this change in
separate headless sessions at 2560×1600 for ten seconds per case. On Xe, compositor GPU busy
was 0.0% → 0.0% settled, 17.9% → 17.5% with two attention widgets, 17.8% → 17.9% during
a held window drag, and 0.5% → 0.5% with goo off. Active steps were 585 → 581 and
586 → 589; settled stayed at zero steps. Compositor CPU was 6.7% → 7.0% for attention and
8.9% → 9.2% for drag. Whole-GPU busy varied between 54.9% and 72.7% from other work, so
the Xe GPU query times are not an uncontended shader comparison. Logs are in
`build/go11-swell-results/perf/xe-{before,after}.log`.

On the RX 580, whole-GPU busy stayed within 0.2 percentage points of the compositor
measurement. Attention GPU busy was 14.9% → 14.3%, median goo GPU query 1.680 → 1.643 ms,
and compositor CPU 7.6% → 7.5%; held drag was 14.0% → 14.0%, 1.632 → 1.625 ms, and
9.5% → 9.5% CPU. Active steps were 577 → 577 for attention and 572 → 573 for drag.
Settled stayed at zero steps and 0.0% compositor GPU busy; goo-off breathing stayed at 0.6%.
The paired logs are `build/go11-swell-results/perf/amd-{before,after}.log`. The unchanged
GO10 benchmark remains within its measured GPU and CPU cost on both machines.

## GO13: inexpensive antialiased contours (2026-10-02)

The draw pass reconstructs the half-resolution field with a cubic B-spline (four
bilinear texture taps). Positive weights avoid overshoot and ringing, and the packed
path interpolates its log-encoded field before decoding, as before. The same
reconstruction supplies the outline, relief normals and control cloudiness. Each
normal's forward sample also uses four taps; the center sample is reused.
Simulation masks, wave/dye updates, input hit tests and shipped settings are unchanged.
The draw remains clipped to conservative goo bands. Their padding now covers four
logical pixels of reconstruction, the normal's one-logical-pixel forward sample,
and one device pixel for the AA derivative quad; previous bands are still erased.

Coverage is `smoothstep(uT - aa, uT + aa, Fe)`, with
`aa = max(0.5 * fwidth(Fe), 1e-6)`. Derivatives run before any nonuniform discard,
in framebuffer device pixels rather than coarse-grid or logical pixels. GLES 3
uses core derivatives; the GLES 2 draw shaders require
`GL_OES_standard_derivatives`. A GLES 2 GPU lacking it retains the existing halo
fallback. Film and control highlights share the same contour and coverage; their
soft internal cloud transition does not acquire a separate outline. The existing
`smoothstep(0., 1., d)` window-content edge is retained. Tenet 4 guides that choice:
smooth the liquid without changing the readable window-content boundary or film's
translucency.

Validation is confined to fresh headless sessions on osanwe and plumbus, with a
unique `SCOTTLAND_HEADLESS_DIR` per run under this checkout's `build/`. Baseline is
an archived `459d758`, built with its own hooks under `build/aa-baseline`; it does
not use the live checkout. Every session starts after its plugin build. Test
scratch files, copied plugins, logs and images stay under `build/`; the widget
harness now honors `TMPDIR` for its temporary fixture directories and puts its
request/reply artifacts beside its other results. All owned headless sessions are
stopped and their state directories removed. No live session is reloaded or used.
These checks are **implemented/headless checked**, not physical-display validation.

### Paired GO10 cost

Unchanged six-window, two-rail-widget fixture, shipped settings, 2560×1600, ten
seconds per case; separate sequential before/after compositors on each GPU. Logs
are `build/aa-results/perf/{xe,amd}-{before,after}.log`.

| GPU / case | Compositor GPU busy before → after | Goo GPU query median before → after | Approx. additional GPU busy per active step |
|---|---|---|---|
| Xe: breathing | 19.3% → 20.1% | 4.621 → 4.496 ms | +0.15 ms |
| Xe: dragging | 18.5% → 20.2% | 4.219 → 4.673 ms | +0.25 ms |
| RX 580: breathing | 14.3% → 15.3% | 1.642 → 1.708 ms | +0.17 ms |
| RX 580: dragging | 14.1% → 14.5% | 1.625 → 1.679 ms | +0.07 ms |

The last column normalizes compositor GPU busy by measured active steps over the
ten-second sample; it includes compositor work outside the goo query. The RX 580
query increase is **0.067 / 0.054 ms** for breathing/dragging. Both measurements of
added cost are comfortably below one millisecond per frame. These are incremental
costs, not a claim that the entire compositor or goo simulation takes less than a
millisecond. RX 580 whole-GPU busy stayed within 0.2 percentage points of compositor
busy in the active cases. Xe had other sessions using the GPU (whole-GPU busy
64.5–67.9%); its elapsed query includes contention and is not an uncontended shader
timing. No clocks, live sessions or update-rate settings were changed.

Active steps stayed 593 → 590 (Xe breathing), 581 → 589 (Xe dragging), and
577 → 577 / 572 → 572 (RX 580). Both GPUs' settled cases stayed at zero steps and
0.0% compositor GPU busy. Goo-off breathing was 0.5% → 1.2% on the contended Xe
and 0.6% → 0.6% on RX 580. Compositor CPU was 6.4% → 6.3% / 10.0% → 8.9% on Xe,
and 7.5% → 7.4% / 9.5% → 9.6% on RX 580 (breathing / dragging).

### Visual evidence

`tests/goo-visual-fixture.py ARTIFACT_DIR [OUTPUT_SCALE]` now also captures a
hovered highlight and can hold the same logical scene at 1×, 1.5× and 2× output
scale. Both builds run that same fixture. Captures include curved corners, a
bridge, overlap film, a hovered outline, a real held/released drag, and return to
the initial geometry. Full images and geometry are in
`build/aa-results/visual-{before,after}-{1,1.5,2}`. Nearest-neighbor zooms in
`build/aa-results/crops` preserve the captured pixels: `curved-edge.png`,
`bridge.png`, `film.png`, `highlight.png`, and their scale-specific companions.

`python3 tests/goo-aa-image-test.py build/aa-results` checks equal paired geometry,
actual framebuffer dimensions and coverage along a straight neutral edge in these
rendered images. Partial coverage (5–95% of the measured rise from background)
is 1 → 1 device pixel at 1×, 1 → 1 at 1.5×, and **2 → 1 at 2×**. This checks the
sampled device-pixel fade; it is not a universal measurement of every animated
contour. The inspected zooms show smoother curved contours and film reconstruction,
with the existing content edge retained. The baseline's wider fade at 2× becomes
crisper. This deliberately changes edge coverage and nearby relief, so the images
are not expected to match bit-for-bit.

### Regression results

| Check | Result |
|---|---|
| Goo input, palette, live settings and sleep, Xe normal / packed GLES 2 | 46 / 46 passed |
| Goo input, palette, live settings and sleep, RX 580 normal / packed GLES 2 | 46 / 46 passed |
| Overlap film, swell, wave/dye join, whole-control highlight and stale-damage cleanup, Xe normal / packed | 27 / 27 passed |
| Same overlap/highlight suite, RX 580 normal / packed | 27 / 27 passed |
| CPU goo model | all assertions passed |
| Widgets on RX 580 | 146 passed |
| Widget morph on RX 580 | 86 passed |
| Hint style on RX 580, goo on / off | 51 / 51 passed |
| GLES 2 without derivatives: halo fallback and real input | 4 passed |
| Paired image coverage at 1× / 1.5× / 2× | all three passed |

Logs and evidence are under `build/aa-results`, including each GPU/path's
`goo` and `goo-overlap-hover` directories and copied widget/morph/hint artifacts.
Normal and packed film/highlight screenshots were inspected. Rendering source
SHA-256 records match between the two hosts. Validation does not cover physical
scanout, rotated outputs, simultaneous mixed-DPI outputs or other GPU families.

## Non-resizable corner verification (2026-10-02)

GO8/GO12 and A6 use the same live resize eligibility for handles and corner dye: Wayfire's
resize permission and the client's minimum/maximum size hints, excluding widgets. A maximum
of zero is unbounded; only fixing both dimensions removes resize eligibility. Keeping the
other dimension usable when just one is fixed follows tenet 4 (resizing remains the user's).

`tests/badges-fixedsize-test.py` uses a Quickshell fixture with explicit equal min/max sizes,
then changes those hints through real keyboard input. It checks all four corners, real corner
and side drags, normal-window resize, single-axis resize, goo highlight pixels, and fallback
corner pixels against a fixed-size window. The combined badge/resize run passes **94/94** in
an isolated headless session with screenshots under `build/badges-fixedsize-evidence/`.
