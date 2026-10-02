# Scottland's live move path (L33)

Super+left-drag, halo pointer/touch drags, touchscreen long-press lift and three-finger
swipe use `scottland::live_drag_t`. The `scottland/move` binding belongs to Scottland;
`move/activate` is disabled in the shipped config. Stock `move` remains loaded before
Scottland (C3) for client-initiated move requests. Its signals are still observed so
those requests retain Scottland's scale/morph/drop behavior.

## What the old path actually did

At baseline `66560b4`, all five move gestures used Wayfire's shared `core_drag_t`,
including halo drags. It disables the transformed view in its normal scene, adds a
`scale_around_grab_t` texture transformer, and renders that subtree through a separate
`dragged_view_node_t` with a render-instance manager. This is an offscreen texture path,
not a one-time frozen screenshot: its manager forwards damage, visibility and
presentation feedback. A GTK frame-clock fixture changing color every frame produced
12 different content samples out of 12 during each of the five baseline drags on Xe.
**The reported freeze was not reproduced on this installed Wayfire build.**

L33 makes Scottland own the path and adds explicit live-content regression coverage;
it does not establish a freeze fix for an unidentified application or another Wayfire
version. This distinction also avoids claiming that old halo drags had a different
renderer from old Super drags.

## Scene and input ownership

The live controller temporarily reparents the enabled transformed view to the global
scene, above the widgets. A translation around the grabbed bounding-box fraction keeps
the same point under the pointer/finger as Scottland's existing frame changes scale or
morphs. Normal scene traversal carries surface damage, visibility and frame callbacks
on every intersected output. The app keeps its size and content framing (tenet 4).
The drag transformer rejects hit tests; a compositor input grab consumes the gesture.

Pointer motion, touch motion and swipe deltas feed the same scale/morph handlers.
Output crossings transfer the input grab without ending the move. The final shown
position is committed in destination-output coordinates before the existing drop path
runs. Esc, Alt pinning, widget entry/restore, click/raise holds and release inertia keep
one owner. Reentrant touch-up delivery while releasing/transferring a grab is guarded.
Unmap and plugin unload return the subtree and release the input resources.

Tenet 3 decides the shared gesture behavior: moving changes priority, and Esc puts the
window back where it was picked up, in its original form. Tenet 2 keeps the window/card
visible throughout rail morphs. Goo uses the live subtree's actual scene order and
transformed geometry, including across output boundaries.

## Validation (2026-10-02)

Only isolated headless compositors are used, on Xe and plumbus RX 580. The runtime is
the normal `XDG_RUNTIME_DIR`; each harness directory and every test artifact is under
this checkout's `build/l33-results/`. Test configs come from shipped defaults. The
baseline is an archived `66560b4` with its own build and hooks. No install, live reload,
physical session, or live widget service is involved. L33 is **implemented/headless
checked**, not physically verified.

`tests/live-drag-test.sh` runs a GTK client whose color changes on every frame-clock
tick, then samples actual screenshot pixels during five held input gestures plus a client move request. It also
asserts the active renderer is `scottland-live`, rather than merely trusting movement.
Its second session checks pointer/touch crossings, changing pixels while held still
on the adjacent output, Esc home, destination-output drops, unmapping a held client,
and plugin unload under a held drag. The swipe hook feeds the real gesture handlers;
stipc supplies actual pointer, key and touch events for the other paths.

The morph suite's load-recovery fixture explicitly enables stock move while Scottland
is unloaded, then restores the original binding. This keeps that test exercising real
input without relying on a binding Scottland now owns.

Final suite and GPU measurements are recorded below. Earlier failed runs remain in
the artifact directory: the initial controller had a touch-release reentrancy crash
(fixed); a morph run needed the stock-binding fixture correction; concurrent Xe runs
had two goo alignment failures and intermittent keyboard undo failures. Those results
are retained rather than hidden by weakening assertions.

### GPU cost

The unchanged GO10 fixture uses six windows, two real rail widgets, a moving held
Super drag, 2560×1600 and ten seconds per case. No GPU clocks, update rates or live
sessions were changed. Xe logs are `xe-before.log` and `xe-after.log`; the comparable
RX 580 low-background samples are `plumbus/amd-before.log` and
`plumbus/amd-after-final.log` (baseline/new respectively).

| Drag measurement | Xe baseline → new | RX 580 baseline → new |
|---|---|---|
| Compositor GPU busy | 20.8% → 18.7% | 14.4% → 14.5% |
| Median goo GPU query | 6.769 → 4.559 ms | 1.677 → 1.682 ms |
| GPU busy time / goo update | 3.817 → 3.275 ms | 2.517 → 2.539 ms |
| Active updates / 10 seconds | 545 → 571 | 572 → 571 |
| Compositor CPU | 10.1% → 10.2% | 9.9% → 10.0% |

GPU busy time/update divides compositor GPU busy time by measured goo updates; it
includes work outside the goo query and is not a direct display-frame latency
measurement. The RX 580 increments are 0.005 ms/query and approximately 0.023 ms/update,
comfortably below the sub-millisecond added-cost criterion used for GO13/GO10. Settled
cases stay at **zero simulation steps and 0.0% compositor GPU busy** on both devices.
Attention GPU busy is 18.5% → 17.5% (Xe), 15.2% → 14.7% (RX 580); goo-off breathing is
0.5% → 0.4% and 0.6% → 0.7%, respectively.

These are shared machines. Xe whole-GPU busy during drag was 76.4% → 63.6%, so its
elapsed queries include different contention; the apparent improvement is not an
uncontended speedup claim. RX 580 whole-GPU busy in the table's drag samples was
14.6% in both cases, close to compositor busy. Intermediate/recheck runs under other
GPU activity changed clock behavior substantially: baseline drag samples ranged
7.6–8.4% and 0.816–0.819 ms, and a new-build sample was 8.4% and 0.879 ms. Those logs
(`amd-before-final`, `amd-baseline-recheck`, `amd-after`) are retained too. They do not
support comparing a high-clock baseline directly to a low-clock new-build sample.
Across comparable observed operating ranges the added cost remains small; no universal
performance guarantee or frozen-client performance saving is claimed.

### Regression results

| Check | Passing run |
|---|---|
| Frame-driven content, Scottland Super/halo/touch/halo-touch/swipe | 12/12 distinct pixel colors per gesture on Xe and RX 580 |
| Retained client-initiated stock move, goo off | 12/12 distinct colors; renderer reports `wayfire-move` |
| Held pointer/touch cross-output motion, Esc/drop, unmap/unload | 11 checks, goo on and off |
| Drag coast (final serial run) | 22 single-output + 3 multi-output |
| Windowing, including L31 scale pinning | 84 |
| Keyboard inertia | 61 single-output + 8 multi-output |
| Widgets, including WG14 and L29 click/raise | 186 on RX 580 |
| Widget morph with goo on | 186 on RX 580 |
| Widget morph with goo off | 162 on Xe |
| Existing drag/resize scripts | scale 1.000 → 0.808 → 1.000 across both sides; resize 696×495 → 816×555 with center fixed at (640,360) |

The table identifies passing runs, not an assertion that every repeat passed. Xe
repeats also hit screenshot/crossfade and goo-alignment timing failures in hint-cycle
cases, plus one keyboard declutter-freeze assertion. Inertia's two undo assertions
failed in earlier runs and passed in the fresh 61+8 run. The unchanged baseline
inertia suite passed 61+8. The exact `66560b4` baseline morph run passed 185/186
and failed the post-reload snapshot-retention check (`morph-before.log`), confirming
some suite instability predates L33 without attributing every new-run failure to it. No timing threshold or assertion was weakened to obtain
these results. Drag-specific frame-content, crossing, Esc, touch-release and cleanup
checks passed. The raw failed and passing logs remain alongside the measurements.
