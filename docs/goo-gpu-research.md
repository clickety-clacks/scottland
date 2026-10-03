# Goo GPU cost: what the live desktop does, and cheaper ways to draw it

Research and measurements behind GO18 ([goo.md](goo.md)). Written 2026-10-02 on branch
`goo-gpu-fable`, from `343419c`. Scope is **core**: any Scottland user's goo.

## The problem

Mike's live compositor (2560x1600, Intel Xe, scale 1) with one 941x940 center window breathing
and the simulation asleep spent about 10% GPU with goo on and 1.0% with it off. The headless
fixture (`tests/goo-draw-bench.py`) showed about one point of difference, so it could not be used
to fix this.

### What the live desktop does that the old fixture did not

Read-only from the live session (`scottland/goo-state`, `scottland/layout-state`, option values,
Xe sysfs and fdinfo), 2026-10-02:

| Fact | Live | Old fixture |
|---|---|---|
| What else draws | nothing: idle | six terminals streaming, already 14.6% with goo off |
| GPU frequency | 1000-1300 MHz of 2500 (idle floor) | raised by the streaming load |
| Breathing strips repainted per tick | 675,000 px (14 rectangles; a window and a widget were breathing when read) | far fewer around a rail widget |
| Shader path | overlap (ordered, per-source loops): the breathing window sits partly under the focused one | same, but hidden in the streaming cost |
| Goo width | Mike's own settings: reach 33, thickness 22, film 10 | shipped 24 / 13 / 4 |
| Time per breathing tick (`draw_gpu_ms`) | **3.17 ms**, 25 times a second | 0.4-0.5 ms on the RX 580 |

3.17 ms x 25 ticks is 79 ms of GPU per second: 7.9 points, which is the gap. Three things
produce it:

1. **Every tick re-ran the full surface shader twice** (GO10's two cache textures) over the
   whole breathing strip. That shader walks every source several times per pixel and
   reconstructs the field with a cubic filter.
2. **Most of the strip is dry.** The strip is sized for the farthest the liquid could possibly
   reach (four reaches, 138 px with Mike's settings, widened again by the film bound); the
   visible liquid is 20-40 px wide. About 60% of the shaded pixels were discarded at the end.
3. **An idle Xe runs at its lowest frequency**, so the same work reads as a larger busy
   percentage than it does beside streaming terminals. The kernel documents Xe's counters as
   cycles on the GPU clock, not work done ([drm-usage-stats], [xe fdinfo], [xe freq]); i915's
   governor only raises frequency above 95% busy in a 13-20 ms window ([intel_rps.c]), which a
   3 ms job every 40 ms never reaches. This is why a fixture with streaming clients hid the
   cost: it is a property of an idle desktop.

`tests/goo-idle-bench.py` reproduces the scene: nothing redrawing, the same window sizes and
stacking read from the live layout, scaled periphery windows, two rail widgets, and optionally
someone's own goo settings (`--options FILE`). It measures goo on and off in one session for
nothing breathing, the center window breathing and a widget breathing. On plumbus's RX 580 the
baseline shows the same structure at that GPU's speed (0.46 ms per tick, 130 full-shader
refreshes per 5 s, 1.8 points over goo off). An RX 580 is several times faster than an idle Xe,
so its percentages are smaller; per-tick time, refresh count and strip area are the figures
that carry over. Xe itself could not be measured for this work: tests may not run on osanwe.

## Techniques surveyed

For each: what it is, what it would cost and change here, and what it would take.

### 1. Metaballs and implicit surfaces: per-pixel sum or grid

Blinn's blobby model sums a density per primitive and draws the threshold surface ([Blinn]).
Evaluated per pixel it costs pixels x primitives: Jamie Wong counts 14 million operations a
frame for 40 balls at 700x500, and his fix is to sample a coarse grid and interpolate the
crossing ([Wong]). GPU Gems 3 goes further and evaluates only at points constrained to the
surface, so cost follows surface area, not volume ([GPU Gems 3]).

*Here:* the goo already is this: a half-resolution summed field, thresholded with a cubic
reconstruction (GO13), evaluated only in tiles around windows. What was not grid-limited was the
**surface** shader, which still looped over sources per full-resolution pixel for the window
SDF and stacking. Nothing new to adopt for the field; the lesson (cost should follow the
surface, not the bounding area) is what the tightened strips below apply.

### 2. Signed distance fields

- *Analytic rounded-box SDFs* ([Quilez 2D]) are one square root each and exact; the goo uses
  exactly this per source. Cheap per source, but the per-pixel loop over sources is the cost.
- *Smooth minimum* ([Quilez smin]) blends SDFs into one soft shape in a single expression.
  It would replace the summed-density model with a different look: no fixed-volume thinning,
  no necking bridges (GO2), and polynomial variants depend on blend order. A visual change
  Mike has not asked for; rejected.
- *Jump flooding* ([Rong & Tan]) builds a distance field for arbitrary shapes in log n
  full-grid passes, independent of the number of seeds. Useful if sources stop being
  rectangles (GO16's alpha-shaped widgets could use it for the field); it does not help a
  static scene, where the field is already cached.
- *Low-resolution field with a derivative-wide threshold* ([Green]) is how GO13 antialiases
  the shore. Already used.

### 3. Reduced resolution and upsampling

KWin and Hyprland blur at 1/2 to 1/16 resolution with dual-Kawase passes ([Bjørge], [KWin
blur]); Bjørge measures 8 passes for a 97-pixel blur and less than half Kawase's bandwidth.
*Here:* the field, waves and dye are already half and quarter resolution. The remaining
full-resolution work is the lighting, whose detail (a one-pixel shore, specular ridge,
refraction) is what makes it read as liquid. Rendering the surface at half resolution would
soften exactly those; rejected as a visible loss.

### 4. Damage-limited and tile-based rendering

Compositors repaint only damaged rectangles and reuse old buffer contents by age ([emersion],
[EGL_EXT_buffer_age], [EGL_KHR_partial_update]). emersion warns that many small rectangles
cost more than their bounding box, and wlroots collapses a long list to its extents
([wlr_damage_ring]); Wayfire feeds its damage through that ring ([Wayfire render-manager]).
*Here:* breathing damage already was limited to strips, but the strips were the conservative
support, mostly dry. **Adopted:** once the goo sleeps, each strip shrinks to the bounding box
of the liquid actually in it, found by sampling the CPU field (the one input uses) at the top
of the breath. The number of rectangles does not grow, so the output does not collapse them.
Cost here: about 23,000 field samples once per settle, on the CPU. Visual change: none; the
fixture checks that a trough-to-peak step through the tight strips is pixel-identical to the
same step through the old ones.

### 5. Cache the static layers, composite the animated part

Core Animation rasterizes a layer once and composites the bitmap ([shouldRasterize], [CA
performance]); Chromium animates only transform and opacity on the compositor because those
need no re-raster ([Chromium compositor]); Android's RenderNode and Flutter's RepaintBoundary
keep a recorded or rasterized subtree and re-use it while properties change ([RenderNode],
[RepaintBoundary]). Hyprland caches the blurred backdrop per monitor and redoes it only when
dirty ([Hyprland]). Apple's Liquid Glass asks apps to put glass shapes in one container so
they are rendered together ([Liquid Glass]), the same idea as one goo field per screen.
*Here:* GO10 already caches the settled surface (intrinsic color and a refraction map) and
composites it over the changing backdrop. But breathing changes the **shape**, not only an
opacity, so it invalidated that cache 25 times a second. The common rule in all of these
systems is: animate only what the compositor can apply to a cached bitmap. That points to the
next technique.

### 6. Temporal reuse: keyframes and cross-fading

Game flipbooks cache a few frames of a periodic effect and blend neighbors; with motion
vectors a 64-frame sheet stretches more than tenfold at the same perceived rate ([Lozar]).
Checkerboard rendering shades half the samples and reconstructs from two frames, saving about
5 ms at 1080p on Intel integrated graphics for low-motion scenes ([Intel checkerboard]).
No source was found describing this for a periodic UI animation; the idea transfers directly.

*Here:* a breath is a one-parameter family of surfaces. The light term is linear in the
breath, and the shore moves 2.7 pt in total with shipped settings (3.6 with Mike's).
**Adopted (GO18):** cache the surface at keys of the breath spaced at most half a device
pixel of shore travel apart (7 keys shipped, 9 for Mike), keep the two keys around the
current breath in two layers, and cross-fade their composites. The full shader runs only
when the breath crosses a key: about 10 times per breath instead of 125. Between keys the
shore cross-fades over less than half a pixel instead of sliding; the light is exact.
Cost: one extra pair of cache textures while something breathes (31 MiB at 2560x1600, freed
when nothing does) and four more texture reads per composited pixel in the strips.
A full flipbook (every key kept) would remove even those refreshes but needs 250 MiB;
rejected.

### 7. Breathing as an overlay or post effect

A glow drawn over the settled goo (additive strip, or a uniform applied in the composite)
would cost almost nothing. The light half of the breath is exactly that and is now handled by
the cross-fade for free. The swell half moves the shore and its highlights; an overlay cannot
move them, so the breath would become a glow with a still outline. That is a visible change
to what Mike approved in GO17 (a gentle swell with the light), and the keyframes reach the
cost target without it. Not adopted; it stays the fallback if a GPU ever needs it.

### 8. Frame-rate decimation

Apple recommends 8-15 or 15-24 Hz ranges for small, slow animations on ProMotion displays
([ProMotion]); its sleep LED patent drives the breath itself at 125 Hz PWM, which is a
hardware precedent only ([Apple LED patent]). GO17 already ticks at 25 Hz independent of
refresh rate. Halving it again would halve the remaining cost, but each tick would move the
light by up to 1.6% and steps become visible at the crest. Skipping only the ticks that
change nothing visible (the flat trough) saves about 5%. Not adopted: the keyframes made each
tick cheap enough that the rate no longer matters much.

### 9. Compute shaders and multiple render targets

Compute shaders are core in GLES 3.1 ([GLES 3.1]) but their speed varies widely by driver
([Levien]), and the goo must still run on GLES 2 with packed targets. Multiple render targets
are in GLES 3.0 ([glDrawBuffers]) and would write both cache textures in one pass, halving
each key refresh. After keyframes, refreshes are about 8% of ticks, so this would save a few
percent of what remains, at the price of a second shader variant in the file another branch
is merging (GO16). Not adopted now; worth doing if the settle refresh ever shows up.

### 10. How other desktops keep such effects cheap

The common pattern is the one in 5: render the expensive thing once, re-do it only when its
inputs change, and animate with operations on the cached result. Hyprland's blur
(`new_optimizations`, `xray`) and KWin's blur both avoid recomputation when nothing behind
changed and copy only dirty rectangles; Hyprland's shadows are one analytic pass with no blur
([Hyprland], [KWin blur]). Android warns a `RenderEffect` shader on a view costs more than a
shader paint ([AGSL]); Flutter calls `BackdropFilter` and `saveLayer` expensive because of the
offscreen buffer and target switch ([Flutter BackdropFilter], [Flutter perf]). None of them
animates the geometry of a refracting shape continuously at rest; where shapes move (Liquid
Glass morphs) it is a short transition, not a standing animation. A breathing halo is unusual
in being both shaped and perpetual, which is why caching it over time (6) is the fitting tool.

## Prototypes and measurements

All on plumbus (RX 580, headless 2560x1600, `tests/goo-idle-bench.sh`, 5 s per case, private
`SCOTTLAND_HEADLESS_DIR`, shipped settings unless noted). "Refreshes" are full-shader passes
over the breathing strips. The final paired runs, with Mike's settings as well, are in
[goo.md](goo.md#go18-breathing-keyframes-2026-10-02); the prototype sequence was:

| Build | Strip area | Refreshes / 5 s | GPU time per tick | Window breathing, goo on - off |
|---|---:|---:|---:|---:|
| `343419c` baseline | 363,000 px | 130 | 0.456 ms | 1.8 points |
| + breath keyframes | 363,000 px | 10 | 0.183 ms | 1.3 points |
| + tightened strips | 148,000 px | 11 | 0.110 ms | 0.8 points |

Both were kept. The other candidates were rejected above without a prototype because each
changes the look or saves little once these two are in.

## Sources

Fetched and read 2026-10-02 unless marked.

- [Blinn]: https://www.microsoft.com/en-us/research/publication/a-generalization-of-algebraic-surface-drawing/ (abstract only; paper body not read)
- [Wong]: https://jamie-wong.com/2014/08/19/metaballs-and-marching-squares/
- [GPU Gems 3]: https://developer.nvidia.com/gpugems/gpugems3/part-i-geometry/chapter-7-point-based-visualization-metaballs-gpu
- [Quilez 2D]: https://iquilezles.org/articles/distfunctions2d/
- [Quilez smin]: https://iquilezles.org/articles/smin/
- [Rong & Tan]: https://www.comp.nus.edu.sg/~tants/jfa/i3d06.pdf
- [Green]: https://steamcdn-a.akamaihd.net/apps/valve/2007/SIGGRAPH2007_AlphaTestedMagnification.pdf
- [Bjørge]: https://community.arm.com/cfs-file/__key/communityserver-blogs-components-weblogfiles/00-00-00-20-66/siggraph2015_2D00_mmg_2D00_marius_2D00_notes.pdf
- [KWin blur]: https://invent.kde.org/plasma/kwin/-/raw/master/src/plugins/blur/blur.cpp (whether its passes are skipped when nothing behind changed was not confirmed)
- [emersion]: https://emersion.fr/blog/2019/intro-to-damage-tracking/
- [EGL_EXT_buffer_age]: https://registry.khronos.org/EGL/extensions/EXT/EGL_EXT_buffer_age.txt
- [EGL_KHR_partial_update]: https://registry.khronos.org/EGL/extensions/KHR/EGL_KHR_partial_update.txt
- [wlr_damage_ring]: https://raw.githubusercontent.com/hyprwm/wlroots-hyprland/main/include/wlr/types/wlr_damage_ring.h (mirror; freedesktop GitLab was not reachable)
- [Wayfire render-manager]: https://raw.githubusercontent.com/WayfireWM/wayfire/master/src/output/render-manager.cpp
- [CA performance]: https://developer.apple.com/library/archive/documentation/Cocoa/Conceptual/CoreAnimation_guide/ImprovingAnimationPerformance/ImprovingAnimationPerformance.html
- [shouldRasterize]: https://developer.apple.com/documentation/quartzcore/calayer/shouldrasterize
- [Chromium compositor]: https://web.dev/articles/stick-to-compositor-only-properties-and-manage-layer-count
- [RenderNode]: https://developer.android.com/reference/android/graphics/RenderNode
- [RepaintBoundary]: https://api.flutter.dev/flutter/widgets/RepaintBoundary-class.html
- [Lozar]: https://www.klemenlozar.com/frame-blending-with-motion-vectors/
- [Intel checkerboard]: https://www.intel.com/content/dam/develop/external/us/en/documents/checkerboard-rendering-for-real-time-upscaling-on-intel-integrated-graphics.pdf
- [ProMotion]: https://developer.apple.com/documentation/quartzcore/optimizing-iphone-and-ipad-apps-to-support-promotion-displays
- [Apple LED patent]: https://patents.google.com/patent/US6658577B2/en
- [GLES 3.1]: https://www.khronos.org/news/press/khronos-releases-opengl-es-3.1-specification
- [glDrawBuffers]: https://registry.khronos.org/OpenGL-Refpages/es3.0/html/glDrawBuffers.xhtml
- [Levien]: https://raphlinus.github.io/gpu/2021/04/28/slow-shader.html
- [Hyprland]: https://raw.githubusercontent.com/hyprwm/hyprland-wiki/main/content/configuring/core/config-options.md, https://raw.githubusercontent.com/hyprwm/Hyprland/main/src/render/Renderer.cpp, https://raw.githubusercontent.com/hyprwm/Hyprland/main/src/render/shaders/glsl/shadow.glsl
- [Liquid Glass]: https://developer.apple.com/documentation/technologyoverviews/adopting-liquid-glass, https://developer.apple.com/documentation/swiftui/glasseffectcontainer
- [AGSL]: https://developer.android.com/develop/ui/views/graphics/agsl/using-agsl
- [Flutter BackdropFilter]: https://api.flutter.dev/flutter/widgets/BackdropFilter-class.html
- [Flutter perf]: https://docs.flutter.dev/perf/best-practices
- [drm-usage-stats]: https://docs.kernel.org/gpu/drm-usage-stats.html
- [xe fdinfo]: https://docs.kernel.org/gpu/xe/xe-drm-usage-stats.html (does not say whether Xe cycles are frequency-weighted)
- [xe freq]: https://docs.kernel.org/gpu/xe/xe_gt_freq.html
- [intel_rps.c]: https://raw.githubusercontent.com/torvalds/linux/master/drivers/gpu/drm/i915/gt/intel_rps.c (i915's governor; Xe's GuC policy was not read)

Not found: a primary source stating outright that busy percentage at a low GPU frequency
overstates work; the kernel's counter definitions and the governor thresholds above are the
support for that reading. No source was read on bilateral or bicubic upsampling.
