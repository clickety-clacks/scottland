# The goo

The halo around windows (core/INVARIANTS.md, A3-A12) becomes **one goo for the whole screen**
instead of a halo per window patched together where they meet. Mike's design (2026-10-01): it should
feel truly organic, goo reaching for goo the way liquids do, waves running through it, color
spreading in it like food coloring in water. The interactive prototype used to explore the feel:
https://claude.ai/artifact/VHTqdn4TqvSN8kZ8CoRV64 (Scottland Goo Lab; its source is in
[prototypes/goo-lab.html](prototypes/goo-lab.html)).

This doc is the design; the rows say what's built. When it's built, A3, A4, A6, A9, A10 and A11
are restated here and their rows in core/INVARIANTS.md point at it.

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
settings do. Anyone can tune it; the shipped values are the ones Mike settles on.

## Invariants

| ID | Invariant | Status |
|---|---|---|
| GO1 | One goo per screen: one field from all windows, drawn as one layer beneath all windows, outlining the union of the window shapes and never drawn over a window. | not built (prototyped) |
| GO2 | The goo clings: each window's goo stays within a reach of its edge; between windows close enough, it bridges, drawing from both borders, and a stretched bridge thins and snaps. | not built (prototyped) |
| GO3 | Inside corners (where windows meet or overlap) fill smoothly because goo pools there; no corner-specific code. | not built (prototyped) |
| GO4 | The goo isn't uniform: its amount along each edge wanders slowly, configurable (mess, lump size, drift). | not built (prototyped) |
| GO5 | Waves start at grabs, drops, swells and attention pulses, travel only through connected goo along the whole merged outline, and fade. | not built (prototyped) |
| GO6 | Color is dye in the goo: each window releases its state's color into its own goo; dye spreads and swirls only within goo, bleeding across bridges between connected windows. | not built (prototyped) |
| GO7 | Halo state markers are dye (plus goo where they need presence), never separately drawn shapes: focus, attention, the hovered resize corner (no hard edges where it meets the rest of the halo), the close dot's glow. | not built |
| GO8 | Resize corners, the close dot and grab areas are hit-tested against the same field; a corner hidden inside another window has no handle. | not built |
| GO9 | Every goo constant, and the falloff curve, is a setting with a live control in the settings app. | not built |
| GO10 | The goo costs nothing while the desktop is still: its simulation sleeps when settled. | not built |
