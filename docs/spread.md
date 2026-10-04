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
existing WG1/WG13 morph. Other widgets shift along the rail by the least distance needed to clear
the dragged item's current visible interval by 1 px. A shift reaches the next widget only when the
previous shift makes that widget too close. Existing gaps and overlaps are otherwise preserved;
the rail is not tiled or compacted. A widget never crosses to the other rail.

The hole left by a widget being dragged remains open after a successful drop. When a chain is pinned
against its rail end but the other side has room, the widgets nearest the dragged item yield that way
instead, one at a time, until both sides fit (a drop at the top of a rail packed from the top moves
the top card down with the rest). Neighbors keep their order. A widget yields only if the dragged
item's center is on it, so either order matches where the user put it, or if it had already yielded
on the previous solve (P11: it keeps its way while that works). A drop that only clips a pinned
widget's far edge, or merely touches it within the 1 px contact gap, does not make it jump across.
Yielding re-runs the two chain passes at most once per widget, so a solve stays bounded (P8).
When neither way fits, the rail is full: each causal chain advances only as far as its members can
fit, stopped by the tightest rail-end limit, and the drop may overlap. The solver reports that case
as `overlap`; it never publishes an off-rail result.

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
| SM1 | A rail solve considers widgets from the dragged item's output and rail, excluding the dragged item. It moves only a causal chain, preserves the rail order and never crosses sides. | verified (plumbus headless, 2026-10-04) |
| SM2 | Contact clearance is 1 px where room exists. A chain pinned at a rail end yields the other way when that side has room (only widgets the drag is on, or that already yielded); a drop may overlap only when the rail is full. | verified (plumbus unit suite and real stipc input, 2026-10-04) |
| SM3 | Shifts stay visual until drop, then commit; Esc and dragging out restore exact geometry and drawn positions. A drop before any completed pause, or more than 4 px from where the last pause solved, solves once more; otherwise it commits the held layout. | verified (Plumbus real stipc input, 2026-10-04) |
| SM4 | Each solve checks its 256-actor bound before allocation, reuses captured order and scratch state, and returns only complete validated results. A drag solves only on pause/drop, never each pointer event; a non-drag widget arrival solves once after mapping. | verified (Plumbus; bounded unit suite and real stipc input, 2026-10-04) |
| SM5 | A 4 px pointer wobble is part of the same pause. The 350 ms default dwell is live configurable from 100–1500 ms; movement after a solve holds that layout until the next completed pause. | verified (Plumbus real stipc input, 2026-10-04) |
| SM6 | Every target change, including large shifts, release-time solves, geometry corrections and returns, eases in and out over 190–360 ms, longer only to stay under the shared 1000 px/s limit; retargets keep their speed and never overshoot; active reduced motion snaps. | verified (Plumbus frame sampling, compositor speed metric and reduced-motion palette, 2026-10-04) |
| SM7 | Direct rail drops, inertial coast arrivals and Window-mode key widgetization run the shared solver at the widget's actual landing spot. | verified (Plumbus real stipc input, 2026-10-04) |
| SM8 | Window avoidance sees a rail ease's target, so it re-solves once per rail layout change, not per animation frame. | verified (Plumbus real stipc input against a no-move control, 2026-10-04) |
