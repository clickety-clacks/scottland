# Spread: solo, and making room during a drag

This document records the two layouts that make room for something the user placed, and the
presentation contract they share: **solo** (one window takes the center; the other center windows
go to the periphery, which spreads; built from the signed-off design, "final.md", kept with the
project's private design notes) and the **WG26 rail profile**. Both follow P14:
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
or a three-finger hold on the focused window (WK35, committed outright, no undo, P5). Present, card
clicks, zone cycling and ordinary drops never spread, and neither does pausing during a drag: the
drag audition that offered a solo there is removed, with its pause setting (ruling 10-05: the
three-finger hold does the job). Its hotspot setting, `solo_audition_hotspot` (50 pt), stays: moving
the pointer beyond it is what counts as starting to drag, which cancels a pointer hold's audition
(ruling 10-05; WK39).

**The solo window.** Already in the center zone: it stays where it is (P2, P14). Otherwise it goes
to its remembered center spot (WP2), else the middle of the screen, padded on screen (WP7), at full
scale, in front. Afterwards it holds still for window avoidance like a pair member (WK36): the
peek-strip engine treats it as anchored, so the windows behind it peek out around it and it is never
nudged off the spot the user asked for (P14), until it moves, is resized or becomes a widget.

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
11 ms on an idle either test machine core). Until the main-loop worker lands (branch mainloop-impl),
the solve runs on the event loop in 2 ms slices with the loop free for at least 1 ms between them
(`spread-job.hpp`: the solve is suspended inside its unit operations on its own small stack, so the
worker can call the same `step()` later). A keyboard solo commits the best validated checkpoint after
12 ms of solving or 30 ms of waiting, at the first event-loop turn after that limit. The first
arrangement offers a checkpoint after each arrival it places (the rest at their seed spots), so a
cut delivers the progress made. Measured in the real build (the ARM test machine, headless,
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
between two charges was 13.5 µs over 40 scenes of 50 windows on the ARM test machine (Astra measured 35.7 µs at 50 windows
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
measure under 40 µs of CPU; longer wall-clock slices seen on the x86 test machine were preemption under load.

### Invariants

| ID | Invariant | Status |
|---|---|---|
| SP1 | Only the focused window's hint hold and its three-finger hold; nothing else spreads (P4). | verified (headless on both test machines, real stipc input, 2026-10-04; re-run on the ARM test machine after the audition's removal, 2026-10-05) |
| SP2 | Arrivals land in the periphery (center outside the center zone and the rails, footprint inside the padded workarea), preferring the nearer side, hanging at most 16 pt into the center zone when they fit (ruling 10-04). | verified (unit fuzz, 600 scenes; headless) |
| SP3 | A resident moves only if the solo target covers it or an arrival would otherwise land below its band (P6); it stays on its side (P1), never grows, never moves inward, ends clear when pushed, and returns when its spot is free again (P2). | verified (unit fuzz and fixtures; headless) |
| SP4 | The spacing pass moves only windows spread moved, each at most one halo, residents vertically only, arrivals within band, and never adds overlap (P7). | verified (unit fuzz) |
| SP5 | The solve's work is charged (item by item, except the cap-bounded batches listed under "Bounded") and runs in measured 2 ms slices; the delivered result is always a complete validated checkpoint or no change; a completed solve and a fixed-work cut are deterministic, and sliced equals synchronous. The commit after a solve is one block outside the slice bound (about 0.4 ms of compositor CPU per moved window). | verified (unit suite; real-build slices and delivery CPU measured headless on the x86 test machine and the ARM test machine); real-GPU latency not yet measured |
| SP6 | A keyboard or three-finger solo commits outright, no undo (P5); the solo window ends in the center at full scale, in front. | verified (headless on both test machines) |
| SP7 | Pausing during a drag does nothing: held still in the center zone for any time, no other window is drawn anywhere but where it is, and dropping there moves no other window. A `solo_audition_delay` left in an older config is ignored (ruling 10-05: the drag audition is removed). | verified (the ARM test machine, headless, real stipc drags judged from pixels: 9 checks in 2 scenarios, shipped config and a stale 300 ms `solo_audition_delay`; the build before the removal fails 7 of them, 2026-10-05) |
| SP8 | A reload with a solve in flight survives, applies nothing half-done and leaves no window displaced; a reload from the previous main build keeps every window, widget and peek. | verified (headless reload rehearsals on both test machines, 2026-10-04; re-run on the ARM test machine, from main into the build without the audition, 2026-10-05) |

Not yet seen on a physical screen or with a physical touchpad (the shared test session was not
reloaded).

### Implementation choices (for review against final.md)

- Outward distances closer than one halo count as equal, so travel decides between near-equal spots
  (P11). Strict lexicographic order sent an arrival across the screen for a 0.6 pt gain (seen on
  the x86 test machine). For a resident, a spot at its own x still beats any outward one, however small
  (decision 4; Fable's review, witnesses W1/W1b).
- A window the solve could not move out from under the solo target is reported as overlap, moved or
  not (Fable's W2).
- The spacing pass also rejects a trial that brings any pair closer than it was or than the
  clearance sought (Astra's review: two nudged windows narrowed a non-close pair from 11.7 to 3.8 pt).
- Keyboard delivery waits up to 30 ms (final.md targets 16 ms): on the event loop, 12 ms of solving
  spread over 2 ms slices cannot be delivered sooner.
- A pinned resident that moves vertically at its own x keeps its pin ("same scale", decision 4);
  anywhere else it takes the natural scale there and loses the pin.
- The work cap is 1,000,000 of this kernel's units (about 12 ms on the x86 test machine); final.md's 150,000 was a
  starting value for a prototype that counted coarser units.
- After its exact restorations, the return pass also tries a moved resident's own column at its own
  scale before the four shortening samples (Fable's round-2 note: a same-x spot can come free after
  the resident moved).
- A solo whose solve moves nothing (unchanged, unavailable) still takes the solo window to the center.
- The spacing pass may also run when the seed checkpoint wins.

### Tests

`tests/spread-unit.sh` (fixtures, fuzz, determinism, slices, starved budgets, cancellation, timing),
`tests/spread-test.sh` (26 real-input checks plus the load measurement),
`tests/spread-reload-test.sh` (reload rehearsal with a solve in flight),
`tests/drag-pause-test.sh` (SP7: a pause during a drag does nothing, judged from pixels, also with
a stale `solo_audition_delay` in the config), and
`tests/reload-rehearsal-test.sh OLD_CHECKOUT` (a session started on an older build, with widgets
and peeking windows, reloaded in place into this one: the new settings metadata is registered first,
as `scottland-reload` does; without that the new plugin cannot load its options and the session is
left without Scottland), all headless on either test machine.

## Rail behavior (WG26)

While a window is shown as a widget during a drag, or a widget is dragged along a rail, only
widgets on that output and rail take part. The dragged item stays under the pointer and keeps its
existing WG1/WG13 morph. The rail spreads to make room for the item's landing interval (Mike,
2026-10-04): any widget may move when that's what it takes, widgets keep their order, total
movement is the least it can be, and widgets overlap the item only when the rail is truly full.
Touching as little as possible is the preference that falls out of least movement, not a rule.
P14 (the user always wins): the dropped item ends up exactly where the user put it; only the
other widgets move around it, and the order around it is decided by where it was placed. A widget
never crosses to the other rail, and the hole left by a widget being dragged stays open.

Pointer motion updates only the dragged item's latest landing interval and the pause timer; it does
not solve or move its neighbors. The pointer must stay within a 4 px radius for
`scottland/widget_make_room_dwell` (default 350 ms, live range 100–1500 ms). Each completed pause
solves against the latest interval. Movement after a solve keeps that arrangement until another
pause completes. Release commits the arrangement the user saw when it was solved for the landing
spot, within the 4 px wobble. If the drag ends before its first pause, or the landing spot moved more
than 4 px since the last solve (pause, move on, drop without pausing again), release runs one more
solve from the captured positions, so the drop clears its real landing spot and any neighbor that no
longer needs to move goes home (P2). This hold buffer follows P11 (calm movement) and P2 (move only
what is needed), while cancel and drag-back-out retain P5's exact restoration.

The solver uses the true widget positions captured when the drag enters the rail, in rail order.
Neighbors must end at least 1 px apart, or no further into each other than they already are (an
earlier full rail), and the item needs 1 px of clearance on each side (touching within that gap
is not overlap). The item never moves, so it splits the widgets into those above it and those
below it. For one split, each side is independent: its widgets go between a rail end and the
item, in order, minimizing the sum of squared displacements from home. Subtracting each widget's
packed offset turns "in order, apart" into "non-decreasing", so a side is an isotonic regression
with bounds, solved exactly by pool-adjacent-violators in O(n). An item hanging off a rail end is
laid out where placement will put it, on the rail, which is where it lands. With n capped at 256 a
solve is O(n²) at most; review round 3 measured about 1 ms average, 2.9 ms worst at the cap.

Which split: where the item was placed decides. A widget whose span the item's center is past
stays on that side (the center below a widget keeps it above, and the reverse). A widget the
item's center is on (the user dropped onto it) may go either way, since both orders match the
placement; among those splits, the ones that fit compete on total movement, ties going to the
split by centers. So a card dropped onto the card at a rail end goes first, and that card makes
room on the other side with the rest.

If none of the allowed splits fits, the widgets overlap the item rather than it moving or the
placed order being broken, by the least amount: their packed run reaches into the item from the
side that lacks room. That happens on a rail that is truly full, and in one more case **for Mike
to look at**: a drop that clips a widget pinned at a rail end, with the drop's center past that
widget, keeps the widget on its aimed side, so it overlaps the drop by at most half the dropped
card (in the review fuzz: 1.5% of pauses, worst 44 px) even though crossing it would make room.
The status is `overlap`. Results never leave the rail or change the widgets' order; a failed
check produces zero shifts with `overlap` status.

During the drag the audition shows the neighbors placed around the item exactly where it is, so
the gap opens under the pointer, where the card will land. On release the drop's layout is
committed as shown. If the card lands on the interval that layout was solved for (within the 4 px
wobble), nothing is solved again, so the cards never rearrange a second time. Only a different
landing (the real card is another size, or placement moved it) is solved again around the card
where it actually is.

P11 hysteresis ("a window keeps its current way out of the way until it stops working"): the
previous split stands while the placement allows it and it fits as well. A widget changes side by
choice only once the item's center has passed that widget's center by `max(6 px, 10% of its
height)` toward its new side, and only if the new split fits with that margin to spare. A widget
the center has just passed keeps its other side until the center is that margin past its edge, and
on a full rail the previous split stands while it overlaps no more than that margin extra. On a
nearly full rail, where the other order would fit only within that margin, the current order stands
while it overlaps by no more than the margin, rather than sending a widget across the item and back
for a sub-pixel fit (review fuzz: 21 of 600,000 pauses, at most 9.5 px). A widget therefore never
switches side and back across small re-pauses (0 in 600,000 pauses of each fuzz). Within one split
every widget moves at most 1 px per pixel of drag.

Each solve is synchronous but, during a drag, runs only on a completed pause or once on drop, never
for every pointer event. A non-drag window-to-widget arrival runs one solve after its widget has
mapped and reached its landing geometry. The maximum actor count is 256, checked before any sorting
or per-actor state allocation. Order and scratch arrays are prepared once for the captured rail;
each pause reuses them and publishes a complete result. An over-cap solve returns the identity/no-
shifts result without iterating actors or changing direction latches. The current admission check
uses the session's total widget count as a constant-time upper bound, so a session with more than 256
widgets skips rail making-room even when the active rail itself has fewer actors. That conservative
check keeps the whole input path bounded.

Every change of rail-layout target eases in and out over 190–360 ms (longer with distance), including
large shifts, release-time solves, geometry-commit corrections and return to zero. No move exceeds
the 1000 px/s automatic-motion speed limit it shares with window avoidance; a move too long for that
within 360 ms takes longer. A retarget in mid-move starts from the speed the widget already has,
so it does not kick, and never overshoots its new target; speed against the new direction is dropped
and the widget turns there. The frame translation keeps the visible
position continuous while target geometry commits. Reduced motion from the active palette applies
to these shifts too and snaps directly to the target. Direct drag drops use the active drag solve;
inertial coast arrivals and Window-mode key widgetization solve against the mapped widget's actual
landing rectangle through the same rail solver. Window avoidance plans against where the rail's
widgets are going, not where each frame of the ease draws them, so a rail layout change costs one
avoidance re-solve rather than one per frame.

The behavior follows P1 (stay on the same side), P2 (move only what is in the way), P5 (show the
proposal, commit it on drop, restore it on cancel), and P8 (bound synchronous work so the pointer
keeps responding).

## Shared drag presentation

`drag_presentation_t` is independent of rails. It records actor origins, supplies additive visual
offsets while a gesture is active, and derives committed positions from those origins. Real geometry is unchanged during the gesture. On drop, the caller
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
| SM1 | A rail solve considers widgets from the dragged item's output and rail, excluding the dragged item (whose spot stays open). Any of them may move; they keep their order and never cross sides; total squared movement around the fixed item is least for the chosen split (exact per side by pool-adjacent-violators). | verified (unit suite incl. brute-force grid search, 2026-10-04) |
| SM2 | P14: the dropped item never moves; the order around it follows where it was placed (a widget its center is on may go either side). Clearance is 1 px (existing overlaps between widgets are never deepened). Widgets overlap the item only when no allowed order fits: a truly full rail, or a clipped widget pinned at a rail end (at most half the item; for Mike to look at). | verified (plumbus/nacelle unit suite, review fuzz: no intrusion into the placed item, order kept, overlap only when no allowed order fits; real stipc input, 2026-10-04) |
| SM3 | Shifts stay visual until drop, then commit; Esc and dragging out restore exact geometry and drawn positions. A drop before any completed pause, or more than 4 px from where the last pause solved, solves once more; otherwise it commits the held layout. | verified (Plumbus real stipc input, 2026-10-04) |
| SM4 | Each solve checks its 256-actor bound before allocation, reuses captured order and scratch state, and returns only complete validated results. A drag solves only on pause/drop, never each pointer event; a non-drag widget arrival solves once after mapping. | verified (Plumbus; bounded unit suite and real stipc input, 2026-10-04) |
| SM5 | A 4 px pointer wobble is part of the same pause. The 350 ms default dwell is live configurable from 100–1500 ms; movement after a solve holds that layout until the next completed pause. | verified (Plumbus real stipc input, 2026-10-04) |
| SM6 | Every target change, including large shifts, release-time solves, geometry corrections and returns, eases in and out over 190–360 ms, longer only to stay under the shared 1000 px/s limit; retargets keep their speed and never overshoot; active reduced motion snaps. | verified (Plumbus frame sampling, planned and drawn frame-to-frame compositor speed, reduced-motion palette, 2026-10-04) |
| SM7 | Direct rail drops, inertial coast arrivals and Window-mode key widgetization run the shared solver at the widget's actual landing spot. | verified (Plumbus real stipc input, 2026-10-04) |
| SM8 | Window avoidance sees a rail ease's target, so it re-solves once per rail layout change, not per animation frame. | verified (Plumbus real stipc input against a no-move control, 2026-10-04) |
| SM9 | The split keeps its value across small re-pauses (P11): a widget changes side by choice only after the item's center passes its center by its direction margin and the new split fits with that margin to spare; forced changes happen only when the old split stops fitting or the placement forbids it. Within a split every widget moves at most as far as the pointer did. After a drop the cards stay as shown: no second solve. | verified (review fuzz, 600,000 solves: 0 back-and-forth; 1 px sweep suite; real stipc re-pause and drop, 2026-10-04) |
