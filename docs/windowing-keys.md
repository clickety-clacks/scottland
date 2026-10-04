# Window keys and placement

Alt is Scottland's window key. Hold it alone for `scottland/alt_hold_delay` milliseconds
(default 300, range 1–3000) to enter **window mode** and show hints. Quick Alt+Tab belongs to
the center-window switcher (WK32); other quick chords keep their app/desktop routing with no replay or input delay. This is core desktop behavior, independent of integrations.

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
| WK1 | Alt alone enters hints only after the configurable short hold (300 ms default). Any other key or Ctrl, Shift, or Super already held, or pressed before the timeout, cancels eligibility for that entire Alt chord. Quick Alt+letter keeps app behavior; quick Alt+Tab and Alt+Shift+Tab use WK32. Both Alt keys are supported; pressing both before entry is not Alt alone. | implemented (headless) |
| WK2 | After entry, every unclaimed key belongs to Scottland until the last held Alt is released, including Ctrl/Super combinations and unassigned keys. Presses and matching releases are consumed; hints/cycles act once per physical press; arrows add impulses on presses and auto-repeat (WK17). Focused-surface key-layer claims retain ordinary delivery (KL7), including while hints are visible; a claimed press before entry cancels the hold. Alt itself is delivered immediately and its matching release is delivered, so quick app chords have no added delay or synthetic replay. | implemented (headless) |
| WK3 | Alt release exits window mode and removes hints. Esc removes hints, stops any remaining keyboard motion at the user's current result (WK22), and keeps keys captured until Alt release. When `scottland/window_avoidance_always` is off, avoidance offsets ease home on either exit (WK13/WK27); when on, the visual reservation continues outside Window mode. `scottland/hint_avoidance_always` remains a compatibility alias. Explicit cycles and moves remain committed; the next entry starts a new cycle from the current zone, skipping select if already selected. | implemented (Plumbus stipc, 2026-10-03) |
| WK4 | Every mapped top-level window and every widget has a large, click-through compositor hint in session palette colors, attached to the most open visible portion of ordinary windows (WK31); widgets use the exterior attachment in WK26, expanded or collapsed. Dialogs are selectable but retain WG1's protection against widgetizing. | implemented (headless) |
| WK5 | Assignment follows opening order, with `a s d f g h j k l q w e r t y u i o p z x c v b n m`. Each window retains its slot while open, including as a widget and across reload. Closed slots can be reused. As in Vimarchy, beyond 26 slots all labels become prefix-free two-letter hints; the assignment slot remains stable. Badges follow Vimarchy: a circle (in the visible window region per WK31, beside widgets per WK26) sized `clamp(min(displayed width, displayed height) × 0.34, 72, 132)` logical px, 21% hint-color fill, bold uppercase letters at 62% of badge height (46% for multiple letters). WK31 caps ordinary circles to visible clearance and supplies a readable fragment floor. Output scale affects raster resolution, never logical badge size. | implemented (headless) |
| WK6 | A window’s first hint selects, focuses, and raises it only if it is not already selected/focused. If already selected (including by Tab), the first press goes straight to the next zone. A widget’s first hint selects and focuses without opening (WK30); if already selected, its hint goes straight to center. Selecting another hint resets the previous selection’s cycle. | implemented (headless) |
| WK7 | All starting zones follow one start-relative loop: visit the other two zones, toward center first, then return to the start. The start is the window’s zone when cycling begins in this Alt hold (before selecting/opening); it stays fixed until another hint is selected or the hold ends. See the cycle table below. | implemented (headless) |
| WK8 | A periphery-start loop visits center → widget → periphery repeatedly (WK7). | implemented (headless) |
| WK9 | A widget-start loop visits center → periphery → widget repeatedly; selection leaves its first center step for the next slow hint (WK6/WK30). | implemented (headless) |
| WK10 | Tab and Shift+Tab select the next/previous window or widget in hint order, wrapping. Tab focuses a widget without opening it; its hint opens it. F4 closes the selected window and its widget through normal linked lifecycle, preserving save-confirmation behavior. | implemented (headless) |
| WK11 | Super+Alt resize (L20) and Alt with Ctrl/Shift held first never show hints. Starting a drag suppresses hints for that chord, even after drop; unless `scottland/window_avoidance_always` is on, window avoidance then eases away (WK13/WK27). Shift during a drag pins scale (L31). Adding any modifier after entry stays in the mode (WK2). | implemented (Plumbus stipc, 2026-10-03) |
| WK12 | Alt still works in full screen (FS1). While hints are active, widgets slide back for their hints; on release/cancel they slide away again if full screen remains in front. Asking does not end full screen or notification holding. An explicit cycle exits full screen before moving, preserves the previous center memory, and queues rapid steps through the exit transaction. | implemented (headless) |
| WK13 | By default, window avoidance exists only while Window mode is active; Alt release or Esc returns temporary offsets to zero unless the user moved the window meanwhile. `scottland/window_avoidance_always` (default off; Settings label “Window avoidance”) keeps visual avoidance active outside that mode; `scottland/hint_avoidance_always` remains a compatibility alias. The focused or grabbed window is anchored. Periphery windows stay on their original horizontal side; center-zone windows stay inside their configured center-zone bounds. Vertically, a top/bottom window stays on its side, while one in the middle 50% of the screen stays in that central band. Each moved window retains a way: which hint it exposes, the moved window, axis and direction. Every layout solve starts from true frames, never transformed frames. Along its retained way, the solver chooses the smallest true-frame offset that works and returns it to zero when the obstruction clears. A zero target is released only after a 4 px clearance margin, so a one-pixel layout change cannot toggle a window between zero and its retained way. A replacement way starts from the current displayed offset, stays on the same displacement side, and advances no more than 64 px in one solve; a deadline or a missing checked patch holds the prior target. Once a layout settles, one bounded full-layout check may adopt a clearly smaller fresh arrangement, only if all previously visible patches remain and no window needs a greater offset; ties retain the current ways. Wanted-size and intermediate-size upgrades move at most `max(12 px, 20% of the wanted diameter)` and wait until drag and inertia end. Window-move search is nearest-travel first; opposite/far-apart probing belongs only inside `visible_label`'s hint-placement search (WK31). True layout changes request solves, at most once every 16 ms during sustained changes; animation ticks only ease toward targets, with a 1000 px/s cap (reduced motion snaps). Entry may request an immediate initial solve. Each refresh has a shared 2 ms deadline and each visible-label search has a 250 μs cap. An unchanged layout gets at most two bounded front-to-back passes; each pass re-solves the stack from the front, so rear windows can repeatedly receive only leftover budget. This limits work but does not guarantee convergence. Current fallback results do not reliably distinguish an exhausted time budget from a complete no-room search; see open items below. A changed layout resets the attempt bound. A settled layout stops requesting avoidance ticks. Every ordinary window hint remains rendered at or above the 48 px × text-scale widget-hint size; if no placement was checked, the minimum hint is centered on the window. If a complete search proves that no patch fits inside the legal side/zone, the single `allow_minimum_patch_zone_overshoot` policy seam currently preserves P1's zone boundary pending Mike's P1/P12 choice. Offsets remain scene-only: they never alter geometry, zone, scale, memory or widget state, and never widgetize a window. Explicit user moves and cycles remain real. | verified on Plumbus (180 solver checks, bounded 16-window fixture, real-input calm regression; dense-stack convergence remains open; 2026-10-04) |
| WK14 | Each assignment has a deterministic distinct color across a 160° hue arc opposite the session accent, with successive slots far apart; opening/closing other windows does not recolor retained letters. Scheme, background, foreground and accent come from `SCOTTLAND_PALETTE`, or the session's `<display>.palette.json`; an atomic palette-file change wakes the hint refresh, and reads are rate-limited to 250 ms while a step is running. Scheme chooses saturation/lightness; lightness is adjusted to at least 3:1 WCAG contrast against the theme background and a typical surface after compositing both tints. The whole window/card gets a 7% hint-color overlay, a 2 logical px full-color rounded border even at the supported 5% window scale, and the halo takes its dye. Fullscreen gets the tint and an inset square rim. Release, Esc, replacement and unload clear the transient dye without altering focus/attention state. With the screen-wide goo (on by default), window mode simply tints the goo with the hint color as dye (GO6): the window/card overlay stays, and there is no separate rim. The 2 logical px border applies to the fallback halo (goo off); with the goo on, the hint shows only as the window/card tint and the goo dye. | implemented (headless) |
| WK15 | Repeating the same hint within `scottland/window_double_tap_delay` (default 300 ms, range 1–3000, inclusive) sends its window to the rail immediately; if already a widget, it does nothing unless a WK34 hint peek is active, where the repeat takes WK30's center step. The first press acts immediately. Slower presses keep cycling. After the shortcut, slow cycling resumes after widget in the original start-relative loop. Tab, another hint, release or cancellation resets double-tap recognition. | implemented (headless) |
| WK16 | Double-taps use physical presses, never key repeat, and apply only in window mode. With prefix-free multi-letter hints, repeat the complete hint to invoke the same shortcut; repeating a prefix alone does not move a window. | implemented (headless) |
| WK17 | In entered Alt window mode, each unclaimed arrow press (including auto-repeat) adds a fixed impulse to its axis's surviving velocity, clamped independently to a maximum. Movement and resize each use their own constant deceleration (S14), integrated until zero including the final partial tick, with no restarted position animation. One default impulse travels v²/(2a) = 92.29 logical px. Hints/cycles retain physical-press-only behavior. | implemented (headless) |
| WK18 | Arrows target the hint/Tab-selected window if selected this hold, otherwise the currently focused window (a focused widget represents its app). Different windows retain independent coasts. Left/Right change x, Up/Down change y; diagonals combine independent axes. An arrow on fullscreen explicitly exits it and waits for restored geometry before applying queued impulses. A later explicit cycle stops that window's coast before its lifecycle/placement action. Closing/unmapping or having no output discards its motion safely. | implemented (headless) |
| WK19 | Ordinary arrow pushes follow their center's zone and scale live (L5/L8), and clear an old pin. Holding Shift for an arrow push keeps the window's displayed scale through that push and its coast, including boundary calculations (L31); releasing Shift does not end that coast's pin. The next ordinary push or drag clears it. Ctrl+arrows still resize (WK21), including with Shift held. Geometry/scale targets enter the desktop model; hints' visual declutter never enters motion coordinates. | implemented (headless) |
| WK20 | Keyboard pushes and drag coasts (L32) never bounce ordinary windows. At an exposed top/bottom workarea edge, motion stops with 100 logical pt of the live scaled content footprint visible and that axis's velocity zeroed (a footprint shorter than 100 pt stays wholly visible). At an exposed side, outward travel widgetizes once onto the matching rail when the scaled footprint first touches its inner boundary, or the workarea boundary if farther inward; WG22 morphs from the contact image. All app motion axes end at that handoff. Existing drops already beyond contact may move inward without docking; an outward push morphs from their existing image without snapping inward. Stationary drops are unchanged. WG1-ineligible windows stop at side contact without changing form. A touching output at the center's orthogonal coordinate permits passage when the workarea reaches the physical seam; crossing transfers the global center, velocity and existing focus. Gaps and reserved edges remain closed. WK21 resize recovery retains its fully contained padded bounds; existing widgets retain WK23. | implemented (headless) |
| WK21 | After mode entry, Ctrl+Right widens, Ctrl+Left narrows, Ctrl+Up grows height, Ctrl+Down shrinks height, with independent inertial size axes. Resizing keeps the window's center, zone and scale (L20) until its scaled footprint exceeds an exposed boundary, then pushes the center back inside the fully contained WP7 padded bounds (and outside exposed rails), within client pixel rounding, including asynchronous client commits. Sizes respect the app minimum and maximum and screen/workarea minus padding (integer size caps round down); an app minimum larger than that limit takes precedence. Movement and resize coasts can coexist. Ctrl+arrows consume input but do nothing for widgets. | implemented (headless) |
| WK22 | Alt release commits keyboard movement/resize and lets existing velocity coast to zero; it stops adding repeats. Esc while still in mode stops inertia and repeats at the current result and records the landing memory. It does not undo explicit arrow pushes or cycle placements; their geometry, size, output, form, scale choice and memories remain the user's result. A resized window keeps a short-lived center anchor for late client commits. Hint-only offsets separately ease to zero (WK13). The cancelled chord remains captured until Alt release. A later hold starts a new cycle from the current zone. | implemented (plumbus headless, 2026-10-02) |
| WK23 | Widgets coast vertically along their current rail and stop at top/bottom workarea limits, keeping the widget inset. An arrow away from the attached rail undocks the widget into its window on the same side, using the usual widget-to-window morph and the arrow's velocity to coast into the periphery. This first undock coast stops at that side's inner periphery edge if its velocity would carry it across the center; it never reaches the opposite rail. Later ordinary pushes can move the window freely. An arrow toward the rail does nothing; Ctrl+arrows do nothing (WK21). Tab can select a widget without restoring it (WK10). Esc stops an active coast at its current rail or periphery landing; it does not undo that explicit arrow move (WK22). | implemented (plumbus headless, 2026-10-02) |
| WK24 | `scottland/key_impulse` (335 px/s) and `scottland/key_friction` (608 px/s²) control constant-deceleration keyboard movement and drag release coasts (L32). Independent `scottland/resize_impulse` (335 px/s) and `scottland/resize_friction` (608 px/s²) control inertial Ctrl+arrow resizing. `scottland/key_max_velocity` (6000 px/s) caps both. The Window mode tab edits each impulse/deceleration pair through a position-over-time graph (S14). Repeats use the keyboard's configured delay/rate, independently for held arrows, and stop on key/Alt release or cancel. Exact focused-surface claims precede arrows (KL7); quick Alt+arrow and Ctrl-first chords keep existing app/desktop routing. A pointer/touch move or resize takes over motion without enabling hints (L31). | implemented (headless) |
| WK25 | Hints follow the desktop's text size and interface font, as Vimarchy follows Omarchy's: the nominal minimum (72 px), maximum (132 px) and proportional size (0.34 of the window's shorter side) are multiplied by the text scaling factor (GTK's `text-scaling-factor`, which `omarchy display text size` sets on Omarchy), and the letters use the interface font (`font-name`'s family). When exposure needs it, WK31 can shrink a window hint to 48 px × text scale but never smaller. The color-scheme helper records both in the palette file (`text_scale`, `font_family`) and follows changes live. | implemented (headless) |
| WK26 | In Window mode, every widget (the default card or a third-party widget, expanded or collapsed) has its hint outside its center-facing edge: right of a left-rail widget, left of a right-rail widget, vertically centered on its drawn frame. The circle overlaps by 15% of its diameter. For large text on short widgets, overlap reduces so the arc entering the widget spans at most the middle 60% of its height, leaving the upper inward count-badge corner clear. WK30/WK31 retain a consistent 48 px × desktop text scale circle; WK14 widget tint/dye/goo remain unchanged. The exterior circle has an opaque theme background under its usual 21% hint-color fill so wallpaper cannot defeat letter contrast; window circles keep their existing transparency. Colliding widget hints declutter with 6 logical px clearance; widgets move only vertically as a temporary visual transform and keep their horizontal attachment, while windows use WK31's exposure solver. Both the hints and widget frames stay vertically on screen; horizontal screen clamping takes precedence if an unusually wide widget leaves no room. Geometry, zone memories and rail attachment are never changed. Release/Esc clears the hints and restores temporary displacement. | implemented (headless) |
| WK27 | Window avoidance re-solves only after true window geometry, focus, stack order, widget placement or output changes and, during sustained changes, no more than once every 16 ms (Window-mode entry may request an immediate initial solve). This includes keyboard pushes, held arrows, inertial coasts and real pointer drags; outside Window mode it runs only when `scottland/window_avoidance_always` (or its alias) is enabled. True frames, displayed offsets, retained move ways and target offsets remain separate; a drawn transform never feeds model geometry, drag/drop geometry, memory or later solve frames. An explicitly dragged window's grabbed point stays under the pointer, including when its drawn frame has an avoidance offset. When avoidance becomes inactive, every temporary offset eases to zero. | verified on Plumbus for geometry separation and ordinary drag/coast; a real grab of an already-shifted window remains unverified (2026-10-04) |
| WK28 | Hint circles pop in when window mode starts: each scales up from nothing with a short springy overshoot (and pops out quickly when the mode ends), and each circle has its own goo: it is a round goo source dyed its hint color, so it is part of the one liquid, joining the goo of the window or widget it touches (a widget's exterior hint visibly connects to the widget). With the goo off, circles get the fallback halo ring. Within the GO10 cost budget. (Mike, 2026-10-02) | implemented (headless); motion, shared liquid, fallback, reduced motion and paired GO10 checks below |
| WK29 | When a hint cycle (keyboard, window mode) moves a window to its next place, it comes to rest with a small elastic overshoot: an underdamped spring passes the target once in position and scale, then settles without wobble in 300 ms. `scottland/cycle_overshoot` is the peak percentage of the move (default 3%, range 0–10%; zero retains the original 260 ms position/180 ms scale motion). Geometry, zone, target scale and memories stay at the destination; drawn scale follows its own spring, never the intermediate position's zone. The live scaled content footprint is constrained to its output, including shared seams, without correcting existing off-screen memories or oversized endpoints; scale stays at least 5%. These limits may reduce overshoot. Keyboard widget opens use this window placement motion; widget morphs, rail glides, drops, drags, coasts and pointer/IPC opens retain their own motion. The cycle-overshoot setting is available through plugin metadata, `scottland-ctl` and the Window mode Settings tab. (Mike, 2026-10-02) | implemented (headless) |
| WK30 | Widgets' hint circles keep a consistent 48 logical px diameter (2/3 of WK5's ordinary 72 px minimum), multiplied by desktop text scale, independent of expanded/collapsed form or client dimensions (WK31). Pressing a widget's hint first selects the widget (like a window that isn't selected yet); only a further press cycles it (to the center, etc.). Supersedes widget behavior in WK6-WK11/WK26 where they differ. (Mike, 2026-10-02) | implemented (headless); motion, shared liquid, fallback, reduced motion and paired GO10 checks below |
| WK31 | In Window mode each ordinary hint seeks the clearest spot inside its own screen-visible window region (displayed rectangle minus foreground rectangles). It uses WK5/WK25 proportional size when feasible, with a 48 px × text-scale minimum and WK28 pop clearance when room allows. Only the **visible-label** search samples far-apart corners and edges before refining its best regions; its per-search cap is 250 μs. Window movement is a separate nearest-travel search from the current displayed offset, within the retained axis/direction way (WK13); periphery windows stay on their side, center-zone windows stay within their zone, and vertical movement stays within the original top/bottom region or central band. A retained way shrinks toward its smallest working offset from true geometry, then returns home after the 4 px release margin. During drag, a replacement way starts at the displayed incumbent and advances by at most 64 px per solve. At rest, one fresh whole-layout check may take a strictly smaller arrangement only when no previously visible patch is lost and no offset grows. The shared WK13 solve deadline is 2 ms, with at most two total front-to-back passes for one unchanged layout. Each pass re-solves the stack from its front; it does not yet resume the unfinished rear-window search, so a dense stack can repeatedly exhaust its budget before every window is checked. Timeout and completed no-room outcomes are not yet clearly distinguished. Wanted-size refinement stops at a valid fit; wanted and intermediate-size upgrades move at most `max(12 px, 20% of wanted diameter)`. A timeout or no-room fallback keeps the last checked target for that frame. Every hint remains rendered at the 48 px × text-scale minimum, even if its circle overlaps foreground content; if there is no positive-clearance point or no point was checked, it is centered on the window. A complete search that proves no patch fits its legal zone reaches the current P1 policy seam while Mike decides whether P12 allows a capped step past the zone edge. The focused or grabbed window never moves from avoidance. Selecting a shifted window makes it the anchor. Hints use the most open visible spot, never exterior attachment; only widgets use WK26 exterior hints. During reflow, a hint may wait for affected offsets to settle, not for additional room. Geometry, zone, scale and memories do not change. `scottland/window_avoidance_always` keeps window avoidance active outside Window mode; its legacy key remains honored. | verified on Plumbus (180 deterministic checks and real-input calm regression; dense-stack convergence remains open, 2026-10-04) |
| WK32 | Quick Alt+Tab previews the next center-zone window in MRU order; Alt+Shift+Tab previews the previous, and further Tab presses while holding Alt keep stepping. Releasing Alt focuses and raises the preview. The small, click-through preview names the next window and its place in the cycle, or says there are no center windows. With one center window it previews that same window; with none, focus stays as it was. Side windows and widgets never enter the list. This owns Wayfire switcher's former bindings and is reserved from Omarchy imports (O5). Once window mode has opened, Tab instead retains WK10 hint order. Tenets 2 and 3 choose the brief visible preview and immediate, no-op empty behavior. | implemented (headless) |
| WK33 | Each completed hint press that acts on a window or widget briefly pulses its hint-color tint over that representation, peaking quickly and fading within about 220 ms. Repeated acting presses pulse again. The flash is visual only: it does not alter focus, zone, scale, memory, or input routing. Tenet 2 gives immediate feedback for the chosen hint. | implemented (headless) |
| WK34 | In window mode, the hint press that first selects an unselected collapsed widget also expands it for a five-second peek; collapsed intent and placement stay unchanged, and it collapses again at expiry unless another peek trigger is active. A further hint press during the peek follows WK30's center-first cycle and restores the app window to center, even within WK15's double-tap interval; this ends the peek. Tab selection keeps WK10 behavior; hint circles remain click-through (WK4). Tenets 2 and 3 make a minimized widget recognizable briefly while preserving its stored place. | implemented (plumbus headless, 2026-10-03) |
| WK35 | Holding the hint of the focused window (past a hold delay, proposed 500 ms, a setting) solos it: it goes to the center and the other center windows go to the periphery, which spreads (docs/spread.md; spread's keyboard solo). It commits outright, no undo (P5). A tap keeps today's behavior (WK15 double-tap, WK6 slow repeats); key auto-repeat never counts as a hold or a repeat (WK16). (Mike, 2026-10-03; focused-window condition 2026-10-04) | not built |
| WK36 | Pairing: holding the hint of an unfocused window pairs it with the focused window: the two are placed side by side, scaled only if needed to fit the screen's width together. Space is divided so both end up at roughly the same magnification (the larger window gets the larger share), best effort, rather than 50/50. Nothing is locked afterwards: dragging, soloing and every other behavior work normally on either window. Open points: what happens to the other center windows (presumably as in solo), and whether the pair spans the whole screen or the center zone. Note: tenet 4 says center windows aren't scaled for side-by-side; pairing is an explicit request that grants that concession. (Mike, 2026-10-04) | not built |
| WP1 | Each open window remembers independent center, left/right periphery, and left/right rail positions. Centers are normalized to screen dimensions and applied to the destination screen, including when a widget moved to a screen with a different scale. Initial placement, real drag drops, finished keyboard coasts, and cycle placements establish memories; visual animation does not. Closing forgets the record; a marked Scottland reload hands it to the new plugin in the atomic desktop model handover. Each zone memory also keeps the window's scale pin there, if the user set one (Shift-drag, L31): returning to that zone by cycling restores both the remembered position and that pinned scale; with no pin the zone's normal scale applies. Only the periphery memories keep a pin; the center and rails never do (see Decisions). (Mike, 2026-10-04) | implemented (plumbus headless, 2026-10-04) |
| WP2 | A remembered destination wins exactly, even when occupied. Only pixel rounding is applied. This is predictable placement, not automatic rearrangement of existing windows. | implemented (headless) |
| WP3 | Side choice uses the most recently visited side with a periphery or rail memory. With neither, choose the side with the largest contiguous free opening (blocked intervals are unioned); when openings differ by no more than 5% of screen height, choose the nearer side. Exact horizontal ties choose right. | implemented (headless) |
| WP4 | Without a memory, use the pure `place_rectangle` routine: minimize summed rectangle intersection area inside the destination region, then prefer the spot nearest the current center. Within 1% of the incoming rectangle's area counts as about equal. Side-zone ties prefer nearby vertical positions. An unremembered periphery destination places its center far enough into the side zone for a visible scale reduction (5% when available, otherwise halfway toward the rail scale), then re-evaluates its natural scaled footprint at the landing position. A remembered spot remains exact (WP2). Rail placement is refined to the actual widget footprint when it maps. | implemented (headless) |
| WP5 | Explicit zone cycling, card opens and presenting a side window clear the current Shift scale pin, but a zone's remembered pin (WP1) is restored when the window returns to that zone. Center destinations keep the original window size and are always at 100%. A cycle's drawn scale target is computed from its destination center before the geometry transaction commits, so the final displayed scale matches that zone; later old-geometry notifications cannot retarget the cycle. Oversized content stays full size. WG17 card clicks use the same placement routine: remembered center first, otherwise the nearest least-overlapping center spot rather than unconditional screen-middle placement. Presenting a side window uses it too (L30). A restored pin is also the cycle's drawn scale target, so the window lands at the pin with no jump. | implemented (plumbus headless, 2026-10-04) |
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

- WK13/WK31, tenets 1, 2, 3, 4 and 5: preserve each window's visual identity within its visible
  content when possible. Moving an unfocused drawn rectangle is the cheapest concession, so
  search for the nearest circle-sized opening before reducing the proportional circle. The
  focused surface stays at its true position, including during movement. If no circle-sized
  region exists, keep attention visible at the 48 px × text-scale minimum in the best checked
  spot, or centered on the window if there is no positive-clearance spot or the search checks none;
  this fallback may overlay another window. Tenet 1's visible attention takes priority over perfect
  containment. Keep a stable
  label point for an unchanged stack. Recognition keeps widget circles at 48 px × text scale
  across client sizes and forms.
- WK13/WK22/WK27, tenet 3: by default, exposure movement exists only to show window-mode hints.
  When the request ends, its offsets return to zero; the optional always-avoid setting keeps the
  same purely visual reservation active between requests. A move the user explicitly made remains
  committed. Esc stops a keyboard coast at its current result instead of treating an explicit move
  as a peek.
- WP4/WP5, tenets 3 and 4: a new periphery placement must read as lower priority through
  its center's zone scale. Exclude the near-center soft band when no side memory exists,
  and pin a cycle's scale target to that chosen center before the move transaction completes.
- WP1/WP5 zone pins (Mike, 2026-10-04): a zone memory stores the L31 pin the window has when that
  memory is established (drop, finished keyboard or drag coast, cycle placement, the spot it is
  leaving on a cycle), or its absence, so an unpinned drop there clears that zone's pin. Edges:
  - Center: no pin is kept or restored. Tenet 4 (the center is full scale, always) and WP5's
    "center destinations are always at 100%" decide it, even though L31 lets a Shift drop leave a
    window scaled in the center for as long as it stays there. Card opens and presenting go only
    to the center, so they restore 100%.
  - Rails: no pin. Widgets are always at 100% (WG4). A widget dragged off its rail follows its new
    zone (widget drags never pin, L31), never a pin its window had before it became a widget; that
    pin stays in its own zone's memory.
  - Left and right periphery keep separate pins; a pin comes back only with its own zone's
    remembered spot and never reaches another zone. If zone settings have since put that spot
    inside the center zone, the window comes back there at 100% (tenet 4).
  - Screens: the pin is the window's own scale factor, the same logical size on any screen, so it
    is applied unchanged on a screen of another size or output scale, like its normalized spot.
  - Persistence: pins travel in the desktop model's placement record (`positions[z].pin`), so a
    marked reload keeps them; closing the window forgets them with the rest of its memory.


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
- Tenet 3 (position is temporary, user intent is durable): visual hint offsets return to zero
  when window mode ends. Explicit arrows, cycles and completed drags remain at the position the
  user chose; Esc stops a live keyboard coast there. Tab navigates widgets without unexpectedly
  opening them.
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
transforms remain the position authority (WK27's historical inertia pause was
superseded by live avoidance in the 2026-10-02 follow-up). The desktop's
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
radius; this avoids polygon ring construction and handles disconnected pieces and unions. A
visible-label search samples opposite coarse regions first, then refines only while the requested
badge still needs more clearance, to 0.5 logical px when its 250 μs sub-deadline permits. No
third-party implementation is copied. Equal probes have a stable order, with center proximity
breaking exact clearance ties.

For window movement, considered [PRISM (Gansner and Hu)](https://www.graphviz.org/documentation/GH10.pdf)
and its [Graphviz integration](https://graphviz.org/docs/attrs/overlap/): they minimize node
overlap, but overlap itself is not WK31's goal. The first implementation minimized whole-window
intersection; Mike's live review corrected the objective. The exposure solver instead searches
circle centers outside foreground rectangles and chooses the least window movement that
contains a proportional badge there. The far-apart-first samples of opposite screen and
occluder-edge placements apply only inside `visible_label`, where the hint circle is placed.
Window-move candidates are a separate nearest-travel search, as specified by P1/P2/P11. A moved
window retains its beneficiary, axis and direction. Contact candidates on that one-dimensional ray
are ranked by offset from true geometry, so the window returns along the same way when the
obstruction recedes; a different way is considered only when the retained one has no valid placement.
A wanted-size circle is a satisficing target; one layout refresh has a shared 2 ms deadline. If a
solve is truncated, it holds the last checked targets for that frame and retries the unchanged layout
after the next 16 ms tick, under the same deadline. Animation-only frames never start a solve. Only
if the wanted diameter cannot fit within the output does it search smaller sizes down to the readable
floor. An unfocused foreground window can move to expose a fully covered rear
one; the focused surface is fixed (WK13). Movement is bounded so a badge-sized portion remains on
screen; a whole large window need not fit. Fixed widgets retain vertical-only motion and their
exterior circles.
Widgets retain the existing vertical badge solver; their displaced frames and exterior circles
are fixed obstacles for windows. Actual scene order determines foreground occlusion after the
solve. The bridge caches local hint anchors and clearance-limited diameters across completed solves;
pending work retries against the same true frames until inputs change. Pure placement and declutter
remain independent of Wayfire.

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

## Historical WK13 / WK27 / WK31 / WP4–WP5 validation (always-on avoidance)

This run predates the temporary-avoidance correction below. It records the previous always-on
behavior and its test results; the current behavior and current test evidence follow it.

Mike's new rule makes keyboard focus the layout anchor. In a three-window stack,
real Alt input shows the focused front at exactly its true geometry while the two
fully covered windows expose interior circles. Selecting a shifted rear window
with its hint raises it and eases its temporary offset to zero; the others
move around it. During a real held Right arrow, the focused window's avoidance
offset stays zero and the other offsets change while its geometry moves. Circle
pixels and letter dye are checked against the current foreground rectangles
before, after, and during the handoff. The first front window's 132 px circle
fits; the packed side strips cap the other two near 129–130 px. A focused
output-sized surface is a geometric exception: it cannot move (WK13), and a
wholly covered rear hint waits for a visible region (WK31).

An unremembered real hint cycle now lands in the periphery at a scale no greater
than 0.95, and its applied scale matches the center's zone scale after the
cycle. A real outward arrow push preserves that agreement. The avoidance
check also uses a real pointer drag outside Alt: the focused window follows its
true drop path while the others reflow. Screenshots and logs are under
`build/wk31-round2/plumbus-final/`, including `visible/fully-hidden.png`,
`visible/fully-hidden-selected.png`, `visible/fully-hidden-pushed.png`,
`visible/always-on-after-drag.png`, and `windowing/new-periphery.png` and
`windowing/periphery-arrow.png`. Tests ran only in isolated headless sessions
on plumbus with checkout-local hooks and real stipc input. The shared dev
install, live session and services were untouched. Physical-display
verification remains outstanding under D2.

| Suite | Result |
|---|---|
| `tests/hint-visible-test.sh` | **83 passed**: interior pixel masks, focus anchoring, selection return, live arrow reflow and pointer drag outside Alt |
| `tests/windowing-test.sh` | **86 passed**: real periphery cycle and arrow scale agreement, plus existing windowing cases |
| `tests/windowing-unit.sh` | **111 passed**: anchored exposure and fixed widget constraints, plus placement cases |
| `tests/hint-style-test.sh` | **53 passed** |
| `tests/widget-hints-test.sh` | **170 passed** |
| `tests/inertia-test.sh` | **65 single-output and 8 two-output passed** |

## Temporary window avoidance and explicit moves (2026-10-02, plumbus headless)

Tenet 3 guides the lifetime: moving a window visually just to expose a hint ends with the hint
request. Real Alt and Esc input through stipc returns every hint offset to zero; checks compare
drawn bounds with true frame geometry after easing. Geometry and placement memories are unchanged
when avoidance alone moves a window. Explicit arrow moves, widget rail moves, resize and completed
drag drops stay committed after Alt release or Esc. Esc ends an arrow coast at its current result,
and late client resize commits keep their center anchored while settling. Pointer drags outside
window mode create no hint offsets.

The final serial plumbus run used only isolated headless sessions and checkout-local hooks. It
stopped every session, removed its runtime directories and retained the logs/screens under the
osanwe checkout's `build/part1-plumbus/final/`. No dev-install, reload or live session was touched.
Physical-display verification remains outstanding under D2.

| Suite | Result |
|---|---|
| `tests/hint-visible-test.sh` | **85 passed**: exact true geometry after Alt/Esc, no offsets outside mode, live exposure, arrow moves and a drag from a shifted hint |
| `tests/windowing-test.sh` | **102 passed**: hints, input ownership, cycles, geometry/memories, fullscreen, rails and overflow labels |
| `tests/inertia-test.sh` | **73 passed** on one output; **8 passed** on two outputs, including retained moves and resize results after Esc |

Screenshots include `hint-visible/stack-held.png`, `fully-hidden-pushed.png`,
`hint-drag-before.png`, `hint-drag-after.png`, `no-avoidance-after-drag.png`,
`inertia/two-output-crossing-kept.png`, `inertia/widget-move-kept.png`, and
`windowing/periphery-arrow.png`.

## Optional always-on window avoidance (2026-10-03, plumbus headless)

`scottland/window_avoidance_always` defaults off and is available in the Window mode Settings tab,
plugin metadata and `scottland-ctl`. The old `scottland/hint_avoidance_always` key remains honored
as a compatibility alias. With the setting on, window avoidance reserves hint-circle space outside
Window mode using scene offsets only; it does not alter true geometry or widget state. Disabling it
eases every offset back to its true position. The settings checks cover default, live on/off changes,
Save/reopen, Defaults and Cancel. The stress fixture overlaps six large windows, checks outward
shifts toward the rails with attention breathing, and uses real input for ten widgetize/restore
cycles of the same window.

Plumbus headless results: `tests/settings-help-test.sh` **177 passed**;
`tests/hint-avoidance-always-test.sh` **40 passed**, including geometry/widget invariance and all
ten widget cycles. Settled Wayfire CPU was **8.7% of one core** during the three-second sample.
Artifacts are in `build/part5-plumbus/settings-help/` and
`build/part5-plumbus/avoidance-always/`, including the overlap, attention and widget-cycle
screenshots plus `stress-metrics.json`. Sessions and their private state directories were stopped
and removed. The always-on preference has not been applied to Mike's live session because it is
read-only under the host rule. To enable it in a personal installation, add this to
`~/.config/scottland/overrides.ini`:

```ini
[scottland]
window_avoidance_always = true
```

## Bounded window avoidance and easing regression (2026-10-03, plumbus)

The captured 9:48:26 AM PT hang core's Wayfire main thread was in
`step_hints -> expose_window_hints -> least_exposure_move -> visible_label`; the
label search had accumulated 894 tied plateau cells. The pre-fix real-input drag
fixture reproduced a 393.05 ms peak Wayfire IPC request (over its 250 ms
responsiveness limit). That earlier solver consumed true frame geometry, ran
only on layout changes (at most every 16 ms during sustained changes), and
observed a shared 2 ms search deadline with at most 250 μs per label. Its move
search also used opposite-first ordering, although Mike's guidance was only for
`visible_label`; that order violated the intended nearest-move search. The
independent simulation below showed that stateless cost ties also flip even
without a deadline, so correcting probe order alone was not sufficient. The
requested badge diameter is a satisficing target: refinement ends once the
least-travel fit is confirmed. This earlier revision accepted a checked
best-so-far answer at deadline and restarted from the previous target. The
temporal-coherence review below replaces that behavior with a one-frame hold
when movement search is truncated. Animation frames only ease toward the last
target.

On the final build, the real pointer-drag fixture with six large overlapping
windows, always-on avoidance and breathing attention completed 38 solves; the
largest measured full solve was **2.533 ms** for the 2 ms search budget (the
fixture asserts under 4 ms), and the slowest Wayfire IPC request was **0.951
ms**. Across seven samples after the pointer stopped, solve counts remained
`[37, 37, 37, 37, 37, 37, 37]`, showing that the drawn offset did not trigger
another solve. The selected window's real frame moved from `(179, 86)` to
`(335, -25)` while remaining a window; another window took the avoidance offset.
Before-fix and after-drag screenshots and machine-readable samples are retained
under `build/avoidance-animation-plumbus/plumbus-output-final/avoidance-animation-plumbus/`.
The pre-fix 393.05 ms measurement is under
`build/avoidance-animation-plumbus/baseline/`.

| Plumbus check | Result |
|---|---|
| `tests/windowing-unit.sh` | **113 passed**; forced 1 ns deadline returns the same finite, valid badge placement on repeated solves |
| `tests/hint-visible-test.sh` | **85 passed**; fully covered stacks, true geometry, release/Esc and explicit moves |
| `tests/hint-avoidance-animation-test.sh` | **15 passed**; eased entry/exit, always-on reflow, setting-off return, and reduced-motion snap |
| `tests/hint-avoidance-hang-test.sh` | **10 passed**; live drag, breathing attention, stable stationary count, timing and IPC responsiveness |
| `tests/windowing-test.sh` | **102 passed** |

The test host load average just before the final drag run was 0.40/0.29/0.38;
no other Wayfire process was running. Each fixture used this checkout's
`SCOTTLAND_HEADLESS_DIR`; all its compositors and exact runtime directories were
stopped and removed. Screenshots include `window-mode-avoiding.png`,
`always-on-new-large-window.png`, `before-live-drag.png` and `after-live-drag.png`.
This is plumbus headless verification with stipc input, not physical-display
verification.

## WK13 / WK31 optimized merged-build recheck (2026-10-03, plumbus)

The `ship-merged5` build includes the optimized GO19 Makefile configuration.
This recheck predates the temporal-coherence correction below. The label and
placement searches shared 250 μs / 2 ms deadlines; window moves were still
checked opposite-edge-first, before the subsequent correction restricted that
order to `visible_label`. A layout with no positive-clearance point centers its hint on the window; otherwise the best checked point is used.
In all cases, including a forced 1 ns budget, the ordinary hint stays visible
at or above 48 × text scale even if it overlaps foreground content. Widget and
window hints share that readable floor, replacing the previous 32 × text scale
window floor.

| Merged-build Plumbus check | Result |
|---|---|
| `tests/windowing-unit.sh` | **114 passed**; forced 1 ns deadline returns a finite, centered minimum hint consistently |
| `tests/hint-style-test.sh` | **53 passed**; 5% window scale retains the 48 px × text-scale floor |
| `tests/hint-visible-test.sh` | **85 passed**; Alt/Esc return, no-room visibility and explicit moves |
| `tests/hint-avoidance-animation-test.sh` | **15 passed**; easing in/out, always-on reflow and reduced-motion snap |
| `tests/hint-avoidance-hang-test.sh` | **10 passed**; real drag with attention, bounded solve, IPC and stationary stability |
| `tests/windowing-test.sh` | **102 passed** |

The optimized-build drag run completed 38 solves under a 2 ms search budget;
the latest largest measured solve was **2.030 ms** and the slowest IPC request
was **1.105 ms**. The fixture allows up to 4 ms for total solve overhead. Repeated
stationary samples kept the solve count fixed at 37 after the final pointer
position. A 1 ns-budget unit case confirms the fallback remains finite and
stable. Logs and screenshots are under
`build/ship-merged5-evidence/avoidance-{animation,hang,visible}/`.

### ship-merged6 recheck (optimized Plumbus build)

The hang-stress rerun completed **10/10** checks with the 2 ms search budget,
38 drag solves, a largest solve of **2.041 ms**, and a slowest IPC request of
**2.400 ms**; the fixture's total-solve ceiling is 4 ms. Stationary samples kept
the solve count stable after the last pointer position. An earlier run failed only
the timing assertion at 5.808 ms while another headless compositor and a widget
process were consuming CPU; neither process was stopped. After that activity ended,
the rerun passed. Logs and timing samples are under
`build/ship-merged6-evidence/avoidance-hang/`.

## P11 temporal coherence review and regression (2026-10-04, Plumbus)

The earlier recon used five large overlapping windows, always-on window
avoidance, a real stipc Super+drag with one-pixel pointer steps through a
cluster, a reversal, and a stationary hold while attention breathed. Across
**820 input/frame samples** it recorded **782 solves; all 782 reached the 2 ms
deadline**. The old targets flipped 58, 8, 6 and 74 times for the four displaced
windows, with jumps up to 423.95 px. The captured trace showed opposite-edge
window-move probes on deadline. That probe order was a bug; Mike clarified that
far-apart-first applies only inside `visible_label`, never to choosing a window
move.

The independent unbounded simulation found the original discontinuity: a
stateless least-travel-from-rest solve flips between equal left/right minima as
the dragged window crosses a midpoint, even with no deadline. The review also
identified a P3/P2 defect in the in-progress branch: a displaced target could
remain after its obstruction had moved away. Its replay omitted
`branch_owner`, `branch_axis` and `branch_sign`, so it did not model the bridge's
retained-way state; code inspection independently confirmed the missing
within-way return. The checked-in deterministic sweep now feeds back the full
bridge state and checks the same out-and-back/hold/park path across repeated
sweeps.

The solver retains the *way* (beneficiary hint, moved window, axis and sign),
then searches from zero toward the checked endpoint to find the smallest
working offset from the moved window's true frame along that ray. The search
checks the moved hint and already protected hints at each probe. A clear layout
returns that offset to exactly zero. A branch is replaced only when no valid
result remains on it; a new branch is ranked from the displayed offset.
Periphery windows stay on their side, center-zone windows stay within their
zone, and vertical movement remains in the top/bottom region or the central
50% band.

Wanted-size upgrades are limited to `max(12 px, 20% of wanted diameter)` and wait
until drag or inertia ends. A failed incumbent with truncated work holds its prior target for
one frame. Retries use fixed front-to-back order, window-move candidates remain
nearest-travel first, and the visible-label subsearch keeps its separate
250 μs deadline. A deterministic work-count budget supplements the 2 ms wall
deadline in the solver tests. Easing has a 1000 px/s cap; reduced motion still
snaps. Pointer grab fractions include the displayed avoidance translation, and
the grabbed transformer is held steady until the explicit drag owns the move.

The earlier Plumbus rerun passed **170/170** deterministic checks and **15/15**
stipc checks. Review-2 found that the old parking assertion was invalid: before
the second drag, the fixture spread its windows into a non-overlapping grid by
IPC, and the parked window could become a widget. The claim that all five
offsets returned to zero is withdrawn; the live drag-through-cluster and
post-drag evidence below supersedes it. The earlier readings remain useful as
history but are not the current acceptance evidence.

The solver retains the *way* (beneficiary hint, moved window, axis and sign),
then searches from zero toward the checked endpoint to find the smallest
working offset from the moved window's true frame along that ray. The search
checks the moved hint and already protected hints at each probe. A clear layout
returns that offset to exactly zero. During movement a branch is replaced only
when no valid result remains on it; once settled, one bounded fresh-layout
check may choose a clearly smaller whole-layout arrangement if every previously
visible patch remains and no window's offset grows. Similar alternatives keep
the incumbent way. Periphery windows stay on their side, center-zone windows
stay within their zone, and vertical movement remains in the top/bottom region
or central 50% band.

Wanted-size and intermediate-size upgrades are limited to
`max(12 px, 20% of wanted diameter)` and wait until drag or inertia ends. A
failed incumbent with truncated work holds its prior target for one frame.
Each refresh has a shared 2 ms deadline, a 250 μs visible-label subsearch cap,
and at most two attempts per window for one unchanged layout. Although the
solver-level API carries results across slices, the runtime bridge currently
re-solves front-to-back on each pass and can starve rear windows in a dense
stack. Once the bridge reaches its pass bound, it stops requesting avoidance
ticks even if a rear window has only a fallback. Easing has a 1000 px/s per-update cap;
reduced motion still snaps. The pointer-grab path is intended to account for
the displayed avoidance translation, but the claimed shifted-window grab
result below was invalidated by review.

On the 2026-10-04 Plumbus rerun, the deterministic solver suite passed
**180/180**. Its 16-window solver fixture converged in **2** bounded slices;
the first slice completed **6** windows, both stayed within **1,800**
inspections, and the completed layout did no more work. That fixture exercised
the carried-progress API directly, but did not reproduce the runtime bridge's
per-pass restart behavior; the dense live stack below shows that this unit case
does not establish runtime convergence. The intermediate-size test produced a
checked 50 px badge with no movement beyond its 26.4 px upgrade cap. The forced
tiny-budget and zone-policy tests passed, including the optional P12 path that
steps just far enough past a zone boundary to fit a 48 px patch.

The real stipc regression passed **18/18** across **349 samples**. Five large
windows were dragged one pixel at a time through a cluster with attention
breathing on, then the front window was dragged by stipc to the top edge; the
other four true frames remained pairwise overlapping. There were **0 side
flips**, **0 held target changes**, and one 49.47 px replacement-way step, below
the 64 px cap. Peak search was **1.432 ms** and maximum per-solve work was
**9,642**. The bounded post-drag route check adopted one fresh whole-layout
solution; all five targets then matched the fresh solve within **0.002 px**.
The fixture's claimed y-shifted grab and **0 px** release/settle drift are
withdrawn. Review-3 found that its `drawn_frame` helper omitted the avoidance
transform: the pointer press landed at the window's true center, not the shifted
drawn frame. The supposedly grabbed window was never grabbed (its target stayed
at dy = -84 px). This run therefore verifies neither grabbing a displaced
window nor its drop behavior. The earlier invalid grid-based parking claim is
also not part of this result. Screenshots and the full trace are in
`build/window-avoidance-calm-plumbus/`.

The 12-window exact-overlap load fixture passed **10/10**. All 12 minimum-size
hint badges stayed rendered, 9 windows had a checked ≥48 px visible patch, and
3 used fallback after the shared per-pass time budget expired. The pass
re-solves front-to-back, so rear windows repeatedly receive only leftover
budget; those 3 searches did not prove that no legal patch exists. The run is
not evidence for a P1 zone-boundary conflict. P1/P12 policy remains pending,
and dense-stack visibility is still incomplete. The
unchanged Window-mode layout stopped at **9** solves and **101** animation
steps, with no new solve or tick over the idle sample. Always-on avoidance
without hints used **11.20%** Wayfire main-thread CPU against a **9.60%**
same-layout no-hint baseline (**+1.60 percentage points**); Window mode with
hints used **15.60%**. Samples were 2.5 seconds each; system load average was
about **2.4 / 2.4 / 2.6**; this task launched no concurrent GPU benchmark.
The screenshots and state counters are in
`build/window-avoidance-load-plumbus/`. P12 is not fully verified: the solver
must first distinguish timeout from completed no-room, and Mike's P1/P12
zone-edge choice remains pending.

### Review-3 corrections and open follow-ups

- **Progress and outcome reporting:** carry useful search progress across
  bounded passes so a dense held layout eventually checks every window. Report
  budget exhaustion separately from a completed search that proves no legal
  patch exists. The current two-pass bound controls CPU, but a repeated
  front-to-back pass can starve the rear; P12 is not verified by a rendered
  fallback badge alone.
- **Nearby same-way alternatives:** the retained-way search can return a far
  answer when feasible positions along that way are non-contiguous. The review
  measured a remaining **306.4 px** slide after a **1 px** drag. Let a nearby
  alternative compete even while the incumbent way technically has a result,
  while preserving P11's side and way stability.
- **Shifted-window pointer grab:** fix the invalid `drawn_frame` fixture to use
  the actual transformed frame, press on that visible frame, assert the grabbed
  view identity, then verify pointer anchoring during drag and the result after
  drop. No shifted-window grab or drop result is currently claimed.
- **P12 peeking policy:** if Mike chooses P12 over P1, cap zone-edge overshoot
  to the smallest necessary movement, restrict it to the window that lacks a
  patch, and define/test the always-on peeking strip (including how an Alt hint
  uses that strip). The current policy switch drops the zone limits without
  that cap or strip behavior; it does not address budget-starved windows.
  Mike's P1/P12 choice remains open.

### Review-3 rerun and idle-tick regression check (2026-10-04, Plumbus)

The eight requested suites ran on `de2af42` in separate headless directories.
`windowing-test.sh` passed **102/102**, hang stress **10/10**, always-on stress
**42/42** (settled Wayfire CPU **6.3% of one core**), `widgets-test.sh` passed,
and `widget-morph-test.sh` passed **270/270**. `hint-visible-test.sh` reported
**83 passed, 2 failed**: a raised-stack circle did not remain inside the
reported exposed region, and no rear window received the fixture's expected
greater-than-100 px shift in the exact-overlap stack. The latter is a
budget-limited visibility failure, not evidence that the P1 zone boundary left
no legal patch.

`hint-avoidance-animation-test.sh` reported **14 passed, 1 failed**. In the
always-on mode round trip its targets and offsets remained unchanged, producing
**0 px** movement at both mode switches; the fixture demanded four intermediate
movement samples even though no movement was needed. The separate entry, exit,
new-window easing, setting-off easing and reduced-motion assertions passed.

`widget-hints-test.sh` first reported **217 passed, 11 failed**. Six failures
were stale 1.5×/3× badge sizes, and five were exterior attachments left at the
old widget width during the WK34 peek transition. The idle-tick change had
removed the only palette-file refresh wakeup and had stopped hint-anchor updates
while the widget morph timer was active. The bridge now watches atomic palette
file replacement and keeps hint stepping alive only while a widget presentation
transition is animating. The targeted Plumbus rerun then passed **228/228**;
both text-scale sizes and WK34 peek attachment checks pass. No avoidance solver
logic changed. Logs and screenshots are under
`build/review3-tests/plumbus/` and
`build/review3-tests/plumbus-artifacts/`.

## WP1/WP5 zone scale pins (2026-10-04, plumbus headless)

`tests/zone-pin-test.sh` (needs `SCOTTLAND_HEADLESS_DIR`; starts its own widget session) drives
real stipc Super/Shift drags and Alt hint presses. It Shift-drags a window from far out in the
left periphery (scale 0.48) inward to a spot whose zone scale is 0.85, cycles it to the center
(100%, no pin) and back, then checks the exact spot, the 0.48 pin, a drawn cycle target equal to the
pin from its first sampled frame, and no scale change in the 0.6 s after landing. It also checks:
a window with no pin returns at the zone scale; a plain drag to the right periphery records no pin
there and cycling back doesn't take the left pin; pins survive a marked reload and are still
restored afterward; and a window Shift-dragged onto the rail, then dragged off as a widget
without Shift, follows its zone. `tests/windowing-unit.sh` covers the pure memory rules
(`remember_spot`/`remembered_pin` in `window-memory.hpp`).

| Suite (plumbus, isolated checkout, final build) | Branch | origin/main `afa8309` |
|---|---|---|
| Windowing unit | 191 passed | — |
| Zone pin real input | 17 passed | 9 passed, 8 failed (the pin returns at the zone scale; it is never stored or reloaded; a stale pin comes off the rail) |
| Windowing end-to-end | 102 passed | 102 passed |
| Present (L30) | 0 failed | 0 failed |
| Drag coast | 25 passed, 2 failed | the same 2 hint-avoidance failures |
| State regressions | stops at the late-widget fixture | stops at the same point |

The drag-coast and state-regression failures are identical on origin/main and don't involve zone
memory. No live session on osanwe or plumbus was installed into, reloaded or used.
