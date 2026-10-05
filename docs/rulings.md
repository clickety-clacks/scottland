# Mike's rulings

A dated log of the decisions Mike has made, in his terms, with where each one is specified. Use it to
review behavior: every ruling here should be true of the desktop, and the linked row says how. When Mike
rules on something, add it here in the same change that updates the spec. Principles (P-rows) live in
[core/INVARIANTS.md](../core/INVARIANTS.md); feature rows in the docs named below.

Started 2026-10-04; earlier rulings from 2026-10-03/04 are back-filled from the session record.

## Principles

| Date | Ruling | Where |
|---|---|---|
| 10-03 | Object permanence: a window stays on the side where you put it; automatic layout may shift it within that side, never across. | P1 |
| 10-03 | Move only what has to move, as little as it has to. | P2 |
| 10-03 | Temporary moves are temporary; avoidance offsets return. | P3 |
| 10-03 | Layout changes come only from explicit requests, never as side effects. | P4 |
| 10-03 | Offer, then commit: a suggested rearrangement shows the real result; finishing accepts, continuing refuses; an outright request (a key) commits with no undo. | P5 |
| 10-03 | Windows that came from the center outrank the periphery, but push only when they'd otherwise land noticeably smaller. | P6 |
| 10-03 | Space between windows is for looking good and is the first thing given up. | P7 |
| 10-03 | Scottland never freezes the pointer; long work goes off the main loop or into bounded slices. | P8 |
| 10-03 | Scottland defines mechanisms; Gooarchy flavorings curate what ships. | P9 |
| 10-03 | No silent overrides: replacing anything the user had is reported with what, now and why. | P10 |
| 10-03 | Automatic movement is calm: small input change, small movement change; no oscillation or cross-screen jumps. | P11 |
| 10-03 | Nothing is ever completely hidden; window avoidance exists for this, the hint is the yardstick. | P12 |
| 10-04 | A peeking window may hang past its zone edge but never moves into another zone; if it still can't peek, only its hint is placed where it can be seen. | P12 |
| 10-04 | The user puts windows in zones; nothing automatic moves a window into another zone, not even visually. | P13 |
| 10-04 | The user always wins: what the user places ends up exactly where they put it; everything else flows around it (no settling the dropped item). | P14 |

## Window avoidance and peeking

| Date | Ruling | Where |
|---|---|---|
| 10-03 | It's called window avoidance, not hint avoidance. | WK13, Settings |
| 10-03 | "Look far apart, then go deep" applies only to searching for a hint spot, never to choosing where a window moves; windows never avoid by moving to the other side. | WK13, WK31 |
| 10-03 | Keep continuous motion during drags (no pause-then-glide). | WK13 |
| 10-03 | One engine, two tests: always-on peeking needs a strip; on Alt, hints get room. | P12 |
| 10-04 | Peek strip: at least 24 pt deep and about 100 pt long; pt are logical points (independent of display scale, not shrunk by zone scale) and grow with the system text size. | WK13 |
| 10-04 | Peek through whichever edge needs the smallest move; interior patches count. | WK13 |
| 10-04 | Peeking direction is best effort: prefer peeking toward the window's position relative to the one covering it; if that's not possible any direction is fine, bottom included. No hard top/bottom limit. | WK13 |
| 10-04 | Entering Window mode, windows are nudged so each hint gets full room, best effort; overlap the front window only when there's no room. | WK13 |
| 10-04 | The frontmost window's hint is always at its exact center. | WK31 |
| 10-04 | Grabbing a shifted (peeking) window keeps it exactly where it's drawn, which becomes its real position; selecting it by tapping its hint in Window mode instead brings it to its true position. | WK13, WK27 |
| 10-04 | In Window mode, a mostly occluded window gets an opaque hint-color outline, in its own layer above all windows. | WK37 |
| 10-04 | Window-mode tint strength is a setting. | WK38 |

## Window mode keys

| Date | Ruling | Where |
|---|---|---|
| 10-03 | Holding a window's hint solos it (keyboard solo), committed, no undo. | WK35 |
| 10-04 | Hold on the focused window's hint solos; hold on an unfocused window's hint pairs it. | WK35, WK36 |
| 10-04 | A three-finger hold on an unfocused window pairs it with the focused window (touchpad equivalent of the hint hold). | WK36 |
| 10-04 | A three-finger hold on the focused window solos it (built with solo). | WK35 |
| 10-04 | General rule: every gesture that drags a window (Super + drag, Super + double-tap drag lock, three-finger drag) has a hold form: the same gesture held still instead of dragged solos the focused window or pairs an unfocused one. | WK35, WK36 |
| 10-04 | Holding the halo counts as a hold form too. While any hold is in progress, a timing circle fills around the hint (or at the pointer), as Vimarchy does, completing when the hold fires. | WK35, WK36, WK39 |
| 10-05 | A pointer or touchpad hold that has fired is an offer while the fingers or button stay down, like the drag audition: dragging out of the hotspot cancels it (everything returns, the drag carries on); releasing inside commits. | WK35, WK36 |
| 10-04 | The focused window's hint acts on key release, so a hold can solo without first moving it. | WK35 |
| 10-04 | Double-tap must work at human timing (measured from release to next press). | WK15 |

## Pairing (WK36)

| Date | Ruling |
|---|---|
| 10-04 | Side by side; sizes preserved, never resized, never scaled up; shrink together only if they don't fit, edge-to-edge only if needed; roughly equal magnification. |
| 10-04 | Other center windows stay and peek via window avoidance; nothing goes to the periphery. |
| 10-04 | Keep current left/right order; vertically centered; pair centered; halo gap given up first. |
| 10-04 | Nothing is locked afterwards. Pairing is an explicit request, so shrinking center windows is a granted exception to tenet 4. |

## Spread and solo (docs/spread.md)

| Date | Ruling |
|---|---|
| 10-03 | Spread is conservative: windows that don't have to move don't. |
| 10-03 | Solo: the soloed window takes the center; other center windows go to the periphery, which spreads; periphery windows move outward only if necessary. |
| 10-03 | Drag audition: pause 3 s in the center to see the solo; drop accepts, keep dragging refuses and everything returns exactly. |
| 10-03 | Arrivals may push periphery windows outward to stay larger, only when needed; no side crossing; vertical first; no undo; halo gap given up first; solo only on explicit request. |
| 10-03 | Audition hotspot up to 50 pt so refusing is clearly intentional. |

## Rails and widgets

| Date | Ruling | Where |
|---|---|---|
| 10-03 | Rail make-room: only widgets in the way move, ripple only when needed, no retiling, visual during drag, real on drop, exact restore on cancel. | WG26 |
| 10-04 | Rail make-room waits for a pause (hold buffer), animates every move, and applies to every way into a rail. | WG26 |
| 10-04 | The rail is a spread: the whole rail may re-lay out when needed. "Don't touch what doesn't need touching" is a preference (least total movement), not a prohibition; overlap only when the rail is truly full. | WG26 |
| 10-04 | Return on a focused widget always opens its window; nothing inside the widget gets it. | WG25 |
| 10-04 | Super+M taps cycle expanded -> collapsed -> hidden; holding is momentary (hide from expanded, expand otherwise). | WG16 |
| 10-04 | Entering Window mode temporarily expands collapsed and hidden widgets until Alt is released; hidden widgets still show attention. | WG16 |
| 10-04 | Attention while widgets are hidden: the widget slides in for the usual attention peek, then leaves a 24 pt strip at the screen edge breathing the attention color until the user goes to it; hovering the strip brings it in. | WG16 |
| 10-04 | The Super+M hold delay is 300 ms (same as the Alt hold), adjustable in Settings. | WG16 |

## Zones and scale

| Date | Ruling | Where |
|---|---|---|
| 10-04 | Returning to a zone restores both the remembered position and the Shift-pinned scale, through every return path (cycling by hint taps, Esc, double-tap and back). | WP1, WP5 |
| 10-04 | The pointer follows the desktop text size. | A17 |
| 10-04 | A window sent to the periphery without a remembered spot goes as close to the center as possible while overlapping the center (zone and its windows) as little as possible: it may hang a little into the center zone where that covers no center window; never just its middle point over the line with half of it still in the center; best effort when space is tight. Whether a few-point full-size sliver past the edge counts as center is secondary. | WP4, WP8 |

## Goo and appearance

| Date | Ruling | Where |
|---|---|---|
| 10-03 | Unfocused edge: adjustable gray tone and strength. | A16 |
| 10-03 | Attention color choice: theme, warm (red/amber) or cool (yellow/green). | GO22 |
| 10-03 | Dye strength setting for state colors. | GO23 |
| 10-03 | Watercolor wallpaper: local pickup and spread, in all of the goo (stronger where thick), persistent after motion settles. | GO24 |
| 10-04 | Pre-computed breath frames: a ceiling (about 50) and widen spacing above it; never fall off a cliff to the expensive path silently. | GO26 |

## Omarchy adapter, distro and flavorings

| Date | Ruling | Where |
|---|---|---|
| 10-03 | Overrides of Omarchy are reported with reasons, grouped by reason, explained by the user's default coding agent in plain words, leading with the user's own custom shortcuts. | O20 |
| 10-03 | Gooarchy flavorings (curated widgets and defaults) install with the distro and with the Omarchy adapter; they may override Omarchy only where that's better for Gooarchy, always reported. | O21, P9 |
| 10-03 | The distro is Gooarchy, pronounced "goo-ah-shee". | distro-notes |
| 10-03 | Chromium ships with the system title bar and borders. | distro-notes |
| 10-04 | Strata is Gooarchy's standard file browser and replaces Files. | distro-notes |
| 10-04 | Gooarchy patterns after Omarchy but starts clean: nothing is borrowed to fill a gap (no bar yet means no bar), so the distro's deficit is clearly defined once it is built. | distro-notes |
| 10-04 | Gooarchy will have its own custom bar and its own notification system; until they exist it ships neither. | distro-notes |

## Knocks (concept in discussion; docs/knocks.md)

| Date | Ruling |
|---|---|
| 10-04 | Attention generalizes beyond agents: objects exist whether or not they're on the desktop; windows/widgets are representations of the ones that are forefront; an object asks for the user with a knock (ephemeral, not a window or widget, with a message and optionally an answer UI). |
| 10-04 | A knock's look is organic to what it is (the attention goo, a vignette), not just another window. |
| 10-04 | From a knock the user can answer directly with its UI, or invite the object in (find or open its window, bring it center, and tell the object to focus on that knock). |
| 10-04 | Mapping a knock to its sender's window must be dependable. |
| 10-04 | The bigger frame is attention regimes: center, periphery, widgets, and the ether (objects not on the desktop that still exist and can be promoted). Scottland models and visualizes all of it. | P15 |
| 10-04 | The ether is a mental place, not a spot on screen: closed things still exist and can keep running. Knocks must be reachable and actionable in every form a sender takes on the desktop: window, scaled window, widget. | docs/knocks.md |
| 10-04 | The breathing border is the direct representation of a knock; the hover swell of a border is the interaction to reach it. | docs/knocks.md |
| 10-04 | Knocks from objects on the desktop matter most (the user elected them to be there). Knock priority comes from another system; Scottland only renders it. | docs/knocks.md |
| 10-04 | Knocks have many channels, not one: every window is a channel for its object's knocks, and knocks from objects not on the desktop coexist with them; nothing is ranked against anything else. | docs/knocks.md |

## Testing

| Date | Ruling | Where |
|---|---|---|
| 10-04 | Testing standard: real input and an independently observable result; wait on state, not sleeps; check the screen for rendering claims; no wall-clock budgets in functional gates (benchmarks separate); count checks honestly; own your session and clean up exactly what you create; no source-text assertions; no retrying the action under test. Ineffective tests are deleted, not kept as gates; what still needs a real test is listed. | AGENTS.md "Testing standard"; docs/tests-todo.md |
