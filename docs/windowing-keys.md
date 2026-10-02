# Window keys and placement

Alt is Scottland's window key. Hold it alone for `scottland/alt_hold_delay` milliseconds
(default 300, range 1–3000) to ask for hints. A quick chord keeps its existing app/desktop
behavior with no replay or input delay. This is core desktop behavior, independent of integrations.

The controller (`alt-mode.*`), rectangle placement (`placement.*`), force solver (`declutter.*`),
and compositor overlay (`hint-overlay.*`) are separate from Wayfire integration.
The desktop model's `window_state_t` owns normalized zone centers, the last side, hint slot,
and pending rail placement; its desktop snapshot publishes them and its atomic handover preserves
them on reload. `windowing-bridge.hpp` adapts the independent algorithms to `model.windows` and
`model.widgets`; widget changes use the existing lifecycle transitions. Cycled rail placement is refined
from the widget's pending size and applies drop and gravity together in its mapping transaction;
geometry notifications only record the committed memory, never issue a corrective move. Hint offsets and overlays
remain rendering resources, never geometry or memory inputs.

## Invariants

Status: **implemented (headless)** means real keyboard/pointer input in an isolated headless
Scottland on plumbus, or the named pure unit suite. **verified** additionally means exercised
on a physical session. This change is not tested on either machine's live display.

| ID | Invariant | Status |
|---|---|---|
| WK1 | Alt alone enters hints only after the configurable short hold (300 ms default). Any other key or Ctrl, Shift, or Super already held, or pressed before the timeout, cancels eligibility for that entire Alt chord. Quick Alt+letter and quick Alt+Tab keep app/desktop behavior. Both Alt keys are supported; pressing both before entry is not Alt alone. | implemented (headless) |
| WK2 | After entry, every unclaimed key belongs to Scottland until the last held Alt is released, including Ctrl/Super combinations and unassigned keys. Presses and matching releases are consumed; hints/cycles act once per physical press; arrows add impulses on presses and auto-repeat (WK14). Focused-surface key-layer claims retain ordinary delivery (KL7), including while hints are visible; a claimed press before entry cancels the hold. Alt itself is delivered immediately and its matching release is delivered, so quick app chords have no added delay or synthetic replay. | implemented (headless) |
| WK3 | Alt release removes hints and restores purely visual displacement. Esc removes hints/displacement, restores windows touched by arrows to their Alt-down origin (WK19), and keeps keys captured until Alt release. Releasing or cancelling mid-cycle preserves explicit cycle steps on windows untouched by arrows; the next mode entry starts at select. | implemented (headless) |
| WK4 | Every mapped top-level window and every widget has a large, click-through compositor hint in session palette colors, following the actual drawn center. A collapsed widget's hint is over its icon. Dialogs are selectable but retain WG1's protection against widgetizing. | implemented (headless) |
| WK5 | Assignment follows opening order, with `a s d f g h j k l q w e r t y u i o p z x c v b n m`. Each window retains its slot while open, including as a widget and across reload. Closed slots can be reused. As in Vimarchy, beyond 26 slots all labels become prefix-free two-letter hints; the assignment slot remains stable. | implemented (headless) |
| WK6 | A window's first hint selects, focuses, and raises it. A widget's first hint opens its window exactly as a card tap does (WG17), in the center. Selecting another hint resets the previous selection's cycle. | implemented (headless) |
| WK7 | Repeating a center window's hint cycles select → periphery → widget → center → periphery → widget → center… | implemented (headless) |
| WK8 | Repeating a periphery window's hint cycles select → center → widget → center → periphery → widget → center… | implemented (headless) |
| WK9 | A widget's first hint opens center, then repetitions cycle periphery → widget → center… | implemented (headless) |
| WK10 | Tab and Shift+Tab select the next/previous window or widget in hint order, wrapping. Tab focuses a widget without opening it; its hint opens it. F4 closes the selected window and its widget through normal linked lifecycle, preserving save-confirmation behavior. | implemented (headless) |
| WK11 | Super+Alt resize (L20) and Alt with Ctrl/Shift held first never show hints. Holding Alt during a drag belongs to L31 and suppresses hints for that entire chord, even after drop. Starting a drag cancels hints. Adding any modifier after entry stays in the mode (WK2). | implemented (headless) |
| WK12 | Alt still works in full screen (FS1). While hints are active, widgets slide back for their hints; on release/cancel they slide away again if full screen remains in front. Asking does not end full screen or notification holding. An explicit cycle exits full screen before moving, preserves the previous center memory, and queues rapid steps through the exit transaction. | implemented (headless) |
| WK13 | Near-coincident window/widget centers repel through a deterministic force-directed graph with springs to real centers. Centers stay within readable hint bounds; already separated centers stay put. Windows themselves animate outward and back, without moving their real geometry, changing their scale, or updating memories. Hints track those transforms. | implemented (headless) |
| WK14 | In entered Alt window mode, each unclaimed arrow press (including auto-repeat) adds a fixed impulse to its axis's surviving velocity, clamped independently to a maximum. Constant deceleration is integrated per tick until zero, including the final partial tick, with no restarted position animation. One default impulse travels v²/(2a) = 92.29 logical px. Hints/cycles retain physical-press-only behavior. | implemented; validation pending |
| WK15 | Arrows target the hint/Tab-selected window if selected this hold, otherwise the currently focused window (a focused widget represents its app). Different windows retain independent coasts. Left/Right change x, Up/Down change y; diagonals combine independent axes. An arrow on fullscreen explicitly exits it and waits for restored geometry before applying queued impulses. A later explicit cycle stops that window's coast before its lifecycle/placement action. Closing/unmapping/output loss discards its motion safely. | implemented; validation pending |
| WK16 | Moving windows follow their center's zone and scale live (L5/L8), even with Alt held: L31 scale pinning belongs only to drags, and arrows clear an old pin. Geometry/scale targets enter the desktop model; hints' visual declutter never enters motion coordinates. | implemented; validation pending |
| WK17 | A keyboard move stops its outward axis at WP7 screen/workarea padding or the rail edge, whichever is farther inward, using the live scaled content footprint. No bounce, no resize, no widgetization. Oversized content follows WP7's unpadded dimension exception; its center still stays outside the rails. Keyboard movement clamps old remembered/user drops too; WP2's exact remembered placement remains the cycle rule. | implemented; validation pending |
| WK18 | After mode entry, Ctrl+Right widens, Ctrl+Left narrows, Ctrl+Up grows height, Ctrl+Down shrinks height, with independent inertial size axes. Resizing keeps the window's center, zone and scale (L20), including asynchronous client commits. Sizes respect the app minimum and maximum and screen/workarea minus padding; an app minimum larger than that limit takes precedence. Movement and resize coasts can coexist. Ctrl+arrows consume input but do nothing for widgets. | implemented; validation pending |
| WK19 | Alt release commits keyboard movement/resize and lets existing velocity coast to zero; it stops adding repeats. Esc while still in mode stops inertia/repeats and glides each arrow-touched window back to its geometry and form captured at Alt-down, including size, fullscreen, widget rail, scale pin and zone memories (WG14/L27). The cancelled chord remains captured until Alt release. A later hold starts a new origin. | implemented; validation pending |
| WK20 | Widgets coast vertically along their current rail, keeping their wider widget inset. Left/Right transfers to the indicated rail with the existing glide, preserving height and widget form; pressing toward the current rail leaves it there. It never opens the window. Tab can select a widget without restoring it (WK10). | implemented; validation pending |
| WK21 | Options `scottland/key_impulse` (335 px/s), `scottland/key_friction` (608 px/s²), and `scottland/key_max_velocity` (6000 px/s) use Ask's defaults and apply to both movement and resizing. Repeats use the keyboard's configured delay/rate, independently for held arrows, and stop on key/Alt release or cancel. Exact focused-surface claims precede arrows (KL7); quick Alt+arrow and Ctrl-first chords keep existing app/desktop routing. A pointer/touch move or resize takes over motion without enabling hints (L31). | implemented; validation pending |
| WP1 | Each open window remembers independent center, left/right periphery, and left/right rail positions. Centers are normalized to screen dimensions and applied to the destination screen, including when a widget moved to a screen with a different scale. Initial placement, real drag drops, finished keyboard coasts, and cycle placements establish memories; visual animation does not. Closing forgets the record; a marked Scottland reload hands it to the new plugin in the atomic desktop model handover. | implemented (headless) |
| WP2 | A remembered destination wins exactly, even when occupied. Only pixel rounding is applied. This is predictable placement, not automatic rearrangement of existing windows. | implemented (headless) |
| WP3 | Side choice uses the most recently visited side with a periphery or rail memory. With neither, choose the side with the largest contiguous free opening (blocked intervals are unioned); when openings differ by no more than 5% of screen height, choose the nearer side. Exact horizontal ties choose right. | implemented (headless) |
| WP4 | Without a memory, use the single pure `place_rectangle` routine: minimize summed rectangle intersection area inside the destination region, then prefer the spot nearest the current center. Within 1% of the incoming rectangle's area counts as about equal. Side-zone ties prefer nearby vertical positions. The entire periphery is eligible, with its natural scaled footprint re-evaluated at the landing position; rail placement is refined to the actual widget footprint when it maps. | implemented (headless) |
| WP5 | Explicit zone cycling, card opens and presenting a side window clear an Alt-drag scale pin. Center destinations keep the original window size and are always at 100%. Oversized content stays full size. WG17 card clicks use the same placement routine: remembered center first, otherwise the nearest least-overlapping center spot rather than unconditional screen-middle placement. Presenting a side window uses it too (L30). | implemented (headless) |
| WP6 | The placement routine and force solver have no Wayfire dependencies and have standalone unit tests. The placement routine is reusable for any rectangle/region contention; it never resizes an incoming rectangle or moves obstacles. | implemented (headless) |
| WP7 | Windows Scottland places (zone cycling, card opens: the placement routine) keep off the screen's edges by the halo's width plus 5 pt (about 16 pt), in each dimension where the window fits; one larger than the screen in a dimension is not padded there. Widgets keep their own, wider rail inset. Remembered spots (WP2) and the user's own drops are kept exactly. | implemented (headless) |

## Decisions at unspecified edges

- Tenet 4 (concede as little as possible): surface layers keep exactly their claimed chords, even
  during hints. Unclaimed navigation remains available; a claimed press before entry bypasses the hold.
- Tenet 2 (recognition): keep assignment slots stable and reuse only closed slots. Vimarchy's
  prefix-free switch changes `a` to `aa` when capacity needs two letters; mixing `a` and `aa`
  would otherwise require a delay or extra input. Beyond two-letter capacity (676 slots), grow
  the common width again so no window loses its hint. Once expanded, keep the label width until
  all windows close, including over reload, so removing an overflow window does not shuffle labels.
- Tenets 2 and 3 (predictability, position means priority): if both sides have memories, the
  most recent side wins. Numerical about-equal thresholds are 1% overlap and 5% screen height.
  A new side destination keeps the nearest useful height when contention is about equal.
- Tenet 3 (peek, then put away): an arrow-touched window returns to its Alt-down origin on Esc;
  unrelated explicit cycle steps remain completed. Tab navigates widgets without unexpectedly opening them.
- Tenet 4 (nothing conceded in center): a large window may exceed the center region; its center
  remains in the zone and the original content size stays intact.
- Tenet 6 (full screen focus): explicitly holding Alt is a request, so widgets may return while
  asking; notifications remain held and full screen remains in effect until the user moves/focuses.

- Tenets 2 and 3: widget arrows keep it visible in the periphery. Vertical movement coasts along
  the current rail; horizontal movement chooses the indicated rail and glides there at the same
  height. This is explicit rail placement, never a keyboard undock gesture.
- Tenet 4: resize is explicit and centered; movement never changes app size. Impossible app
  minimum/screen limits favor the app minimum, as with pointer resize.

The pure `inertia.*` owns velocity integration only. `keyboard-motion.hpp` bridges raw keys and
keyboard repeat timing into Wayfire transactions and model geometry targets. Velocities, timer
clocks and short-lived resize centering are input-controller resources; model geometry, natural
scale, rail and placement memories remain authoritative. Mode origins are cancellation snapshots,
never renderer offsets. Timers use a monotonic clock and exact constant-deceleration integration;
there is no simulation-clock IPC. A marked reload ends transient input/coasts and preserves the
committed desktop state through the existing model handover.

## Verification

`tests/inertia-unit.sh` checks the independent axis math at several frame rates.
`tests/inertia-test.sh` drives actual stipc arrow presses and holds in a private headless
session with real GTK clients and widgets. It checks analytic travel, accumulation, repeats,
axis independence, rail/padding boundaries, scale samples, min/max center resize, Esc, quick
chords, KL7 claims, selected/focused targeting, live settings, fullscreen and widget form.
Screenshots and sampled states are retained beside the isolated session directory.


`tests/windowing-unit.sh` compiles the pure algorithms/controller in an isolated temporary
directory. `tests/windowing-test.sh` requires `SCOTTLAND_HEADLESS_DIR`, starts a private widget bus,
drives real stipc keyboard/pointer input, records received app keys, checks cycle and memory state,
and reloads a copied library under open windows. Artifacts (key records, screenshots, Wayfire log)
are saved beside that session directory. `tests/widgets-test.sh` supplies widget/attention/fullscreen
regression coverage and now honors the supplied directory for its display, reload library, and
artifacts too.

Integrated validation on plumbus, 2026-10-01, code commit `756b8d7` (merges main `e76bc56`
and key-layers `9a24c92`). Main's subsequent documentation-only tip `16286df` is included
by merge `56b0cc1`; it changes no code under test.

| Suite | Result |
|---|---|
| Windowing unit | 41 passed, 0 failed |
| Windowing end-to-end | 73 passed, 0 failed |
| Key layers, including hints | 59 passed, 0 failed |
| Widgets | 146 passed, 0 failed, including 43 collapse-input/preview checks; no runner errors |
| Model seed 271828, 50 operations | 95 passed |
| Model seed 104729, 50 operations, legacy D-Bus | 100 passed |
| Focused model regressions | 7 passed, including different-scale destination-output card restore |
| Attention / launcher / widget-bus units | 5 / 17 / 16 passed |
| Config concurrency | 5 rounds passed, 20 simultaneous builds each |
| Notification focus hooks | 3 passed |

The windowing suite checks real app receipt and modifiers, all starting-zone cycles, L31 pinning
without hints (also after drop), real L20 resize, occupied memories, both sides/rails, free-side
choice, fullscreen reveal and rapid cycles, declutter/restoration, 27-window hints, and marked
reload with a live widget. It also checks normalized memory in a newer subscribed desktop snapshot,
unchanged model memories during declutter, removal on close, and external-slice filtering.
The layer suite checks claimed Alt, quick claimed chords, claims during hints, a clear while held,
and unclaimed navigation. A two-output real card drag/click reproduced the wrong-output restore
on the preceding build; the corrected build restores the destination-relative memory at 100%.

Every compositor was started after its build. Deployment used `--tests-only` in
`~/Projects/scottland-hints-merge`; `TMPDIR=~/.cache/scottland-test-tmp` and
`SCOTTLAND_HEADLESS_DIR=$XDG_RUNTIME_DIR/scottland-headless-hints-merge`, with private D-Bus and
the checkout's own helpers. Logs are in `~/.cache/scottland-hints-merge-results/final-756b8d7`. All suites exited zero,
and the isolated sessions were stopped afterward.
Headless screenshots show distinct hints tracking displaced windows and native/layer-shell surfaces.
No live session on osanwe or physical screen on plumbus was installed into, reloaded or used.
Physical verification and rehearsal from the installed build before live reload remain the
coordinating session's rollout work.

The final matrix includes main's Super+M, transaction gravity, preview mode and settings/import
fixes. Rail placement uses pending widget size and the gravity transaction, and the collapse
raw-key tracker respects focused-surface claims and keys consumed by hints.
