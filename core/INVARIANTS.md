# Scottland core invariants

Behaviors of the desktop itself, on any distro. Nothing here may depend on Omarchy, Hyprland or
uwsm; those live in [../omarchy/INVARIANTS.md](../omarchy/INVARIANTS.md). Project-wide scope and
rules are in [../AGENTS.md](../AGENTS.md).

Status: **verified** = exercised with real input on a real session (plumbus); **implemented** =
built and tested headless or by IPC only; **not built** = agreed but not implemented yet.

## Principles (tenet candidates)

Rules Mike stated while deciding specific features that apply across features. They decide edges
the way the tenets in [../docs/tenets.md](../docs/tenets.md) do; Mike may promote them into the
tenets themselves. Status says whether every feature that should follow one does.

| ID | Principle | Status |
|---|---|---|
| P1 | **Object permanence.** A periphery window stays on the side of the screen where the user put it. Automatic layout (spread, window avoidance, making room) may shift it within that side, never send it to the other side: "if I put it on the left I should find it on the left, even if it's shifted around some." A center-zone window stays within the center zone. (Mike, 2026-10-03; center-zone boundary follows Scottland's layout model) | window avoidance, rail make-room and spread: implemented (spread: docs/spread.md SP3, headless 2026-10-04) |
| P2 | **Move only what has to move, as little as it has to.** An automatic rearrangement leaves every window it doesn't need to move exactly where it is, and moves the others the least it can. (Mike, 2026-10-03) | window avoidance, rail make-room and spread: implemented (spread: SP3, return pass; headless 2026-10-04) |
| P3 | **Temporary moves are temporary.** Window avoidance offsets used to place hints are visual only: they never change geometry, zone, scale, memory or widget state. By default they exist only in Window mode and return to zero when it ends, unless the user explicitly moved the window meanwhile. The optional always setting keeps this visual reservation active outside Window mode; turning it off eases offsets home. (Mike, 2026-10-03) | verified on Plumbus (Alt/Esc and explicit-move input, 2026-10-04) |
| P4 | **Layout changes come from explicit requests.** Scottland rearranges other windows only when the user explicitly asks for that rearrangement (a solo key, a held drag audition), never as a side effect of another action such as presenting, clicking a card or zone cycling. (Mike, 2026-10-03) | spread: implemented (SP1; headless on both test machines, 2026-10-04) |
| P5 | **Offer, then commit.** An automatic rearrangement suggested during a gesture is shown as the real outcome before it happens; finishing the gesture accepts it, continuing refuses it and every window returns exactly. A rearrangement the user asked for outright (a key) commits with no undo. (Mike, 2026-10-03) | rail audition and spread audition: implemented (SP6, SP7; headless 2026-10-04) |
| P6 | **What came from the center outranks the periphery.** When windows leave the center to make room, they matter more right now than windows already in the periphery: they may push those outward to stay larger, but only when they would otherwise have to land noticeably smaller (P2 still holds). Follows tenet 3. (Mike, 2026-10-03) | spread: implemented (SP3: push only below 85% of the reference scale; unit fuzz, 2026-10-04) |
| P7 | **Space is for looking good; it gives way first.** Gaps between windows are chosen for appearance (a halo-sized gap). When room runs out, the gap is the first rule given up, before anything overlaps. (Mike, 2026-10-03) | pairing (WK36) and spread: implemented (spread: SP4 bounded spacing pass; unit fuzz, 2026-10-04) |
| P8 | **Scottland never freezes the pointer.** Work Scottland does on the compositor's main loop stays short; anything that can take long (layout solves, geometry, shape analysis) runs off the main loop or in bounded slices with a usable best-so-far result. Input always wins. (Mike, 2026-10-03) | partly: rail make-room is bounded and verified; window avoidance runs resumable front-to-back passes in work-unit slices (170,000 units, about 1.5 ms) with a 2 ms pause, results independent of slicing; worst refresh 1.1 ms in dense 16-window stacks on the ARM test machine (2026-10-04); breathing is bounded; worker-thread design remains in review; spread solves in measured 2 ms event-loop slices until the worker takes it over; its commit is one block of about 0.4 ms per moved window (docs/spread.md SP5) |
| P9 | **Scottland defines mechanisms; Gooarchy flavorings curate what ships.** Core defines what a widget (or theme, or default) is; which app widgets and app defaults ship is curated in Gooarchy flavorings (gooarchy-flavorings), which the Omarchy adapter also installs because Omarchy has no such concept. Core never ships an app-specific widget. (Mike, 2026-10-03) | decided; package started, empty |
| P10 | **No silent overrides.** Anything that replaces or displaces something the user already had (a shortcut, a mapping, an app setting) must be reported to the user with what it was, what it is now and why. A change that adds an override adds its reason to that report. (Mike, 2026-10-03; adapter rule O20) | partly: adapter report implemented under O20; wider core coverage pending |
| P11 | **Automatic movement is calm.** Windows that move out of the way do so smoothly and predictably: a small change in what you're doing (a pointer moving a few pixels) causes a small change in where they go. They don't oscillate, don't flip between alternatives, and don't fly across the screen when a nearby spot works; a window keeps its current way out of the way until it stops working. (Mike, 2026-10-03, on window avoidance during a drag) | window avoidance: verified by the peek engine's calm rules (keep the current way within 16 px, else the least offset within 64 px of where it is drawn, 6 px switch margin) in `tests/peek-unit.sh` and with real 1 px drags in `tests/peek-strip-test.sh` (ARM test machine, 2026-10-04) |
| P12 | **Nothing is ever completely hidden.** Every window peeks out from behind the windows in front of it, so you can always see that it's there (tenet 2; object permanence, like having no workspaces). Window avoidance exists for this: it keeps a visible patch of every window, at least the size of a hint circle, so the same patch also holds the window's hint in Window mode. The hint is the yardstick, not the purpose. Direction agreed: one engine, two tests: always-on peeking needs only a clearly visible strip (preferably along the window's top edge), and on Alt each hint sits on the strip already showing (the badge may overlap the front window's edge), nudging only windows whose strip can't hold one. (Mike, 2026-10-03) Decided (Mike, 2026-10-04): a peeking window may hang past its zone edge (zones are decided by the center), but peeking never moves a window into another zone: its displayed center stays in its own zone (P13). If it still can't peek, only its hint is placed where it can be seen. | window avoidance: implemented as the peeking strip (WK13); verified on the ARM test machine with real input, strips measured from screenshots, including dense 12- and 16-window stacks with nothing hidden (2026-10-04) |
| P13 | **The user puts windows in zones; nothing else does.** A window is in a zone because the user put it there (drag, keys, cycling, presenting). Automatic behavior (window avoidance, peeking, making room, spread) may shift a window within its zone but never moves it into another zone, not even visually. (Mike, 2026-10-04) | window avoidance: implemented (WK13: the displayed center stays in its own zone; verified in `tests/peek-unit.sh` and on the ARM test machine, 2026-10-04) |
| P14 | **The user always wins.** Whatever the user places (a window or a widget, by drag, drop, fling or keys) ends up exactly where the user put it; everything else flows around it. Automatic layout never relocates the thing the user just placed to make its own solution cheaper; when nothing else can move out of the way, things overlap rather than the placed item being moved. (Mike, 2026-10-04: "literally an invariant of the entire system") | rail make-room: implemented (the dropped widget never moves; the gap opens under the pointer; verified by unit fuzz and real stipc input on the test machine, 2026-10-04) |
| P15 | **Attention regimes.** The desktop is the user's window onto what has their attention, in regimes: center (forefront), periphery, widgets on the rails, and the ether (everything not represented on the desktop, which still exists). Objects are promoted and demoted between regimes by the user; an object asks for promotion with a knock; only the user grants it (tenet 5). Extends tenets 1 and 3. Concept: [docs/knocks.md](../docs/knocks.md). (Mike, 2026-10-04) | concept; center/periphery/rails built, ether and knocks not designed |

## Layout and scaling

| ID | Invariant | Status |
|---|---|---|
| L1 | Each screen has five vertical zones: widget rail, continuous zone, center zone, continuous zone, widget rail. | verified |
| L2 | Center zone: default one third of the width (33.333%); windows there are at 100%. | verified |
| L3 | Widget rails: thin strips at the far left and right, default 2% of the width (~50 pt on a 2560-wide screen). | verified |
| L4 | Continuous zones: scale follows the scale curve from the largest scale (next to the center, default 100%) to the smallest (next to the rails, default 20%); without a curve it's a straight line between them. | verified |
| L18 | Just outside the center zone, a blend band (default 40 pt, "Center edge softness" slider, up to 300 pt) eases the scale from 100% into the scale curve: flat where it meets the center, matching the curve's starting slope where it meets the curve, so there is no jump or corner at the edge. | implemented |
| L5 | A window's zone and scale are set by its center. It scales around its center. | verified |
| L6 | True scaling: the real window is transformed, not a thumbnail, and stays fully interactive (click, type, scroll) at any scale. | verified (scale); interaction at small scale: implemented |
| L7 | Scaling is the only transform: no rotation or other distortion. Wayfire's window rotation (flat and 3D) and desktop cube are not enabled. | implemented |
| L20 | Super + right-drag, or Super + Alt + drag, resizes around the window's center, like visionOS: it grows or shrinks symmetrically, the center stays put (also when the app snaps to its own size, or won't grow past a limit such as the screen's size: no flicker), so the window keeps its zone and scale; the edges track the cursor at the window's current scale. Direction is absolute wherever you grab: dragging right makes it wider, left narrower, up taller, down shorter. | verified |
| L8 | Windows rescale live while being dragged, with the grabbed point staying under the pointer. Crossing a jump in scale (e.g. the center zone's edge) changes size once, never flickering between sizes: a size is chosen only if it agrees with the center it produces, so the switch has a little hysteresis wherever the window is grabbed. What you see while dragging is what you get: the size doesn't change on release (a drop inside a jump keeps the size shown and nudges the window by the least distance that agrees). Same for every drag: Super+drag, the halo, three-finger and touch. | verified |
| L9 | Dropping a window causes no jump: the scale after release equals the scale just before it. | verified |
| L10 | Scale jumps (e.g. leaving the 100% center for a zone that starts lower) animate smoothly (~180 ms) instead of snapping. Small changes during a drag apply immediately; a moving target re-aims a running animation. | verified |
| L11 | Dropping a window near an edge or corner does not snap or resize it (Wayfire edge snapping is off). | verified |
| L12 | No wobbly windows, in shipped and installed configs. | verified |
| L21 | Touchpad clicks use clickfinger: a two-finger press anywhere is a right click, three fingers a middle click (shipped default). | implemented |
| L22 | Touchpad double-tap-and-drag has a grace period: lifting the finger mid-drag keeps the drag for about 300 ms (libinput drag lock, timeout mode), so putting it back down continues the same drag; past the grace period the drag drops. Resizes don't get the grace period: lifting the finger ends a resize at once (drag lock is off while one runs), as a resize magnifies motion and a resumed one surprises. Shipped default. | implemented |
| L23 | A three-finger drag on a touchpad moves the window under the pointer (no click, no modifier), with the same live scaling as Super+drag; the pointer follows the fingers. Lifting the fingers ends the drag at once (no grace period; picking the window up again within 2.5 s only counts as the same move for where Esc sends it back, L27). A three-finger hold (fingers still for the hint hold delay, before any drag or click) is not a drag but WK35/WK36 ([windowing-keys.md](../docs/windowing-keys.md)); a drag or click that starts first keeps this meaning. | implemented |
| L24 | A three-finger click-drag on a touchpad (clickfinger's middle button) resizes the window under the pointer around its center, like Super+right-drag. A three-finger click without moving is still a middle click, delivered to the app on release. Mice keep their normal middle button. A three-finger hold that ends in no click is WK35/WK36; a click that comes first keeps this meaning. | implemented |
| L25 | Touchscreen: holding one finger still (within ~10 pt) on a window for ~350 ms lifts it: the app's touch is cancelled, the window bulges out elastically and pulls back to its normal size, its halo swells as on hover, a synthesized bloop plays, and the window follows the finger with live scaling until the finger lifts. Touches that move sooner, and touches with two or more fingers, belong to the app with no added delay. | implemented |
| L26 | Touchscreen for apps that ignore touch: for apps listed in `[scottland] touch_scroll_<name> = <app-id regex>` (shipped: common terminals, Ghostty first), the app gets none of the touch (Scottland takes it before delivery): a one-finger drag scrolls with the content following the finger (momentum after a flick) and a quick tap clicks; long press still lifts. Scrolling is smooth, or a high-resolution wheel for apps in `touch_scroll_wheel` (shipped: Ghostty, which ignores smooth scrolling from Scottland's pointer). Users add, change or empty entries in overrides.ini. | implemented |
| L27 | Esc cancels any window drag: the window glides back to where it was picked up (a re-grab within 2.5 s continues the same move), in its original form (docs/widgets.md, WG14), including the Shift scale pin it had (L31, WP1); the halos it glides over are whole again behind it. | implemented |
| L28 | Apps don't take focus by asking for it: an xdg-activation request takes focus only when it comes from input in the app the user is using (a link opened from it); otherwise it's attention (docs/widgets.md, WG15). Scottland implements the protocol; Wayfire's xdg-activation plugin isn't loaded. | implemented |
| L29 | A window being dragged is above the widgets, and stays above them after it's let go for as long as a re-grab would continue the move (2.5 s, L27: fingers lifted to reset on the touchpad); then the widgets float above it again. Esc cancellation ends the hold, including after a re-grab. Going to another window ends the hold at once, and that window comes (and stays) in front: the hold never leaves a dropped window in front of the one the user is on. A window the user set always-on-top keeps that. | implemented (headless) |
| FS1 | Full screen is for focus (tenet 6): while a fullscreen window is in front on a screen, that screen's widgets slide off its edges (each toward its own rail's edge) and are hidden; when no fullscreen window is in front anymore (left full screen, closed, or another window came in front), they slide back to their places. Explicitly entering Alt hints brings them back while asking, and ending hints hides them again if full screen remains in front ([windowing-keys.md](../docs/windowing-keys.md), WK12). While any screen is in focus, Scottland runs the `focus.d` hooks with `on`, and with `off` after (`scottland-focus-mode`: the integrations' hooks, then the user's in ~/.config/scottland/focus.d); integrations use them to hold notifications. A test session runs only its own hooks (`SCOTTLAND_FOCUS_HOOKS`). Widgets mapping late arrive hidden while their output is in fullscreen focus (unless hints explicitly reveal them). Reload reconstructs promotion on every output, including one without keyboard focus. | implemented (headless) |
| L30 | Presenting a window ("I want to see this now", e.g. picking it or its agent in a launcher): any process can ask for it over IPC (`scottland/present {window}`, the window's id or its widget's). A widget opens as if clicked (WG17); a window in a side zone flies to its remembered center spot, else the nearest least-overlapping full-size center spot, and grows to 100% ([windowing-keys.md](../docs/windowing-keys.md), WP1–WP5); a window already in the center zone stays put. Each ends up raised and focused. A plain focus request (a switcher, xdg-activation) does not move anything. | implemented (headless) |
| L31 | Holding Shift while dragging a window keeps its current scale wherever it goes, and dropping it with Shift held leaves it at that scale. Letting go of Shift during a drag returns it to zone scaling; a later drag without Shift clears the pin. Shift+arrow in Window mode also pins the scale through its coast (WK19). Each periphery zone memory keeps the pin the window had there, restored when a cycle returns it (WP1). Alt-drag does not pin; Super+Alt+drag remains resize (L20). Starting a drag suppresses Alt window hints for that chord. | implemented (headless) |
| L34 | Quick Alt+Tab and Alt+Shift+Tab switch among center-zone windows in most-recently-used order, focusing and raising the previewed window on Alt release. Side-zone windows and widgets are excluded. See [WK32](../docs/windowing-keys.md). | implemented (headless) |
| L35 | Native Wayland content drag and drop preserves the app's pointer-press serial and implicit grab through `wl_data_device.start_drag`, without starting a Scottland window move. A receiving window gets the offer and drop at its client-local coordinates, including peripheral scaling; an interactive rail widget that accepts the payload receives it at 100%. See [native DnD evidence](../docs/native-dnd.md). | existing behavior verified on plumbus headless for GTK text, Chromium links and Nautilus files; Mike's trapped-source preview remains unreproduced |
| L32 | Pointer, touch and three-finger move releases coast from a least-squares fit of the last 100 ms of timestamped input positions, with no coast below 60 logical px/s or after a 50 ms pause. The same per-axis constant movement deceleration and velocity cap as keyboard inertia apply (WK17/WK20/WK24), including the final partial tick. Zone/scale follow the center live; an L31 scale pin survives and sets the collision footprint. Exposed boundaries follow WK20: top/bottom stop with 100 pt of the live scaled footprint visible; an exposed side widgetizes once that footprint touches the rail boundary, using WG22's morph, and adjacent outputs permit passage. All app motion axes end at that handoff; WG1-ineligible windows stop at side contact without changing form. An explicit pointer/finger rail drop still widgetizes without coast (WG1). Esc after release stops motion where it is; a new grab catches that window immediately. Esc while held retains L27 return behavior. Stationary drops retain their exact position. See [drag-coast.md](../docs/drag-coast.md). | implemented (headless) |
| L17 | Trackpad (two-finger) scrolling honors `input/touchpad_scroll_speed`, applied live; the shipped default is 0.2. (Wayfire 0.11 ignores it for touchpads, WayfireWM/wayfire#3148; the plugin applies it until the fix ships.) | verified (on Mike's trackpad, osanwe) |
| L13 | A window moved onto a widget rail becomes a widget (any program, chosen per app, default card); see [docs/widgets.md](../docs/widgets.md) (WG1–WG27, including immediate, continuously paced window → widget morphs, elastic size transitions, and focused-widget Return). | implemented (headless) |
| L14 | Tiling rules apply to windows. | not built (rules not yet specified) |
| L15 | Scottland copies the display scale the user's Hyprland uses (e.g. 4K panels are not tiny). | not built |
| L16 | How widgets sit on a rail: free-floating where dropped, at the widget's own size ([docs/widgets.md](../docs/widgets.md), WG4). | implemented (headless) |

L33 changes the move renderer for L8–L10, L23/L25/L27/L29/L31/L32 and A5/A12.
Their current-path validation is isolated headless input and pixel checks, recorded in
[the live-drag report](../docs/live-drag.md); earlier physical verification does not
verify this new path. No physical session was used for L33.

## Window appearance and handles

The goo is the default (`scottland/goo = true`), with independent per-window halo bands
when switched off or when the GPU cannot run it. With goo enabled, one liquid
field per screen replaces its drawing and field input; the restated appearance and handle behavior
is in [docs/goo.md](../docs/goo.md#halo-jobs-with-goo-enabled). Input, proximity and palette jobs remain.

Default-on/fallback regression results for A3–A12 are recorded in
[goo-default validation](../docs/goo.md#goo-default-validation-2026-10-01); these are isolated
headless checks, not physical-display verification. GO11/GO12 add stacking-aware overlap film
and whole-control cloud/glow highlights; see [their validation](../docs/goo.md#overlap-film-and-control-highlight-2026-10-02).
GO13 adds cubic reconstruction and a device-pixel antialiased outline to goo, film and
highlight edges; see [its headless visual and cost checks](../docs/goo.md#go13-inexpensive-antialiased-contours-2026-10-02).
GO14/GO15 add a rounded depth profile and wallpaper dye; see [their design, controls and
headless evidence](../docs/goo.md#go14go15-depth-and-wallpaper-dye-2026-10-02).
GO16 makes widget and inset CSD goo, fallback halos and their controls follow the visible alpha
contour, including badges; see [its headless shape and cost checks](../docs/goo.md#go16-widget-alpha-contours-2026-10-02).
GO27: the goo never paints over dry window content (a reused-backdrop breath restores only outside it); see
[the bug, cause and test](../docs/goo.md#go27-no-backdrop-inside-a-window-2026-10-04).
GO26 gives breath keyframes a ceiling-and-scale rule (only the keys the swing needs; above the ceiling the
spacing widens; the exact path only for real failures, with the reason reported); see [the rule and
measurements](../docs/goo.md#go26-ceiling-and-scale-keys-2026-10-04).
GO24 makes wallpaper soak a watercolor: local pigment in all of the goo, graded by thickness, state colors in a
narrow wall band, coasting to rest and staying; see [the design, pictures and checks](../docs/goo.md#go24-watercolor-2026-10-03).
GO21 makes the sleeping goo's cheap paths exact in device pixels at any scale, rotation and layout, and
fixes backdrop reuse not hearing changes under a strip; see [the test matrix](../docs/goo.md#go21-exact-under-scale-rotation-and-two-outputs-2026-10-03).
GO20 wakes the goo for a wallpaper only when its pixels change, and leaves dry bands and window
content out of the work an app frame causes; see [its causes and measurements](../docs/goo.md#go20-wallpaper-wakes-and-app-frames-over-a-sleeping-goo-2026-10-03).
GO19 makes a breath cost only what it changes (no repaint under the strips, quiet wakes, a ring-only
fallback halo); see [its causes and measurements](../docs/goo.md#go19-what-a-breath-still-cost-and-cutting-it-2026-10-03).
GO17 makes attention breathing a local draw-only modulation while simulation sleeps;
see [its curve, damage and cost evidence](../docs/goo.md#go17-draw-only-attention-breathing-2026-10-02).
GO10 also caches the settled goo surface for inexpensive composition over redrawing apps;
see [its redraw-cost validation](../docs/goo.md#go10-settled-goo-over-redrawing-windows-2026-10-02).


| ID | Invariant | Status |
|---|---|---|
| A1 | No window chrome: Scottland asks every app that allows it for server-side decorations and draws none (no title bars, no borders). GTK4/libadwaita apps keep the header bars they draw themselves; Chromium is switched to server-side decorations through additive app tuning (C7). | implemented (Chromium tuning not built) |
| A2 | Every window is a rounded rectangle; the corner radius is double Omarchy's (10 pt), scaling with the window. | verified |
| A3 | Every window has a halo, always visible: a translucent rounded band around it, as if a larger rounded rectangle hung behind it, with corners concentric to the window's. Widgets and client-decorated surfaces with inset root bounds use their visible alpha contour, including badges (GO16). It visually separates overlapping windows. Its thickness scales with the window (about 10.7 pt at 100%). Goo mode: see [docs/goo.md](../docs/goo.md#halo-jobs-with-goo-enabled). | implemented |
| A4 | The focused window's halo is less translucent, in the theme's highlight color; other windows' halos are more translucent, in a neutral tone that follows light/dark (A8). Focus changes cross-fade, and the newly focused window's halo is disturbed: it bulges briefly and settles in slow waves.  Goo mode: see [docs/goo.md](../docs/goo.md#halo-jobs-with-goo-enabled). | implemented |
| A5 | The halo is the move handle: dragging it anywhere outside the corners moves the window, with the same live scaling as Super+drag. Its grab area is never thinner than 12 pt on screen, even when the drawn halo is. | implemented |
| A6 | The halo's corners are resize handles only when the window can resize: dragging one resizes around the window's center, outward growing. Widgets and non-resizable windows (resize permission denied, or both dimensions fixed by min/max hints) have no resize-corner handle or corner cloud/glow, with goo on or off; the band still moves them and sides still highlight normally. A window with only one fixed dimension can still resize in the other. When the cursor comes near a corner, that part of the halo turns cloudy (the goo thickens, more opaque) and its glints strengthen, as if the light source brightened.  Goo mode: see [docs/goo.md](../docs/goo.md#halo-jobs-with-goo-enabled). | implemented; isolated headless fixed/free-size input and pixels checked with goo on/off (2026-10-02) |
| A7 | When the cursor pauses for 0.5 s within 50 pt of the halo (inside or outside the window, including over another window's content; windows behind the one the cursor is over don't respond), the halo swells to a fixed on-screen thickness of twice the full-size halo (~21 pt), however small the window (only the resting halo scales; the 50 pt doesn't scale either); any motion restarts the wait. The swell moves like goo: it bulges, overshoots and settles with slow, irregular, low-frequency waves along the edge. It stays while the cursor is within the 50 pt, and sinks back the same way about 0.5 s after the cursor leaves (never while dragging). Dragging doesn't count as pausing: the wait starts when the window is let go, so the swell always animates. | implemented |
| A9 | The halo looks like liquid: shaded as a rounded surface with a bright rim and glints, with soft organic variation, not a flat tint.  Goo mode: see [docs/goo.md](../docs/goo.md#halo-jobs-with-goo-enabled). | implemented |
| A10 | Each fallback halo is its own band: halos do not merge, fill concave corners or bridge gaps, and input belongs only to the window's own band (with A5's minimum grab target). Screen-wide goo is on by default and owns pooling and bridges instead: [GO2/GO3](../docs/goo.md). Switching goo off or an unsupported GPU retains the independent halo and A3–A9/A11–A12, including the live attention breath. | implemented; plumbus headless separate-band pixels, real pointer/touch input, and goo-off attention pixels checked |
| A11 | A close dot sits at the middle of the halo's bottom edge, appearing as the cursor comes near; clicking it closes the window. There is no minimize.  Goo mode: see [docs/goo.md](../docs/goo.md#halo-jobs-with-goo-enabled). | implemented |
| A12 | The halo works by finger with no wait: dragging it moves the window (grabbed exactly at the finger), dragging a corner resizes around the center, and touching the halo shows the close dot for a few seconds so it can be tapped. | implemented |
| A13 | Interface sounds are synthesized at runtime (no sample files), follow the system volume and mute, and can be turned off (`scottland/sounds`). | implemented |
| A8 | The halo's neutral tone and the close dot follow the desktop's light or dark color scheme (the desktop portal's appearance setting, or GNOME's color-scheme), switching live. The highlight color comes from the desktop portal's accent color, or from an integration (the Omarchy adapter supplies its theme's accent), and also switches live. | implemented |
| A14 | Windows and widgets use live, eased focused/unfocused opacity for their center, side or widget zone; Window mode has its own focused/unfocused pair while active, and fullscreen is always opaque. The eight settings default to 1.0 and are exposed in metadata, `scottland-ctl` and Settings (S20). | implemented (headless) |
| A15 | Sun following (on by default) sets the standard light/dark preference only when local sunrise/sunset disagrees with its current mode. Geoclue is preferred, saved coordinates are the fallback, and IP location is the last fallback (on by default, can be turned off). Scottland follows the active theme; distro/adapter engines choose named themes (S21). | implemented (headless) |
| A16 | The unfocused neutral edge tone is adjustable from black to white separately for light and dark schemes, with shipped values preserving today's light/dark colors exactly. Its strength ranges continuously from clear refraction to today's full tint in both goo and fallback halo, independently of GO23 state dye strength. Focus, attention and Window mode hint colors retain full strength under this A16 control; the live controls are in Settings (S22). | implemented; plumbus unit, live Settings and goo/halo visual checks (2026-10-03) |
| A17 | The pointer follows the desktop's text size: cursor size = 24 times GTK's standard `text-scaling-factor`, rounded up to the next nominal image size provided by the active cursor theme (1.6364 → 48). It applies to Scottland's own cursor (`input/cursor_size`) and apps (`org.gnome.desktop.interface cursor-size` and `XCURSOR_SIZE` for new launches), and changes live when text scaling changes. Core behavior, no Omarchy dependency. (Mike, 2026-10-03) | implemented (plumbus headless live-setting check) |
| A18 | Attention color is selectable in the Goo settings: Theme follows the active palette; Warm and Cool use the documented light/dark colors in [GO22](../docs/goo.md#go22). The choice applies live to breathing and bulging attention on windows and widgets in Goo and fallback halos; the theme family and colors follow light/dark changes. Save, Cancel and Defaults include the choice. | implemented; Plumbus headless Settings input and Goo/fallback screenshots (2026-10-03) |
| A19 | The Goo Dye strength setting scales the focus accent, attention and Window mode hint colors in Goo and fallback halos from faint to stronger than today's look. Default 1 preserves the original Goo shader path; above 1, the final dye weight is capped at full opacity. A16's unfocused neutral edge and wallpaper soak remain independent. The setting applies live and is saved with Settings. | implemented; Plumbus live Settings input and Goo/fallback light/dark screenshots across 0.25, 1 and 1.5 (2026-10-03) |

## Layout configurator (`scottland-settings`)

The settings app (Layout, Goo, Window mode, Translucency, Widgets and Sunlight tabs, zone overlay and hints): see [docs/settings.md](../docs/settings.md) (S1–S22).

## Desktop state

The single desktop model and its reactive subscription/launch/audit contracts are in
[../docs/desktop-model.md](../docs/desktop-model.md) (DM1-DM9).

## Key layers

| ID | Invariant | Status |
|---|---|---|
| K1 | A focused surface can add a temporary shortcut layer: claimed keys reach that surface as ordinary press/release events with modifiers intact, while every unclaimed key uses the user's current shortcuts (including live changes, imported functions, release bindings and remaps). Native toplevels and layer-shell surfaces work independently even within one client. Unmap, close or client disconnect removes the layer; set replaces it. Full rules and IPC: [../docs/key-layers.md](../docs/key-layers.md), KL1–KL8. | implemented (plumbus headless, including combined Alt hints); physical verification pending |

## Session

| ID | Invariant | Status |
|---|---|---|
| E1 | When Scottland is the user's only graphical session, it acts as the graphical session for systemd (scottland-session.target), so user services tied to graphical-session.target start with it and stop with it, however the session ends. | verified |
| E2 | Quit keys inside Scottland: Ctrl+Alt+Backspace, Super+Shift+Escape. | implemented |
| E4 | `scottland-reload` (Super+Ctrl+Alt+R) loads the current plugin build and config into the running session in place: windows stay open, new settings are registered and take their saved values. | verified |
| E5 | Each session records its environment at startup; `scottland-exec` runs commands in a chosen session with that environment and `scottland-reload` goes through it, so reloading from any shell (another desktop, ssh, an agent) behaves exactly like Super+Ctrl+Alt+R and never leaks the caller's variables into the session's helpers. | verified |
| E6 | Scottland ships an agent skill (`scottland`) and links it into each installed coding-agent harness's skills folder at login (Claude Code, Codex, ~/.agents, pi, Hermes), as Omarchy does with its own: how to configure Scottland, find app-ids (`scottland-ctl windows`), set touchscreen scrolling, and later the widget system. | implemented |
| E3 | Personal overrides in `~/.config/scottland/overrides.ini` are applied last and win over shipped defaults; Scottland never writes that file. | implemented |
| E7 | The session's config is never left empty or half-written by Scottland: config builds (the config watcher, `scottland-reload`, session start) run one at a time, each from its own temporary file, and an empty build never replaces the config. (An interleaved pair once emptied it, and Wayfire loaded every setting at its default: snapping on, natural scrolling off.) | implemented (tests/build-config-test.sh) |
| E8 | A new session retains the preceding session's `wayfire.log` as `wayfire.log.previous`. The launcher enables core dumps within the login's hard limit; after the core plugin loads, ABRT and QUIT use kernel core dispositions so a hung main loop can be captured with all threads. On older builds use QUIT. Widget launches record their initiating path. See [compositor-hangs.md](../docs/compositor-hangs.md) for capture commands and host requirements. | implemented (plumbus: 3 launcher checks; real ABRT/QUIT cores, 19 thread stacks each); live hang unresolved |

## Window keys and contention-aware placement

See [docs/windowing-keys.md](../docs/windowing-keys.md): WK1–WK39 (window mode: Alt-alone hold, hint holds and pairing, theme-derived Vimarchy hints with desktop text sizing and exterior widget attachment,
start-relative cycles, double-tap to rail, inertial arrows/center resize, input ownership, full screen and focused-window-anchored temporary visual exposure for interior window hints)
and WP1–WP8 (zone memory, side choice, shared rectangle placement, and which zone a spot reads as). Statuses and verification are recorded there.

WK15–WK16 double-taps use the gap from the final key release to the first key press of
the repeated complete hint. The 300 ms default therefore leaves the same repeat gap
for single- and multi-letter hints. Real human-timing validation on plumbus covers
focused/unfocused windows, both avoidance settings, physical-repeat filtering and
the immediate WK6/WK34 first-press behavior; see the report in that design document.

L29 regression: `state-model-test.py` re-grabs an ordinary dropped window during the hold
and cancels with real Esc input; the scene must return to its ordinary layer and the hold ends.

FS1 regression: late-mapped widgets respect fullscreen before their first visible frame.
`state-regressions-test.sh` covers late adoption and reload with keyboard focus on another
headless output. The model audit checks visibility against Wayfire fullscreen promotion.

E4 regression: `tests/reload-touch-focus-test.sh` reloads after a window and a card were
moved by finger, and with a finger drag, a mouse morph, a collapse/expand animation and a
card peek in flight; the next mouse and touch input must reach a running compositor. (A
touch drag's grab stayed Wayfire's pointer focus after release; the first mouse motion after
a reload sent it a pointer leave in the unloaded plugin. Unload now hands that focus to an
inert node.)

Attention bulging is measured as shore motion, separately from emission, by
`tests/attention-bulge-test.sh` (GO17/WG15): cached and exact goo stay asleep;
per-window fallback halos visibly bulge too. See the 2026-10-03 correction in docs/goo.md.
