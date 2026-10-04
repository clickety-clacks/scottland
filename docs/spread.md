# Making room during a drag

This document records the signed-off WG26 rail profile and the reusable presentation contract it
uses. The implementation in this change is **rail-only**. The separate whole-screen spread policy
is not implemented here.

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

`drag_presentation_t` is independent of rails and can serve later layout auditions. It records
actor origins, supplies additive visual offsets while a gesture is active, and derives committed
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
| SM1 | A rail solve considers widgets from the dragged item's output and rail, excluding the dragged item (whose spot stays open). Any of them may move; they keep their order and never cross sides; total squared movement around the fixed item is least for the chosen split (exact per side by pool-adjacent-violators). | verified (unit suite incl. brute-force grid search, 2026-10-04) |
| SM2 | P14: the dropped item never moves; the order around it follows where it was placed (a widget its center is on may go either side). Clearance is 1 px (existing overlaps between widgets are never deepened). Widgets overlap the item only when no allowed order fits: a truly full rail, or a clipped widget pinned at a rail end (at most half the item; for Mike to look at). | verified (plumbus/nacelle unit suite, review fuzz: no intrusion into the placed item, order kept, overlap only when no allowed order fits; real stipc input, 2026-10-04) |
| SM3 | Shifts stay visual until drop, then commit; Esc and dragging out restore exact geometry and drawn positions. A drop before any completed pause, or more than 4 px from where the last pause solved, solves once more; otherwise it commits the held layout. | verified (Plumbus real stipc input, 2026-10-04) |
| SM4 | Each solve checks its 256-actor bound before allocation, reuses captured order and scratch state, and returns only complete validated results. A drag solves only on pause/drop, never each pointer event; a non-drag widget arrival solves once after mapping. | verified (Plumbus; bounded unit suite and real stipc input, 2026-10-04) |
| SM5 | A 4 px pointer wobble is part of the same pause. The 350 ms default dwell is live configurable from 100–1500 ms; movement after a solve holds that layout until the next completed pause. | verified (Plumbus real stipc input, 2026-10-04) |
| SM6 | Every target change, including large shifts, release-time solves, geometry corrections and returns, eases in and out over 190–360 ms, longer only to stay under the shared 1000 px/s limit; retargets keep their speed and never overshoot; active reduced motion snaps. | verified (Plumbus frame sampling, planned and drawn frame-to-frame compositor speed, reduced-motion palette, 2026-10-04) |
| SM7 | Direct rail drops, inertial coast arrivals and Window-mode key widgetization run the shared solver at the widget's actual landing spot. | verified (Plumbus real stipc input, 2026-10-04) |
| SM8 | Window avoidance sees a rail ease's target, so it re-solves once per rail layout change, not per animation frame. | verified (Plumbus real stipc input against a no-move control, 2026-10-04) |
| SM9 | The split keeps its value across small re-pauses (P11): a widget changes side by choice only after the item's center passes its center by its direction margin and the new split fits with that margin to spare; forced changes happen only when the old split stops fitting or the placement forbids it. Within a split every widget moves at most as far as the pointer did. After a drop the cards stay as shown: no second solve. | verified (review fuzz, 600,000 solves: 0 back-and-forth; 1 px sweep suite; real stipc re-pause and drop, 2026-10-04) |
