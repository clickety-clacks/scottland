# Goo GPU research and experiments

2026-10-02. Core work, based on `343419c`, branch `goo-gpu-astra`.
The aim is a sleeping desktop with one breathing window within 2–3 compositor
GPU-busy percentage points of goo off, preserving the liquid and attention cue.
Tenets 1 and 5 choose visible, continuous attention without changing focus;
tenet 4 rules out hiding the cost by shrinking or obscuring the center window.
This document separates published techniques, code inspection, and measurements.

## What is still expensive

GO17 already freezes simulation during breathing. GO10 caches intrinsic light,
coverage, refraction and backdrop-light coefficient, then composites current app
content through that surface. However, every breath tick refreshes **two** caches
with largely the same full-resolution shader. Its stacked distance evaluations,
cubic density reconstruction, surface height derivatives and refraction exclusion
are repeated. App damage and animation invalidation are different workloads.

The output has 4,096,000 pixels at 2560×1600. One RGBA8 output-sized texture is
15.625 MiB; the existing two surface caches use 31.25 MiB, separate from the
backdrop and simulation. At 25 Hz, repeatedly shading half a million pixels twice
means 25 million expensive fragment evaluations/s even with zero simulation steps.
A scissor limits fragment work but does not make the shader inside it inexpensive.
These are arithmetic estimates, not measured GPU time.

### Why the previous fixture was misleading

The old `goo-draw-bench.py` stresses 18 windows, six streaming terminals and a
small rail attention source. It tests cache reuse during app damage well, but
its many overlapping strips and continually redrawing clients hide the cost of
a single large attention window. Its fallback halo already pays substantial
content/damage cost, so subtracting it can conceal unnecessary work in both modes.

Read-only inspection of Mike's live `wayland-1` on osanwe found:

- A 941×940 attention window overlapping a 992×1146 focused window, peripheral
  windows and real widget cards; 2560×1600 logical output. The compositor log
  records physical **2560×1600 at 120.002 Hz**, whereas the older fixture uses 60 Hz.
- Sleeping simulation, zero energy; a large padded breathing region. One five-second
  read-only fdinfo sample: **9.9% compositor / 10.6% whole GPU**, 1.9 Mcycles/s
  at a 19.3 MHz Xe reference-counter rate. This is not a new on/off comparison.
- Goo reach **33 versus shipped 24**, thickness **22 versus 13**, overlap film
  **10 versus 4**, wallpaper soak **0.9 versus 0.12**, plus a custom falloff and
  other appearance values. The unfocused center opacity is **0.91 versus 1**.
  These affect support size, overlap work and backdrop composition.

`tests/goo-idle-bench.py` reproduces the large pair, peripheral sources, real-input
rail cards, static wallpaper and one center attention request. It has explicit
`shipped` and anonymous `wide` numeric presets, idle/controlled terminal redraw
rates, and output scale. It never loads personal config. Process counters,
whole-GPU load, simulation steps, breath cadence, actual surface/copy/composite
pixel counts and screenshots accompany every on/off/on sample. Its diagnostic
counters measure submitted device pixels before discard; they are not counts of
visible goo pixels. Final results and remaining mismatches are recorded below.

## Techniques and fit

Costs below describe this renderer; they are engineering estimates until measured.
`N` is sources, `P` shaded pixels, `B` damaged band pixels, `G` grid texels.

| Technique | Cost here | Visual consequences | Work required here |
|---|---|---|---|
| Analytic metaballs / implicit density | O(NP), or O(NB) with band restriction; compact support or source bins can reduce the loop. | Summed fields naturally join/pool. Truncating the existing exponential tails changes bridge shape and dye even beside another window. | Already the basis of `gooField`. Keep its falloff, volume approximation and CPU input matched. A new compact kernel is a product change, not a free optimization. |
| Analytic rectangle SDF | Cheap arithmetic for one rectangle; multiple ordered walks per pixel are expensive. | Exact current rounded content boundaries; no resolution loss. | Existing `sdBox`, `surfaceSdf`, `backdrop` and `unionSdf` can be reused, specialized or cached. Shape-independent renderer optimizations also compose better with GO16 alpha shapes. |
| Smooth-min of SDFs | O(NB), potentially accelerated by a hierarchy; soft-min often needs exp/log. | Smooth distance union differs from summed density: pool volume, tails, wall profile and source ownership change. | Replace density, blending, bounds and CPU hit rules; substantial revalidation. Not an equivalent substitute for today's goo. [Smooth distance research](https://arxiv.org/abs/2108.10480). |
| Grid field / jump flooding | A grid amortizes source evaluation. JFA takes logarithmically many grid passes in grid extent, with work independent of seed count per pass; ping-pong textures and several neighbor fetches. | Approximate nearest boundary; thin details and ties can differ. Nearest distance alone cannot encode ordered film or every source's dye contribution. | Useful for future arbitrary alpha masks, less attractive for six rectangles. Need seed/mask generation, signed interior, ordered ownership and invalidation. [Rong and Tan's paper](https://www.comp.nus.edu.sg/~tants/jfa/i3d06-submitted.pdf). |
| Reduced resolution + upsampling | Half each axis gives one-quarter fragments; simulation already does this. | Blurring final color loses the one-device-pixel contour and crisp film. Reconstructing density then computing coverage at output resolution retains better edges. | Keep GO13's cubic field reconstruction. Lower-resolution geometry parameters are possible, but errors at stacked boundaries and normals need tests. |
| Tiles and damage restriction | O(B) plus region construction and draw submission. A conservative CPU band is often much wider than visible goo. | None if support, derivative quads, refraction margin, old positions and joined sources are covered. Bad bounds clip or leave ghosts. | Existing tile/band infrastructure is reusable. Tighter settled coverage can be derived from cached maximum-breath coverage, but GPU readback or a GPU mask adds startup cost and complexity. |
| Cache static surface + animated band | Static cost once per invalidation; cheap composition per damaged frame. | Exact if cached quantities truly do not depend on breath/backdrop. Keep refraction sampling the current app contents. | Already GO10. Split cache refresh from animated draw, or share the two refresh evaluations using multiple render targets. Track output size/transform, source/state changes and failed allocations. |
| Temporal reuse / phase cache | Store a cycle's surface samples and interpolate. Two endpoint samples are cheap; dozens of phase textures are large. | Endpoint interpolation can ghost moving contours, change sharp specular highlights and shift refraction; temporal lag occurs after state changes. | Cache generation, memory cap and invalidation. Better cache invariant geometry or coefficients than a large phase atlas. |
| Breathing overlay / post effect | One small texture-based pass, no geometry loops if all needed data are precomputed. | Pure brightness preserves the light cue but drops the requested swell. Warping cached color can distort joins/content edges. | A parameter cache with density, wall distance, film and dye could keep actual swell; refraction's moving union query still needs a faithful solution. An additive ring would violate GO7. |
| Frame-rate decimation | Approximately linear reduction in animated work. | 25→12.5 Hz visibly increases contour/light steps; holding app backdrops causes stale content. | GO17 is already 25 Hz, separate from refresh. Further decimation is a quality choice and is not the first optimization under tenet 1. |
| Compute rather than fragments | Workgroup shared data and source bins can amortize neighborhood/source reads; synchronization, dispatch and image writes add overhead. | No necessary visual difference if math/precision and sampling match. | Requires ES 3.1 rather than today's ES 2/3 paths, a second implementation, barriers and fallback. A sleeping simulation does not benefit from moving its update to compute. [Khronos ES 3.1](https://registry.khronos.org/OpenGL/specs/es/3.1/es_spec_3.1.pdf). |
| Multiple render targets (MRT) | One surface evaluation writes both existing RGBA8 cache attachments; bandwidth unchanged, duplicated arithmetic removed. | Same resolution and stored channels; expect equivalent quantization, no quality compromise. | ES 3 supports it directly; retain two passes for ES 2. Small, localized renderer change. Must restore framebuffer state and handle incomplete attachments. |

The metaball comparison is grounded in [GPU Gems 3, chapter 7](https://developer.nvidia.com/gpugems/gpugems3/part-i-geometry/chapter-7-point-based-visualization-metaballs-gpu),
which discusses implicit function/gradient evaluation and spatial organization.
Its particle-surface rendering is not a drop-in 2D compositor technique. Our
cost and suitability judgments above are deductions from Scottland's implementation.

## Lessons from other compositors and toolkits

- **Apple:** Core Animation's [performance guide](https://developer.apple.com/library/archive/documentation/Cocoa/Conceptual/CoreAnimation_guide/ImprovingAnimationPerformance/ImprovingAnimationPerformance.html)
  recommends supplying a shadow path so a stable shape can be cached. Apple's
  [render-phase talk](https://developer.apple.com/videos/play/tech-talks/10857/)
  explains that visual effects copy underlying content into offscreen textures;
  extra passes are real cost. Applicable lesson: keep shape work separate from
  backdrop changes and minimize repeated passes. Public guidance does not disclose
  a universal internal material algorithm; it is not evidence that Apple uses
  Scottland's proposed scheme.
- **KWin:** the [blur implementation](https://github.com/KDE/kwin/blob/master/src/plugins/blur/blur.cpp)
  clips the effective blur shape to the device region, reuses allocated targets,
  copies dirty backdrop regions, and downsamples/upsamples in a dual-Kawase chain.
  It explicitly balances blur radius and sampling artifacts. This supports bounded
  captures and multiresolution smooth effects; it does not justify freezing live
  content under our refracting film.
- **Hyprland:** [rendering code](https://github.com/hyprwm/Hyprland/blob/main/src/render/OpenGL.cpp)
  intersects damage with effect geometry, uses clip/stencil regions for blur and
  samples a prepared blurred background when compositing. Shadow/blur optimization
  choices must preserve what is beneath the effect. For Scottland, source geometry
  and temporal validity matter more than adopting another compositor's blur kernel.
  Its [shadow decoration](https://github.com/hyprwm/Hyprland/blob/main/src/render/decorations/CHyprDropShadowDecoration.cpp)
  bounds damage to the decoration; the [shadow shader](https://github.com/hyprwm/Hyprland/blob/main/src/render/shaders/glsl/shadow.glsl)
  evaluates rounded-edge distance and a power falloff, excludes the window interior,
  and discards zero alpha. That is a cheap analytic primitive, but replacing our
  joined, refracting liquid with independent shadows would change the product.
- **Android:** [RenderNode](https://developer.android.com/reference/android/graphics/RenderNode)
  separates display lists from transform properties and lets small pieces update
  independently. [Hardware layers](https://developer.android.com/topic/performance/hardware-accel)
  cache drawing until invalidation, making alpha/transform animation cheap at a
  memory cost. [RenderEffect](https://developer.android.com/reference/android/graphics/RenderEffect)
  provides blur and chained filters on node content; [AGSL](https://developer.android.com/develop/ui/views/graphics/agsl/using-agsl)
  permits shader effects. The transferable pattern is cache invariant content and
  change cheap composition parameters, not repeatedly rasterize a static scene.
- **Flutter:** [BackdropFilter](https://api.flutter.dev/flutter/widgets/BackdropFilter-class.html)
  recommends clipping to the exact effect area and using ImageFiltered for a single
  child rather than an unnecessarily broad backdrop filter. [BackdropGroup](https://api.flutter.dev/flutter/widgets/BackdropGroup-class.html)
  shares backdrop work across filters. Scottland already has one common field;
  preserve shared joins while restricting work to changed bands.

## Prototype decisions

First test a single surface evaluation instead of two on each breath tick. Compare
MRT cache refresh against directly drawing the animated band while retaining cached
static strips. The latter avoids two cache writes and a composite there, but repeats
surface work at the client's redraw rate when it exceeds 25 Hz. Measurements must
include idle and 30 Hz content before choosing it. Neither experiment reduces
resolution, changes the breath curve, changes simulation/input, or uses app names.

A richer static-parameter cache is a later candidate if eliminating duplicate work
is insufficient. Reject phase interpolation and pure brightness overlays for this
iteration: their visual differences need a product decision, whereas exact work
elimination has a clear first claim on the budget.

## Measurements and validation

The selected implementation is `8a46e4f`: draw only the animated portion directly,
retain the original two-pass cache for static strips. MRT was prototyped and
measured, but adds ES-3-specific resource/shader handling for a smaller benefit.
Its code is not shipped. The direct prototype initially included MRT for the
one-time static fill; the final version removes that unnecessary machinery.
Fractional-DPI partitioning is done in framebuffer pixels, preventing overlapping
scissors at the boundary between direct and cached rendering.

All new tests run on plumbus's RX 580, in this checkout's own disk-backed
`build/headless-gpu-astra` directory, with no private `XDG_RUNTIME_DIR`, dev-install,
reload of a real session, GPU-clock change, or personal-config import. Baseline
is `343419c` plus work-counter instrumentation; its saved plugin is reused for
matched comparisons. Each session starts after the selected build is in place.
Performance cases use five-second on/off/on samples; visual runs use three-second
cost samples plus two full breathing periods. Timers are observed, never paced
by benchmark polling. Each sample's full process list and artifacts accompany it.

### Exact footprint, incomplete hardware reproduction

After fixing the fixture's initial window-placement sequencing and disabling
foot's resize-to-cell snapping, the attention window is **941×940** and the
focused window **992×1146**. Actual Super drags establish their final presentation
and create both cards. The wide preset's breathing rectangles exactly match the
read-only live snapshot: `(1055,0,1217,89)`, `(1055,89,154,908)`,
`(2118,89,154,908)`, `(1055,997,1217,154)`. Their area is **575,395 pixels**, or
**14.05%** of the output, compared with GO17's old widget fixture's <2%.

This reproduces the live **work footprint**, not its 9–10-point Xe busy increment.
AMD is the only GPU on the authorized test host. The fixture uses foot in place
of Ghostty, controlled output rates in place of Mike's terminal content, a test
wallpaper and smaller default cards. It matches the logical/output dimensions,
center-window sizes and breathing damage, but not physical scanout or the live
client's actual frame cadence. `--refresh-hz 120` additionally matches the nominal
refresh discovered in the live modeset log; initial pairs use the old 60 Hz rate.
The live read-only interfaces expose no draw-count
counter in `343419c`; the new counters make future comparisons more specific.

Initial diagnostic runs are retained as `baseline-wide-r2` and
`baseline-wide-stream`, but are not the matched comparisons: terminal cell
rounding and an incompletely settled placement affected their geometry. The
fixture re-arms its own attention request after switching off goo and asserts a
changing fallback swell. A later screenshot check revealed that **model swell
alone is insufficient**: with idle clients, the fallback image remains frozen.
The `--verify` path now checks off peak/trough pixels and fails on this case;
the limitation is retained rather than weakening that assertion. The earlier
streaming fixture can hide this by supplying client damage continuously.

### RX 580 measurements

These are ranges of the two on samples, **not confidence intervals**. Whole-GPU
load varied with other agents and the shared desktop; no agent was coordinated
with and no other checkout was read. Lower process busy under higher external
load can reflect GPU clock changes. It is not a negative cost for goo.

| Exact fixture | Baseline goo on | Final goo on | Goo off | Final increment over off |
|---|---:|---:|---:|---:|
| Shipped settings, idle large window, 60 Hz output | 1.9–2.0% | **1.3%** | 0.0% | **1.3 points** |
| Wide live numeric preset, idle large window, 60 Hz | 1.6–2.5% | **1.6–1.7%** | 0.0% | **1.6–1.7 points** |
| Wide preset, attention terminal prints at 30 Hz, 60 Hz output | 6.2–8.4% | **4.4–7.6%** | 5.4% | **at most 2.2 points** in these samples |
| Wide preset, idle large window, 120 Hz output | 2.3–2.4% | **1.7%** | 0.0% | **1.7 points** |
| Wide preset, full-window redraw requested at 120 Hz, 120 Hz output | 18.2–22.9% | **22.0–22.1%** | 20.1% baseline / 20.4% final | **1.6–1.7 points** |

**Idle off-comparator caveat:** all 0.0% off rows above have changing fallback
model swell but no visible fallback animation. Their goo-on before/after costs
and work counters remain useful; they are **not** validation of equal visible
breathing in the on/off comparison. Final idle goo-on itself costs only 1.3–1.7%
on this AMD host, a conservative total-cost bound, but the matched visual
off-comparator defect must be resolved before claiming full fixture parity.

The stream pair is especially contended: baseline whole GPU is 12.6–17.9%,
final 11.0–18.1%, and off 6.3%/9.2%. The low 4.4% final sample is retained, not
interpreted as faster than having no goo. A separate direct-prototype pair gave
**7.5–7.6%** against the same **5.4%** off, whole GPU 11.0–11.6%.

The 120 Hz stress case changes the entire terminal background using OSC 11, so
it damages the full attention client rather than only newly printed lines. It
actually yields about **110 affected compositor draws/s**. The final process
busy lies inside the baseline range; this is **not a proven speedup** for this
case. Baseline whole GPU is 21.5–22.9%, final 22.6–23.2%; CPU is 14.5–15.6%
before and 14.7–14.9% after. It exposes the client-redraw cost hidden by a quiet
terminal, while the final goo increment stays within the requested budget in
these samples. The idle 120 Hz case retains 25 breath ticks/s, with whole GPU
2.3–5.8% before and 1.7–3.9% after.

The stable structural measurements are more informative:

| Workload | Surface pixels per draw or per second, before → final | Extra cache composite | Median idle draw query, before → final |
|---|---|---|---|
| Wide idle | **1,150,790 → 575,395 per draw (−50%)** | 575,395 → **0** pixels/draw | 0.357–0.557 → **0.281–0.283 ms** |
| Shipped idle | **740,314 → 370,157 per draw (−50%)** | 370,157 → **0** pixels/draw | 0.405–0.409 → **0.217–0.218 ms** |
| Wide 30 Hz terminal | approximately **28.7 → 15.7 million surface pixels/s** | approximately 21.8 → **6.2 million pixels/s** | mixed app/breath queries are not an isolated pass comparison |
| Wide idle, 120 Hz output | **1,150,790 → 575,395 per draw (−50%)** | 575,395 → **0** pixels/draw | 0.539–0.552 → **0.277–0.280 ms** |
| Wide full-window 120 Hz redraw | approximately **28.5 → 19.3 million surface pixels/s** | approximately 42 → **22.7 million pixels/s** | direct app-frame surface work replaces cache-only composition, so mixed queries are not comparable |

All on samples have **zero simulation steps**, sleeping=true, approximately 25
breath ticks/s. Wide idle submits one draw per breath; the 30 Hz client produces
about 55 affected draws/s. Direct drawing therefore does more surface work than
MRT on some app frames, but avoids the two render-target writes and composite.
CPU stayed about 2.2% in final wide idle versus 2.0–2.3% baseline, and 6.1–6.3%
streaming versus 6.3–6.5% baseline (including IPC sampling). No cache memory added.

For the MRT prototype, wide idle was **2.0–2.2%**, 0.425–0.443 ms/draw, and wide
streaming **7.9%** versus 5.4% off. It halves surface evaluations at each breath
but still writes both cache textures and composites the band. Direct drawing won
both tested workloads, and works on the existing GLES 2 path as well.

### Visual and regression evidence

The nine goo-on large-source checks pass at **1× and 1.5×**, and on **packed GLES 2**:
unchanged five-second curve, approximately 25 Hz, no simulation/energy changes
across two cycles, visible peak/trough, and zero changed pixels outside the
reported breathing strips. The packed answered case returns **0.0% GPU** with
zero draws, simulation steps, breath ticks, surface/copy/composite pixels.
The zero GLES-2 timer-query value is unavailable timing, not a free draw.
The subsequently added goo-off screenshot assertion fails with **zero changed
channels** at peak/trough in the idle scene on **both final and `343419c`**.
These retained failures are in `final-fallback-comparator` and
`baseline-fallback-comparator-r2`; they are additional
to the nine passing goo-on checks. This does not justify changing the production
frame/input code within a contained goo-renderer optimization.
The initial three-second baseline attempt stopped at the model amplitude check
before screenshots; short cost samples now require changing swell, reserving
the full amplitude bound for samples spanning at least one five-second period.

**Packed-path caveat:** log auditing found repeated `glCopyTexSubImage2D(missing
readbuffer)` / `GL_INVALID_OPERATION` errors in forced GLES 2 despite those
assertions passing. A fresh instrumented `343419c` packed run reproduces the
same errors. This is not introduced by the animated-band change, but means the
packed screenshots do not certify correct live backdrop/refraction. Keep this
baseline defect open; normal GLES 3 logs are clean, and all GPU cost comparisons
above use that normal path. The packed implementation was exercised, but is not
an unqualified compatibility pass.

Peak/trough and packed overlap screenshots were inspected. The full direct
shader keeps the same coverage, depth, dye, lighting and live refraction, while
avoiding intermediate RGBA8 quantization in the animated band. Initial paired
screenshots exposed stale widget palette files from reused display names; the
fixture now initializes its private session palette before opening cards.

The final `baseline-visual-matched` / `final-visual-matched` pair has a mean
absolute channel difference of **0.052 / 255 inside the breathing band**, maximum
4, with only four of 975,300 channels differing by more than 2. Subtracting each
scene's trough from its peak gives a maximum baseline/final animation-delta
difference of **3 / 255** (five channels exceed 2). These are close visual matches,
not bitwise identity. Full-image differences are larger because the peripheral
windows settle at slightly different fractional positions (about 0.2 pixels),
changing resampled text and static goo. Raw whole-image and band comparisons are
retained. Within each final scene, pixels outside the animated support are
identical over two periods. Physical-display visual acceptance remains outstanding.

| Final regression on plumbus | Result |
|---|---|
| Goo input, palette, settings, live toggle and sleep, normal / packed | **45/46 each**; same Alt-declutter return assertion fails on instrumented `343419c` baseline (45/46); packed GL caveat above |
| Film, swell, joined waves/dye, whole-control highlights, drag and stale-damage cleanup, normal / packed | **28/28 each** |
| Connected/gapped flow, fullscreen exclusion, two-output dragging | **12/12** |
| GLES without derivatives, halo fallback and real drag | **4/4** |
| Widget morph, collapse/expand, reversal, attention dye and live goo toggle | **270/270** |

The normal goo failure repeats in two final runs and in the baseline comparison.
It checks `window_distance` after Alt release, not rendered cache pixels. The
packed run fails the same assertion. No assertion was removed or relaxed; this
existing return-from-declutter failure remains unresolved. All other suite checks
above pass. The baseline, initial final failure and final logs/screenshots are
retained under `build/gpu-astra/regressions/`.

### Reproduction and remaining work

Deploy only with `SCOTTLAND_DEPLOY_DIR=Projects/scottland-gpu-astra
 tests/deploy.sh plumbus --tests-only`. On plumbus, set
`TMPDIR=$HOME/.cache/scottland-test-tmp` and a unique
`SCOTTLAND_HEADLESS_DIR=$PWD/build/headless-gpu-astra`, then run
`tests/goo-idle-bench.sh build/gpu-astra/run --seconds 5`.
Add `--settings shipped`, `--stream-hz 30`, or `--deterministic --verify --scale 1.5`
for the corresponding cases; `SCOTTLAND_TEST_GOO_GLES=2` selects the packed path.
Use `--refresh-hz 120` for the panel's nominal rate; adding
`--stream-hz 120 --full-redraw` selects the full-client damage stress case.
The wrapper preserves Wayfire logs and stops its private session.
`--verify` currently exits nonzero on the idle fallback-animation assertion;
this deliberately records the unresolved comparator defect.

Artifacts are copied back under `build/gpu-astra/`, including `fixture.json`,
`measurements.jsonl`, `peak.png`, `trough.png`, checks and Wayfire logs. Baseline
and prototypes are named explicitly; failed initial fixtures are retained.
No candidate build has been installed in or measured on Mike's live session;
only the already-running baseline was inspected read-only. All task sessions
were stopped and their private headless directories removed. The shared runtime
remained at 1% usage. No GL/shader errors were found in the normal GLES benchmark
logs; the forced GLES 2 logs require the caveat recorded above.

The **Intel Xe 2–3-point target remains unverified**. The measured shader-work
reduction cannot be converted into an assured live GPU-busy reduction. Next:
resolve the frozen idle fallback comparator, run the matched fixture on an
authorized Xe test machine, collect the new draw counters alongside client frame
pacing, then consider cached geometry parameters
or tighter settled tile coverage if direct draw still exceeds budget. Neither a
lower breath cadence nor a changed liquid model is justified by these samples.
