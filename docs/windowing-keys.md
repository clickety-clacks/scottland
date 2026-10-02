# Window keys and placement

Alt is Scottland's window key. Hold it alone for `scottland/alt_hold_delay` milliseconds
(default 300, range 1–3000) to ask for hints. A quick chord keeps its existing app/desktop
behavior with no replay or input delay. This is core desktop behavior, independent of integrations.

The controller (`alt-mode.*`), rectangle placement (`placement.*`), force solver (`declutter.*`),
and compositor overlay (`hint-overlay.*`) are separate from Wayfire integration.
The desktop model's `window_state_t` owns normalized zone centers, the last side, hint slot,
and pending rail placement; its desktop snapshot publishes them and its atomic handover preserves
them on reload. `windowing-bridge.hpp` adapts the independent algorithms to `model.windows` and
`model.widgets`; widget changes use the existing lifecycle transitions. Hint offsets and overlays
remain rendering resources, never geometry or memory inputs.

## Invariants

Status: **implemented (headless)** means real keyboard/pointer input in an isolated headless
Scottland on plumbus, or the named pure unit suite. **verified** additionally means exercised
on a physical session. This change is not tested on either machine's live display.

| ID | Invariant | Status |
|---|---|---|
| WK1 | Alt alone enters hints only after the configurable short hold (300 ms default). Any other key or Ctrl, Shift, or Super already held, or pressed before the timeout, cancels eligibility for that entire Alt chord. Quick Alt+letter and quick Alt+Tab keep app/desktop behavior. Both Alt keys are supported; pressing both before entry is not Alt alone. | implemented (headless) |
| WK2 | After entry, every unclaimed key belongs to Scottland until the last held Alt is released, including Ctrl/Super combinations and unassigned keys. Presses and matching releases are consumed; one action occurs per physical press, not repeat. Focused-surface key-layer claims retain ordinary delivery (KL7), including while hints are visible; a claimed press before entry cancels the hold. Alt itself is delivered immediately and its matching release is delivered, so quick app chords have no added delay or synthetic replay. | implemented (headless) |
| WK3 | Alt release removes hints and restores purely visual displacement. Esc removes hints/displacement without another window action, and keeps keys captured until Alt release. Releasing or cancelling mid-cycle preserves every explicit step already taken; the next mode entry starts at select. | implemented (headless) |
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
| WP1 | Each open window remembers independent center, left/right periphery, and left/right rail positions. Centers are normalized to screen dimensions and applied to the destination screen, including when a widget moved to a screen with a different scale. Initial placement, real drag drops, and cycle placements establish memories; visual animation does not. Closing forgets the record; a marked Scottland reload hands it to the new plugin in the atomic desktop model handover. | implemented (headless) |
| WP2 | A remembered destination wins exactly, even when occupied. Only pixel rounding is applied. This is predictable placement, not automatic rearrangement of existing windows. | implemented (headless) |
| WP3 | Side choice uses the most recently visited side with a periphery or rail memory. With neither, choose the side with the largest contiguous free opening (blocked intervals are unioned); when openings differ by no more than 5% of screen height, choose the nearer side. Exact horizontal ties choose right. | implemented (headless) |
| WP4 | Without a memory, use the single pure `place_rectangle` routine: minimize summed rectangle intersection area inside the destination region, then prefer the spot nearest the current center. Within 1% of the incoming rectangle's area counts as about equal. Side-zone ties prefer nearby vertical positions. The entire periphery is eligible, with its natural scaled footprint re-evaluated at the landing position; rail placement is refined to the actual widget footprint when it maps. | implemented (headless) |
| WP5 | Explicit zone cycling, card opens and presenting a side window clear an Alt-drag scale pin. Center destinations keep the original window size and are always at 100%. Oversized content stays full size. WG17 card clicks use the same placement routine: remembered center first, otherwise the nearest least-overlapping center spot rather than unconditional screen-middle placement. Presenting a side window uses it too (L30). | implemented (headless) |
| WP6 | The placement routine and force solver have no Wayfire dependencies and have standalone unit tests. The placement routine is reusable for any rectangle/region contention; it never resizes an incoming rectangle or moves obstacles. | implemented (headless) |

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
- Tenet 3 (peek, then put away): Esc cancels the current request, not actions explicitly
  completed earlier in the chord. Tab navigates widgets without unexpectedly opening them.
- Tenet 4 (nothing conceded in center): a large window may exceed the center region; its center
  remains in the zone and the original content size stays intact.
- Tenet 6 (full screen focus): explicitly holding Alt is a request, so widgets may return while
  asking; notifications remain held and full screen remains in effect until the user moves/focuses.

## Verification

`tests/windowing-unit.sh` compiles the pure algorithms/controller in an isolated temporary
directory. `tests/windowing-test.sh` requires `SCOTTLAND_HEADLESS_DIR`, starts a private widget bus,
drives real stipc keyboard/pointer input, records received app keys, checks cycle and memory state,
and reloads a copied library under open windows. Artifacts (key records, screenshots, Wayfire log)
are saved beside that session directory. `tests/widgets-test.sh` supplies widget/attention/fullscreen
regression coverage and now honors the supplied directory for its display, reload library, and
artifacts too.

On plumbus (2026-10-01), `tests/windowing-unit.sh`: **41 passed, 0 failed**;
`tests/windowing-test.sh`: **61 passed, 0 failed**; `tests/widgets-test.sh`: **100 passed,
0 failed**, with no skipped arithmetic commands. The input suite includes actual app key/modifier
receipt, all starting-zone cycles, drag and card input, occupied memories, both peripheries and
rails, free-side choice, fullscreen reveal and rapid cycles, declutter/restoration, 27-window
hints, and marked reload with a live widget. The compositor screenshot was inspected for distinct
hints tracking the displaced windows. Every tested compositor was started after the build, in
`$XDG_RUNTIME_DIR/scottland-headless-alt-hints`; no live session was installed into or reloaded.

Integration with current main is awaiting the isolated plumbus matrix; the results above describe the pre-merge branch.
