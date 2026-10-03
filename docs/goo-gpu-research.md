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
  windows and real widget cards; 2560×1600 logical output.
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

Pending experiments. No claim yet that the live Intel Xe target is achieved;
plumbus has an RX 580, and osanwe permits read-only live observation only.
