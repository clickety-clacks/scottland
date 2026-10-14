# Window-mode tint layer pixel-test packet (r4)

This packet covers WK14/WK38's tint layer. Run it against an exact composed source tree on a
task-owned isolated host capable of the required compositor and pixel-capture behavior. A shared
runner may also run it, but runner admission is not a prerequisite for product evidence. Record the
source tree and host in the result. The owned-session marker and scratch cleanup protect execution;
they are not product acceptance checks. No hostname, machine-ID, output-count or
minimum-output-size assertion is part of the tint result.

## Executable focused check

`tests/window-mode-tint-pixel-test.sh` owns an isolated headless session and records PPM captures
plus `observations.json` under `build/window-mode-tint-pixel-results/`, or under
`SCOTTLAND_TEST_EVIDENCE_DIR` when supplied. Run it from the exact candidate tree with a fresh
`SCOTTLAND_HEADLESS_DIR` under that checkout's `build/` directory.
The invocation is `SCOTTLAND_HEADLESS_DIR="$PWD/build/headless-window-mode-tint-$$" tests/window-mode-tint-pixel-test.sh`.
It drives Alt and Super-drag through stipc, covers Goo on/off, the 0/7/30/100% mix on exclusive
window regions, overlapping windows and a widget card, checks that hints stay unchanged, verifies
hint dye or the fallback halo at both ends, checks for edge strokes, and confirms the tint layer
clears after Alt release. `tests/hint-outline-test.py` adds the eight scenario assertions for
covered/single extents, card/fullscreen mixing and all four fullscreen edges, Settings/list-picker
isolation, hint-opacity separation, motion-step edge tracking, and Alt/Esc clearing. The focused
executable and scenario assertions complement each other.

## Gate-to-behavior map

- `make plugin` is a compile prerequisite only; it proves that this source builds, not user behavior.
- After compilation, run the focused tint pixel behavior check first; then run the executable
  scenario assertions and required broader unit and hint/pairing suites. Report the eight scenario
  results separately.
- `tests/windowing-unit.sh` checks placement and decluttering constraints plus Window-mode hint
  cycling (selection, movement, key timing and clearing) through the pure unit seams.
- `I=tint tests/driver.sh` runs WK14/WK37/WK38 hint and tint rendering behavior plus item2's
  pairing opaque-once behavior, including the later return to configured opacity.
- `tests/window-mode-tint-pixel-test.sh` checks tint mixing at 0/7/30/100%, stacking over windows
  and cards, no tint outside drawn extents or added strokes, unchanged hint visuals, Goo dye or
  fallback halo, and layer clearing after Alt release.
- The separately reported eight scenarios prove fullscreen extent and edges, Settings and
  list-picker isolation, hint-opacity independence, live strength changes, frame tracking,
  Goo-on/off edge treatment, and Alt/Esc clearing. Each remains a product-behavior acceptance
  check.

The caller-owned-session marker, unique scratch, cleanup, and exact source/command record are
execution safeguards. They protect the session and make evidence attributable; they do not prove
product behavior and cannot substitute for a behavior result.

## Fixture and measurement

Use a clean Scottland session with two or more clients that paint stable, distinct solid colors,
plus the default widget card. Give each view enough inset area to sample away from rounded edges,
halo, hints and press flashes. Arrange a partially covered rear view, a front view, one uncovered
view, and a widget card that overlaps both. Use real Alt, arrow, pointer and drag input to enter
Window mode and move the views; test setup IPC may position the fixtures but does not count as the
input under test.

The executable selects the largest reported output for capture and asks `grim` to capture only
that output; additional outputs are allowed. Fixture dimensions and positions derive from the
selected output's geometry. Capture framing and fixture setup do not count as tint behavior.

Capture the same settled layout at `window_mode_tint=0` to obtain base pixel `P`, then at each
strength under test. Read tint color `cᵢ` from an interior hint-letter pixel in the same capture;
do not use the compositor's outline/tint telemetry as proof. For rear-to-front colors `c₁…cₙ`,
calculate `T₀=P`, `Tₖ=s·cₖ+(1−s)·Tₖ₋₁` for each extent covering the sample. Every interior RGB
channel must be within 4/255 of the calculated result. A pixel outside all extents and hints must
match `P` within 4/255.

Record source pins and the composed tree hash, renderer, selected-output geometry/scale, commands,
screenshots, scenario results, and cleanup. These environment details are diagnostic context, not
acceptance predicates. Use pixel captures as the rendering evidence; IPC and layout state can locate
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
7. **Motion tracking.** The hint-outline test captures tint after each sampled peek-ease and
   keyboard movement step. It probes one device pixel inside and outside the current drawn edge;
   the colored pixel must follow the frame while the outside pixel stays untinted.
8. **Clear.** Capture the same settled layout before Window mode and after Alt release, then repeat
   with Esc. After avoidance offsets settle home, all pixels outside persistent Goo/halo effects
   match their corresponding pre-entry capture within 4/255.

## Required report

Report each scenario and its channel/edge measurements separately, along with iteration counts and
any failed samples. The product claims proved are additive tint over each drawn window/card extent,
rear-first overlap mixing, unchanged overlays and hint visuals, retained Goo dye/fallback halo,
absence of new strokes, tracking the drawn frame, and clearing after Window-mode exit. Settings and
list-picker isolation, fullscreen coverage, slider behavior, opacity separation, motion tracking and
the Esc clear path are proved by the separately reported eight scenarios above; setup geometry is
not a pass criterion. Renderer and selected-output geometry/scale may be recorded as diagnostic
context, but host identity is not an acceptance check. Preserve the screenshots and test log under
the test checkout's `build/` directory, stop the owned session on every exit path, and remove only
test-created paths. Record behavior against the exact source tree; do not hold product evidence on
a runner receipt when a capable isolated host executes the checks.
