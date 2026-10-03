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
- **Waves.** A shared wave surface pushes the goo's boundary in and out. A grab, a drop or an
  interaction swell starts a ripple there; it travels only through connected goo, around the whole
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
wave persistence, wave height, dye spread, dye swirl, dye release, shine, relief, liquid depth, wall wetting, wallpaper soak, overlap film,
control cloudiness, control glow, control proximity, and the falloff
curve (how density drops away from an edge) in the curve editor. Changes apply live, as the zone
settings do. Every numeric Goo row now shows a short explanation on hover or keyboard selection,
inside the shared slider; see [settings help and preview](settings.md). Anyone can tune it.
The initial defaults are the prototype’s Scottland preset.

## Invariants

| ID | Invariant | Status |
|---|---|---|
| GO1 | One goo per screen: one field from all windows, outlining the window and widget shapes; GO11 adds stacking-aware film over content behind a window, while foreground content clips it. Widget expand/collapse follows the animated frame and its visible alpha contour (GO16), including reversals and rail anchoring. | implemented; headless union/content, two-output drag and screenshot checks; per-widget presentation morph: plumbus headless geometry and screenshots checked |
| GO2 | The goo clings: each window's goo stays within a reach of its edge; between windows close enough, it bridges, drawing from both borders, and a stretched bridge thins and snaps. | implemented; prototype volume approximation, bridge/snap input checks |
| GO3 | Where windows meet or overlap, the summed field pools and bridges naturally; there is no separate concave-corner infill or corner-specific code (the old halo's infill was removed 2026-10-01). GO14 adds a general surface meniscus. | implemented; overlap pooling screenshot inspected |
| GO4 | The goo isn't uniform: its amount along each edge wanders slowly, configurable (mess, lump size, drift). | implemented; prototype noise port, inspected; drift freezes to settle |
| GO5 | Waves start at grabs, drops and interaction swells (attention breathing is render-only: GO17), travel only through connected goo along the whole merged outline, and fade. | implemented; grab propagation across bridge and isolation across gap sampled on normal and packed GPU paths |
| GO6 | Color is dye in the goo: each window or widget releases its state's color at its presented edge, including while expanding/collapsing; dye spreads and swirls only within goo, bleeding across bridges between connected windows. | implemented; bridge/gap dye sample checks; widget presentation morph retains attention dye |
| GO7 | Halo state markers are dye (plus goo where they need presence), never separately drawn shapes: focus, attention, the hovered resize corner (no hard edges where it meets the rest of the halo), the close dot's glow. | implemented; palette, corner and close screenshots/input checks |
| GO8 | Resize corners, the close dot and grab areas are hit-tested against the same field; a corner hidden inside another window has no handle. Widgets and non-resizable windows (resize permission denied, or both dimensions fixed by min/max hints) have no resize handles; their band remains a move handle. A single fixed dimension still permits resizing the other. | implemented; pointer/touch move, resize, close and hidden-corner checks |
| GO9 | Every goo constant, and the falloff curve, is a setting with a live control in the settings app. The Goo tab includes depth, wall wetting and wallpaper soak with metadata hints verbatim, live preview and Save/Cancel/Defaults. | implemented; Goo coverage test matches all 24 numeric Goo controls and GO14/GO15 hints/ranges; isolated headless input checks |
| GO10 | The goo costs nothing while the desktop is still: its simulation sleeps when settled. Attention breathing refreshes only its local strip at 25 Hz (GO17); interaction-driven field work uses occupied tiles. When apps redraw beneath settled overlap film, cached surface properties are composited with the current backdrop instead of re-evaluating depth, SDF and antialiasing for every app frame. Backdrop capture is limited to drawable bands plus refraction margin. | implemented; Xe/RX 580 redraw-cost validation, including GO16 merge, below |
| GO11 | Overlapping windows stay readable through the goo, not a border: each window's goo lies on top of whatever is behind that window, so a front window's edge shows its goo over the back window's content (a film whose width over windows behind is a setting with a Goo Panel row, `goo_overlap_film`, default a thin 4 pt, thickening to the full goo where it reaches open desktop). At rest the film has the set width; when that window's outer goo expands for proximity/hover, lift while dragging, or attention breathing, its film swells in the same proportion, governed by `goo_swell`, and eases back with it. It is still one liquid: where that film meets other windows' goo it merges, and waves and dye cross the join. Hidden only by windows in front of it. (Mike, 2026-10-02; core; swell clarification 2026-10-02) | implemented; isolated headless validation recorded below |
| GO12 | The goo highlights its controls the way a UI highlights an interactive control: when the pointer nears or is over one of a window's goo controls (a corner's resize handle, a side's grab area), that control's whole goo surface (not a spot under the pointer) turns cloudy (denser, milkier dye with swirl) and glows as if lit from within (emissive: it brightens on its own, not only by reflecting light), strengthening as the pointer approaches and full while over it, then easing back when the pointer leaves. Only resizable windows have corner cloud/glow: widgets and non-resizable windows (including equal min/max size hints) never show it, in goo or the fallback halo. Their sides still highlight and move normally. Visual only: it does not change what the sides or corners do. Goo Panel settings with sensible defaults: cloudiness, emissivity (0 = no glow), and how near the pointer must be for it to begin. (Mike, 2026-10-02: corner clouding is barely visible in the goo today; the dye mark is released at only `release` strength.) | implemented; isolated headless validation recorded below |
| GO13 | Goo outlines fade over approximately one device pixel using screen-space field derivatives, at every output/window scale. The full-resolution draw reconstructs the coarse field with smooth cubic filtering, restricted to goo bands; GO11 film and GO12 control outlines use the same coverage. Keep the existing window-edge SDF antialiasing and otherwise preserve the look, simulation and input. Added active cost stays well below one millisecond per frame, checked with the paired GO10 benchmark on Xe and RX 580. (Mike, 2026-10-02; core) | implemented; isolated headless validation recorded below |
| GO14 | The goo stands out of the screen along straight edges as well as corners: a rounded bead across the band, thin at its outer shore, cresting and wetting the window wall. Summed bridges and pools have the same domed surface; waves and noise perturb it. Surface normals drive lighting and ridge highlights; refraction is proportional to slope like a lens. Depth and wall-wetting profile are live settings with sensible defaults and Goo tab hints. (Mike, 2026-10-02; core) | implemented; isolated headless validation below and Goo tab rows |
| GO15 | Wallpaper hues are picked up as a weak watercolor dye in each simulation step, then spread and swirl through connected goo. Pickup fades to zero right at each window edge and strengthens across the wet band and where liquid pools or bridges. Focus, attention and hint dye remain dominant at their window borders; wallpaper hues appear as softer washes away from them. Only the background layer supplies that color, including under overlap film; window contents never enter it or keep the simulation awake. Wallpaper changes wake it, static wallpaper settles, and strength zero disables injection. (Mike, 2026-10-02; core) | implemented; isolated headless validation below and Goo tab row |
| GO16 | Widget goo hugs the widget's rendered alpha contour, including any overhanging badge, instead of the whole client surface rectangle. Transparent reservation space has no body/shore. Generic custom shapes get the same treatment. Ordinary Wayland surfaces whose root buffer bounds extend beyond their xdg window geometry also use the rendered alpha contour, covering client-side decoration insets generically; surfaces with matching bounds retain the analytic rounded box. Commit/presentation damage coalesces into at most five alpha checks per second; only a changed quantized mask or resolution rebuilds a GPU distance field. Goo field, rendering, content clipping, fallback halo, move/close hit testing and presentation morphs use that same shape. Transparent insets retain their natural size through elastic expand/collapse; parent transforms carry the whole shape. (Mike, 2026-10-02; core; CSD contour extension 2026-10-03) | implemented; Plumbus Chromium CSD, server-decoration and GTK/libadwaita captures below; physical-display verification remains open |
| GO17 | Attention breathes with a five-second Apple-inspired light curve and gentle source-local swell at draw time. Breathing never injects waves, advances field/dye simulation, or prevents sleep. Only the attention source’s conservative band and nearby joined goo within its modulation support receive breathing damage, at 25 Hz. Settled goo with no attention has no timer or GPU work. | implemented; isolated headless validation below; no physical-display validation |
| GO18 | Settled attention breathing uses nearby cached surface keyframes by default and cross-fades their current-backdrop composites. Tight strips cover the wet liquid plus reconstruction margin. If the keyframe pair is disabled, too costly for the visual spacing bound, or unavailable, draw the breathing strips exactly. The keyframe option changes live without reload. Goo-off fallback halos still visibly breathe. | implemented; plumbus paired 5 s RX 580 measurements and pixel checks below; Intel Xe and physical-display review remain open |
| GO19 | Breathing costs what the breath itself changes. (1) A breath-only frame repaints nothing under the strips: the goo restores its cached backdrop there and draws the breath on it; any other scene damage, or one frame a second, takes the normal path. (2) A quiet outline change (a widget card re-fitting its text; nothing moving far enough to raise a wave) does not restart the drift or the three-second response window, so the simulation sleeps again within about half a second. (3) The fallback halo repaints only its ring, and a breath alone at 25 Hz. (4) Shrinking the breathing strips never blocks the compositor: it runs in slices of about 2 ms per tick. `goo-state` reports why the simulation woke (`wakes`, `last_wake`). (Mike, 2026-10-03; core) | implemented; RX 580 headless below. **Intel Xe not measured**: needs Mike's live counters |

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

Drift and curl time freeze two seconds after the last geometry/state change, including while
attention breathes. GPU max reduction of wave energy and dye change every 30 updates decides sleep
after at least three seconds. Sleeping disconnects the simulation timer, stops simulation and
source uploads, and reuses the settled field when other desktop damage needs painting. GO17 has
a separate bounded-damage breathing timer. This interprets GO10 as no simulation work at rest;
ordinary compositor repainting still costs a draw. Tenet 1 favors stillness after the liquid
response over endless unattended motion.

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
| Liquid depth | depth | 6 pt |
| Wall wetting | profile | 0.65 |
| Wallpaper soak | soak | 0.12 (0 disables injection) |
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
export TMPDIR=$PWD/build/test-tmp
export SCOTTLAND_HEADLESS_DIR=$PWD/build/scottland-headless-goo
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

Historical six-window benchmark: GO17 below supersedes its attention cost and
sleep claims with a ten-window live-like fixture and render-only breathing.

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

## L33 live move controller cost (2026-10-02)

Scottland's own enabled drag subtree replaces stock move's overlay for Super, halo,
touch and swipe gestures. Goo follows that subtree's transformed rectangle and actual
scene order. The paired GO10 fixture preserves settled sleep and has small measured
added cost on RX 580; Xe remains shared with other GPU work. See
[live-drag.md](live-drag.md#gpu-cost) for GPU busy, query/update timing, CPU, operating-state
caveats and the full isolated headless regression results. This changes drag ownership,
not goo simulation/shaders, and the baseline's content freeze was not reproduced.

## WK28 hint-circle cost (2026-10-02)

[WK28](windowing-keys.md#wk28-pop-and-circle-liquid-2026-10-02) adds animated round,
visual-only sources to the existing field, ahead of window islands. Circle goo
uses its own scaled thickness over app content, independently of window film width.
The same bands, occupied tiles, dye mixing and AA draw their connections. Input
continues to use window/widget sources only. Small closed rings use the existing
packed-path displacement damping on either GPU path while circles are present;
otherwise a constant-height wave can prevent sleep. Simulated dye does not delay
sleep when every emitter is hinted, because WK14 draws immediate weighted hint dye
instead. Visible wave energy must still settle. Normal dye wakes on leaving hints.

The paired GO10 fixture remains six windows/two rail widgets at 2560×1600, ten
seconds per case, shipped settings and real input. Baseline is archived `85b1794`,
built with its own hooks. There are no live installs or reloads. The optional
`--hints` suffix adds attention and settled measurements with real Alt held, after
the original four cases; it does not change those cases. No grid resolution or
configured update rate was reduced. GPU clocks and other sessions are untouched.

Final Xe logs are `build/wk28-results/bench-xe-pair-{before,after}.log`. RX 580
logs are copied under `build/wk28-results/amd/`, including the initial baseline
`bench-amd-before.log` and final `bench-amd-pair-{before,after}.log`. The two RX
baseline runs vary substantially as other GPU work changes; both are retained.

| Original GO10 case | Xe compositor GPU busy, before → after | RX 580 GPU busy, baseline range → after |
|---|---|---|
| Settled | 0.0% → 0.0% | 0.0% → 0.0% |
| Two attention widgets | 15.6% → 17.1% | 9.3–14.2% → 14.7% |
| Held window drag | 16.3% → 19.1% | 11.7–14.3% → 14.1% |
| Goo off, attention | 0.4% → 1.0% | 0.6% → 0.6% |

Xe active steps are 494 → 555 (attention) and 495 → 565 (drag). Normalizing
compositor GPU busy by those measured steps gives changes of **−0.077 / +0.088 ms
per update**. Query medians are 4.867 → 4.723 ms and 4.691 → 4.859 ms. Whole-GPU
busy is 61.6–71.8% in these active samples; these elapsed queries include other
sessions' contention. CPU is 7.7% → 7.5% / 11.8% → 10.4%. The goo-off Xe sample
also has substantially different external GPU activity.

RX 580 query medians are 0.836–1.684 → 1.694 ms for attention and
1.670–1.685 → 1.683 ms for drag. Steps are 581–582 → 582 and 576–578 → 577.
CPU is 6.9% → 6.9% and 9.3–9.5% → 9.2%. These preserve the original GO10
workload's measured cost within the baseline's clock/contention variation; the
samples do not establish an uncontended universal timing bound.

The new held-hint workload is more expensive while animated: six additional
sources and their film over app contents select the ordered-field path. It is
reported separately, rather than claiming the added circles are free.

| Held-hint case | Xe before → after | RX 580 baseline range → after |
|---|---|---|
| Attention GPU busy | 14.5% → 33.5% | 8.4–13.4% → 22.3% |
| Attention goo GPU query median | 3.656 → 9.701 ms | 0.751–1.284 → 3.136 ms |
| Attention compositor CPU | 17.3% → 15.6% | 15.4–15.5% → 12.6% |
| Settled GPU busy | 5.9% → **0.0%** | 8.9–9.0% → **0.0%** |
| Settled compositor CPU | 15.3% → 4.5% | 13.8–14.1% → 3.2% |

Held-hint active steps are 465 → 424 on Xe and 565–567 → 563 on RX 580.
The Xe comparison is contended, and the RX baseline's varying query times show
clock/workload variation too. Both GPUs settle to **zero simulation steps** and
zero visible wave energy while Alt remains held. Removing redundant hint/offset
damage also removes the old continuous compositor drawing at rest. Hint polling
still consumes CPU; zero GPU work is not a claim of zero total compositor CPU.

## GO14/GO15: depth and wallpaper dye (2026-10-02)

Core implementation on `goo-depth`, based on `90faf39`. The profile changes rendered
height, lighting and refraction; density contours, wave masks, hit testing, volume
approximation and source colors retain their existing roles. Tenet 4 guides scaling
depth down on narrow overlap film and keeping window content readable. Tenets 1 and 2
guide a restrained default soak: wallpaper hue enriches the dye while state colors
remain recognizable, and the response settles instead of animating indefinitely.

The shader converts threshold-relative log density to an approximate inward distance
and combines it with distance from the window wall. This gives a band coordinate `t`
from outer shore (0) to wall (1). Height is a sine bead plus `profile × t^6` wall wetting,
scaled by `depth` and local band width. The summed field provides the same surface in
bridges/pools; existing noise and waves alter its shape. Film compresses the density
distance by its configured width/thickness ratio. This is an artistic meniscus profile,
not a conserved-volume or physically simulated capillary surface.

Screen derivatives of that height are transformed to logical surface slopes using the
position Jacobian before any fragment discard. Lighting uses the resulting normal;
refraction is proportional to slope (bounded at steep slopes), with the existing
window-content exclusion. This reuses GO13's four-tap cubic reconstruction and removes
the two extra cubic evaluations previously used for forward-difference normals. The
one-device-pixel contour coverage remains. Relief is the slope multiplier (5 is unity);
Liquid depth sets height, and Wall wetting sets the cross-section at the inner wall.

The dye pass samples a separate quarter-resolution background-layer cache. Its render
instances exclude the goo and all window layers. Only background damage, changed
background children or output size cause a new capture. A wallpaper change wakes the
simulation; app redraws and the goo's own damage do not. Each active dye step mixes in at most
`soak × min(0.03, release/2) × wet-mask` of that cached wallpaper. The wet mask
rises from zero at the nearest window wall, follows liquid thickness and is
stronger where multiple sources pool. State dye is released more strongly at
the border on the same step; its release is reinforced locally when wallpaper
pickup is enabled so the state color stays clear despite diffusion from the wash.
Hint dye also takes precedence during rendering. The draw blends toward the
state dye at the window wall when wallpaper soak is active, so saturated paper
does not recolor a focused edge.
Subsequent advection and diffusion carry the weaker wallpaper hue through the
existing history. It is not a tint added at draw time. The same
source works beneath overlap film without sampling the window behind it. The usual
energy reduction decides sleep. Strength zero skips injection and wallpaper capture;
no CPU texture readback is added. With no background-layer client there is no
wallpaper dye source, rather than an implicit black color injection.

### Goo tab integration contract

The redesigned Goo tab has these ordinary live rows with Save, Cancel and
Defaults behavior, using the metadata hints verbatim. A coverage test checks
every Goo and inertial metadata option against Settings controls:

| Option | Label | Range / default / suggested step | Hint |
|---|---|---|---|
| `goo_depth` | Liquid depth | 0–20 / 6 / 0.1 | Height of the rounded liquid above the screen, in logical pixels. Higher makes a deeper lens; zero flattens it. |
| `goo_profile` | Wall wetting | 0–1 / 0.65 / 0.01 | How strongly the rounded bead climbs the window wall. Higher raises the inner meniscus; zero leaves a free rounded bead. |
| `goo_soak` | Wallpaper soak | 0–1 / 0.12 / 0.01 | Weak wallpaper color washes through thicker goo, fading near window edges so state colors stay clear. Zero turns it off. |

### Isolation and evidence

All checks use fresh isolated headless sessions with their own hooks on osanwe (Xe)
and `Projects/scottland-goodepth-tests` on plumbus (RX 580). The baseline is archived
under each checkout's `build/depth-baseline`. Session state, temporary files, screenshots
and logs stay under `build/`; `XDG_RUNTIME_DIR` is inherited without redirection. Every
owned session is stopped and its exact state directory removed. No live checkout,
`wayland-1`, live widget service, installed defaults or physical session is modified.
These results are **implemented/headless checked**, not physical-display validation.

The paired scene uses `tests/GooWallpaper.qml`, a static colorful background-layer
wallpaper, and the same `goo-visual-fixture.py` on both builds. Full screenshots and
frame geometry are in `build/depth-results/visual-{before,after}`. Nearest-neighbor
5× crops with before on the left are in `build/depth-results/crops/`: `straight-edge.png`,
`corner.png` and `bridge.png`. Noise/waves/swirl are disabled and dye release accelerated
only in this deterministic visual fixture; performance uses unchanged shipped values.
The new `tests/goo-depth-soak-test.py` separately checks actual GPU dye history,
watercolor wash versus border state color and a pooled bridge, wallpaper
replacement/removal, animated content beneath film, zero/full/default soak, flat versus
rounded straight-edge lighting, live option discovery, real grab/drop and settled sleep.


### Regression checks

| Check | Xe | RX 580 |
|---|---|---|
| Goo input, palette, live settings and sleep, normal / packed GLES 2 | 46 / 46 passed | 46 / 46 passed |
| Overlap film, swell, dye/wave join and whole-control highlights, normal / packed | 27 / 27 passed | 27 / 27 passed |
| New depth/soak, visible state dye at the wall, watercolor wash/pooling, wallpaper lifecycle, animated-content isolation and sleep, normal / packed | 26 / 26 passed | 26 / 26 passed |
| CPU goo model | all assertions passed | all assertions passed |
| Widget morph | 186 passed | 186 passed |
| Hint style, goo enabled | 53 passed | 53 passed |

The Xe final goo/morph logs are `build/depth-results/final-*.log`; the corrected
depth/soak runs and pooled-bridge screenshots are `final-pool-{normal,packed}-goo-depth-soak`.
The normal wallpaper lifecycle log is `final-normal-soak-lifecycle.log`. RX 580
logs and its new screenshots are copied into `build/depth-results/amd/`; the complete morph
and hint artifacts remain under the isolated plumbus checkout's `build/`.
Renderer/settings source SHA-256 records match between hosts. The Xe 1.5× output
screenshots are in `visual-after-1.5`; they were also inspected. At 1× all six paired
screenshots have identical window geometry.

### Paired GO10 cost

The unchanged 10-second GO10 fixture measures six windows and two attention
widgets at 2560×1600, then a held drag. Each host ran archived `90faf39` and
this build in separate headless sessions, first with the fixture's original
background and again with a static colorful background-layer wallpaper. Raw
logs are `build/depth-results/perf/{original,wallpaper}-{before,after}.log`
on Xe and the corresponding `amd/perf/` copies from RX 580. The GPU-query
column is the median goo query time; busy per step is the compositor GPU busy
time over the 10-second sample divided by the number of active simulation steps.
These are paired observations, not isolated single-pass shader timings.

| GPU / scene | Median goo GPU query before → after | Change | Compositor busy/step change |
|---|---:|---:|---:|
| RX 580 / original breathing | 1.716 → 1.666 ms | −0.050 ms | −0.017 ms |
| RX 580 / original drag | 1.679 → 1.656 ms | −0.023 ms | −0.018 ms |
| RX 580 / wallpaper breathing | 1.771 → 1.721 ms | −0.050 ms | −0.035 ms |
| RX 580 / wallpaper drag | 1.718 → 1.703 ms | −0.015 ms | +0.040 ms |
| Xe / original breathing | 5.456 → 8.585 ms | +3.129 ms | +4.358 ms |
| Xe / original drag | 5.318 → 4.315 ms | −1.002 ms | +0.463 ms |
| Xe / wallpaper breathing | 5.800 → 5.352 ms | −0.447 ms | +0.030 ms |
| Xe / wallpaper drag | 5.468 → 6.936 ms | +1.469 ms | +0.050 ms |

RX 580 whole-GPU load stayed near compositor load (roughly 0.2–0.5 points
above it), and no paired median increased. Xe whole-GPU load swung from 9.9%
to 82.9% across samples and often exceeded compositor load by more than 50
points; its query changes have opposite signs across the pairs, so they do
not identify added GO14/GO15 cost. All eight settled samples reported **0.0%
compositor GPU, zero simulation steps and sleeping=true**. After goo was
disabled, breathing likewise took zero simulation steps on both hosts.

Earlier failures remain in the evidence. The first packed dye run exposed injection
of black when no wallpaper client existed; the renderer now has no wallpaper dye
source in that case. A wallpaper-removal fixture initially killed only the bubblewrap
launcher, leaving its Quickshell child mapped; it now stops its own private process
group. Initial Xe film-pixel and widget-morph checks also failed; fresh final runs
pass all checks, and an archived-baseline Xe morph run passes 186. No assertion
threshold was relaxed. These observations do not establish the cause of the initial
Xe pixel/timing failures. Coverage excludes physical scanout, output rotation,
simultaneous mixed-DPI outputs and GPU families beyond Xe/RX 580.

## GO16: widget alpha contours (2026-10-02)

Core behavior: any widget's visible body defines its liquid shore. Tenet 2
(recognition, not recall) keeps badges part of that recognizable body. WG10's
six-point top/outer-side reservation stays in the client surface for placement,
but empty reservation space no longer leaves a gap between card and goo.

### Shape and lifetime

`goo-shape.cpp` captures the frame's actual content composition into a transparent
RGBA8 target at half logical resolution, capped at 512 texels per body axis, with
two transparent padding texels. Widget and presentation/morph damage marks it dirty;
a trailing timer coalesces checks to at most five per second, including the last
commit after a burst. Only alpha enters the comparison, quantized to 16 levels.
RGB-only animation does not rebuild the SDF. Shared shaders compile at plugin
startup, rather than blocking the first widget/morph frame.

An alpha=0.5 contour defines the body, with interpolated crossings for antialiased
edges. GPU jump flooding plus a final stride-one correction produces signed
16-bit distances at 1/16-texel precision in RGBA8. This works on both goo GPU paths
and when the simulation is unsupported. The small distance image is read once
after rebuilding: CPU input uses precisely that image, the fallback halo retains
its texture, and goo packs changed images into a shelf atlas sampled by its field,
content/film clipping, dye and render passes. Atlas overflow retains masked
fallback halos. GLES 3 capture/backdrop reads explicitly bind the read framebuffer;
the shared GL guard restores both framebuffer bindings.

Ordinary steady windows whose root surface bounds match xdg window geometry keep
the analytic rounded box and have no alpha cache or atlas fetch. A normal client
surface whose bounds extend beyond that geometry uses the same half-resolution
alpha capture as GO16 widgets. The comparison is against Wayfire's root-surface
bounds and xdg geometry, not an app identity or decoration setting. This lets CSD
corners, transparent resize margins and overhanging drawing define the goo and
fallback-halo shore. Damage invalidates the comparison and mask; at most five
checks per second coalesce animated client commits. An app temporarily participating
in a widget morph can use the composited alpha shape, then releases that cache on
handoff/settlement. Parent
transforms carry the body bounds. Elastic presentation resizing preserves the
measured transparent insets and stretches the contour's departure from its opaque
bounds; damage, rendering and input therefore share the animated body. Masked
sources use conservative whole-body damage tiles to cover arbitrary shapes/holes.

Fallback bands, minimum move targets and content exclusion use the same SDF.
The close dot follows the visible bottom shore at the frame's horizontal center;
when that column is empty, a cached body point near the opaque bounds' center
provides its attachment instead. Both coordinates carry through parent transforms.
Window-mode tint preserves widget alpha and does not become a shape source.
No badge, card or app-specific compositor code is involved.

### Validation

`tests/goo-shape-test.py` uses real rail drags and Super+M, actual Unity badge
commits, screenshots and field/input samples. It exercises expanded/collapsed
cards on both rails with counts 0, 7, 123 and removal; a badge waking settled goo;
an independently packaged round widget; color-only commits; throttling; fallback
bands; halo dragging; and close controls which close both app and widget. Before
crops come from archived main `82f2b28`; after crops and the labeled comparison
are under `build/go16-evidence/`. The round widget's 40 Hz color animation leaves
its SDF rebuild count unchanged.
`--offset-only` separately checks a round body entirely to one side of the client
surface's center, including goo and fallback close input. These focused checks
pass 5/5 on Xe and RX 580 normal and packed paths and 2/2 with forced fallback
on each host. Their crops/logs are in `build/go16-evidence/offset-*` and the
corresponding `rx580/offset-*` copies.

| Check | Xe | RX 580 |
|---|---|---|
| Shape/badge/control checks, normal / packed | 133 / 133 passed | 133 / 133 passed |
| Forced unsupported-goo shape/fallback checks | — | 43 passed |
| Goo regression, normal / packed | 46 / 46 passed | 46 / 46 passed |
| Widget morph, goo / forced fallback | 270 passed / — | — / 242 passed |
| CPU goo model | all assertions passed | — |

The widget lifecycle/input suite also passes all checks on Xe and RX 580. Its Xe log is
`build/go16-regressions/widgets-verified.log`; final shape logs/screenshots are in
`build/go16-evidence/{complete,packed-complete}/`. RX 580 results are in the
`rx580/shapes-{normal,packed,fallback}/` copies, and the fallback morph log is
`build/go16-regressions/morph-fallback-rx580.log`; RX 580 widget results are in
`build/go16-regressions/widgets-complete-rx580.log`.

### Client-side decoration insets (2026-10-03)

Wayfire's `list-views.geometry` is the xdg toplevel geometry; `base-geometry` is the root
wl_surface buffer bounds, and `bbox` is the complete scene bound after transforms. Scottland's
frame `screen_rect`, fallback halo and Goo source all hug the xdg geometry. On Plumbus, Chromium
with “Use system title bar and borders” off requested client-side decoration (xdg-decoration
mode 1) and reported geometry
`{106,56,1068,608}` inside surface bounds `{90,46,1100,650}`. The mismatch is 16 px left/right,
10 px top and 32 px bottom. With the setting on, it negotiated server-side mode 2 and both
rectangles were `{90,35,1100,650}`. Nautilus (GTK/libadwaita CSD) showed 25 px insets on all
sides. Wayfire's `core/preferred_decoration_mode=server` is a preference; the protocol still
reports which mode each client selected. Chromium and Nautilus therefore expose the same generic
inset-surface case, not an app-specific geometry rule. Chromium's xdg-decoration response matched
each setting; Scottland doesn't override the client's decoration choice.

For ordinary surfaces, a root buffer extending beyond xdg geometry opts into GO16's rendered
alpha capture. The quantized body contour is shared by the rendered frame, goo field/render,
fallback halo and input; equal-bound surfaces keep the analytic rounded box and avoid the shape
readback. This recognizes actual transparent corners and inset margins even when the client
uses CSD. It does not force server decorations or change the app's window geometry.

The Plumbus fixture records Wayfire rectangles and decoration protocol modes, asserts that inset
CSD clients build the alpha contour and that Chromium's server-decoration case does not, then
captures goo and fallback screenshots. `tests/chromium-gap-test.py` passed for normal and packed
GLES. Screenshots show the goo and fallback shore touching the rendered rounded frame in both
Chromium modes and Nautilus; normal captures and logs are under `build/chromium-gap-plumbus/`,
with packed captures under `build/chromium-gap-plumbus/packed/`.

The final alpha-mask/SDF rebuild telemetry was 15.39 ms for Chromium and 10.40 ms for Nautilus
on normal GLES, and 13.68 ms for Chromium and 15.00 ms for Nautilus on packed GLES. The last
unchanged-mask checks took 2.51–3.91 ms. Both runs captured only after `goo-state` reported a
sleeping surface with cached breath keyframes active, so the GO10 settled cache was exercised.
These are per-shape capture timings, not full frame-time measurements, and a rebuilt shape is
captured synchronously. Firefox is not installed on Plumbus and the attempted Mozilla download
timed out, so its contour remains unchecked. The physical display also remains unverified.

The morph suite now compares the liquid shore against measured alpha body bounds
rather than the transparent client rectangle. Its scheduling, pixel, duration and
reversal thresholds are unchanged. It still exercises expand/collapse, rail
anchoring, peeks, reversals, live window/widget morphs, input, zero/max bounce,
unload/reload and resource cleanup. Early Xe Goo editor/Save failures also occurred
in the archived baseline; fresh final runs pass 46/46 on both paths. An isolated
development run exposed shared shaders being freed during per-widget teardown;
they are now freed only at plugin teardown, and final lifecycle/reload runs pass.

### Historical paired GO10 cost, before settled surface caching

This ten-second six-window/two-widget comparison is the GO16 change against
the pre-cache baseline archived at `82f2b28`; it predates the settled surface
cache described in GO10 below. The current combined GO10+GO16 result is
recorded beside the RX 580 GO10 rows below. Raw Xe logs are
`build/go16-evidence/bench-{before,after}.log`; RX 580 logs and shape screenshots
are copied into `build/go16-evidence/rx580/` from the isolated plumbus checkout.
Query times include the goo update; mask timings below separately include
content capture, jump flooding and synchronous readbacks.

| GPU / workload | Median goo GPU query before → after | Change | Compositor GPU before → after |
|---|---:|---:|---:|
| RX 580 / attention | 1.656 → 1.977 ms | +0.321 ms | 14.8% → 16.2% |
| RX 580 / drag | 1.654 → 1.949 ms | +0.294 ms | 14.3% → 15.5% |
| Xe / attention | 8.232 → 6.855 ms | −1.376 ms | 25.3% → 30.1% |
| Xe / drag | 8.165 → 9.488 ms | +1.323 ms | 26.2% → 31.3% |

RX 580 whole-GPU use stayed within 0.7 percentage points of compositor use.
Xe whole-GPU use was 60.8–88.5%, substantially above compositor use; the opposing
query changes do not isolate GO16 cost. Both pairs settled to **0.0% compositor
GPU, zero simulation steps and sleeping=true**. Goo-disabled breathing also took
zero simulation steps. Static masks do no rebuild work while settled.

`layout-state` exposes checks/builds and the last check/rebuild wall time per
frame. The following samples deduplicate unchanged last-rebuild readings after
each fixture scene (33 distinct samples for normal/packed, 17 for forced
fallback); they are observed update costs, not an exhaustive animation trace.
Aggregates are in `build/go16-evidence/mask-costs.json`; raw values are in each
shape run's `results.json`.

| Mask rebuild path | Median | 95th percentile | Maximum |
|---|---:|---:|---:|
| RX 580 / normal goo | 1.150 ms | 1.930 ms | 5.942 ms |
| RX 580 / packed goo | 1.081 ms | 1.778 ms | 2.238 ms |
| RX 580 / forced fallback | 0.999 ms | 1.685 ms | 1.685 ms |
| Xe / normal goo | 4.308 ms | 8.932 ms | 9.512 ms |
| Xe / packed goo | 4.707 ms | 8.408 ms | 8.660 ms |

These timings include GPU synchronization and, on Xe, the same external
contention as the paired benchmark. Rebuilds occur at most five times a second
and only after the quantized alpha changes; RGB-only commits still require a
throttled alpha check but skip jump flooding and atlas replacement.

The earlier sessions used inherited `XDG_RUNTIME_DIR`, shipped config only and
artifacts under the checkout's `build/`; they did not reload or dev-install the
live session. After osanwe froze during later headless activity, all subsequent
test sessions moved to plumbus (see integration validation below). This is
headless verification; physical scanout, mixed DPI/rotation and GPUs beyond
Xe/RX 580 remain unverified.

## GO17: draw-only attention breathing (2026-10-02)

Core, guided by tenets 1 and 5: a quiet request for attention, without moving a
window or maintaining an expensive screen-wide animation. For widgets,
GO16 supplies the visible alpha contour as the source boundary.

### Curve and cadence

Apple’s [US6658577B2, Breathing status LED indicator](https://patents.google.com/patent/US6658577B2/en)
describes positively biased sinusoidal PWM with a quiet interval. Its illustrated
cycle is **1.8 seconds**, including a 0.4-second quiet interval; it does not specify
`exp(sin(t))` or 12 breaths/minute. We use the requested slower **five-second cycle**
(12/minute), with the normalized exponential-sine approximation described by its
implementer [ThingPulse](https://thingpulse.com/breathing-leds-cracking-the-algorithm-behind-our-breathing-pattern/):

```
b(t) = (exp(-cos(2πt/5)) - exp(-1)) / (exp(1) - exp(-1))
```

This is an Apple-inspired approximation, not a claim to reproduce the patent’s
PWM waveform. Monotonic time prevents wall-clock changes from shifting the pulse.
A 40 ms timer gives 125 samples per breath; the largest preset light increment is
under 0.8% and the exponential contour displacement is under 0.09 logical pixels.
The light has a long low portion and a smooth crest. This cadence is independent
of the output refresh rate. All sources share the curve; their existing attention
dye remains visible even at its trough. The fallback halo uses the same period.

### Why the old strips stayed busy

Three mechanisms interacted. `frame_t` continuously drove the attention spring,
so every changing swell looked like a changed simulation source. `goo.cpp` also
injected positive attention impulses every 1.5 seconds and kept noise/curl time
advancing during attention. Every wake damaged all current and previous bands;
field tiles saved some work but did not isolate breathing from simulation.

The wave stencil also damped velocity without damping height on ordinary float
sources. A closed band can retain a constant displacement despite zero velocity;
repeated positive impulses accumulate it, saturating the energy readback at 1.0.
Both float and packed paths now damp height as well as velocity. New impulses
invalidate a previous settled energy reading so a just-woken response cannot be
mistaken for sleep. Split wave/dye energy diagnostics make the cause observable.
Attention requests still update state dye once and WG19 still animates its peek;
neither transition implies ongoing simulation after it settles.

### Cached influence, local drawing and input

The field pass stores the contribution-weighted attention influence in its unused
alpha channel. The shared source texture has eleven columns: seven existing
values, GO17 attention at column 7, and GO16 mask data at columns 8–10. GO17
adds no render target, per-breath upload, field pass, wave impulse, or dye update. The draw multiplies density by
`1 + 0.12 × (goo_swell/0.7) × b(t) × influence` and adds a small dye-colored emission.
Depth, overlap film and antialiasing use that same modulated density. CPU field
input uses the same source weighting and curve; the existing grab dilation remains.

Only this new decorative influence has finite support: full through three reaches,
smoothly tapering to zero at four reaches from the source’s presented boundary.
**The original field/dye exponential tails remain unchanged.** Nearby joined goo
inherits the source’s weighted light and swell within that support. Damage is the
intersection of its padded perimeter support, all drawable goo bands, and the
output. Cubic-filter/AA padding and maximum breath swell are included. It does not
invalidate every other source, window interior or the whole output. Geometry/state
changes still damage old/new simulation bands and refresh the influence cache.
Fullscreen, removal and an empty attention set disconnect the breathing timer.

For a masked widget or CSD surface, the draw's local influence uses GO16's sampled
alpha SDF, and breathing damage covers its opaque-body bounds plus that finite
support. This includes a badge, a hole, or a body deeply inset from a transparent
client rectangle; ordinary windows without surface insets keep the narrower
analytic perimeter strips.

`goo-state` now reports `breath`, `breath_ticks`, `breath_damage`, `wave_energy`,
`dye_energy` and `draw_gpu_ms`. Existing `gpu_ms` measures active simulation plus
draw, and is stale during sleep. `draw_gpu_ms` times draw-only frames; it too is
stale on a completely still desktop. Neither stale number means ongoing work.

### Live-like cost and regression evidence

Validation uses fresh isolated headless sessions only, on Xe (osanwe) and RX 580
(plumbus), never physical scanout or a live-session reload. Ten foot windows are
arranged at 2560×1600; real Super drags turn two into rail widgets, and one requests
attention. Several remaining windows overlap. Shipped film, depth, soak and noise
remain enabled, with an actual static colorful background-layer client. Baseline
is `511d9f1`; artifacts are under `build/go17/`. Test config contains no personal
layout or overrides, and all owned headless sessions are stopped after use.

Run `tests/goo-breath-bench.py HEADLESS_DIR 10 --verify` through `tests/headless.sh run`
after starting a fresh session with `--widgets`. It records process GPU/CPU busy,
step deltas, sleep, the five-second curve, 25 Hz cadence, peak/trough screenshots,
and pixel identity outside the reported damage strips. A separate energy sample
on the unmodified live session confirmed energy 1.0 and about 15 ms per step with
11 sources. No live attention was cleared or live code replaced.

Ten-second samples, sequential baseline/after attention workloads on each GPU:

| GPU | Attention compositor GPU busy before → after | GPU query before (simulation + draw) → after (draw-only median) | Attention simulation steps before → after |
|---|---:|---:|---:|
| Intel Xe | 48.7% → **0.5%** | 15.924 → **0.102 ms** | 470 → **0** |
| RX 580 | 20.2% → **0.3%** | 3.183 → **0.037 ms** | 550 → **0** |

Both after builds report `sleeping=true` during attention. With no attention,
settled **and answered** samples are **0.0% compositor GPU, zero steps** on both
GPUs. Before attention, the baseline failed to settle within 45 seconds on this
fixture: 50.2% GPU on Xe and 21.3% on RX 580. It eventually slept after attention
was cleared. The first answered after-run sampled the transition because the
harness accepted a stale sleeping flag before `prepare()`; the final runs allow
the clear transition to reach the renderer before waiting for sleep.

Xe is shared: baseline whole-GPU busy was 96.4%, versus 17.5% after; its elapsed
query includes that contention, so the query ratio alone is not an isolated
shader-speed claim. Process work fell from 9.4 to approximately 0.1 Mcycles/s
at a ~19.2 MHz reference-counter timebase. RX 580 whole-GPU busy was 20.6% before
and 0.9% after. Attention compositor CPU fell from 11.5% to 3.4% (Xe), and 11.8%
to 2.9% (RX 580), including benchmark IPC sampling. No GPU clocks or other sessions
were changed.

The final Xe and RX visual samples cover two complete breathing periods with
zero simulation advances and unchanged cached energy. Cadence is 24.96/24.92 Hz.
Peak/trough screenshots change 7,428/7,420 pixels inside the widget’s four strips,
and **zero pixels outside those strips**. The strips occupy under 2% of the
output; screenshots show the gentle swell and stronger light at the crest.
Evidence: `build/go17/visual.jsonl`, `bench-visual/{peak,trough}.png`,
`bench-visual/breath-samples.json`, and `build/go17/rx/final-rx.log` plus its
`bench-final-rx/` captures. These timings measure the actual active draw, not the
last simulation query retained while asleep.

A later permitted read-only live sample also found `sleeping=true`, energy
0.007843 and step count 819 unchanged across two readings (`live-readonly-sleep.json`).
The live counter had reset since the initial observation, so this is a separate
live-state observation, not a before/after test of this branch. This task never
changed the live session or cleared its attention, and the old diagnostic does
not expose attention-source counts.

Regression coverage on Xe: `goo-test` **46/46 normal and 46/46 packed**, overlap/hover
**28/28 on both paths**, flow **9/9 on both paths**, and the CPU field/bounds test.
The additional packed GO17 fixture passes **12 checks**, including switching to
fallback breathing and back into sleeping goo with attention still outstanding;
its two-period screenshot comparison changes 7,443 local pixels and zero outside.
Normal Xe/RX GO17 visual fixtures each pass the original ten checks.

Two old assertions explicitly depended on the removed behavior: the bridge dye
probe now samples x=594 in the far half of the resting 570–610 bridge, retaining
the original color-change threshold, instead of x=600 where the old large swell
made the attention source dominate. The film-join wave test now uses a real pointer
hover on the rear edge to excite the wave, preserving stacking, rather than using
an attention request as an impulse. Film breathing checks the new gentle field
modulation and an unchanged simulation spring over a full five-second period.
Initial failing logs and diagnostic dye samples remain under `build/go17/`.

`widgets-test.sh` passes in the isolated RX 580 session (192 PASS records), including its embedded
89-case widget input regression and all lifecycle, source ownership, attention,
peek, fullscreen, reload and scope-cleanup checks. That build precedes only the
fallback-switch follow-up; the final packed GO17 fixture above specifically
verifies that follow-up on an attention widget.

The RX 580 widget morph suite passes **270/270**, including attention presentation
and retained dye through morph/reversal. No live session reload is performed:
reload checks belong to the test's own headless session. Remote results are copied
to `build/go17/rx/{widgets-rx,morph-rx}.log`.

The final Xe build also passes **270/270 widget morph checks**
(`build/go17/morph-final-xe.log`). All task-owned local and remote headless sessions
and their runtime directories have been stopped/removed; evidence stays on disk
under each isolated checkout’s `build/`. This remains headless validation, not a
physical-display acceptance or deployment.

## GO10: settled goo over redrawing windows (2026-10-02)

This is a **core** cost correction under the quiet-attention and proportional-work
tenets. Mike measured his live 2560×1600 Xe session at 33% compositor GPU busy
with goo and 4.2% without it while terminals streamed and one widget breathed.
`goo-state` showed `sleeping=true`, energy zero and about 0.7 ms for a draw query.
Those are read-only live observations from the prior build, not measurements of
this change on his session. The old GO17 ten-window fixture did not exercise
continuous app damage beneath GO11 film.

The settled simulation was already asleep. Each app redraw nevertheless passed
through the full GO11/GO13/GO14 surface shader wherever its output damage met a
goo band. Wayfire can collapse many damage rectangles to a bounding box; the
renderer then made one full-output quad for each scissor rectangle. Scissoring
limited fragments, so quad geometry was not the main cost, but the surviving
fragments repeatedly reconstructed the cubic field, evaluated the stacked
window SDF, normal, depth, dye and AA. Merely clipping the background copy to
the bands plus a 17-logical-pixel refraction margin changed the Xe fixture from
12.8–13.2% to 12.6–13.4% GPU busy: it did **not** cure the shader cost.

The copy path had a separate correctness bug. Wayfire can bind the output FBO
for drawing while a different framebuffer remains bound for reading. The old
`glCopyTexSubImage2D` then raised `GL_INVALID_OPERATION (missing readbuffer)`
on thousands of redraws and left refracted background pixels stale. The draw
now reads explicitly from the output FBO's color attachment, restores the
previous read framebuffer/buffer, and captures only damage inside the padded
goo bands. The final headless Wayfire log has no such copy errors.

Once simulation settles, the expensive shader stores two output-sized,
band-limited surface caches:
intrinsic color with coverage, and background refraction offset with lighting
coefficient. Active simulation retains the original direct draw; after it
settles, the current bands are cached once. GO17 breathing refreshes only its
local strip at 25 Hz. Ordinary app redraws combine those cached
properties with the current backdrop in a small shader. This retains GO11 film
over live window content and the GO14 refraction; input, wave and dye simulation
are unchanged. Both caches use RGBA8 (about 31 MiB together at 2560×1600),
including on the packed GLES 2 path. Intrinsic light can be clamped before
storage because the backdrop contribution is nonnegative and the final color
is clamped after addition. If cache allocation fails, the renderer uses its
prior direct draw.

`tests/goo-draw-bench.py` arranges 18 foot windows at 2560×1600, including six
terminals printing continuously, overlapping windows, two widgets made by real
Super drags, and one attention widget. A static background-layer client supplies
wallpaper color; shipped overlap film, soak and depth remain on. Each isolated
headless session waits for simulation sleep and samples goo on/off/on in the same
scene. The off case uses the existing halo. Xe samples are ten seconds per case;
RX 580 samples are five seconds to limit shared test-machine load. These are
process GPU-busy counters, not whole-GPU usage or physical-display acceptance.

| GPU / build | Goo on first | Goo off | Goo on again | Increment over off |
|---|---:|---:|---:|---:|
| Xe, `c99f116` | 13.2% | 5.4% | 12.8% | 7.4–7.8 points |
| Xe, cached pre-final build | 6.9% | 5.1% | 7.1% | 1.8–2.0 points |
| RX 580, `c99f116` | 10.4% | 8.1% | 9.6% | 1.5–2.3 points |
| RX 580, cached pre-final build | 9.7% | 7.7% | 9.9% | 2.0–2.2 points |
| RX 580, final build | 10.7% | 8.1% | 10.4% | 2.3–2.6 points |
| RX 580, GO10 + GO16 merged build | 8.7% | 7.0% | 9.6% | 1.7–2.6 points |
| RX 580, GO10 + GO16 + GO18 default keys | 9.4% | 8.4% | 9.5% | 1.0–1.1 points |

The merged build's five-second goo-on samples stayed asleep with zero simulation
steps. Their median draw queries were **0.300 ms** and **0.520 ms**. One other
headless goo x-check was active on the shared RX 580 during the run; whole-GPU
busy ranged from **17.8%** to **14.3%** across the three samples, so that total
includes the other workload. The per-compositor result remains within the prior
RX 580 increment range.

The GO18 row uses the same 18-window redraw fixture, with five-second samples.
All three GO18 samples had zero simulation steps; the goo-on draw-query medians
were **0.367 ms** and **0.321 ms**. Whole-GPU busy was 9.4–9.5% in those samples,
and a concurrent plumbus Chromium renderer plus the shared attention and agent
services were active. As with the other RX 580 rows, these are short shared-GPU
observations, not isolated speedup measurements.

All goo-on measurement windows report `sleeping=true` and **zero simulation
steps** despite streaming terminals and roughly 25 breathing ticks per second.
On Xe the measured incremental GPU cost fell by about three quarters to within
two points of goo off. This cached Xe sample preceded the final framebuffer
state-restoration and active-simulation direct-draw adjustments; Mike's new
no-testing-on-osanwe rule prevents an exact-final-build Xe rerun. The RX 580
fixture already had a small increment, and the final cached path remained
within three points rather than showing a material busy reduction.
RX 580 median draw queries span 0.399–0.471 ms before and 0.397–0.453 ms in
the final build.
Xe draw-query elapsed times vary with other sessions' GPU contention, so the
process busy counters are the useful paired result there. Evidence from the
pre-reset Xe run is under `build/go10-redraw-baseline/` and
`build/go10-readfix/`; the plumbus runs, including the final screenshot and
Wayfire log, are copied to `build/go10-rx/`. Other agents had isolated
headless sessions open during the final RX 580 run, so shared-GPU contention
remains a measurement limitation. The headless Xe baseline also showed a much
smaller increment than Mike's physical-display session (7.4–7.8 versus about
29 points). The live target therefore remains unverified until this build is
measured on his physical session; the live session was not changed here.

The earlier broad goo test had stale coordinates for Scottland Settings' newer
six-tab, half-height panel. Its test now derives input from the panel snapshot
and uses a visible curve knot. This changes only the test fixture. On plumbus,
the final build passed goo-test on normal and packed GLES paths (46/46 each)
and overlap/hover (28/28); widgets (192/192) and widget morph (270/270) passed
before the final GL-state adjustment. All headless sessions were stopped.
## GO10 + GO16 merged render path (2026-10-02)

The source texture keeps eleven columns: the common seven, GO17 attention at
column 7, then GO16's atlas tile, mask bounds and body bounds at columns 8–10.
Windows whose root surface bounds match xdg geometry keep their analytic
rounded-box SDF and do not sample the atlas. Widget sources and client-decorated
surfaces with transparent insets use the same alpha SDF in field, direct render,
and GO10's cached intrinsic/refraction passes; those cached shaders are derived
from the same render shader as the direct path. When the ordered masked-shape set changes, the renderer
rebuilds the RGBA8 shelf atlas and marks the settled surface cache dirty. Active
simulation keeps the GO10 direct path, and the cache is regenerated from the new
atlas once the field settles. The atlas is allocated without a framebuffer and is
released with the other render targets. `goo-gl.hpp` owns the shared GL helpers;
its state guard preserves separate read framebuffer and read-buffer state.

The GO16 shape/contour suite passed **133/133** on normal goo and **133/133** on
packed goo. The inset widget breathing case passed **7/7**, including visible
contour movement while the simulation slept. The GO17 breathing verifier passed
**12/12**; overlap/hover passed **28/28**. The widgets lifecycle suite passed all
checks, including **91/91** embedded real-input regressions; widget morph passed
**270/270**; windowing passed **102/102**. The base goo regression passed
**46/46** on both normal and packed paths.

Plumbus headless screenshots and logs are under
`build/part2-plumbus/{shape-normal,shape-packed,shape-inset-breath,breathing,overlap-hover,widgets,widget-morph,windowing,goo-normal,goo-packed,goo-draw-bench}/`.
The GO10 draw-benchmark result for this merged build is recorded in the RX 580
comparison table above. Validation used isolated plumbus headless sessions;
physical-display scanout remains unverified.

## GO18: keyframed settled breathing (2026-10-03)

GO18 combines Fable's breathing keyframes with Astra's exact direct-strip
fallback and the tight breathing bounds in both paths. In settled goo, two
adjacent intrinsic/refraction surfaces hold nearby breathing values. The
renderer cross-fades their composite over the live backdrop, keeping GO10's
cached static surface and current-app redraw path. Keyframe spacing follows the
estimated screen-space shore travel and stays within the half-device-pixel
bound; a request needing more than sixteen intervals selects the exact path.
With keyframes disabled or their extra target pair unavailable, only the wet
breathing strips run the full surface shader. The static settled area remains
cached. The extra pair is released when no breathing strips need it or the
option is switched off.

The shipped live option is **scottland/goo_breath_keys**, enabled by default.
To switch immediately to exact strip rendering, run
**scottland-ctl set goo_breath_keys false**; restore keyframes with
**scottland-ctl set goo_breath_keys true**. No reload is required. `goo-state`
reports the option, active interpolation, key values, layer refreshes, and
submitted surface/capture/composite pixels.

Tight strips are recalculated when settled using the CPU density model and
reconstruction padding. GO16 alpha-shaped sources use a denser sample lattice.
Packed GLES 2 energy readback also gets a packed-only sleep bound of 16/255 plus
a small epsilon: the reducer multiplies deltas by sixteen before RGBA8
quantization, so one remaining source-color level otherwise kept the field
awake. Larger changes still wake simulation; normal precision keeps its
existing threshold.

`tests/goo-idle-bench.py` now folds both investigations' fixture options into
one isolated run. It sampled the wide 2560×1600, 120 Hz scene for five seconds
per case on plumbus's RX 580, with keyframes enabled and disabled live, then
compared keyframes at intermediate breaths against the exact path. The idle
desktop, a large overlapping attention window, and an attention widget each
recorded zero simulation steps in both GPU formats. At rest with no attention,
the measured compositor/whole-GPU samples were 0.0/0.1% or below.

| RX 580 path | Window, keyframes on | Widget, keyframes on | Window, exact strips | Widget, exact strips | No attention, on/off |
|---|---:|---:|---:|---:|---:|
| Normal | 1.0 / 1.0% | 0.4 / 0.4% | 0.8 / 1.2% | 0.5 / 0.5% | 0.0 / 0.1%; 0.0 / 0.0% |
| Packed GLES 2 | 0.8 / 0.8% | 0.4 / 0.4% | 0.8 / 0.9% | 0.4 / 0.5% | 0.0 / 0.1%; 0.0 / 0.1% |

Each percentage is compositor / whole-GPU busy during a five-second sample. On
normal precision, median draw queries for the window were **0.123 ms** with
keys and **0.190 ms** exact; for the widget they were **0.040 ms** and
**0.070 ms**. Packed GLES 2 does not expose the timer query. Surface work over
five seconds fell from **24.58 M to 6.43 M pixels** for the window and from
**10.47 M to 2.35 M** for the widget. The 25 Hz refresh cadence stayed around
24.9 Hz with no simulation work. Keyframe runs visited nine breath values and
refreshed cached layers 14 times for the window and 15 for the widget. GPU busy
percentages are close and noisy; the pixel counters and query medians show the
measured work reduction more clearly.

Across the keyframe midpoint comparisons, normal precision differed from exact
rendering by at most 20 pixels at 16 or more channel levels, and 1,600 pixels
at 8 or more levels. Packed peaked at 243 and 1,861 pixels at those thresholds
(under 0.006% and 0.046% of the 4.1 M-pixel output). Tightening changed the
breathing support from **575,395 to 233,780 pixels** (59.4% smaller) while the
captured peak image remained pixel-identical to the conservative strip draw.
Peak/trough screenshots changed 63,896/63,265 pixels for normal/packed output,
all inside the breathing bands. With goo disabled, the independent fallback
halo changed 80,811/79,570 color channels at 24.9 Hz on the same paths, so the
off comparison now shows live attention rather than a stale halo.

The paired samples recorded compositor and whole-GPU counters plus load and
process snapshots. One-minute load during the idle runs was approximately
0.42–0.59; a Chromium renderer and the shared attention/agent services were
also visible. During the separate GO10 redraw fixture, the whole GPU stayed at
8.4–9.5% and another Chromium renderer was active. These loads make the busy
percentages noisy; the results do not claim an isolated GPU speedup.

The plumbus regression run passed goo-test **50/50** on both normal and packed
paths, GO16 shapes/contours **133/133** on both, GO17 breathing **12/12** on both,
overlap/hover **28/28**, depth/soak **26/26**, widgets **91/91** embedded input
checks, widget morph **270/270**, and windowing **102/102** on each path. The
combined idle fixture passed its sleep, exact-path, interpolation, tightening,
damage-bound and goo-off screenshot checks on both paths. Artifacts and
screenshots are under `build/part4-plumbus/`; the GO18 design and limitations
are in [goo-gpu-research.md](goo-gpu-research.md).

This is isolated headless validation on one RX 580. Xe, forced keyframe-target
allocation failure, physical scanout and Mike's visual acceptance remain open.

## GO19: what a breath still cost, and cutting it (2026-10-03)

Core. On the merged build (`5623284`) Mike's Xe session measured 11-14% compositor GPU with
one large window breathing and keyframes on, 14-16% in exact mode and 17% with goo off
(the fallback halo). Tenet 1 keeps the breath as it is (curve, swell, light, 25 Hz); the work
behind it changes.

### Where the cost went

Read-only samples of the live session on 2026-10-03 (four-second fdinfo windows beside
`goo-state` counters; `build/live-series-r2*.txt`), same build:

| Live state (Xe, idle clock) | Compositor GPU |
|---|---:|
| Quiet desktop, goo asleep, nothing breathing | 0.1-0.2% |
| A window breathing, goo asleep, keyframes (550,000 px of strips, 26 ticks/s, 0.27-0.35 ms per tick) | **2.8-3.9%** |
| Goo simulation awake (with or without breathing) | **27-54%** |

So a sleeping breath costs about three points there, and the rest of the 11-14% is time
the simulation spent awake. In those samples it woke every 10-15 seconds while a window was
breathing; each wake runs the full simulation and redraws every band for three seconds or
more. Mike was using the desktop during the samples, so they do not say what woke it during
his measurement; the new `wakes` counter will.

Three causes were found and reproduced on plumbus:

1. **Small changes fully wake the simulation.** A widgetized terminal whose title changes
   every five seconds (an agent session does this) re-fits its card by a pixel, which
   changes that source's outline. The fixture's `--title-hz 0.2` case went from 1.0% to
   7.8% with the window breathing: more than half the time awake.
2. **The fallback halo repainted the whole window, and everything under it, sixty times a
   second** to animate a ring a few pixels wide.
3. **Every breath tick repainted the wallpaper and windows under the strips and copied the
   result back into the backdrop cache**, although nothing under them had changed.

A fourth was found on the way: shrinking the breathing strips (GO18) sampled the CPU field
for **about one second** in a debug build (which is what `make plugin` and dev-install
produce), blocking the compositor each time the goo fell asleep with something breathing.

### What changed

- **Breath-only frames reuse the backdrop.** The goo node watches scene damage. If nothing
  but its own breathing tick damaged the output since the last frame, and all of the
  frame's damage lies in the strips, it claims the strips: nodes behind it skip them, and
  the goo writes its cached backdrop there before drawing the breath. No window, wallpaper
  or backdrop copy is touched. Any other scene damage takes the normal path, as does one
  frame in 25, so a change that arrived without scene damage cannot leave a stale backdrop
  for more than a second. Works in keyframe and exact modes.
- **Quiet wakes.** A change of outline (`shape`, `geometry`) that raises no wave does not
  restart drift or the three-second window; the simulation takes it up and sleeps once one
  full energy interval (30 steps) reads settled. Changes of color, hover, swell, hints,
  attention, stacking or window count stay loud, as do moves of more than a pixel.
- **Shape checks ignore alpha away from the contour.** A widget's distance field depends
  only on its 50% alpha contour; alpha changing elsewhere no longer rebuilds it.
- **Fallback halo.** Ticks that only animate the halo damage its ring (the bounding box
  minus the window inset by its corner radius). A breath alone repaints at 25 Hz; hover,
  focus and color transitions keep the spring's rate. Alpha-shaped widgets and window-mode
  hint tints, which can draw inside the rectangle, keep whole-window damage.
- **Tightening in slices.** About 2 ms per breath tick, long strips sampled four times
  coarser along their length; the conservative strips stay in use until it finishes
  (about five seconds in a debug build).
- **Diagnostics** in `goo-state`: `wakes` (count per cause), `last_wake`,
  `backdrop_reuses`, `breath_tightens`, `tighten_ms`, `tick_ms`, `breath_loose`.

### Fixture

`tests/goo-idle-bench.sh` gains `--title-hz` (a widgetized terminal re-titles itself),
`--awake` (samples the awake simulation and times settling), `--no-reuse`, a per-case
`wakes` and `backdrop_reuses` delta, settle times, and a visual check that frames drawn
over the reused backdrop equal frames whose scene was repainted. With `--stream-hz 2` it is
the slowly redrawing terminal under a breathing window.

### Measurements (RX 580, plumbus)

Private headless sessions, 2560×1600 at 120 Hz, Mike's goo settings preset, 5 s per case
(10 s with `--title-hz`), before is `5623284`. At most one other agent's compositor was
running; whole-GPU busy stayed within a point of the compositor's in the rows used.

| Center window breathing | Keyframes | Exact | Goo off (halo) |
|---|---:|---:|---:|
| Idle desktop | 1.0% → **0.4-0.7%** | 1.1% → **0.7-0.8%** | 5.1% → **0.4-0.5%** |
| Terminal under it redraws at 2 Hz | 0.9% → 0.8% | 1.3% → 1.0% | 5.5% → 0.7% |
| A widget re-titles every 5 s | 7.8% → **1.8-2.5%** | 7.1% → 3.1% | 5.3% → 0.4% |

- Breath-only frames: 124-130 of 129-140 frames reuse the backdrop; the backdrop copy falls
  from 229,000 to under 10,000 pixels per frame.
- Awake simulation on this GPU: 21% (it is 27-54% on Mike's Xe).
- Simulation steps per quiet wake: 215-230 → 30.
- Tightening: one 988 ms block → 247 ms in slices; longest breath tick 2.9 ms.
- A quiet wake still lasts until the energy reads settled, and a re-title that moves a
  card edge by more than a pixel is loud as before. In the same fixture the
  nothing-breathing case stayed at 13% (about 195 steps per wake) and the
  breathing-widget case varied between 2.8% and 11.6% across runs. Re-titling widgets
  remain the most expensive thing found; this change removes part of it, not all.

**Expected on Xe (not measured).** Halo: the same 60 Hz whole-window repaint is gone, so
most of its 17%. Goo on: the sleeping breath (2.8-3.9% live) loses the repaint under the
strips and the backdrop copy; the awake share falls only for wakes caused by quiet outline
changes. If Mike's wakes have another cause (`wakes` will name it: hover, color,
wallpaper, frame, settings), that share remains and is the next thing to cut.

### Fidelity

- Goo on: no pixel differs by design. Frames over the reused backdrop were identical to
  repainted frames in six paired screenshots; GO17's checks (curve, cadence, zero steps,
  pixels outside the strips unchanged) pass; GO18's key comparison is unchanged.
- Quiet wakes: the liquid's mess no longer drifts for two seconds after a one-pixel
  outline change. The outline and dye still update.
- Fallback halo: a breath steps at 25 Hz instead of 60, as the goo's has since GO17. The
  fixture's halo check (it visibly breathes) passes.
- Tight strips are up to 12 pt wider at the ends of long strips.

### Regression

plumbus, private headless sessions, normal and packed GLES 2 paths unless noted; the same
suites were run on unmodified main in a scratch copy where a failure appeared.

| Suite | Result |
|---|---|
| goo-test | 50 / 50 on both paths |
| goo-overlap-hover | 28 / 28 on both paths |
| goo-breath-bench `--verify` (GO17) | 12 / 12 on both paths |
| goo-depth-soak | 26 / 26 on both paths |
| goo-flow (connected/gapped waves, fullscreen) | 9 / 9 |
| goo-shape | 123 pass, 7 fail on both paths: the same seven fail on main (round-widget contour checks) |
| widget morph | 270 / 270 |
| widgets, goo on / goo off | 194 / 194 each |
| unsupported-GPU halo fallback | 4 / 4 |
| halo separation (goo off) | 10 pass, 1 fail: same on main (the check expects the shipped goo default while the run forces goo off) |
| hint style, goo on / off | 52 pass 1 fail / 51 pass 2 fail: the same widget-card checks fail on main |
| idle fixture `--verify --visual` | GO17 checks, key comparison, tight strips, backdrop reuse and "halo visibly breathes" all pass |

One assertion was changed with the behavior: goo-shape's "badge commit wakes sleeping
field" looked for the simulation still awake some time after a badge appeared; a quiet wake
can already be asleep again, so it now accepts simulation steps having advanced. The first
candidate build skipped scheduling the goo when a frame's damage missed it, which stopped
the simulation from stepping (and sleeping) behind an opaque wallpaper; the depth suite
caught it and the scheduling is as before. Artifacts: `build/go19/`.

Not covered: fractional output scale and two outputs for backdrop reuse (at a fractional
scale the frame damage may never fit the strips, which only means the normal path is
used; on two outputs each breathing screen sees the other's ticks as foreign damage, the
same). No physical display.
