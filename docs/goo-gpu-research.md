# Goo GPU cost research and combined GO18 design

This folds together the 2026-10-02 Fable and Astra investigations, which both
started from `343419c`. It describes the settled GO10/GO16 implementation in
this branch and keeps the original measurements labeled as historical: they
were collected with different fixtures, under variable shared GPU load, and
are not a substitute for the merged build's results in [goo.md](goo.md).
Scope is core behavior for any Scottland user.

## Why idle breathing needed a separate fixture

The earlier `goo-draw-bench.py` is useful for settled cache reuse under client
damage, but its 18-window scene, six streaming terminals and small attention
widget do not isolate one large breathing window on an otherwise idle desktop.
The two investigations therefore added `goo-idle-bench.py`, which arranges a
partly overlapped center attention window, a focused center window, scaled
periphery windows, two real-input rail widgets and stationary wallpaper. It
supports both the wide live-like numeric preset and shipped settings, optional
app redraw, output refresh/scale, explicit test options, keyframe comparisons,
and a goo-off pixel check. It does not read personal config.

The earlier read-only osanwe snapshot showed a 941×940 breathing window partly
under a 992×1146 focused window at 2560×1600 and 120 Hz. Reach 33, thickness
22, overlap film 10, wallpaper soak 0.9, custom falloff and 0.91 unfocused
opacity differ from shipped values. One live sample reported 3.17 ms per
breathing draw; a 25 Hz timer makes that a meaningful idle cost. That old
sample included both a window and a widget breathing and was not a matched
test of this change.

The shared plumbus RX 580 comparisons on the independent branches put the
single-window idle goo-on cost at 1.0–1.5% compositor GPU for Astra's exact
direct draw and 0.6–0.9% for Fable's keyframes, against 1.9–2.4% baseline.
They ran under uneven external GPU load (up to 35% whole-GPU busy); use these
as historical observations, not precise speedup ratios. The more stable
structural comparison was roughly 575,000 conservative pixels per tick for
Astra versus 223,000 tight pixels and around 16 layer refreshes over five
seconds for Fable. Both slept with zero simulation steps and retained the
25 Hz breath cadence.

## Findings and design choices

GO10 already caches intrinsic light, coverage, refraction and backdrop-light
coefficient for settled goo; it composites against current app content. GO17
keeps simulation asleep during the light-and-shape breath. The remaining cost
was repeated full-resolution surface shading in the breathing area, much of
which was dry support rather than visible liquid. A scissor limits fragments
but does not make each fragment's source walk, cubic field reconstruction,
normal and refraction calculations cheap.

The selected combination follows Tenet 1: an attention cue must remain
visible and animate with goo enabled or disabled. Tenet 4 keeps the full-size
center window and liquid treatment. Tenet 5 leaves focus and placement alone.

1. **Tight breathing regions.** When the field settles, CPU samples of the same
   density model find the wet bounds inside each conservative strip, with
   reconstruction padding. It needs no GPU readback and benefits both render
   paths. A masked alpha source whose broad support intersects a strip selects
   a denser CPU sample lattice; the CPU SDF is the same GO16 shape used by the
   field, and the padding covers the sample spacing and reconstruction. The
   pixel-difference check compares a trough-to-peak repaint through tight
   support with the conservative support.
2. **Default keyframe path.** The renderer holds two neighboring intrinsic /
   refraction surfaces at breath values and cross-fades their composites over
   the live backdrop. Light follows the exact breath; the shore cross-fades
   between sampled positions. At the common exponential preset, spacing is
   selected from the estimated shore travel and limited to 16 intervals; if
   the estimate needs more, the renderer uses exact strips. A second pair of
   output-sized RGBA8 targets costs 31.25 MiB at 2560×1600 while needed and is
   released when no strip breathes or the path is switched off. A key refresh
   performs intrinsic and refraction passes; steady frames only composite.
3. **Exact live fallback.** If the keyframe targets cannot be allocated, the
   live `scottland/goo_breath_keys` option is false, or the interval cap is
   exceeded, the renderer shades the breathing strip directly with the GO10
   surface shader. The static settled region stays cached. Allocation failure
   does not disable goo or attention. Turning keyframes off through
   `scottland-ctl set goo_breath_keys false` applies without reload.
4. **Device-pixel partitioning.** The cached and breathing regions are split
   after conversion to framebuffer pixels, so fractional-scale boundaries are
   neither blended twice nor skipped. Current backdrop capture still includes
   the GO10 refraction margin.
5. **Fallback halo invalidation.** The independent goo-off check found that
   the model swell advanced while the rendered per-window attention halo did
   not. Damage now invalidates the frame transformer's cached surface as well
   as its parent, and the fixture compares peak/trough screenshots. This fixes
   the baseline comparator as well as the visible attention cue.
6. **Packed energy settling.** The packed GLES 2 reduction multiplies a source
   delta by sixteen before quantizing its energy readback into RGBA8. A remaining
   one-level color change therefore sat at an energy floor of 16/255 and kept
   attention widgets from sleeping. The packed-only sleep bound now includes
   that quantization floor plus a small epsilon; larger changes still wake the
   field. Normal precision retains its original threshold.

`goo-state` reports submitted draws, surface/capture/composite pixel counts,
keyframe layer refreshes, key values, and whether interpolation is active.
These are work counters, not visible-pixel counts. `breath_refreshes` excludes
whole cache fills; each layer refresh runs two surface passes. Exact per-tick
strip work is represented by `surface_pixels` and draw deltas.

## Alternatives considered

The existing summed density field already gives pooled liquid, thinning and
connected bridges. Replacing it with a smooth-min SDF would change those
behaviors. Jump flooding builds arbitrary-shape distance fields, already used
for GO16 widget contours, but does not reduce the cost of a settled scene.
Lowering the final surface resolution would soften the one-device-pixel shore,
specular ridge and refraction. Pure brightness overlays do not move the shore
or highlights. Further reducing the 25 Hz cadence risks visible steps. Compute
shaders would require a separate GLES 3.1 path alongside the GLES 2 fallback.
Multiple render targets can remove one of the two refresh passes but add ES3
attachment handling for a smaller share of the new cost; that experiment was
not selected. Client redraw and idle breathing are distinct workloads, so both
remain in the test fixture.

## Limits and unverified cases

Keyframes approximate intermediate geometry; the exact setting is available
for immediate rollback. On plumbus, midpoint comparisons across the wide
breathing window differed from exact output by at most 20 channel levels in 20
pixels at or above 16 levels, and 1,600 pixels at or above 8 levels in normal
precision. Packed precision peaked at 243 and 1,861 pixels at those thresholds.
The tested peak-to-trough damage stayed pixel-identical when tightened
(575,395 to 233,780 pixels). These results bound the tested layout and settings;
they do not prove the half-device-pixel estimate for every custom plateau, thin
joined film, shape, highlight or refraction configuration. CPU tight-strip
sampling also depends on padding and the tested density model. The denser
sample lattice used when GO16 alpha shapes intersect breathing support still
needs broader contour and custom-curve checks. Extreme settings, rotation,
many outputs, allocation failure and physical-display animation also need
targeted checks. The extra keyframe textures scale with output pixels. plumbus
has an RX 580; the Intel Xe live target and Mike's visual acceptance on the
120 Hz physical panel remain unmeasured. Shared-GPU contention makes short busy
percentages noisy, so every idle sample records whole-GPU and compositor busy
plus process/load snapshots and submitted-pixel counters.

The earlier packed GLES 2 comparisons also logged
`glCopyTexSubImage2D(missing readbuffer)` errors, reproduced on the instrumented
baseline. Report any occurrence separately from assertion counts; a passing
packed screenshot is not by itself proof of correct live backdrop reads.

## References retained from both investigations

- Blinn, [A Generalization of Algebraic Surface Drawing](https://www.microsoft.com/en-us/research/publication/a-generalization-of-algebraic-surface-drawing/) (abstract only); Wong, [Metaballs and Marching Squares](https://jamie-wong.com/2014/08/19/metaballs-and-marching-squares/); [GPU Gems 3, Point-Based Visualization of Metaballs](https://developer.nvidia.com/gpugems/gpugems3/part-i-geometry/chapter-7-point-based-visualization-metaballs-gpu).
- Inigo Quilez, [2D distance functions](https://iquilezles.org/articles/distfunctions2d/) and [smooth minimum](https://iquilezles.org/articles/smin/); Rong and Tan, [Jump Flooding](https://www.comp.nus.edu.sg/~tants/jfa/i3d06.pdf); [Valve alpha-tested magnification](https://steamcdn-a.akamaihd.net/apps/valve/2007/SIGGRAPH2007_AlphaTestedMagnification.pdf).
- [Damage tracking](https://emersion.fr/blog/2019/intro-to-damage-tracking/), [EGL buffer age](https://registry.khronos.org/EGL/extensions/EXT/EGL_EXT_buffer_age.txt), [partial update](https://registry.khronos.org/EGL/extensions/KHR/EGL_KHR_partial_update.txt), [wlroots damage ring](https://raw.githubusercontent.com/hyprwm/wlroots-hyprland/main/include/wlr/types/wlr_damage_ring.h), and [Wayfire render manager](https://raw.githubusercontent.com/WayfireWM/wayfire/master/src/output/render-manager.cpp).
- Apple, [Core Animation performance](https://developer.apple.com/library/archive/documentation/Cocoa/Conceptual/CoreAnimation_guide/ImprovingAnimationPerformance/ImprovingAnimationPerformance.html) and [ProMotion optimization](https://developer.apple.com/documentation/quartzcore/optimizing-iphone-and-ipad-apps-to-support-promotion-displays); [Chromium compositor-only properties](https://web.dev/articles/stick-to-compositor-only-properties-and-manage-layer-count); Android [RenderNode](https://developer.android.com/reference/android/graphics/RenderNode) and [RenderEffect](https://developer.android.com/reference/android/graphics/RenderEffect); Flutter [RepaintBoundary](https://api.flutter.dev/flutter/widgets/RepaintBoundary-class.html) and [BackdropFilter](https://api.flutter.dev/flutter/widgets/BackdropFilter-class.html).
- KWin [blur implementation](https://invent.kde.org/plasma/kwin/-/raw/master/src/plugins/blur/blur.cpp); Hyprland [renderer](https://raw.githubusercontent.com/hyprwm/Hyprland/main/src/render/Renderer.cpp); Khronos [OpenGL ES 3.1](https://www.khronos.org/news/press/khronos-releases-opengl-es-3.1-specification) and [ES 3.0 `glDrawBuffers`](https://registry.khronos.org/OpenGL-Refpages/es3.0/html/glDrawBuffers.xhtml); Intel [checkerboard rendering](https://www.intel.com/content/dam/develop/external/us/en/documents/checkerboard-rendering-for-real-time-upscaling-on-intel-integrated-graphics.pdf).
- Linux DRM [GPU usage stats](https://docs.kernel.org/gpu/drm-usage-stats.html), Xe [frequency management](https://docs.kernel.org/gpu/xe/xe_gt_freq.html), and [Xe client counters](https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/xe/xe_drm_client.c). The counter timebase is not the execution frequency; the original Xe frequency explanation was a hypothesis, not a measured cause.


## Round 2: retain the scene beneath the breathing band

The combined GO18 build's live Xe numbers (Mike, October 3) changed the
bottleneck: goo cost less than the breathing fallback, but the entire
compositor still spent 11–16% with goo. See [GO19](goo.md#go19-breathing-damage-and-retained-backdrop-2026-10-03)
for the follow-up fixture, changes, measurements and remaining verification.

Tracing the installed Wayfire 0.11 rendering contract explains why band
damage alone does not eliminate content composition. A transformer schedules
its damaged intersection without occluding layers below. Its cached child
texture avoids rebuilding unchanged client surfaces, but that texture is
still sampled and blended into the output on every intersecting breath.
Scottland's goo then copies those pixels into its refraction backdrop.
The selected temporal-reuse prototype restores the **existing** backdrop
and removes that band from lower-layer scheduling, invalidating it on scene
or client changes. This changes composition work, not the field or its
resolution. The fallback independently needed edge-only damage and a 25 Hz
passive presentation timer. Its 16 ms physics stays unchanged.

Sources inspected for this implementation:
[Wayfire transformer rendering](https://github.com/WayfireWM/wayfire/blob/v0.11.0/src/api/wayfire/view-transform.hpp),
[render-instance scheduling contract](https://github.com/WayfireWM/wayfire/blob/v0.11.0/src/api/wayfire/scene-render.hpp),
[scene damage observer](https://github.com/WayfireWM/wayfire/blob/v0.11.0/src/core/scene.cpp),
[output damage and buffer-age handling](https://github.com/WayfireWM/wayfire/blob/v0.11.0/src/output/render-manager.cpp).
The wlroots damage ring can collapse more than 20 rectangles into their
bounding box; the read-only live snapshot had four breathing rectangles,
so that mechanism does not explain its steady breathing region.
[wlroots damage ring](https://gitlab.freedesktop.org/wlroots/wlroots/-/blob/0.20/types/wlr_damage_ring.c).
