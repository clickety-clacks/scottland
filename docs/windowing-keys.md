# Window keys and placement

Alt is Scottland's window key. Hold it alone for `scottland/alt_hold_delay` milliseconds
(default 300, range 1–3000) to enter **window mode** and show hints. A quick chord keeps its
existing app/desktop behavior with no replay or input delay. This is core desktop behavior, independent of integrations.

The controller (`alt-mode.*`), rectangle placement (`placement.*`), bounded overlap solver (`declutter.*`),
inertial axes (`inertia.*`), and compositor overlay (`hint-overlay.*`, with pure palette/contrast math in `hint-style.hpp`) are separate from Wayfire integration.
The desktop model's `window_state_t` owns normalized zone centers, the last side, hint slot,
and pending rail placement; its desktop snapshot publishes them and its atomic handover preserves
them on reload. `windowing-bridge.hpp` adapts the independent algorithms to `model.windows` and
`model.widgets`; widget changes use the existing lifecycle transitions. Cycled rail placement is refined
from the widget's pending size and applies drop and gravity together in its mapping transaction;
cycle-placement geometry notifications only record the committed memory, never issue a corrective move.
Keyboard resize separately recenters late client commits and recovers boundary overflow (WK21). Hint offsets and overlays
remain rendering resources, never geometry or memory inputs.

## Invariants

Status: **implemented (headless)** means real keyboard/pointer input in an isolated headless
Scottland, or the named pure unit suite. **verified** additionally means exercised
on a physical session. This change is not tested on either machine's live display.

| ID | Invariant | Status |
|---|---|---|
| WK1 | Alt alone enters hints only after the configurable short hold (300 ms default). Any other key or Ctrl, Shift, or Super already held, or pressed before the timeout, cancels eligibility for that entire Alt chord. Quick Alt+letter and quick Alt+Tab keep app/desktop behavior. Both Alt keys are supported; pressing both before entry is not Alt alone. | implemented (headless) |
| WK2 | After entry, every unclaimed key belongs to Scottland until the last held Alt is released, including Ctrl/Super combinations and unassigned keys. Presses and matching releases are consumed; hints/cycles act once per physical press; arrows add impulses on presses and auto-repeat (WK17). Focused-surface key-layer claims retain ordinary delivery (KL7), including while hints are visible; a claimed press before entry cancels the hold. Alt itself is delivered immediately and its matching release is delivered, so quick app chords have no added delay or synthetic replay. | implemented (headless) |
| WK3 | Alt release exits window mode, removes hints and restores purely visual displacement. Esc removes hints/displacement, restores windows touched by arrows to their Alt-down origin (WK22), and keeps keys captured until Alt release. Releasing or cancelling mid-cycle preserves explicit cycle steps on windows untouched by arrows; the next entry starts a new cycle from the current zone, skipping select if already selected. | implemented (headless) |
| WK4 | Every mapped top-level window and every widget has a large, click-through compositor hint in session palette colors, attached to the most open visible portion of ordinary windows (WK31); widgets use the exterior attachment in WK26, expanded or collapsed. Dialogs are selectable but retain WG1's protection against widgetizing. | implemented (headless) |
| WK5 | Assignment follows opening order, with `a s d f g h j k l q w e r t y u i o p z x c v b n m`. Each window retains its slot while open, including as a widget and across reload. Closed slots can be reused. As in Vimarchy, beyond 26 slots all labels become prefix-free two-letter hints; the assignment slot remains stable. Badges follow Vimarchy: a circle (in the visible window region per WK31, beside widgets per WK26) sized `clamp(min(displayed width, displayed height) × 0.34, 72, 132)` logical px, 21% hint-color fill, bold uppercase letters at 62% of badge height (46% for multiple letters). WK31 caps ordinary circles to visible clearance and supplies a readable fragment floor. Output scale affects raster resolution, never logical badge size. | implemented (headless) |
| WK6 | A window’s first hint selects, focuses, and raises it only if it is not already selected/focused. If already selected (including by Tab), the first press goes straight to the next zone. A widget’s first hint selects and focuses without opening (WK30); if already selected, its hint goes straight to center. Selecting another hint resets the previous selection’s cycle. | implemented (headless) |
| WK7 | All starting zones follow one start-relative loop: visit the other two zones, toward center first, then return to the start. The start is the window’s zone when cycling begins in this Alt hold (before selecting/opening); it stays fixed until another hint is selected or the hold ends. See the cycle table below. | implemented (headless) |
| WK8 | A periphery-start loop visits center → widget → periphery repeatedly (WK7). | implemented (headless) |
| WK9 | A widget-start loop visits center → periphery → widget repeatedly; selection leaves its first center step for the next slow hint (WK6/WK30). | implemented (headless) |
| WK10 | Tab and Shift+Tab select the next/previous window or widget in hint order, wrapping. Tab focuses a widget without opening it; its hint opens it. F4 closes the selected window and its widget through normal linked lifecycle, preserving save-confirmation behavior. | implemented (headless) |
| WK11 | Super+Alt resize (L20) and Alt with Ctrl/Shift held first never show hints. Holding Alt during a drag belongs to L31 and suppresses hints for that entire chord, even after drop. Starting a drag cancels hints. Adding any modifier after entry stays in the mode (WK2). | implemented (headless) |
| WK12 | Alt still works in full screen (FS1). While hints are active, widgets slide back for their hints; on release/cancel they slide away again if full screen remains in front. Asking does not end full screen or notification holding. An explicit cycle exits full screen before moving, preserves the previous center memory, and queues rapid steps through the exit transaction. | implemented (headless) |
| WK13 | While Alt is held, declutter moves the drawn windows only as far as needed to expose an interior circle for each window's hint (WK31). A window that already exposes enough does not move for its own hint; a foreground window can move when that is necessary to reveal a fully covered one. Windows animate back on release/Esc. The transform changes no real geometry, scale, or memories. Widget exterior hints keep their deterministic vertical collision solver and horizontal attachment (WK26). | implemented (headless) |
| WK14 | Each assignment has a deterministic distinct color across a 160° hue arc opposite the session accent, with successive slots far apart; opening/closing other windows does not recolor retained letters. Scheme, background, foreground and accent come from `SCOTTLAND_PALETTE`, or the session's `<display>.palette.json`, checked every 250 ms while showing hints. Scheme chooses saturation/lightness; lightness is adjusted to at least 3:1 WCAG contrast against the theme background and a typical surface after compositing both tints. The whole window/card gets a 7% hint-color overlay, a 2 logical px full-color rounded border even at the supported 5% window scale, and the halo takes its dye. Fullscreen gets the tint and an inset square rim. Release, Esc, replacement and unload clear the transient dye without altering focus/attention state. With the screen-wide goo (on by default), window mode simply tints the goo with the hint color as dye (GO6): the window/card overlay stays, and there is no separate rim. The 2 logical px border applies to the fallback halo (goo off); with the goo on, the hint shows only as the window/card tint and the goo dye. | implemented (headless) |
| WK15 | Repeating the same hint within `scottland/window_double_tap_delay` (default 300 ms, range 1–3000, inclusive) sends its window to the rail immediately; if already a widget, it does nothing. The first press acts immediately. Slower presses keep cycling. After the shortcut, slow cycling resumes after widget in the original start-relative loop. Tab, another hint, release or cancellation resets double-tap recognition. | implemented (headless) |
| WK16 | Double-taps use physical presses, never key repeat, and apply only in window mode. With prefix-free multi-letter hints, repeat the complete hint to invoke the same shortcut; repeating a prefix alone does not move a window. | implemented (headless) |
| WK17 | In entered Alt window mode, each unclaimed arrow press (including auto-repeat) adds a fixed impulse to its axis's surviving velocity, clamped independently to a maximum. Movement and resize each use their own constant deceleration (S14), integrated until zero including the final partial tick, with no restarted position animation. One default impulse travels v²/(2a) = 92.29 logical px. Hints/cycles retain physical-press-only behavior. | implemented (headless) |
| WK18 | Arrows target the hint/Tab-selected window if selected this hold, otherwise the currently focused window (a focused widget represents its app). Different windows retain independent coasts. Left/Right change x, Up/Down change y; diagonals combine independent axes. An arrow on fullscreen explicitly exits it and waits for restored geometry before applying queued impulses. A later explicit cycle stops that window's coast before its lifecycle/placement action. Closing/unmapping or having no output discards its motion safely. | implemented (headless) |
| WK19 | Moving windows follow their center's zone and scale live (L5/L8), even with Alt held: L31 scale pinning belongs only to drags, and arrows clear an old pin. Geometry/scale targets enter the desktop model; hints' visual declutter never enters motion coordinates. | implemented (headless) |
| WK20 | Keyboard pushes and drag coasts (L32) never bounce ordinary windows. At an exposed top/bottom workarea edge, motion stops with 100 logical pt of the live scaled content footprint visible and that axis's velocity zeroed (a footprint shorter than 100 pt stays wholly visible). At an exposed side, outward travel widgetizes once onto the matching rail when the scaled footprint first touches its inner boundary, or the workarea boundary if farther inward; WG22 morphs from the contact image. All app motion axes end at that handoff. Existing drops already beyond contact may move inward without docking; an outward push morphs from their existing image without snapping inward. Stationary drops are unchanged. WG1-ineligible windows stop at side contact without changing form. A touching output at the center's orthogonal coordinate permits passage when the workarea reaches the physical seam; crossing transfers the global center, velocity and existing focus. Gaps and reserved edges remain closed. WK21 resize recovery retains its fully contained padded bounds; existing widgets retain WK23. | implemented (headless) |
| WK21 | After mode entry, Ctrl+Right widens, Ctrl+Left narrows, Ctrl+Up grows height, Ctrl+Down shrinks height, with independent inertial size axes. Resizing keeps the window's center, zone and scale (L20) until its scaled footprint exceeds an exposed boundary, then pushes the center back inside the fully contained WP7 padded bounds (and outside exposed rails), within client pixel rounding, including asynchronous client commits. Sizes respect the app minimum and maximum and screen/workarea minus padding (integer size caps round down); an app minimum larger than that limit takes precedence. Movement and resize coasts can coexist. Ctrl+arrows consume input but do nothing for widgets. | implemented (headless) |
| WK22 | Alt release commits keyboard movement/resize and lets existing velocity coast to zero; it stops adding repeats. Esc while still in mode stops inertia/repeats and glides each arrow-touched window back to its geometry and form captured at Alt-down, including output, size, fullscreen, widget rail, scale pin and zone memories (WG14/L27). The cancelled chord remains captured until Alt release. A later hold starts a new origin. | implemented (headless) |
| WK23 | Widgets coast vertically along their current rail and stop at their top/bottom workarea limits, keeping their wider widget inset. No push or coast rebounds. Left/Right transfers to the indicated rail with the existing glide, preserving height and widget form; pressing toward the current rail leaves it there. It never opens the window. Tab can select a widget without restoring it (WK10). | implemented (headless) |
| WK24 | `scottland/key_impulse` (335 px/s) and `scottland/key_friction` (608 px/s²) control constant-deceleration keyboard movement and drag release coasts (L32). Independent `scottland/resize_impulse` (335 px/s) and `scottland/resize_friction` (608 px/s²) control inertial Ctrl+arrow resizing. `scottland/key_max_velocity` (6000 px/s) caps both. The Window mode tab edits each impulse/deceleration pair through a position-over-time graph (S14). Repeats use the keyboard's configured delay/rate, independently for held arrows, and stop on key/Alt release or cancel. Exact focused-surface claims precede arrows (KL7); quick Alt+arrow and Ctrl-first chords keep existing app/desktop routing. A pointer/touch move or resize takes over motion without enabling hints (L31). | implemented (headless) |
| WK25 | Hints follow the desktop's text size and interface font, as Vimarchy follows Omarchy's: the badge's minimum (72 px), maximum (132 px) and proportional size (0.34 of the window's shorter side) are multiplied by the text scaling factor (GTK's `text-scaling-factor`, which `omarchy display text size` sets on Omarchy), and the letters use the interface font (`font-name`'s family). The color-scheme helper records both in the palette file (`text_scale`, `font_family`) and follows changes live. | implemented (headless) |
| WK26 | In Window mode, every widget (the default card or a third-party widget, expanded or collapsed) has its hint outside its center-facing edge: right of a left-rail widget, left of a right-rail widget, vertically centered on its drawn frame. The circle overlaps by 15% of its diameter. For large text on short widgets, overlap reduces so the arc entering the widget spans at most the middle 60% of its height, leaving the upper inward count-badge corner clear. WK30/WK31 retain a consistent 48 px × desktop text scale circle; WK14 widget tint/dye/goo remain unchanged. The exterior circle has an opaque theme background under its usual 21% hint-color fill so wallpaper cannot defeat letter contrast; window circles keep their existing transparency. Colliding widget hints declutter with 6 logical px clearance; widgets move only vertically as a temporary visual transform and keep their horizontal attachment, while windows use WK31's exposure solver. Both the hints and widget frames stay vertically on screen; horizontal screen clamping takes precedence if an unusually wide widget leaves no room. Geometry, zone memories and rail attachment are never changed. Release/Esc clears the hints and restores temporary displacement. | implemented (headless) |
| WK27 | While any window or widget has inertial movement or resize velocity (keyboard or drag release), pause the declutter solver globally and hold its current visual offsets. Hints still follow real geometry. As soon as all velocity reaches zero, recompute from the resting geometry and interpolate to the new offsets, with no jump or geometry/memory change. | implemented (headless) |
| WK28 | Hint circles pop in when window mode starts: each scales up from nothing with a short springy overshoot (and pops out quickly when the mode ends), and each circle has its own goo: it is a round goo source dyed its hint color, so it is part of the one liquid, joining the goo of the window or widget it touches (a widget's exterior hint visibly connects to the widget). With the goo off, circles get the fallback halo ring. Within the GO10 cost budget. (Mike, 2026-10-02) | implemented (headless); motion, shared liquid, fallback, reduced motion and paired GO10 checks below |
| WK29 | When a hint cycle (keyboard, window mode) moves a window to its next place, it comes to rest with a small elastic overshoot: an underdamped spring passes the target once in position and scale, then settles without wobble in 300 ms. `scottland/cycle_overshoot` is the peak percentage of the move (default 3%, range 0–10%; zero retains the original 260 ms position/180 ms scale motion). Geometry, zone, target scale and memories stay at the destination; drawn scale follows its own spring, never the intermediate position's zone. The live scaled content footprint is constrained to its output, including shared seams, without correcting existing off-screen memories or oversized endpoints; scale stays at least 5%. These limits may reduce overshoot. Keyboard widget opens use this window placement motion; widget morphs, rail glides, drops, drags, coasts and pointer/IPC opens retain their own motion. The cycle-overshoot setting is available through plugin metadata, `scottland-ctl` and the Window mode Settings tab. (Mike, 2026-10-02) | implemented (headless) |
| WK30 | Widgets' hint circles keep a consistent 48 logical px diameter (2/3 of WK5's ordinary 72 px minimum), multiplied by desktop text scale, independent of expanded/collapsed form or client dimensions (WK31). Pressing a widget's hint first selects the widget (like a window that isn't selected yet); only a further press cycles it (to the center, etc.). Supersedes widget behavior in WK6-WK11/WK26 where they differ. (Mike, 2026-10-02) | implemented (headless); motion, shared liquid, fallback, reduced motion and paired GO10 checks below |
| WK31 | Every ordinary window hint is wholly inside its own screen-visible window region (the displayed rectangle minus the union of foreground rectangles). The circle uses WK5/WK25 proportional size when feasible, shrinking only as needed to a readable 32 px × desktop text scale floor; the WK28 pop fits too. If a rear window cannot fit its hint, temporarily move windows by the least bounded distance needed to expose a circle; an already adequate visible strip needs no movement. A wholly covered window is revealed by movement, including movement of a foreground window when the rear cannot expose itself. The hint goes at the most open spot after movement and never uses an exterior edge attachment; only widgets use WK26 exterior hints. During the opening animation, a window hint waits until its circle is wholly visible; a changed attachment eases when its path remains visible. Scene stacking, not hint assignment order, determines occlusion. Geometry, scale and memories do not change, and release/Esc restores visual offsets. WK27 pause and WK28 goo/pop still apply. (Mike, 2026-10-02) | implemented (headless); validation below |
| WP1 | Each open window remembers independent center, left/right periphery, and left/right rail positions. Centers are normalized to screen dimensions and applied to the destination screen, including when a widget moved to a screen with a different scale. Initial placement, real drag drops, finished keyboard coasts, and cycle placements establish memories; visual animation does not. Closing forgets the record; a marked Scottland reload hands it to the new plugin in the atomic desktop model handover. | implemented (headless) |
| WP2 | A remembered destination wins exactly, even when occupied. Only pixel rounding is applied. This is predictable placement, not automatic rearrangement of existing windows. | implemented (headless) |
| WP3 | Side choice uses the most recently visited side with a periphery or rail memory. With neither, choose the side with the largest contiguous free opening (blocked intervals are unioned); when openings differ by no more than 5% of screen height, choose the nearer side. Exact horizontal ties choose right. | implemented (headless) |
| WP4 | Without a memory, use the single pure `place_rectangle` routine: minimize summed rectangle intersection area inside the destination region, then prefer the spot nearest the current center. Within 1% of the incoming rectangle's area counts as about equal. Side-zone ties prefer nearby vertical positions. The entire periphery is eligible, with its natural scaled footprint re-evaluated at the landing position; rail placement is refined to the actual widget footprint when it maps. | implemented (headless) |
| WP5 | Explicit zone cycling, card opens and presenting a side window clear an Alt-drag scale pin. Center destinations keep the original window size and are always at 100%. Oversized content stays full size. WG17 card clicks use the same placement routine: remembered center first, otherwise the nearest least-overlapping center spot rather than unconditional screen-middle placement. Presenting a side window uses it too (L30). | implemented (headless) |
| WP6 | The placement routine and force solver have no Wayfire dependencies and have standalone unit tests. The placement routine is reusable for any rectangle/region contention; it never resizes an incoming rectangle or moves obstacles. | implemented (headless) |
| WP7 | Windows Scottland places (zone cycling, card opens: the placement routine) keep off the screen's edges by the halo's width plus 5 pt (about 16 pt), in each dimension where the window fits; one larger than the screen in a dimension is not padded there. Widgets keep their own, wider rail inset. Remembered spots (WP2) and the user's own drops are kept exactly. | implemented (headless) |

## Cycle rule (WK7)

An unselected window or widget first selects without moving. Already selected windows and
widgets (including Tab selections) skip that step. A widget’s next slow hint opens center.
Double-tap requests the widget step directly.

| Start zone | Repeating slow presses: other zones, then back (repeat) |
|---|---|
| Center | periphery → widget → center → … |
| Periphery | center → widget → periphery → … |
| Widget | center → periphery → widget → … |

## Decisions at unspecified edges

- WK31, tenets 2 and 4: preserve each window's visual identity within its own visible
  content. Moving the drawn rectangle is the cheapest concession, so search for the
  nearest circle-sized opening before reducing the proportional circle. A fully covered
  window forces movement of a foreground window if moving the rear cannot expose it.
  No ordinary window hint may leave its visible window, even during animation; defer its
  pop until movement makes room. Keep a stable label point for an unchanged stack.
  Recognition keeps widget circles at 48 px × text scale across client sizes and forms.


- WK29, tenets 2 and 4: animate only the drawn position and scale, preserving the destination,
  client size and spatial memories. `cycle-spring.hpp` evaluates an underdamped unit step through
  its first peak at 180 ms, followed by a tangent-continuous cubic return to exact rest at 300 ms.
  The tail suppresses further oscillations. Position and scale use separate endpoint values;
  the screen constraint uses their combined live footprint. A 3% peak gives a small visible settle
  with only 40 ms added to the existing placement duration. A new cycle starts from the drawn
  frame; arrow/drag takeover and docking stop its transient animation through the existing glide
  cancellation path. The widget shape/fade animation is not changed.

- WK26, tenet 2 (recognition): use 15% circle overlap to visibly attach a hint without covering
  the widget's contents. Restrict the entering arc on short widgets at large text sizes to keep
  the upper count corner free. Use the theme background under an exterior circle's tint so
  recognition does not depend on the wallpaper. Tenet 4 (cheapest change): keep the existing temporary visual
  declutter, constrained vertically for widgets, without changing their geometry or memories.

- Tenet 2 (predictability): single presses act immediately; the rapid second complete hint requests
  the rail. Continue the original loop after its widget step. Prefix-free hints keep the same
  complete-label meaning at every capacity, so an `aa` prefix cannot itself trigger a shortcut.

- Tenet 4 (concede as little as possible): surface layers keep exactly their claimed chords, even
  during hints. Unclaimed navigation remains available; a claimed press before entry bypasses the hold.
- Tenet 2 (recognition): hue position follows the retained assignment slot, not the number of
  open windows. A golden-ratio sequence distributes slots over the opposite 160° arc without
  a fixed palette or recoloring existing letters. Adjacent hues jump about 61° or 99°.
  Dark colors start at HSL saturation 0.78 / lightness 0.70; light colors at 0.72 / 0.34.
  Lightness moves toward the scheme's contrasting pole until the minimum contrast reaches 3.1:1
  (rounding headroom above 3:1), checking both background and background with 5% foreground
  mixed in as a typical surface, under the 7% window and 21% badge tints. Unusual mid-tone
  backgrounds may need the opposite pole. This targets theme-following surfaces; arbitrary app
  content behind a translucent badge can differ. Missing/invalid palette values fall back to the
  compositor scheme/accent and Scottland's neutral background/foreground.
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
- Tenet 3 (position means priority): an adjoining output is an open passage where the window center meets its edge; keyboard motion transfers in layout coordinates. Widgets retain their explicit rail interaction.
- Tenet 4 (concede as little as possible): keyboard resize recovers overflow by moving the center, retaining the requested content size. This correction is scoped to keyboard resize, including late commits, and excludes mouse/touch drops.
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

WK29 coverage: `tests/cycle-overshoot-test.sh` takes a fresh `SCOTTLAND_HEADLESS_DIR` under
`build/`, starts two isolated outputs, drives real Super drags to establish memories and real
Alt/hint presses to cycle, then stops that session. Its 79 checks sample position and scale,
one target crossing, exact settlement, zero's original easing/tolerance, both horizontal edges,
top/bottom edges, maximum overshoot, minimum scale, stable output/zone/target scale and rapid
double-tap handoff to the existing widget morph. It checks widget hint restoration too.
JSON samples, original screenshots and a cropped frame strip with a target-center guide remain
beside the session directory. `tests/cycle-spring-unit.cpp` checks four amplitudes over 10,000
samples each: bounded first peak, one crossing, monotone return, exact endpoints and continuous
velocity at both joins. Compile it with `-Icore/plugin/src` into `build/`.

The branch-specific records below describe their original code, before this combined window-mode build.
They are historical evidence; the final combined matrix is recorded separately.


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

## Hint appearance validation (2026-10-01)

`tests/hint-style-test.sh` uses an isolated headless widget session on plumbus, real stipc Alt
holds and Super drags, theme-following solid GTK surfaces of different sizes, a scaled side
window and a real default card. It saves light/dark screenshots and checks rendered pixel colors
for surface tint, badge fill and border, min/max/displayed sizing, complementary/adjacent hues,
contrast, live recoloring of letters/surfaces/outer halos while held, stable retained colors,
release/Esc cleanup, fullscreen and the minimum supported 5% window scale.
The unit suite checks 676 colors for each of five palettes (including a mid-tone background),
contrast through the stacked opacities, distinct hues, and different-sized badge decluttering.
The windowing suite also saves `double-hints.png` for the 27-window prefix-free labels.

Final renderer commit `2aca04c`, fixture commit `b4deede`: **641 passed, 0 failed**.
All final suite runs exited zero, including the appearance retry. The plugin binary's SHA-256
was identical across the fixture-only updates, so the full matrix exercises the renderer being
pushed. Every headless compositor started after its build.

| Suite | Result |
|---|---|
| Windowing unit (palette and mixed badge sizes included) | 59 passed |
| Appearance, real Alt + screenshot pixels | 50 passed |
| Windowing end-to-end | 73 passed |
| Key layers | 59 passed |
| Widgets (collapse/input/attention/fullscreen included) | 146 passed |
| Model seed 271828, 50 operations | 95 passed |
| Model seed 104729, 50 operations, legacy D-Bus | 100 passed |
| Focused model regressions | 7 passed |
| Attention / launcher / widget-bus units | 5 / 17 / 16 passed |
| Config concurrency | 5 rounds passed, 20 simultaneous builds each |
| Notification focus hooks | 3 passed |
| Present | 6 passed |

The appearance fixture initially raced frame attachment. Its minimum-scale case also inherited
the session's custom scale curve and kept the halo swollen under the pointer. It now waits for a
frame, clears the curve only in its private test session, moves the pointer away, waits for the
halo to rest, and records the actual frame/scale. The final 5% case shows a 72 px badge and a full
2 px rim even with a resting halo thinner than 1 px. Earlier attempts remain in the logs.

Deployment used `SCOTTLAND_DEPLOY_DIR=Projects/scottland-hint-style tests/deploy.sh plumbus
--tests-only`, `TMPDIR=~/.cache/scottland-test-tmp` and
`SCOTTLAND_HEADLESS_DIR=$XDG_RUNTIME_DIR/scottland-headless-hint-style`.
Logs are on plumbus in `~/.cache/scottland-hint-style-results/final-2aca04c`, with reviewed copies
and a final count manifest in this worktree's `build/hint-style-results/`.

Reviewed screenshots in this worktree (originals are beside the headless directory on plumbus):

- `build/hint-style-artifacts/dark-hints.png`
- `build/hint-style-artifacts/light-hints.png`
- `build/hint-style-artifacts/light-accent-file-only-held.png`
- `build/hint-style-artifacts/fullscreen-hints.png`
- `build/hint-style-artifacts/minimum-window-scale-hints.png`
- `build/hint-style-windowing-artifacts/double-hints.png`

The light/dark shots show the min/max badges, scaled windows, surface tint, rounded rims and card
treatment. The accent-only shot shows the letter and outer halo dye changing while Alt is held;
the fullscreen and minimum-scale shots show both special border paths. The double-letter shot
checks text proportion and displacement; contrast targets theme-following surfaces, as specified
above, rather than arbitrary application colors. All isolated sessions and task tmux runners
were stopped; no Wayfire remained using this test directory. Physical verification remains
pending. No live session on osanwe or real screen on plumbus was installed into, reloaded or used.
## Window mode cycles validation (2026-10-01)

Code and test commit `8cdae8d` implements WK3, WK6–WK9 and WK15–WK16 above. The setting is
`scottland/window_double_tap_delay` (300 ms default). Cycle order has a standalone pure function;
the controller receives monotonic press timestamps, so timing boundaries are deterministic in units.

| Suite on plumbus | Result |
|---|---|
| `tests/windowing-unit.sh` | 57 passed, 0 failed |
| `tests/windowing-test.sh` | 84 passed, 0 failed |
| `tests/widgets-test.sh` | 146 passed, 0 failed; no runner errors |

Real stipc input covers each starting zone’s full loop, repeated periphery loops, selected and
unselected windows, switching hints, double-taps from both ordinary zones and from a widget,
a widget no-op preserving its view identity and memories, repeat suppression, slower presses,
live interval changes, and complete two-letter hints. Existing fullscreen exit/queued actions,
reload, normalized memories, contention and WP7 padding checks also pass. Headless hints were
captured and visually inspected. Physical-screen verification remains pending.

Deployment used `SCOTTLAND_DEPLOY_DIR=Projects/scottland-cycles tests/deploy.sh plumbus --tests-only`,
`TMPDIR=$HOME/.cache/scottland-test-tmp`, and
`SCOTTLAND_HEADLESS_DIR=$XDG_RUNTIME_DIR/scottland-headless-cycles`, with private D-Bus and the
checkout’s own helpers. Source hashes matched this commit. Each compositor started after the
build; only isolated test sessions were used. Logs are retained in
`~/.cache/scottland-cycles-results/` on plumbus. Neither osanwe’s live session nor plumbus’s
physical screen was touched. All isolated sessions were stopped after testing.

## Inertia validation (2026-10-01)

Final code: `bd4ca0f` (with `ec0a1e9` integration and `a61ba7c` independent math).
All 16 suites below exited zero on plumbus: **717 checks passed, zero failed**.
Config concurrency counts five rounds, each containing 20 simultaneous config builds.

| Suite | Passed |
|---|---:|
| `tests/inertia-unit.sh` | 28 |
| `tests/inertia-test.sh` (real stipc input) | 43 |
| `tests/windowing-unit.sh` | 41 |
| `tests/windowing-test.sh` | 74 |
| `tests/key-layers-test.sh` | 59 |
| `tests/widgets-test.sh` | 146 |
| `tests/widget-morph-test.sh` | 76 |
| `tests/state-model-test.sh 271828 50` | 95 |
| `tests/state-model-test.sh 104729 50`, legacy D-Bus | 100 |
| `tests/state-regressions-test.sh` | 7 |
| `tests/upgrade-test.sh` (archived legacy main `ea1d0f4`) | 2 |
| Attention / launcher / widget-bus units | 5 / 17 / 16 |
| `tests/build-config-test.sh` | 5 rounds |
| `tests/omarchy-focus-test.sh` | 3 |

The 43 motion checks cover a default impulse's analytic travel (within native pixel rounding),
accumulation with repeated physical presses and timed held-key repeats, diagonal axes, stops at
padding and rails, absence of bounce/widgetization, and live center-based scale samples. Resize
checks cover all four directions, late client commits, strict screen-minus-padding caps, app
minimum sizes and settled odd sizes. Esc checks include movement, resize, fullscreen, widget
rail changes, hint-opened widgets, and resize followed by a dock cycle and widget movement.
Quick Alt+arrow and exact KL7 movement/resize claims reach the app; selected and current-focus
targets, live settings and Ctrl's widget no-op are independently checked.

The maximum client size cap rounds down before requests: rounding up could exceed padding by a
pixel and shift the next resize's anchor through odd-size rounding. Commit and tick centering
both use L20's pixel rounding, with the anchor retained until 300 ms after the latest client
geometry commit. Cancellation's settling resource leaves the existing glide/scale renderer intact.

Deployment used `SCOTTLAND_DEPLOY_DIR=Projects/scottland-inertia tests/deploy.sh plumbus
--tests-only`, with `TMPDIR=$HOME/.cache/scottland-test-tmp` and
`SCOTTLAND_HEADLESS_DIR=$XDG_RUNTIME_DIR/scottland-headless-inertia`. Every ordinary test
compositor started after the final build, using checkout-local helpers and private D-Bus.
The legacy upgrade used a `git archive` snapshot under this checkout's `build/legacy-upgrade`,
with current XML metadata registering the new options and the original legacy plugin/helpers;
no other checkout was modified. The older compositor was deliberately replaced by this build
under open windows, and its surviving launch identity and subsequent badge routing passed.

Final logs and exit/pass counts are in
`~/.cache/scottland-inertia-results/final-bd4ca0f/` on plumbus. Inertia screenshots and scale
samples are in `$XDG_RUNTIME_DIR/scottland-headless-inertia.inertia-artifacts/`; rail-boundary,
maximum-resize and restored-widget screenshots were inspected. All isolated sessions were
stopped. No live session on osanwe or physical screen on plumbus was used or reloaded.
Statuses remain **implemented (headless)**; physical verification is intentionally not claimed.

## Combined window mode validation (2026-10-01)

At this validation `window-mode` combined integrate's goo (then off by default; on by default since 2026-10-01), hint styling and O5 shortcut rule
with `hint-cycles` and `inertial-keys`. Code under test: `f9568ec`; the subsequent validation
commit changes documentation only. Integrate's WK1–WK14 keep their IDs: WK6–WK9 now express
the start-relative loops and skipped redundant select. WK15–WK16 cover double-tap recognition;
the inertia branch's eight rules are WK17–WK24, in their original order: impulses, targeting,
live scale, keyboard boundaries, resize, commit/cancel, widgets, settings/input routing.

WK20 now stops vertical window motion with 100 logical pt visible and morphs outward-moving
windows into rail widgets at side contact. Tenets 2 (recognition) and 3 (position means priority)
decide contact: the live scaled footprint reaches the inner rail boundary, not the center or an
invisible pointer. Tenet 2 also keeps a footprint shorter than 100 pt wholly visible. WG1's dialog
exclusion remains: an ineligible window stops on contact. Existing widgets stop at WK23's rail limits. Touching output edges permit passage, preserving global center,
velocity and existing focus. Esc restores the starting output too. Keyboard resize (WK21)
retains its center until the scaled footprint overflows, then pushes it inside, including late
client commits. Mouse/touch drops and exact remembered cycle destinations keep their own rules.

All 17 suites have successful runs on plumbus: **893 checks**, counting five config-concurrency
rounds and the goo-model aggregate as one check. The duplicate model confirmation is not added
to that total. Each suite below exited zero in its successful run.

| Suite | Passed |
|---|---:|
| Windowing unit (palette, mixed badges, start-relative loops and double-taps) | 75 |
| Inertia unit (including boundary stops and axis isolation) | 39 |
| Inertia real input, one output | 61 |
| Inertia real input, two-output crossing, return and Esc | 8 |
| Windowing end-to-end | 84 |
| Hint appearance | 50 |
| Key layers | 59 |
| Widgets, goo off | 146 |
| Widgets, goo on | 146 |
| Widget morph | 76 |
| Model seed 271828, 50 operations | 95 (two successful runs) |
| Focused model regressions | 7 |
| Attention / launcher / widget-bus units | 5 / 17 / 16 |
| Config concurrency | 5 rounds, 20 simultaneous builds each |
| Notification focus hooks | 3 |
| Goo-model unit | 1 aggregate (falloff, bridging, volume, clipping, finite input, curves, swell, fullscreen) |

The first bounce build's two-output return/cancel checks exposed lost keyboard focus on transfer;
`f9568ec` fixes that and explicitly selects the arrow target when a hint skips its select step.
The final matrix's first model attempt stopped at the initial docking audit: the card's diagnostic
render report was revision 7/version 35 while the service was revision 8/version 37. All compared
visible card fields matched. Two unchanged-code reruns passed all 95 checks each; no assertion,
timeout or production code was changed to obtain these passes. The transient diagnostic mismatch's
cause is unconfirmed, and the original failure observations/log are retained alongside the reruns.

Deployment used `SCOTTLAND_DEPLOY_DIR=Projects/scottland-wm tests/deploy.sh plumbus --tests-only`,
`TMPDIR=$HOME/.cache/scottland-test-tmp` and
`SCOTTLAND_HEADLESS_DIR=$XDG_RUNTIME_DIR/scottland-headless-wm`. Plugin and test source hashes
matched the worktree. Every compositor started after the build, with checkout-local helpers and
private widget D-Bus. Marked reloads were exercised only within the isolated test sessions.
All isolated runtimes were stopped. No other checkout or live desktop was modified, and plumbus's
physical screen was not used. Status remains **implemented (headless)**; physical verification
is not claimed.

Logs, original exit records and a successful-run manifest are on plumbus in
`~/.cache/scottland-window-mode-results/final-f9568ec/`, with a local copy under
`build/window-mode-results/final-f9568ec/`. Reviewed screenshots are under
`build/window-mode-artifacts/`: `inertia/rail-boundary.png`,
`two-output/two-output-crossing-restored.png`, `windowing/double-hints.png`,
`appearance/dark-hints.png` and `appearance/minimum-window-scale-hints.png`.


Goo-default follow-up (2026-10-01): WK14 uses the shared goo source dye with immediate palette
replacement; the 2 px goo rim built then was removed (Mike: window mode just tints the goo; goo.md).
Hint styling passed 51 checks with the default goo, 51 with the fallback halo, and 51 with
GLES 2/packed goo. Windowing passed 84 per mode; inertia passed 61 single-output plus
8 two-output checks per mode. See [goo-default validation](goo.md#goo-default-validation-2026-10-01).

## WK26 validation (2026-10-02, osanwe headless)

Based on `e63ad97`, using only this checkout's plugin and helpers. Every run had its own
`SCOTTLAND_HEADLESS_DIR` under `build/wk26/`; screenshots, sampled geometry and logs remain
there. Sessions were stopped and their directories removed. Neither the main checkout nor
`wayland-1` was modified; this is headless validation, not physical-display verification.

| Suite | Result |
|---|---|
| Windowing unit suite | **83 passed**; includes stacked mixed-size rail hints at both screen ends, free-window collisions and a window pinned to a screen boundary |
| `tests/widget-hints-test.sh`, goo on/off | **167 passed each**; real Alt holds, Super+M and pointer drags; cards and a third-party widget; both rails, expanded/collapsed, stacked hints, release/Esc, unchanged placement, live 1.5×/3× text sizes and screen ends |
| `tests/hint-style-test.sh`, goo on/off | **53 passed each**; existing palette, tint, goo/rim, fullscreen and size checks, plus exterior-circle fill pixels and letter contrast in both themes |
| `tests/windowing-test.sh` | **84 passed**, including cycles, input ownership, fullscreen, geometry/memories, reload and prefix-free hints |
| `tests/widgets-test.sh` | **146 passed**, including **43 input regressions** |
| Shortcut fixture using `TMPDIR` under `build/` | **14 passed**; fixture storage now honors the caller's temporary directory |

The final solver resolves residual rail/window collisions vertically, including when a window
has already reached the screen's horizontal clamp. Window-only declutter remains unchanged.
Screenshots were inspected for attachment, readable backgrounds and stacked/edge placement.

A further **167-check** run staged only the card QML from `badges-fixedsize` at `c3e928f`
in `build/wk26/count-corner-fixture/`, without merging or modifying that branch. Its relocated
22-pixel-high rounded count badge stays clear of the exterior hint at all tested text sizes;
the nearest rounded end is 11 pixels inward/down from the frame's upper inward corner.
The compatibility screenshots are in `build/wk26/count-corner-integration.widget-hints-artifacts/`.
The new suite accepts `SCOTTLAND_WIDGET_PATH` for this isolated fixture override.

## WK29 validation (2026-10-02, osanwe headless)

Only `scottland-overshoot` and its fresh headless sessions were used. All session directories,
screenshots, frame strips, sampled JSON, build logs and source/library hashes are under
`build/wk29/`. Each session started after its tested build. No private `XDG_RUNTIME_DIR`, personal
config, dev install, physical display, live widget service or live checkout was used.

The final spring suite (`spring-verified`) passed **79 checks**. Both free-space directions pass
their target once, with measured peaks about 2.9–3.0% (the configured peak is 3%), then reach exact
position and target scale by the post-340 ms samples. Screen constraints reduce the excursion at
the edges, including the seam to the second output. Zero retains the old easing and its existing
0.0005 scale tolerance. The maximum 10% setting stays bounded at the minimum 5% scale. The rapid
double-tap check caught and now guards the ordering of docking's snapshot and spring cancellation:
the existing widget transition captures the drawn frame before stopping its glide. All four pure
spring amplitude cases passed, as did XML/Python/shell syntax checks and `git diff --check`.

The requested broad suites were run. The final-source serial matrix is **not clean**:

| Suite | Result |
|---|---|
| Windowing | 83 passed, 1 failed: the app was not yet hidden at the slow-cycle widget-arrival check; subsequent cycles and memories passed |
| Hint appearance | 53 passed, 0 failed |
| Inertia | 61 passed on one output; 7 passed, 1 failed on two outputs (sampled monotonicity at the seam; travel, transfer, return and Esc passed) |
| Widget morph | 182 passed, 4 failed: Esc-return intermediate-frame/field sampling and goo collapse/reversal field sampling; cycle and double-tap paths passed |

Earlier runs include 84/84 windowing and 61+8 inertia passes, but also an IPC resource exception,
an Esc-resize failure and varying morph sampling failures. Their logs are retained, not replaced
by the successful runs. A separate control using this same build with `cycle_overshoot=0` also
reproduced missing intermediate goo/crossfade samples; the new spring is not required for those
failures. That does not establish the cause of every failure or prove the complete matrix green.
No broad-suite assertions or timing tolerances were changed. Physical verification and S14's
Window mode tab remain pending. Inspect `spring-verified.cycle-artifacts/frame-strip.png` (cyan
line = target center) and the original full-output captures beside it.

## WK28: pop and circle liquid (2026-10-02)

Tenet 2 (recognition, not recall) guides the motion: preserve the assignment and its
attachment while it becomes visible. A circle grows for 200 ms with one approximately
5% overshoot and no oscillation; its texture, including the letter, fades in with the
scale. Release/Esc shrinks it over 100 ms. Switching between a window and its widget
retains the hint node and eases its anchor and diameter over 160 ms. Existing declutter
transforms and WK27's inertia pause remain the position authority. The desktop's
`org.gnome.desktop.interface enable-animations=false` is carried as `reduced_motion`
in the session palette and removes the pop, relocation and declutter easing. The
palette watcher follows changes live; absent that preference, animation is enabled.

Each visible circle contributes its animated center, radius, thickness and hint dye
as a foreground round source to the existing screen liquid. Its film over app content
uses its own thickness, even with window overlap film disabled. Exterior circles join
the widget's source naturally. They are visual-only sources: input excludes them so
letters, circle interiors and their liquid never acquire grab or resize ownership.
Goo disabled or unavailable uses a soft dyed halo with a 2 px outside edge. The badge's
settled fill, type size, palette contrast and logical sizing stay the same.

Textures are cached at their target raster size and scaled during motion. Settled
hint and displacement nodes no longer cause unconditional damage every hint tick.
Small closed circle bands use the packed path’s existing displacement damping on
both GPU paths while circles are present, so a constant-height wave cannot keep a
ring awake forever. The simulation still waits for visible wave energy to settle. When every emitting
source is hinted, the draw uses WK14's immediate contribution-weighted hint dye;
convergence of the hidden simulation dye does not keep the output awake. Leaving
hints changes the sources and wakes ordinary dye simulation again.

`tests/hint-pop-test.sh` uses real Alt holds and retains entry/exit frame strips,
settled goo/fallback screenshots, JSON geometry/scale samples, zero-window-film and
reduced-motion evidence. `tests/goo-bench.sh REPO STATE_DIR 10 --hints` keeps all four
original GO10 cases and adds held-hint attention and settled cases using the same
six-window/two-widget fixture. All state directories, temporary files and artifacts
for this change are under `build/wk28-results/`; no live session or installed build
is changed. This is headless verification, not physical-display verification.

Validation uses fresh isolated compositors on osanwe (Xe) and plumbus (RX 580),
started after the tested build; all owned sessions are stopped and their state
directories removed. No physical display, live widget service or live checkout is
used. The plumbus test-only checkout is `Projects/scottland-wk28-hintgoo-tests`.

| Check | Result |
|---|---|
| Pop, one overshoot, exit shrink, circle dye, widget connection, zero window film, reduced motion; Xe normal / packed GLES 2 | 19 / 19 passed |
| Unsupported GPU: animated fallback circles | 16 passed |
| Same pop/liquid checks on RX 580 | 19 passed |
| Hint style, dark/light contrast, live palette, fullscreen, 5% window scale; goo on / off | 53 / 53 passed on both GPUs |
| Widget hints: both rails, expanded/collapsed, custom widgets, text scaling and declutter | 167 passed on both GPUs |
| Windowing input, cycles, stable assignments, fullscreen, reload and overflow labels | 84 passed on both GPUs |
| Goo input, settings, palette, fallback and sleep on final Xe build | 46 passed |
| CPU goo model, including round islands, zero window film and pop thickness | all assertions passed |

The tiny-window style probe now samples outside the combined circle/window
silhouette: a 72 px circle can enclose a 5%-scale window and obscure the former
probe at its top edge. Its color threshold is unchanged. Initial failures and
iterations are retained: one join probe crossed the antialiased inner edge; slow
PNG capture missed short animation intervals, so motion is sampled independently
of screenshot readback; an initial compositor startup outran first-use shader
compilation; and the first held-hint benchmark exposed the closed-ring sleep issue.

Evidence is in `build/wk28-results/`. `pop-delivery.hint-pop-artifacts/` contains
inspected `goo-enter-strip.png`, `goo-exit-strip.png`, `goo-settled.png`,
`fallback-enter-strip.png`, `fallback-exit-strip.png`, `fallback-settled.png`,
`goo-zero-window-film.png` and `reduced-motion.png`, plus unscaled individual
frames and independently sampled motion. `pop-wave-packed.hint-pop-artifacts/`
holds the packed-path checks. The initial and final benchmark logs are retained;
GO10's cost and the additional held-hint workload are recorded in [goo.md](goo.md#wk28-hint-circle-cost-2026-10-02).

## WK20 no-bounce edges (2026-10-02, isolated headless)

Keyboard pushes and L32 drag coasts share the movement-boundary handler and a constant-deceleration
integrator with no restitution. Resize containment remains in its own WK21 handler. Side contact ends the app's
motion and invokes WG22 once, retaining the app image through widget startup. Esc restores the
Alt-down window even during that startup/morph. Existing widgets also stop at WK23 rail limits.

Validation and retained artifact paths: [drag-coast.md](drag-coast.md#wk20-edge-replacement-2026-10-02).
Only isolated headless sessions were used, with shipped config and the normal runtime directory;
no live session or live widget service was touched. WK20 remains **implemented (headless)**,
not physical-screen verified.

## WG23 / WK30 validation (2026-10-02, isolated headless)

`tests/hint-style-test.sh` passed **53 checks** with the exterior widget badge at 2/3 of window badge size, preserving the same minimum, maximum and 1.5× text scaling ratio. `tests/widget-hints-test.sh` passed **170 checks**, including real Alt input: a widget’s first hint selects in place, its next slow hint opens center, and Tab selection makes the next hint open immediately. `tests/windowing-test.sh` passed **84 checks**, including the widget-start loop, double-tap behavior and cycle state. `tests/windowing-unit.sh` passed **95 checks**. Screenshots and logs are under `build/elastic-final/`; only isolated headless sessions were used.

## WK31 algorithm research (2026-10-02)

Considered [Mapbox polylabel](https://github.com/mapbox/polylabel), a priority-queue
branch-and-bound search for a polygon's largest inscribed circle; a
[largest empty rectangle](https://drops.dagstuhl.de/storage/00lipics/lipics-vol189-socg2021/LIPIcs.SoCG.2021.24/LIPIcs.SoCG.2021.24.pdf),
which optimizes rectangular area rather than this circular badge; and a raster distance
transform, which would allocate resolution-dependent masks and approximate small slivers.
Chosen: polylabel's 1-Lipschitz clearance bound, evaluated analytically against an output-clipped
rectangle and foreground rectangle union. Positive clearance is exactly the available circle
radius; this avoids polygon ring construction, handles disconnected pieces and unions, and
searches to 0.5 logical px. No third-party implementation is copied. Equal probes have a
stable order, with center proximity breaking exact clearance ties.

For window movement, considered [PRISM (Gansner and Hu)](https://www.graphviz.org/documentation/GH10.pdf)
and its [Graphviz integration](https://graphviz.org/docs/attrs/overlap/): they minimize node
overlap, but overlap itself is not WK31's goal. The first implementation minimized whole-window
intersection; Mike's live review corrected the objective. The exposure solver instead searches
circle centers outside foreground rectangles and chooses the least window movement that
contains a proportional badge there. Only if that cannot fit within the output does it search
smaller sizes down to the readable floor. A foreground window can move to expose a fully covered
rear one. Movement is bounded so a badge-sized portion remains on screen; a whole large window
need not fit. Fixed widgets retain vertical-only motion and their exterior circles.
Widgets retain the existing vertical badge solver; their displaced frames and exterior circles
are fixed obstacles for windows. Actual scene order determines foreground occlusion after the
solve. The bridge caches local hint anchors and clearance-limited diameters until the solve
inputs change; pure placement and declutter remain independent of Wayfire.

## WK13 / WK31 exposure validation (2026-10-02, isolated headless)

The initial whole-window overlap solver was superseded after Mike clarified that
window movement serves only to reveal an interior hint circle. The final build
uses the exposure solver above. In Mike's left-strip layout, the rear window
moves roughly 62 logical px to fit its 132 px circle in the exposed strip;
the front window stays put and its hint remains inside it. A completely covered
three-window stack reveals all three proportional, interior circles by moving
foreground surfaces temporarily. Every visible badge's circle pixels were
checked against its own drawn rectangle and all rectangles above it; the
real Alt hold and raise use Wayfire stipc input. A focus raise conceals
potentially covered badges immediately, before the next placement tick.

Only this checkout's build and isolated headless sessions were used. Test
sessions used shipped config and the normal runtime directory; screenshots
and logs are under `build/wk31-followup/`. The session directories were
stopped and removed by the test harness. The live checkout, `wayland-1`,
services and personal config were not changed. Physical-display verification
remains outstanding under D2.

| Suite | Final result |
|---|---|
| `tests/hint-visible-test.sh` | **57 passed** (twice after the focus-transition fix): circle pixel masks, Mike's left strip, fully covered windows, proportional sizes, transient raise, stability, geometry/memory preservation, release/Esc |
| `tests/hint-style-test.sh` | **53 passed**: surface tint, shifted fullscreen rim, 5% displayed scale and WK28 goo/pop |
| `tests/widget-hints-test.sh` | **170 passed**: exterior attachment, consistent WK30 size, widget interactions and text scaling |
| `tests/windowing-test.sh` | **84 passed**: real input, fullscreen, cycles, geometry/memories and overflow hints |
| `tests/windowing-unit.sh` | **108 passed**: least exposure movement, visible clearance, full coverage and bounded placement |

Final screenshots: `build/wk31-followup/visible-fixed.hint-visible-artifacts/mike-left-strip.png`
with its JSON, `fully-hidden.png`, and `stack-held.png`. The corresponding
logs are `visible-fixed.log`, `visible-postfocus-confirm.log`,
`style-postfocus.log`, `widgets-postfocus.log`, `windowing-postfocus.log`,
and `unit-final.log`. No live session was reloaded.
