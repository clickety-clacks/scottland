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
A widget never crosses to the other rail, and the hole left by a widget being dragged stays open.

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
earlier full rail), and the item needs 1 px of clearance on each side. The item splits the
widgets into those above it and those below it. For one split, each side is independent: its
widgets go between a rail end and the item, in order, minimizing the sum of squared displacements
from home. Subtracting each widget's packed offset turns "in order, apart" into "non-decreasing",
so a side is an isotonic regression with bounds, solved exactly by pool-adjacent-violators in O(n).
The item moves only as much as its split needs: if a side can't fit between the item and its
rail end, the item settles toward the side with room by the least amount, and otherwise not at
all. It never settles off the rail, and an item hanging off a rail end is laid out where placement
will put it, on the rail. So each split's layout is the **least total movement for that split,
with the item settled the least it must be**. It is not a joint minimum in which the item shares
the move with its neighbors; that would move the dropped card more often and by more. Every split
is tried and the one with the least total movement, the item's settle included, wins (ties go to
the split by centers). A widget therefore crosses the item only when that is less movement, and
the item settles only when its split needs it and that split is the cheapest. With n capped at 256
a solve is O(n²); review round 3 measured about 1 ms average, 2.9 ms worst at the cap.

While dragging, the item is under the pointer and can't settle; the audition shows the neighbors
placed for the settled item. On release the drop's solve is committed as shown. If it settles the
item and the item lands on the interval that solve was for (within the 4 px wobble), exactly that
settle is applied once it lands, easing it into place; nothing is solved again, so the cards never
rearrange a second time. Only a different landing (the real card is another size, or placement
moved it) is solved again for its actual interval.

**Pending Mike's decision (review round 3, finding 1): does the user's aim decide the order?** By
default total movement decides, so a card dropped onto a card at a rail end usually settles past
it (in the review-2 fuzz: 10% of pauses settle, median 31 px, up to 119 px), and a card can't be put
first ahead of a card sitting at a rail end. The prepared alternative, behind the hidden option
`scottland/widget_make_room_by_aim` (default off, not in the settings app), keeps the split by
centers (each widget stays on the side of the item its center is on) whenever the item settles at
most half its own height for it; otherwise the fewest widgets nearest the item cross it so that
the settle is at most that leftover; only if no such split exists does total movement decide. In
the same fuzz it settles in 6% of pauses, median 18 px, 90th percentile 40 px, and a drop at the
top of a packed rail goes first. Every other guarantee (order, legality, P11, overlap only when
full) holds either way.

When no split fits even with settling, the rail is truly full. The split that needs the least
overlap is used, and the widgets encroach on the item by exactly that amount, half at each edge;
it grows continuously from zero as a rail fills. The status is `overlap`. Results never leave the
rail or change the widgets' order; a failed check produces zero shifts with `overlap` status.

P11 hysteresis applies to the split: it keeps its previous value until the item's center has moved
`max(6 px, 10% of the height of a widget that would change side)` from where it last changed, as
long as it still fits as well. A widget therefore never switches side and back across small
re-pauses. Within one split every widget moves at most 1 px per pixel of drag.

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
| SM1 | A rail solve considers widgets from the dragged item's output and rail, excluding the dragged item (whose spot stays open). Any of them may move; they keep their order and never cross sides; total squared movement is least (exact per side by pool-adjacent-violators, all splits tried). | verified (plumbus unit suite incl. brute-force grid search, 2026-10-04) |
| SM2 | Clearance is 1 px (existing overlaps between widgets are never deepened). The dropped item settles toward the side with room by exactly the least its split needs (0 when it fits as dropped); the split with the least total movement for its split wins. Widgets overlap the item only when the rail is truly full, by exactly the shortfall. Whether aim decides the order instead is pending Mike (hidden switch). | verified (plumbus unit suite and review fuzz: 0 overlaps with room, settle equals the split's least need in 600,000 solves; real stipc input, 2026-10-04) |
| SM3 | Shifts stay visual until drop, then commit; Esc and dragging out restore exact geometry and drawn positions. A drop before any completed pause, or more than 4 px from where the last pause solved, solves once more; otherwise it commits the held layout. | verified (Plumbus real stipc input, 2026-10-04) |
| SM4 | Each solve checks its 256-actor bound before allocation, reuses captured order and scratch state, and returns only complete validated results. A drag solves only on pause/drop, never each pointer event; a non-drag widget arrival solves once after mapping. | verified (Plumbus; bounded unit suite and real stipc input, 2026-10-04) |
| SM5 | A 4 px pointer wobble is part of the same pause. The 350 ms default dwell is live configurable from 100–1500 ms; movement after a solve holds that layout until the next completed pause. | verified (Plumbus real stipc input, 2026-10-04) |
| SM6 | Every target change, including large shifts, release-time solves, geometry corrections, the dropped item's settle and returns, eases in and out over 190–360 ms, longer only to stay under the shared 1000 px/s limit; retargets keep their speed and never overshoot; active reduced motion snaps. | verified (Plumbus frame sampling, planned and drawn frame-to-frame compositor speed, reduced-motion palette, 2026-10-04) |
| SM7 | Direct rail drops, inertial coast arrivals and Window-mode key widgetization run the shared solver at the widget's actual landing spot. | verified (Plumbus real stipc input, 2026-10-04) |
| SM8 | Window avoidance sees a rail ease's target, so it re-solves once per rail layout change, not per animation frame. | verified (Plumbus real stipc input against a no-move control, 2026-10-04) |
| SM9 | The split (which widgets are above the item) keeps its value across small re-pauses: no widget switches side and back within its direction margin (P11); within a split every widget, and the item's settle, moves at most as far as the pointer did. After a drop the cards stay as shown: the drop's settle is applied without solving again. | verified (review fuzz, 600,000 solves: 0 back-and-forth, 0 settle jumps; 1 px sweep suite; real stipc re-pause and drop, 2026-10-04) |
