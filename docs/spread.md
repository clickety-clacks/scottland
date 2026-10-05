# Spread: solo, and making room during a drag

This document records the two layouts that make room for something the user placed, and the
presentation contract they share: **solo** (one window takes the center; the other center windows
go to the periphery, which spreads; built from the signed-off design in
`~/.local/state/scottland-jobs/spread/final.md`) and the **WG26 rail profile**. Both follow P14:
the thing the user placed stays exactly where it was put; everything else flows around it.

Window avoidance for Window-mode hints is a separate policy ([WK13](windowing-keys.md#invariants),
[WK31](windowing-keys.md#invariants)). It follows P1/P2/P11: periphery windows stay on their
original side, center-zone windows stay in Scottland's center zone, and automatic moves do not
cross between the top and bottom regions (a window already in the vertical center band stays in
that band). A retained way identifies the hint it exposes and the moved window's axis and
direction. While that way works, each solve chooses the smallest offset from the moved window's
true frame along that ray, returning to zero when the obstruction clears. If the way stops working,
the solver chooses a new candidate by travel from the displayed position. The whole-screen spread
search does not choose window-avoidance moves. Far-apart-first sampling is limited to placing a
hint inside its window.

## Spread and solo

**Triggers (P4).** Only explicit requests solo: holding the focused window's hint in Window mode
or a three-finger hold on the focused window (WK35, committed outright, no undo, P5), and the drag
audition below (committed by the drop). Present, card clicks, zone cycling and ordinary drops never
spread.

**The solo window.** Already in the center zone: it stays where it is (P2, P14). Otherwise it goes
to its remembered center spot (WP2), else the middle of the screen, padded on screen (WP7), at full
scale, in front.

**The solve** (`core/plugin/src/spread.{hpp,cpp}`, pure, no Wayfire). Windows whose center is in the
center zone are *arrivals*; periphery windows are *residents*; widgets and rail windows are fixed.
Residents never change zone (P13); arrivals leave the center only because the user asked for
the solo, which is what a solo is. In order: a legal seed for every arrival; residents the solo target really covers move first (own
side, same scale at their own x first, else outward, never larger); then up to four arrangements,
residents frozen first, then privileged (P6), each largest-first then most-recent-first. Each arrival
takes the first rung that gives a spot, at 1 pt contact clearance: clear and in band (scale at least
85% of the largest it could have beside the fixed things alone); push residents aside (only when it
would otherwise land below that band, only residents that then land clear on their own side, depth
one); clear at any scale; least overlap. A return pass puts back every pushed resident whose spot
came free. The best checkpoint under the lexicographic comparison of final.md wins. A bounded
spacing pass then tries a halo-sized gap (P7) between the windows it moved, else one smaller common
gap, else none; residents only slide vertically for it, nobody moves more than one halo for it, and
nothing untouched moves. Arrivals hang into the center zone at most 16 pt when they fit (ruling
10-04). If nothing clear exists the least-overlap legal layout is used, solo in front; nothing ever
goes to a rail or becomes a widget.

**Bounded (P8).** Every unit operation charges one work counter (cap 1,000,000 units, about 9 to
11 ms on an idle plumbus or nacelle core). Until the main-loop worker lands (branch mainloop-impl),
the solve runs on the event loop in 2 ms slices with the loop free for at least 1 ms between them
(`spread-job.hpp`: the solve is suspended inside its unit operations on its own small stack, so the
worker can call the same `step()` later). A keyboard solo commits the best validated checkpoint after
12 ms of solving or 30 ms of waiting, at the first event-loop turn after that limit. The first
arrangement offers a checkpoint after each arrival it places (the rest at their seed spots), so a
cut delivers the progress made. Measured in the real build (nacelle headless,
`tests/spread-load-test.py`, 2026-10-04): 12, 24 and 40 windows took 1, 5 and 5 slices, longest
1.53, 2.00 and 2.00 ms; the 40-window solve was cut at 10 ms of solving with 10 of 13 arrivals
placed, delivered after 78 ms because one event-loop turn spent 64 ms drawing 40 windows in
software between two slices (the solve itself never held the loop longer than a slice). Work is
charged before it runs, item by item, except a few batches bounded by the caps that run whole
between two charges: sorts of at most 256 intervals and of the least-overlap sweep's events (four
per obstacle, at most 4 × 256), charged by their length in advance; the arrival and resident order
sorts (at most 128 windows, once per solve); and setup and copy loops over at most 256 obstacles
(building obstacle lists, copying layouts). This is the measured main-thread alternative of final.md
section 4, not the main-loop design's literal per-item cancellation everywhere: the longest CPU time
between two charges was 13.5 µs over 40 scenes of 50 windows on nacelle (Astra measured 35.7 µs at 50 windows
and 19.8 µs at the 128-window cap). Each checkpoint's result is built inside the budget when it
becomes the best, so a stopped or cancelled solve delivers it without further work; the input caps
are checked before anything is collected. Outside the slices, delivering a solo (the result copy,
the record, freeing the job and the commit) took 1.2, 8.1 and 8.9 ms of compositor CPU for 12, 24
and 40 windows (`deliver_cpu_ms`; wall-clock figures on a shared host add preemption; Fable measured
13.6 ms at 40), nearly all of it Wayfire moving each window (about 0.4 ms per moved window); the
model is published once per commit, not per window. **The commit is outside the 2 ms slice bound:**
it is one block on the event loop, under a 60 Hz frame at 40 windows, but would be about 50 ms at
the 128-window cap. None of these figures is an end-to-end latency bound; they still need measuring
on a real GPU session. The job's `step(allowance)` is not yet the worker's `step(cancel_t)`: the
worker will wrap it with a cancellation adapter, and a job stays on the thread that started it
(destroying an unfinished one resumes it there to unwind). Inside that
session the kernel did about 31 units/µs against 112 in the unit suite on the same host, so its
12 ms reach less far there; this is still to be measured on a real GPU session. Unit operations
measure under 40 µs of CPU; longer wall-clock slices seen on plumbus were preemption under load.

**The drag audition.** A drag of a window (not a widget, not a Shift drag, L31) whose center is in
the center zone, with the pointer resting within 8 pt for `solo_audition_delay` (3000 ms; 0 turns it
off), is offered the solo. At 1 s the anchor is frozen and the solve runs against a reservation: the
window's full-scale footprint grown by the hotspot on every side, so any accepted drop is honest. At
the delay the result is shown as a presentation layer only (a translation and scale per window on the
shared drag layer); no true geometry, zone, memory, pin or widget state changes. Moving the pointer
more than `solo_audition_hotspot` (50 pt) from the anchor, leaving the center zone, Esc, or a client
mapping, closing or resizing refuses it: every window eases back to exactly where it was (its true
geometry never changed) and the pause is timed again. A drop inside the hotspot accepts it: each
window glides from where it is drawn to its spot, and the dropped window stays exactly where it was
dropped (no settle, no coast, P14). A Shift drop, a changed desktop or zone setting (rechecked on
every motion and at the drop) and a resize of the dragged window refuse it too. Unloading the
plugin refuses an offer before it releases the drag.

### Invariants

| ID | Invariant | Status |
|---|---|---|
| SP1 | Only the focused window's hint hold, its three-finger hold and an accepted audition solo; nothing else spreads (P4). | verified (plumbus and nacelle headless, real stipc input, 2026-10-04) |
| SP2 | Arrivals land in the periphery (center outside the center zone and the rails, footprint inside the padded workarea), preferring the nearer side, hanging at most 16 pt into the center zone when they fit (ruling 10-04). | verified (unit fuzz, 600 scenes; headless) |
| SP3 | A resident moves only if the solo target covers it or an arrival would otherwise land below its band (P6); it stays on its side (P1), never grows, never moves inward, ends clear when pushed, and returns when its spot is free again (P2). | verified (unit fuzz and fixtures; headless) |
| SP4 | The spacing pass moves only windows spread moved, each at most one halo, residents vertically only, arrivals within band, and never adds overlap (P7). | verified (unit fuzz) |
| SP5 | The solve's work is charged (item by item, except the cap-bounded batches listed under "Bounded") and runs in measured 2 ms slices; the delivered result is always a complete validated checkpoint or no change; a completed solve and a fixed-work cut are deterministic, and sliced equals synchronous. The commit after a solve is one block outside the slice bound (about 0.4 ms of compositor CPU per moved window). | verified (unit suite; real-build slices and delivery CPU measured headless on plumbus and nacelle); real-GPU latency not yet measured |
| SP6 | A keyboard or three-finger solo commits outright, no undo (P5); the solo window ends in the center at full scale, in front. | verified (plumbus and nacelle headless) |
| SP7 | The audition offers after the pause, changes no true state before the drop, refuses on leaving the hotspot or the center zone, on Esc, on Shift (with or without motion), on a zone-setting change, on a resize of the dragged window or a client change (never rolling that change back), returns every window exactly, draws offered windows exactly at their spots over running glides (and once avoidance offsets have settled), refuses on a change of the hotspot setting, and on a drop inside the hotspot commits with the dropped window exactly where it was dropped (P5, P14). | verified (nacelle headless, real stipc drags, 59 checks, 2026-10-04) |
| SP8 | A reload with a solve in flight or an offer showing survives, applies nothing half-done and leaves no window displaced. | verified (plumbus and nacelle headless reload rehearsal) |

Not yet seen on a physical screen or with a physical touchpad (the shared plumbus session was not
reloaded). The two settings have no row in Scottland Settings yet; `scottland-ctl` sets them.

### Implementation choices (for review against final.md)

- Outward distances closer than one halo count as equal, so travel decides between near-equal spots
  (P11). Strict lexicographic order sent an arrival across the screen for a 0.6 pt gain (seen on
  plumbus). For a resident, a spot at its own x still beats any outward one, however small
  (decision 4; Fable's review, witnesses W1/W1b).
- A window the solve could not move out from under the solo target is reported as overlap, moved or
  not (Fable's W2).
- The spacing pass also rejects a trial that brings any pair closer than it was or than the
  clearance sought (Astra's review: two nudged windows narrowed a non-close pair from 11.7 to 3.8 pt).
- Keyboard delivery waits up to 30 ms (final.md targets 16 ms): on the event loop, 12 ms of solving
  spread over 2 ms slices cannot be delivered sooner.
- A pinned resident that moves vertically at its own x keeps its pin ("same scale", decision 4);
  anywhere else it takes the natural scale there and loses the pin.
- The work cap is 1,000,000 of this kernel's units (about 12 ms on plumbus); final.md's 150,000 was a
  starting value for a prototype that counted coarser units.
- A glide running on a window when the offer starts is suspended at its current sample and resumed
  on refusal (a cycle glide keeps its clock; another restarts from the sample to its own
  destination). The offer layer is computed every frame against what lies under it, so at full
  progress each window is drawn exactly at its offered spot and scale even while a scale animation
  runs underneath, and easing back ends on the live state. A window still coasting, or gliding
  away, delays the offer. Hint-avoidance offsets of offered windows ease to zero while the offer
  shows (they are held still, as for a pair) and are recomputed after: the offered spot is exact
  once they have settled, not during that easing, and the earlier avoidance state is recomputed
  rather than restored (Astra's review asked for suspension and accepted this equivalent; tested
  with avoidance on).
- The hotspot radius is fixed when the offer arms (its reservation is built with it); changing the
  setting during an offer refuses it, and a drop is accepted only if the dropped window lies inside
  the reservation.
- After its exact restorations, the return pass also tries a moved resident's own column at its own
  scale before the four shortening samples (Fable's round-2 note: a same-x spot can come free after
  the resident moved).
- A solo whose solve moves nothing (unchanged, unavailable) still takes the solo window to the center.
- The spacing pass may also run when the seed checkpoint wins.

### Tests

`tests/spread-unit.sh` (fixtures, fuzz, determinism, slices, starved budgets, cancellation, timing),
`tests/spread-test.sh` (46 real-input checks plus the load measurement),
`tests/spread-reload-test.sh` (reload rehearsal), all headless on plumbus or nacelle.

## Rail behavior (WG26)

While a window is shown as a widget during a drag, or a widget is dragged along a rail, only
widgets on that output and rail take part. The dragged item stays under the pointer and keeps its
existing WG1/WG13 morph. Other widgets shift along the rail by the least distance needed to clear
the dragged item's current visible interval by 1 px. A shift reaches the next widget only when the
previous shift makes that widget too close. Existing gaps and overlaps are otherwise preserved;
the rail is not tiled or compacted. A widget never crosses to the other rail.

The hole left by a widget being dragged remains open after a successful drop. A full rail may have
no space to clear the landing interval. In that case each causal chain advances only as far as its
members can fit, stopped by the tightest rail-end limit, and the drop may overlap. The solver reports
that case as `overlap`; it never publishes an off-rail or reordered result.

The solver uses the true widget positions captured when the drag enters the rail. For each widget,
its initial yield direction is chosen by comparing its center with the dragged interval's center.
That direction stays latched until the dragged center passes the widget's center by
`max(6 px, 10% of the widget height)`. This prevents small pointer wobble from reversing a chain.

For one direction, the solver visits widgets in their captured rail order. It requests only the
CONTACT displacement needed by the first widget, then propagates it through neighbors. For each
pair it preserves any existing overlap and keeps any gap larger than 1 px. The first displacement
is clamped by the available rail-end space of **every** member of the chain; each later member moves
only after the slack before it has been used. The opposite direction uses the mirrored calculation.
The final intervals are checked against both rail ends and their original order. Invalid input or a
failed check produces zero shifts with `overlap` status.

Each solve is synchronous in the drag path. The maximum actor count is 256, checked before any
sorting or per-actor state allocation. Order and scratch arrays are prepared once for the captured
rail; pointer updates reuse them and publish a complete result. An over-cap update returns the
identity/no-shifts result without iterating actors or changing direction latches. The current
admission check uses the session's total widget count as a constant-time upper bound, so a session
with more than 256 widgets skips rail making-room even when the active rail itself has fewer actors.
That conservative check keeps the whole input path bounded.

The behavior follows P1 (stay on the same side), P2 (move only what is in the way), P5 (show the
proposal, commit it on drop, restore it on cancel), and P8 (bound synchronous work so the pointer
keeps responding).

## Shared drag presentation

`drag_presentation_t` is independent of rails and serves both auditions. It records
actor origins, supplies additive visual offsets (and, for the solo audition, a scale factor on the
window's own scale; the rail leaves it at 1) while a gesture is active, and derives committed
positions from those origins. Real geometry is unchanged during the audition. On drop, the caller
applies the target positions and retains the visual offsets until those geometry transactions
apply. A widget's hidden app window and saved drop anchor follow the same committed vertical
displacement. On cancel, the caller clears the offsets and discards the presentation; because the
real positions never changed, every actor returns exactly to its origin.

The widget frame's drag-layout translation composes with its scale, morph, live drag, goo, hit
testing and glide translations. It does not take ownership of those existing effects. Only the
actors with nonzero solver offsets receive a real move on drop.

## Invariants

| ID | Invariant | Status |
|---|---|---|
| SM1 | A rail solve considers widgets from the dragged item's output and rail, excluding the dragged item. It moves only a causal chain, preserves the rail order and never crosses sides. | verified (plumbus headless, 2026-10-03) |
| SM2 | Contact clearance is 1 px where room exists. A shortage is clamped by every chain member's rail-end headroom; a drop may overlap when the rail is full. | verified (plumbus headless, 2026-10-03) |
| SM3 | Shifts are visual for the duration of the drag, become real moves on drop, and return exactly on Esc or when the item leaves the rail. | verified (plumbus headless, 2026-10-03) |
| SM4 | The synchronous solve checks its 256-actor bound before allocation, reuses its captured order and scratch state, and returns only complete validated results. | verified (plumbus headless, 2026-10-03; bounded unit suite) |
