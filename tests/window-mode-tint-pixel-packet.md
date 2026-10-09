# Window-mode tint layer pixel-test packet (r4)

This packet covers WK14/WK38's tint layer. Run it only from the shared runner's exact source and
host grant. Keep this tint branch separate; the runner composes it with the landed item2 and
transparency candidates and records the resulting full-tree hash. This packet has not been run.

## Executable focused check

`tests/window-mode-tint-pixel-test.sh` owns an isolated headless session and records PPM captures
plus `observations.json` under `build/window-mode-tint-pixel-results/`. Run it from the exact
shared-runner tree with a fresh `SCOTTLAND_HEADLESS_DIR` under that checkout's `build/` directory.
The invocation is `SCOTTLAND_HEADLESS_DIR="$PWD/build/headless-window-mode-tint-$$" tests/window-mode-tint-pixel-test.sh`.
It drives Alt and Super-drag through stipc, covers Goo on/off, the 0/7/30/100% mix on exclusive
window regions, overlapping windows and a widget card, checks that hints stay unchanged, verifies
hint dye or the fallback halo at both ends, checks for edge strokes, and confirms the tint layer
clears after Alt release. The eight manual scenarios below remain required for Settings/list
picker isolation, fullscreen edges, separation from hint-background opacity, motion tracking, and
the complete clear/escape matrix; the focused executable does not replace them.

## Fixture and measurement

Use a clean Scottland session with two or more clients that paint stable, distinct solid colors,
plus the default widget card. Give each view enough inset area to sample away from rounded edges,
halo, hints and press flashes. Arrange a partially covered rear view, a front view, one uncovered
view, and a widget card that overlaps both. Use real Alt, arrow, pointer and drag input to enter
Window mode and move the views; test setup IPC may position the fixtures but does not count as the
input under test.

Capture the same settled layout at `window_mode_tint=0` to obtain base pixel `P`, then at each
strength under test. Read tint color `cᵢ` from an interior hint-letter pixel in the same capture;
do not use the compositor's outline/tint telemetry as proof. For rear-to-front colors `c₁…cₙ`,
calculate `T₀=P`, `Tₖ=s·cₖ+(1−s)·Tₖ₋₁` for each extent covering the sample. Every interior RGB
channel must be within 4/255 of the calculated result. A pixel outside all extents and hints must
match `P` within 4/255.

Record source pins and the composed tree hash, host/renderer, commands, screenshots, scenario
results, and cleanup. Use pixel captures as the rendering evidence; IPC and layout state can locate
views and diagnose a failed frame but cannot establish a pass.

## Scenarios

1. **Covered windows and single extents.** Sample inside only the rear view, inside only the front
   view, inside their overlap over the front client's content, and outside every extent. The
   overlap must show both tints in drawn stacking order; at 100%, it approaches the front color.
2. **Widget card.** Sample a card-only pixel and an overlap pixel where the card covers a window.
   Confirm the card's tint participates in the same rear-first mix and the window tint shows
   through above the window content.
3. **Fullscreen.** Show a fullscreen view with a widget card above it. The fullscreen-only region
   must tint; the overlap must mix both colors. Inspect all four edges for the absence of a
   fullscreen rim.
4. **Scottland overlays.** Open Settings and the list picker over a tinted view. Their panel pixels
   stay equal to their own untinted control capture, including with the slider at 100%; adjacent
   exposed window pixels retain their expected tint.
5. **No Window-mode strokes; dye retained.** With Goo on and off, inspect mostly covered, scaled,
   fullscreen and card edges. More than one device pixel inside an edge, samples follow the tint
   formula. Within one device pixel of each rounded edge, samples interpolate between the formula
   with and without that extent. Confirm the hint-color goo dye or fallback halo remains visible
   as it was before the layer, with no new border, outline or fullscreen rim.
6. **Strength and separation.** Check 0%, 7%, 30% and 100%. Move the strength control live while
   hints are visible and capture its next rendered frame. Repeat with hint-background opacity at
   0% and 100%: tint samples must not change, and hint backing samples must not change when tint
   strength changes. The row must reach 100% and remain usable at that value.
7. **Motion tracking.** Capture each frame during a peek ease, keyboard coast and pointer drag.
   Measure the tint edge against the drawn frame/card; maximum separation is one device pixel.
8. **Clear.** Capture the same settled layout before Window mode and after Alt release, then repeat
   with Esc. After avoidance offsets settle home, all pixels outside persistent Goo/halo effects
   match their corresponding pre-entry capture within 4/255.

## Required report

Report each scenario and its channel/edge measurements separately, along with iteration counts and
any failed samples. Preserve the screenshots and test log under the test checkout's `build/`
directory, stop the owned session on every exit path, and remove only test-created paths. The
separate source-tree changes in this candidate remain untested until that runner report exists.
