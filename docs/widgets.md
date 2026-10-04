# Widgets

When a window is moved onto a screen-edge widget rail, Scottland shows a **widget** in its
place: a compact, live stand-in for the app. Widgets don't depend on apps adding support.
Scottland decides what to show for each app, ships defaults, lets apps ship their own widget,
and lets the user swap in any widget for any app.

Status: designed with Mike (2026-09-30/10-01) and implemented (2026-10-01). Statuses follow
[core/INVARIANTS.md](../core/INVARIANTS.md): **implemented (headless)** = exercised with real
input, real widget programs and real D-Bus in a headless session (`tests/widgets-test.sh`, end
to end) or by unit tests (`tests/widget-launch-test.py`, `tests/widget-bus-test.py`); **verified** = also on a
real session (plumbus). Pieces: the plugin (rail drops, hiding, adopting
the widget's window, placement, tied lifecycles; IPC `scottland/widgets`, `scottland/widget-action`,
full-snapshot events `scottland-widgets#`, `scottland-model#`), `scottland-widget-launch` (choice, context,
exec), `scottland-widget-bus` (D-Bus, badges, mailbox, state files), the card
(`core/widgets/card/`).

The L33 move-controller change rechecks WG1/WG13/WG14/WG22 through the same drag
handlers with Scottland's enabled live scene subtree. See [live-drag.md](live-drag.md)
for isolated headless input, pixels, regression and GPU results; prior physical
verification of the stock move path does not verify this new path.

## Invariants

| ID | Invariant | Status |
|---|---|---|
| WG1 | Moving a window onto a widget rail (any drag: Super+drag, the halo, three-finger or touch) turns it into a widget when the pointer (or finger) enters the rail, wherever the window was grabbed; the window's own position doesn't decide. It changes while you drag, not on the drop (WG13). A widget stays a widget while the pointer is between the screen edge and the widget's inner side (wider than the rail itself, so it can be grabbed anywhere and slide along the rail or switch rails); the pointer going past that turns it back into the window, centered where the widget was dropped, on the screen it was dropped on. Dialogs (windows with a parent) and fullscreen windows aren't widgetized. | verified (Super+drag, on plumbus 2026-10-01); halo/three-finger/touch drags: implemented |
| WG2 | A widget is any program: a Quickshell (QML) file, a GTK/Qt app, a web view, a TUI, anything (its window may come from a process it starts and leaves running). It runs with the user's privileges, like any app, and may use anything on the system to render itself (files, D-Bus, commands, the network). Scottland imposes no widget API. | implemented (headless) |
| WG3 | Widgets are fully interactive: their windows get keyboard, pointer and touch input like any window. | implemented (widgets are ordinary windows; input inside one not yet tested) |
| WG4 | Placement: a widget is free-floating on the rail, above all ordinary windows (always on top), centered where its window was dropped and kept wholly on screen with room for its halo at its widest; each drop on the rail places it again, gliding (~260 ms) from where it was let go to its place against the edge rather than jumping. Widgets are always at 100%: they never follow the zone scale, while dragged or when dropped. A widget's window is placed by Scottland as it maps (Wayfire's place plugin is told it's positioned), and keeps its screen-edge side when it changes size. Client identity is read from the toplevel’s Wayland surface resource before mapping, without unstable Wayfire headers, so rail gravity is present in the mapping transaction and changes atomically with rail placement. Resize placement uses pending geometry in that transaction, with no corrective move after a size notification. | implemented (plumbus headless 2026-10-01: 7 mapping/resize checks, including both rail changes; two extra-move checks failed before the fix; no real-screen run for this change) |
| WG5 | Lifecycle: the real window stays alive while widgetized, so restoring is instant. Its image remains visible through startup until the card can take it over (WG22); the real window is hidden after that handoff. The window and its widget are tied: closing the widget closes the window (shown again first, so an app's "save changes?" question is visible), and closing the window closes the widget. Dragging the widget off the rail restores the window and dismisses the widget (not a close). A widget whose window doesn't appear within 8 s is abandoned: it's ended and the app's window restored. A widget asked to close that's still running 3 s later is ended. Ending a widget ends every process it started (each widget runs in its own systemd scope: SIGTERM, then SIGKILL after 2 s), never an unrelated process. Unloading the plugin restores every app window and ends every widget; a reload (scottland-reload) keeps them: the outgoing plugin hands its widgets to the new one. On load, any window whose center is on a rail becomes a widget again (WG1), whatever left it there; a reload also replaces Scottland's helper services when their installed code is newer. | verified for drag-off restore (plumbus); the rest implemented (headless) |
| WG21 | A widget has one lifecycle: previewing, docked, restoring, closing or handed-over. One transition function applies visibility to Wayfire; renderer disable leases are resources, never independent logical flags, and are returned on unmap/unload (only handed-over app leases transfer). Collapsed intent and temporary peek presentation are independent of lifecycle. | implemented (headless) |
| WG22 | Every window → widget transition is a continuous compositor morph, like widget → window: the visible app image moves/shrinks into the card’s place and cross-fades into it; no hide/show cut. This includes inertial pushes and drag coasts reaching exposed side rails (WK20), every starting-zone hint cycle, double-tap to rail, collapsed-mode arrivals, rail drops (including release before the preview is ready), Esc returning an undocked app to its original widget, and rail recovery on plugin load. The existing snapshot mixer owns the handoff; shape uses WG23’s 360 ms spring (240 ms circle easing when `widget_bounce` is zero), and contents retain their 180 ms fade. A card’s ordinary Wayfire map animation is suppressed so it cannot zoom/fade the composition a second time. Goo (or the fallback halo) follows the visible rectangle and interpolated scale. The app stays visible and moves/shrinks during startup toward the same provisional size used by drag previews (WG27); the applied card corrects that estimate over 180 ms while retaining the full 180 ms content fade. A card disappearing during the handoff restores the app and never closes it (WG5). | implemented (headless); validation below |
| WG23 | Every widget expansion and contraction (Super+M, peeking on hover or attention, collapsed arrival, any other trigger) settles with a single elastic size/shape bounce, including its goo. `scottland/widget_bounce` sets the amount (default 0.04, range 0–0.1); zero disables the overshoot. S19 provides its Widgets tab control for this setting. (Mike, 2026-10-02) | implemented (headless); Settings control implemented; validation below |
| WG24 | Repeated window/widget transitions, including attention, interrupted entry, re-grabs and cancellation, keep the compositor responsive. Window avoidance alone never changes a window's real zone or widget lifecycle. | repeat/race and large-window probes pass on plumbus headless; October 2 live hangs unresolved; see [compositor-hangs.md](compositor-hangs.md) |
| WG25 | Return or keypad Enter on a focused widget always opens its window like a click (WG17: to its remembered center spot, else the nearest least-overlapping full-size center spot, raised and focused). Scottland consumes the key before the widget receives it, including when a text field has focus or the widget claims it through a key layer (K1). Other keys still reach the widget normally; windows are unaffected. (Mike, 2026-10-03) | implemented (headless) |
| WG26 | Making room on a rail: while a window is dragged onto a widget rail, or a widget is dragged along a rail, the widgets in the way move aside live during the drag, only as far as needed (P2): only those that overlap the dragged item's landing spot move, and a push ripples to neighbors only when they in turn are in the way. No retiling of the rail. Widgets stay on their rail (P1). The shifts are visual during the drag and become real on drop; cancelling the drag (Esc, or dragging back out of the rail) returns every widget exactly (P5). The solve is bounded and never blocks the pointer (P8). (Mike, 2026-10-03) See [Making room during a drag](spread.md) for the signed-off rail profile and shared presentation contract. Clarified (Mike, 2026-10-04): the rail make-room is a spread over the whole rail: any widget on the rail may move when that's what it takes, with the least total movement, widgets keeping their order; touching as little as possible is the preference, not a hard limit; widgets overlap only when the rail is truly full. Superseding any 'dropped card settles' behavior (Mike, 2026-10-04, P14): the dropped widget ends up exactly where the user put it; only the other widgets move around it, in least total movement; if the rail is truly full they overlap it. | verified (plumbus headless, 2026-10-03; unit suite and real stipc drags) Revised (Mike, 2026-10-04): (a) a hold buffer: dragging over widgets doesn't push them right away; when the drag pauses (dwell), the rail re-lays out, and it re-lays out again at the next pause; (b) every widget move is animated, large ones included, never a snap; (c) it applies to every way something enters a rail: dragging or flinging a window onto it, Window-mode keys that widgetize, as well as dragging a widget along it. |
| WG27 | Window → widget conversion starts from the displayed app image on the next morph tick, including drag/drop, fling and Window-mode keys. It continues toward a provisional card rectangle while the widget client starts, then corrects from the current drawn rectangle to the applied card and cross-fades without a jump. Healthy conversion callbacks never fork the compositor or compile the blend shader. Simple client textures and the last composed app buffer are retained rather than recaptured; complex uncached scenes retain the capture fallback. Launch cancellation, scoped teardown and reload retain WG5’s process ownership. | implemented (plumbus headless); validation below |
| WG6 | Choosing a widget, in order: the user's assignment (`~/.config/scottland/widgets.ini`, app-id → widget), else the app's own widget (named by its `.desktop` entry, `X-Scottland-Widget=`, or installed for its app-id), else a Scottland built-in for that kind of app, else the default card (WG10). Any widget can be assigned to any app. | implemented (unit test) |
| WG7 | A widget package is a directory with a `widget.toml` manifest (`id`, `name`, `apps` = app-id regexes it suits, `exec` = the command) and whatever the command needs. Packages are found in `$SCOTTLAND_WIDGET_PATH` (colon-separated, if set; relative entries are taken from the current directory), `~/.local/share/scottland/widgets/` (the user's), `/usr/share/scottland/widgets/` (installed with apps, removed with them) and Scottland's built-ins (`/usr/lib/scottland/widgets/`); the first package with an id wins. | implemented (unit test) |
| WG8 | Launch context: the environment carries the window and launch identity (`SCOTTLAND_WIDGET_ID`, `_APP_ID`, `_ICON`, `_NAME`, `_DESKTOP`, `_PID`, `_WINDOW`, `_STATE`) and the palette path (`SCOTTLAND_PALETTE`). Mutable title, rail, collapsed mode and badge come only from the complete state file, written before exec. Prepare returns an explicit error if it cannot write that file; it never launches with stale or partial state. `.desktop`-style placeholders still fill the manifest's command per argument (`%a` app-id, `%t` initial title, `%i` icon, `%p` pid, `%w` window id, `%r` initial rail, `%d` package directory, `%%`). The widget runs in its package directory. Resolved identity and traits are submitted to the plugin for that launch. See [desktop-model.md](desktop-model.md), DM4. | implemented (headless) |
| WG9 | Live updates and actions over D-Bus: Scottland's widget service (`org.scottland.Widgets`, one per session bus) publishes one `org.scottland.Widget` object per widget (`/org/scottland/widget/<id>`) with the window's properties (Id, Version, Revision, AppId, Title, Pid, Window, Rail, Focused, Urgent, Badge, Data, with PropertiesChanged signals) and methods `Restore()`, `Close()`, `Focus()`. Widgets that don't need it ignore it. Driven by complete versioned model snapshots, not polling; property signals carry the complete public property set, with the model version and the revision of the already-written presentation file. Storage failures never drop the event watch or block other cards: pending files retry on the next snapshot (even the same version) and once a second until written. Broken IPC subscriptions reconnect and take a complete snapshot, including when subscribe is temporarily unavailable during reload; healthy subscriptions do not poll. | implemented (headless: properties, live Title, Restore, Close; not Focus, Urgent) |
| WG10 | The default widget, for any app with none configured, is a card: the app's icon (from its `.desktop` entry via the icon theme; web apps are matched by their site; else the theme's generic app icon, else the app's initial), the window's title (bold: what's in it) over the app's name (regular), and an alert badge when the app publishes a count (Unity Launcher API; partial updates, e.g. progress only, keep the count). The icon is on the screen-edge side of the text (left of it on the left rail, right of it on the right rail), with the alert badge overlapping the **card's upper corner opposite the screen edge**: upper-right on the left rail, upper-left on the right rail, in both expanded and collapsed (icon-only) form. The badge follows rail changes, stays readable (counts above 99 read `99+`), and its overhang stays inside the client surface so rail placement keeps it wholly on screen. Space for that overhang is reserved even without a count; badge updates never shift the contents. The icon is centered on the card's visible body, not on that reserved room: exactly centered when collapsed, and the open card's row is vertically centered on it (fixed 2026-10-04: it sat 3 px toward the badge corner; `tests/widget-icon-center-test.sh` measures it in screenshots). Goo and fallback halos follow the visible card body and badge (GO16), ignoring that transparent reservation. The card is as wide as its text needs, up to 320 pt, and square around the icon when there's no text; as it changes size, its screen-edge side stays put. Title, badge and rail stay live through the state file named by `SCOTTLAND_WIDGET_STATE`. Its colors follow the session's palette (`SCOTTLAND_PALETTE`), live. Mike approved the look (2026-10-01); layout, typography and sizing per his review the same day. | implemented (headless); badge rail/collapse/count pixels checked 2026-10-02 |
| WG11 | Optional data mailbox between an app and its widget, for apps without a service of their own: the app calls `org.scottland.WidgetData.Publish(json)` on `/org/scottland/Widgets` and its widget's `Data` property (and state file) changes; a widget's `Send(json)` is broadcast as `Received(app_pid, window, json)` for its app. Callers are identified by their D-Bus credentials and process tree: only an app (or its helpers) can publish for its windows, only a widget can send for its window. (X11 apps are identified by the process they declare, `_NET_WM_PID`: X11 offers nothing stronger, and X11 apps can already see each other.) | implemented (headless: Publish, Send, refusals of both) |
| WG12 | Apps learn their state from Scottland: `org.scottland.Windows.GetState()` (called from the app's own process tree) returns whether it's widgetized and its window's scale (where it's going, never a step of an animation); `StateChanged(pid, widgetized, scale)` signals changes. Both give the same answer: an app with several windows is widgetized if any is, with that window's scale, else the scale of its first open window. Apps that don't listen are unaffected. | implemented (headless) |
| WG13 | Live morph: a window dragged onto a rail changes into its widget while it's dragged, and a widget dragged off its rail changes back into its window, both ways as often as the drag goes in and out. The change animates (WG23: 360 ms with bounce, 240 ms without): the frame (and halo) reshapes between the window's size at its scale there and the widget's size, while the contents cross-fade from one form to the other, each scaled evenly to cover the frame. The widget is started (unseen) when the drag first reaches the rail; until it exists (its program takes a moment to start the first time), the frame reshapes toward the size the default card will have for that window (or the collapsed square) around the window's contents, and when the widget appears the frame eases onto its real size (~150 ms) and its contents fade in: no snap. The other form's contents are live during the morph: a widget's hidden window is asked to draw as it's dragged out, so peeking shows what the app shows now. The drop keeps the form shown, as shown: a window dropped from a widget drag is at its size at once (no animation); a widget dropped glides only to align with the screen edge (WG4). A widget previewed this way and dragged back off is ended. | implemented (headless) |
| WG14 | Esc cancels any window drag (pointer, touchpad or touch; Super+drag, the halo, three-finger or touch): the window goes back to where it was picked up, on the screen it was picked up on (picking the same window up again within 2.5 s of letting it go, e.g. to reset fingers on the touchpad, continues the same move: Esc goes back to where the move began), gliding there from where it was let go (~260 ms), and if the drag changed it into its other form (window/widget), it morphs back. A cancel only ever uses that window's own origin (a drag cancelled before it moved stays put). A re-grab of what a drop turned it into (the widget a window became, or the window a widget became) continues the move too: Esc brings back the first form where the move began. Cancellation restores the saved rail and drop anchor as well as the scene position, so a subsequent card resize retains the restored position. The origin, re-grab chain and morph are owned by the desktop model's drag session ([desktop-model.md](desktop-model.md), DM7). | implemented (headless) |
| WG15 | Attention: when an app needs the user, its halo (its widget's, when it's a widget) takes the attention color (a secondary highlight from the desktop palette: an Omarchy theme's optional `attention` color, falling back to `yellow`) and breathes (a five-second Apple-inspired brightness curve and visible bulging; with goo enabled this is local render-only modulation of its cached field, never ongoing waves or dye simulation: [GO17](goo.md#go17-draw-only-attention-breathing-2026-10-02)), until the user goes to it (the window or its widget). An app needs the user when it asks to be focused (xdg-activation: a terminal's bell, a finished task), sets the urgency hint, or sends a desktop notification from the process that owns its window (one window only, so it's clear which). Other programs are sources too, by configuration ([attention.md](attention.md), AT1-AT3): each turns attention on and off under its own name, never clearing another's or the app's own bell. Scottland implements xdg-activation itself: a request made from input in the app the user is using (a link opened from it) takes focus; any other request becomes attention and takes no focus (Wayfire's plugin dropped those, so a bell never arrived). | implemented (headless: bell, notification, IPC, clearing; GO17 local breathing/sleep and GPU cost checked on Xe/RX 580) |
| WG16 | Super+M (`scottland/minimize_widget`) sets the widget mode, one of three (Mike, 2026-10-04). **Tapping** it cycles expanded -> collapsed (each widget shrinks to its icon) -> hidden (widgets slide off their screen edges, like full screen, FS1) -> expanded. **Holding** it is momentary until released: from expanded it hides the widgets, from collapsed or hidden it expands them (bringing hidden ones back); releasing returns to the mode it was in. A press is a tap if released within `scottland/minimize_hold_delay` (default 300 ms, Settings Widgets tab), so a tap acts on release and a hold never first takes a tap's step. Hidden is an explicit user choice and an exception to tenet 2's "smaller, never gone". **Window mode** (Alt) temporarily shows collapsed and hidden widgets expanded, hidden ones back in their places with their hints, until Alt is released; the mode never changes. **Attention while hidden** still shows (Mike, 2026-10-04): the widget comes in for WG19's attention peek, then keeps a 24 pt strip of itself in at its screen edge, its halo breathing the attention color, until the user goes to it; hovering the strip brings it in (WG19's delays). Full screen still wins (tenet 6). Widgets learn their presentation from the `Minimized` property and the state file; the default card becomes a square around its icon, keeping its screen-edge side; rail gravity and pending placement preserve its screen-edge side without a follow-up corrective move (WG4). Each widget independently morphs from its old snapshot to its new client buffer with WG23’s 360 ms spring (200 ms smoothstep when bounce is zero); contents retain their 200 ms blend. Capture precedes publication; a 300 ms response bound handles clients that keep their size or do not respond. The frame stays rail-anchored, content stays at natural scale with the card's icon inset interpolated, and premultiplied images mix into one result. Reversing freezes the currently displayed composition. Damage follows the animated rectangle; hit testing and each widget's halo band follow its presented alpha contour (GO16), preserving transparent insets; when goo is enabled its outline and dye sources follow it too; snapshots and the transition timer end on settlement. See presentation rendering and [widget modes](#widget-modes-wg16-2026-10-04) below. It's a mode: a window widgetized while widgets are collapsed starts collapsed (the first state-file snapshot); one docked while hidden lands, then slides off with the others; the chosen mode survives a reload (a held, momentary mode does not). Running previews follow mode changes in `model.widgets` through versioned snapshots, and reconcile to the current mode before transitioning to docked; their apps are still windows until committed. The compatibility `scottland/widgets` read excludes previews; the model subscription includes their lifecycle. A widget docked while collapsed shows its title and app once widgets are expanded again, like any other widget. | implemented (plumbus headless, 2026-10-04: real stipc taps, holds, Alt in each mode, attention while hidden, full screen, reload; see [validation](#wg16-modes-validation-2026-10-04-plumbus-headless)); no physical-screen verification |
| WG17 | Clicking the default card opens its app's window at its remembered center position, otherwise the least-overlapping full-size spot in the center nearest the card (the shared placement routine, [windowing-keys.md](windowing-keys.md), WP1–WP5): the window flies out of the card and grows to its size there; the card goes. (A click on the card's halo is no move, WG13.) | implemented (headless) |
| WG18 | A widget whose manifest sets `touch_drag = true` moves with a single-finger drag anywhere on it, at once (no long press), while a tap is still the widget's. For widgets that drag nothing themselves; the default card sets it. Off by default, so a widget's own finger drags (sliders, drawing) stay its own, and it's lifted with a long press as any window. The launcher tells Scottland over IPC (`scottland/widget-traits`). | implemented (headless) |
| WG19 | Peeking at a collapsed widget: after `scottland/widget_peek_enter_delay` (default 150 ms, range 0–3000) over its visible card or goo/handles it shows expanded (title and app); leaving for `widget_peek_leave_delay` (default 100 ms, range 0–3000) collapses it again. Sweeps and brief departures do not flicker. A collapsed widget receiving attention shows expanded for `widget_attention_peek_duration` (default 5000 ms, range 100–30000); each renewed attention request restarts that interval, including the same source. S19 edits all three timings live in the Widgets tab. A pointer over it at expiry keeps it expanded until leaving. Held drags preserve the current presentation until release. Collapsed intent and Super+M's mode never change: trigger state and temporary presentation belong to the desktop model. Super+M and reload end temporary peeks; fullscreen widgets never peek into view. | implemented (isolated headless); validation below |
| WG20 | The collapse binding activates once per held key. Duplicate downs, including overlapping devices, do not toggle it again; only release of that key on all held devices rearms it (device removal clears that device). Releasing a modifier does not rearm it. No time debounce discards rapid intentional presses. Edges are tracked and logged only while the binding modifiers are held or a tracked press is in progress (including its release after the modifier); plain typing emits no collapse diagnostics. Those edges record device, input/receipt time, key and latch/mode state; activations and ignored duplicate callbacks are distinct. | verified (plumbus headless 2026-10-01: 10 input/diagnostic checks, including quiet plain-M/Shift+M typing and the tracked release after Super; duplicate down failed before the fix; device overlap/removal not exercised) |

## Focused widget Return (WG25)

Return and keypad Enter use the same `open_widget()` action as clicking the card, so WG17's
remembered-center and least-overlapping placement, raise and focus stay shared. Scottland consumes
both the press and its matching release before the widget receives either one. A text field inside
the widget cannot keep Return, and a widget's key-layer claim of Return is ignored while it is a
widget. Other keys keep their usual delivery and key-layer behavior; windows are unaffected.

`tests/widget-input-test.py return` uses real `stipc` key input for the built-in card, a custom
widget, a focused GTK text entry that does not receive Return, and a custom widget whose Return
and keypad Enter claims are ignored. It also checks that an unclaimed Space still reaches the widget.

## Window-to-widget handoff (WG22)

Tenet 2 (recognition) decides the unspecified startup edge: retain the window’s visible image
until the card has a committed buffer and rail position. `widgetize` and `commit_preview`
capture before hiding; a drop freezes the current drag composition before its renderer ends.
If no card window appears, the app stays visible until WG5's launch timeout restores it.
The timeout test therefore asserts visible-and-linked during startup, then visible-and-unlinked
after eight seconds; it still checks termination of the launcher and every child. Hiding the app
before any replacement can render would contradict WG22 and tenet 2, so the old startup-hidden
assertion is intentionally replaced, not relaxed.
The existing per-widget presentation resources transfer that image to the card, mixing scaled
contents through `widget-morph.hpp` rather than adding an animation renderer. Snapshot ownership
is independent of the linked lifecycle; the visibility projection keeps the app shown until the
transfer. Restoration, disappearance, timeout and unload release these resources. Wayfire transform-update
brackets propagate both old and new bounds through enclosing cached transforms; merely damaging
the frame can leave strips of the old app image behind. The complete destination presentation is
installed before enabling the card. Esc captures before any move back to the rail. After handoff,
the hidden app is parked on its rail so unmarked unload/load can recover it too. Each transition
reads its own tick time: adopting several cards can insert new transitions during iteration, whose
start times must not be subtracted from an older unsigned tick timestamp.

All non-drag rail requests funnel through `widgetize`: WK20 inertial side contact, the window-mode cycles and double tap,
Esc returning an undocked window to a widget, and recovery of rail windows on load. Dragging
launches a preview and the drop funnels through `commit_preview`. Super+M and IPC/menu
`widget-action minimize` change existing cards’ presentation only; neither widgetizes an ordinary
window. Their collapse/expand tests remain part of the morph suite, along with collapsed arrivals.

At `1e8e57f`, both starting-zone slow cycles, double taps and immediate rail drops cut; a held
rail drag already morphed. Non-preview widgetization hid the app before card startup, while drops
ended the live morph and showed the card without retaining its intermediate image. Wayfire’s
ordinary card map zoom/fade further distorted the handoff. The new suite retains frame-by-frame
PNG screenshots, sampled scene rectangles, crossfade pixels and goo-distance observations in
its isolated session’s results directory; it also checks a disappearing card mid-morph.

### WG22 validation (2026-10-02, osanwe headless)

Merged `origin/main` at `1372aaf` (goo overlap/hover) into `widget-morph-in`. Every run used
its own `SCOTTLAND_HEADLESS_DIR`; neither the main checkout nor `wayland-1` was touched.
Geometry sampling uses a separate IPC connection so screenshot encoding cannot undersample
the 240 ms transition. The existing shape, pixel, timing, goo and cleanup assertions remain;
new checks cover concurrent arrivals, app survival on unload, rail recovery, and marked reload
during entry, including the service/scene lifecycle audit.

The earlier WIP logs contain `No space left on device` failures writing attention-source state
and copying the reload plugin. Prior test artifacts were preserved outside the runtime tmpfs
before rerunning. Those failures were not evidence of a valid lifecycle regression run.

| Suite | Result |
|---|---|
| `tests/widget-morph-test.sh` (goo on) | **185 passed, 0 failed** |
| `SCOTTLAND_TEST_GOO=0 tests/widget-morph-test.sh` | **162 passed, 0 failed** |
| `tests/widgets-test.sh` | **146 passed, 0 failed**, including 43 widget-input checks |
| `tests/windowing-test.sh` | **84 passed, 0 failed** |
| `tests/hint-style-test.sh` | **51 passed, 0 failed** |

The two final morph runs ran one at a time; an earlier concurrent run missed the existing
reversal pixel timing bound. Assertions were not widened. Final logs, sampled geometry and
PNG frames are retained locally in `build/wg22-validation.tar.gz`. This is headless validation,
not physical-display verification or a live-session deployment.

## Widget service recovery (WG8–WG9, 2026-10-02)

A full state filesystem could leave the service alive on D-Bus but permanently stop its
compositor subscription. `write_state()` raised through `on_event()`; PyGObject removed the
GLib I/O watch when that callback failed. The replica had already advanced its model version
and replaced its fields, so even another identical snapshot could not repair the missed file.
Revision counters also advanced before successful writes, and partial temporary files remained.
The old `refresh()` caught both IPC and file-write errors as “compositor unreachable”, so an
ENOSPC message with that prefix did not establish a broken IPC connection.

The live log records exactly that `on_event → replace_snapshot → write_state` traceback at
**8:55:06 AM PT on October 2**, for a `wayland-1` card. The original implementation reproduced
the lost watch with an injected ENOSPC under a real GLib main context: after storage recovered,
the next socket event was never consumed and the card file stayed unchanged. Synchronous
Prepare/refresh calls could still update files on later launches, explaining how file activity
could continue until 10:25:08 AM PT while subsequent Super+M changes at 10:27 AM PT were missed.
The shared historical log also contains test services, including tests that reused `wayland-1`
under a private runtime; its unattributed subscribe/disconnect messages cannot all be assigned
to the live service. The live owner was later found dead between 10:50 and 10:54 AM PT after a
plugin reload, without an attributable exit line. The old event callback called `sys.exit` on
IPC hangup, which is one plausible reload path, but the shared log cannot prove that it caused
this death. Current service log entries include PID and display to distinguish them.

Failed presentation writes now stay dirty, with their old complete file/revision intact. The
service finishes applying the entire snapshot, keeps its I/O watch, and retries each pending
file independently. A one-second timer runs only while output is pending; a new or duplicate
snapshot also retries it. Successful atomic replacement advances the revision before emitting
PropertiesChanged. Partial temporary files are removed on failure. Badge/mailbox persistence
uses the same atomic helper and retry mechanism. Even a failure to append the diagnostic log
cannot throw out of the event callback. Prepare returns a D-Bus error promptly on failure.

EOF, socket errors and a rejected subscription close the affected sockets and schedule another
subscription after one second. The subscription's complete snapshot catches up missed state;
the request connection is recreated when needed too. Losing the D-Bus name quits the actual
running main loop. This follows tenet 2: cards must remain recognizable, current stand-ins for
their windows without requiring the user to restart a helper.

`tests/widget-bus-test.py` exercises partial ENOSPC, an unwritable directory, continuation to
another card, unchanged-snapshot and event-free recovery, successful-file-before-signal ordering,
Prepare refusal, badge persistence retry, a real Unix-socket hangup, temporary subscribe rejection
and subsequent event delivery through GLib. It uses files under this checkout's `build/` and
never changes `XDG_RUNTIME_DIR` or contacts a session bus.

The headless harness now gives all children private config/state/cache directories and a private
D-Bus. Quickshell's hardcoded runtime log subtree is bind-mounted to the test directory using
Bubblewrap **only in those clients**; the real runtime and its Wayland/systemd sockets stay in
place. Display names and scope names therefore remain unique across live and test compositors.
Explicit `scottland-exec --display` resolves only that display, without probing other sessions.
The widget suite's temporary files and artifacts default to `build/`, and its cleanup names only
files and directories it created. Stopping the harness also stops its own widget helper. Never
use a private `XDG_RUNTIME_DIR` to isolate tests; the older validation notes below describe
historical runs, not the current rule.

The same headless suite exposed an independent WG17 card action failure: Quickshell received a
real pointer click, but its detached command did not invoke `Open`. A Quickshell `Process`
component now sends that D-Bus call. A scoped card was verified to inherit the private test bus,
and its click opened the app there.

Final validation: `tests/widgets-test.sh` passed 186 checks (including reload, pointer click,
touch tap and stacking) with no failures; `tests/widget-bus-test.py` passed all 30 checks. Its
headless session stopped. Logs and screenshots are under `build/validation/` and
`build/widgets-complete.results/`.

## Peek triggers (WG19)

Tenet 1 (managing attention) chooses a **150 ms enter delay** to ignore rail sweeps and a
**100 ms leave delay** to absorb small pointer excursions. Tenet 2 (recognition) includes the
revealed title area and the widget's actual goo/handle hit regions. Proximity lighting alone
does not count as hover. The animated visible frame and stacking determine the hit, for either
halo renderer. A held drag freezes presentation; release reconciles hover and attention expiry.
The Widgets tab (S19) edits the enter, leave and attention timers live. In this batch,
`tests/widget-peek-options-test.sh` set them to 500, 400 and 800 ms in an isolated
headless session; real stipc pointer input and an attention request passed all four
nondefault timing checks (`build/widget-peek-options-batch1-r2.log`).

Each model widget owns pointer membership, qualified hover, the pending hover deadline, and
the attention deadline. A compositor timer observes these during active peeks and delays (including
geometry changing beneath a stationary pointer); it stops when no peek/hover/grab remains.
An attention raise starts or restarts five seconds, independently of whether its source was
already present. Removing a source never renews the interval. Expiry does not acknowledge
attention or take focus (tenet 5). At expiry, a pointer already inside takes over immediately,
even if it has not completed the enter delay. Attention received during a grab waits for release
without resizing under the pointer. Fullscreen suppresses peeks, preserving attention sources
and focus (tenet 6). Lifecycle exit, Super+M and reload discard transient trigger state.

### WG19 validation (2026-10-02, isolated headless)

Final build in `scottland-peek` (branch `widget-peek`, based on `3f9f906`):

| Suite | Result |
|---|---|
| `tests/widgets-test.sh` | **186 passed**, including 40 new WG19 checks and model/service/card-render audits |
| `tests/widget-morph-test.sh`, goo off | **162 passed** |
| `tests/widget-morph-test.sh`, goo on | **186 passed** |
| `tests/windowing-test.sh` | **84 passed** |
| `tests/widget-input-test.py peek`, goo off | **40 passed**, including both rails, actual handles, stationary grabs and five-second attention timing |

Pointer and keyboard checks use stipc; attention uses `scottland/attention`. Hover screenshots
were inspected: title and app are visible on the peeking card, while the other card stays
icon-only. Both the global mode and per-widget collapsed intent remain unchanged. The goo morph
fixture now establishes expanded intent after launching its widget: toggling without any widgets
is intentionally a no-op, and an attention peek can no longer mask an incorrect fixture mode.

All sessions used distinct `SCOTTLAND_HEADLESS_DIR` and private runtimes under this checkout's
`build/`, with only the user manager's socket linked in for the existing process-scope tests.
The first attempt omitted that socket and failed scope/adoption checks. An earlier concurrent run
also missed the existing delayed re-grab timing check; the complete final run passed it without
changing its assertion. Final and earlier logs, screenshots and frame samples are retained in
`build/wg19-validation.tar.gz`. Headless sessions and their runtimes were stopped and removed.
No live session or other checkout was used, and no physical-display verification is claimed.

## Presentation rendering (WG16; mechanism for WG19)

Tenet 2 keeps collapsed intent intact through a temporary peek; tenet 4 keeps the animation
in the compositor. The card still requests its final client size once. `minimized` in widget
snapshots (and D-Bus `Minimized`) describes effective presentation; `collapsed` and `peek` in
the model describe intent and the temporary override. Super+M ends any peek and sets the mode.
A reload preserves intent and ends temporary presentation and animation resources.

`widget-presentation.hpp` owns a transition for each widget view, separate from the drag morph.
Before publishing a changed presentation it captures the applied surface, or renders an existing
transition's exact blend into a fresh buffer when interrupted. It watches the main surface's
scene damage and applied texture, keeping the original texture locked until the new one is
observed. Wayfire emits that damage after `apply_state`; a transaction or an acknowledgement
alone is insufficient. It also accepts same-buffer commits that carry a newly applied buffer.
An acknowledgement-only commit can follow the drawing commit, so the current commit flags
alone are insufficient too. The next timer step captures the applied buffer and geometry.

The old image and rectangle stay displayed while waiting. After 300 ms, a client that hasn't
responded falls back to its actual applied image and dimensions: it is never forced to a guessed
size or left frozen indefinitely. A response arriving after that bound can subsequently resize
normally. There is no new widget API or polling of presentation files in the compositor.

`widget-morph.hpp` mixes two premultiplied snapshots in one shader invocation. Content is
rail-aligned at its original scale, clipped to the frame; where the smaller image does not
cover the growing frame its edge pixels extend, rather than enlarging its icon. The built-in
card's icon inset moves between 16 and 17 pixels with the blend (17 centers the collapsed icon on the card's body, beside its badge room). Center-based frame scaling
gets a rail-side translation correction, separate from glide translation. Size-induced placement
changes are compensated and eased as well, including a taller widget clamped near the bottom.
`frame_t::screen_rect()` remains the common rectangle for the current halo renderer,
damage and the goo field. With goo enabled, its outline and state dye follow this same
animated rectangle; with goo disabled, the existing per-frame halo path is unchanged.

Input clips to the drawn frame and uses natural content coordinates. Visible snapshot pixels
that have no coordinate in the smaller live client consume input rather than clicking through
to a window behind. Drag rendering uses the current composition, including a hidden preview's
presentation; glides retain their own translation. Fullscreen visibility and widget lifecycle
remain model-owned. Unmap, restore, close, unload and settlement release transition resources.
There is no work from the morph timer once all widgets have settled.

This uses Scottland's GLES frame renderer. Non-GLES rendering retains immediate client
presentation changes, as it already does for the custom frame effects. Scottland currently has
no reduced-motion setting to honor. WG19 drives this same mechanism from pointer and attention
inputs; `test-peek` remains an isolated `SCOTTLAND_TEST_MODEL` rendering probe.

`tests/widget-morph-test.sh` samples real Super+M input on both rails and multiple adjacent
widgets, deliberate reversal, held dragging and gliding, fullscreen, close and marked reload.
A GTK fixture supplies delayed, unchanged-size and absent responses. PPM screenshots test
opacity, natural content scale and reversal pixels; the test records geometry samples and
checks that the transition count and step counter stop after settlement. All test sessions use
a private runtime on plumbus, with this checkout's helpers; no physical display is used.

Validation of the compositor morph on plumbus, 2026-10-01 (fresh headless sessions,
GLES software rendering; no physical screen or live session reload):

| Suite | Final result |
|---|---|
| `tests/widget-morph-test.sh` | **76 passed**: three simultaneous widgets, both rails, duration/geometry/reversal, neighboring halos, input clipping, peek intent, drag/glide, fullscreen, close/reload, applied buffers/fallback, pixel opacity/scale/orientation, rounded-card background and height-changing placement |
| `tests/widgets-test.sh` | **146 passed**, including all 43 existing input regressions |
| `tests/state-model-test.sh 271828 50` | **95 passed** |
| `tests/state-regressions-test.sh` | **6 passed** |
| `tests/widget-bus-test.py` | **16 passed** |
| `tests/widget-launch-test.py` | **17 passed** |
| `tests/attention-sources-test.py` | **5 passed** |
| `tests/build-config-test.sh` | **5 rounds passed**, 20 concurrent builds each |
| `tests/omarchy-focus-test.sh` | **3 passed** |
| `tests/upgrade-test.sh` against an archived `ea1d0f4` build | **2 passed** |
| `tests/present-test.sh` | **6 passed** |

The goo integration merge at main `50e563e` rechecked WG16 on plumbus: widgets passed
146 checks with goo off and on, morphs passed 76 with goo off and 86 with goo on, and the
packed GLES 2 morph passed 86. Goo's field follows the presented frame through collapse,
expansion and reversal; attention dye survives and the renderer switch remains live.
The complete matrix and initial fixture corrections are recorded in
[goo.md](goo.md#widget-presentation-merge-validation-2026-10-01). All were isolated headless
sessions; physical-screen verification remains outstanding.

The final sampled card motions lasted 200–207 ms, after 18–39 ms for applied buffers.
After settlement the transition count is zero and the step counter stays unchanged over a
500 ms observation. Intermediate card and fixture screenshots were inspected. Builds ran on
osanwe and plumbus; all tests ran on plumbus. The legacy source archive was built under this
checkout's `build/` directory; no other checkout was modified.

The private runtime is `$XDG_RUNTIME_DIR/scottland-collapse-morph-runtime`, with its own
`scottland-headless-collapse-morph` session directory and D-Bus. It links only `systemd/` to the
existing user manager so the suite can create its own widget scopes. An initial run without
that socket failed six scope/lifecycle checks; the complete final matrix above passed after
correcting the harness environment. Reload tests swap the plugin directly inside the headless
session. Logs, frame samples and screenshots are retained in
`~/.cache/scottland-test-tmp/collapse-morph-validation.tar.gz` on plumbus and copied to this
worktree's `build/collapse-morph-validation.tar.gz`. WG21 labels the lifecycle invariant that
previously duplicated WG19's ID; WG19 continues to mean peeking.

Review rework validation on plumbus, 2026-10-01, after merging `main` at `480bee1`
(desktop model, L31 and FS1) into `super-m-fixes`:

| Suite | Result |
|---|---|
| `tests/widgets-test.sh` | **146 passed**, including the 43 input regressions below |
| `tests/widget-input-test.py` (inside the widget suite) | **43 passed**: WG20 10, WG4 7, WG16 previews 12, O5 import 14 |
| `tests/state-model-test.sh 271828 50` | **95 passed** |
| `tests/state-regressions-test.sh` | **6 passed**, including late fullscreen adoption and reload on another output |
| `tests/widget-bus-test.py` | **16 passed** |
| `tests/widget-launch-test.py` | **17 passed** |
| `tests/attention-sources-test.py` | **5 passed** |
| `tests/build-config-test.sh` | **5 rounds passed**, 20 concurrent builds each |
| `tests/omarchy-focus-test.sh` | **3 passed** |

The gravity fixture now reads its title from the prepared model snapshot. The first rework
run exposed its use of the removed launch-title environment variable; aborting that input run
left collapsed mode set and caused two later collapse assertions to fail. The corrected full
run passed every check above, without weakening assertions. The model service supplies running
previews through its existing versioned subscription and explicit lifecycle; no second polling
interface or preview flag was retained.

Deployed with `SCOTTLAND_DEPLOY_DIR=Projects/scottland-super-m tests/deploy.sh plumbus --tests-only`.
Tests used `TMPDIR=$HOME/.cache/scottland-test-tmp` and
`SCOTTLAND_HEADLESS_DIR=$XDG_RUNTIME_DIR/scottland-headless-super-m`, the checkout's helpers,
and private D-Bus. Fresh sessions postdated the plugin build, and deployed source hashes matched
the checkout. `make plugin` also passed on osanwe without system Vulkan headers or the former
extra header include path; no tests or live-session changes ran there. Placement, expanded-card,
final-scene and fullscreen screenshots were retained and inspected. The isolated session was
stopped afterward. Runtime logs are in `~/.cache/scottland-super-m-review-163af63/` on plumbus;
unit logs and the initial failed run are in `~/.cache/scottland-super-m-review-725bb41/`.

No real-screen session was used. Physical overlapping devices and device removal remain
unexercised, the source of the originally reported duplicate physical input remains unproven,
and automatic hover/attention peek triggers remain deferred.

Planned built-in widgets besides the card (not built): **live miniature** (Scottland draws the real window
small on the rail itself; no screen capture involved) and **media** (MPRIS controls for players).

## Widget modes (WG16, 2026-10-04)

The desktop model holds the chosen mode (`expanded`, `collapsed` or `hidden`) and, while Super+M is
held past the hold delay, a momentary mode on top of it. Window mode and a momentary expand are
temporary presentation, like WG19's peeks: they set `peek` on collapsed widgets and bring hidden
ones in, never changing `collapsed` intent or the mode. A tap or `scottland/widget-mode` changes
the mode and ends peeks, as Super+M always has. The model snapshot carries `widget_mode`,
`widget_mode_shown` and, for compatibility, `collapsed` (the chosen mode is collapsed); each
widget carries its `place` (`in`, `away` or `peeking`) and `away`.

**Keys.** The binding's press is consumed when there are docked widgets (otherwise the key goes
on to the app, as before). Released within `minimize_hold_delay` it is a tap; still held then, it
is a hold. Acting on release follows the rule Mike set for a focused hint (WK35): a hold must not
first take a tap's step. WG20 is unchanged: one gesture per held key across devices, the last
release ends it, a modifier release doesn't, and no debounce drops rapid taps. The default delay
matches `alt_hold_delay` (300 ms). Releasing a hold returns to the mode as it was.

**Hidden is expanded, away.** Hidden widgets slide off their screen edges with the same rail slide
that full screen uses (FS1, which now shares it), then their windows are hidden. They keep the
look they had (a collapsed icon slides off as an icon); every way they come back fully shows them
expanded: a tap to expanded, a hold, Window mode, hover and attention. Presentation changes of a
widget coming back start after it is shown again, so its client draws and the morph is visible.
A slide starts from where the widget is drawn, so reversals (tap during a slide, Alt during a hold)
never jump. A widget docked while hidden, by a drop, a fling or a key, lands first (its handoff
and drop glide finish) and then slides off: tenet 2, you see where it went. Making room on a rail
(WG26) still counts hidden widgets, so they come back to places that don't overlap (P1).

**Attention while hidden** (tenets 1 and 5). Mike asked that hidden widgets still show attention
and left the how open. A widget whose app needs the user comes in for WG19's attention peek
(`widget_attention_peek_duration`, restarted by each request) so it says what it is (tenet 2);
afterwards it keeps a strip of itself in at its screen edge: 24 pt of the widget, the peek-strip
depth Mike set for windows, growing with text size, with its halo breathing the attention color
(WG15) beyond it. It stays until the user goes to it (attention cleared), then slides off. Hovering
the strip brings the widget in with WG19's enter/leave delays; a grab never slides from under the
pointer. Only the strip remains because the user chose clear edges: others may ask for attention,
only the user grants it (tenet 5). Full screen still sends it away; attention shows again when
full screen ends (tenet 6).

`scottland/widget-mode` reports `{mode, shown}` and takes `{"mode": "expanded" | "collapsed" |
"hidden" | "next"}`, for menus and tests; it is the same mode change as a tap.

### WG16 modes validation (2026-10-04, plumbus headless)

Branch `superm-modes` rebased on main at `5a2fb5d` (final runs; earlier runs at `2bf738e`); every run in its own `SCOTTLAND_HEADLESS_DIR`
under the plumbus checkout's `build/`, with real stipc input; nothing ran on osanwe and nothing
was installed or reloaded outside the private sessions. plumbus was shared and loaded (load ~3-5),
so failures were compared against origin/main run the same way.

| Suite | This branch | origin/main, same conditions |
|---|---|---|
| `widget-input-test.py modes` (new: tap/hold timing, Alt in each mode, attention while hidden, full screen, arrival, reload) | **29/29**, three runs | (new) |
| `tests/widgets-test.sh` (includes all widget-input cases) | **232/0** | 203/0 |
| `tests/widget-morph-test.sh`, goo | **270/0** (a first run under load: 264/6, timing samples) | 268/2 (goo intermediate frame) |
| `SCOTTLAND_TEST_GOO=0 tests/widget-morph-test.sh` | **242/0** | not run |
| `tests/widget-hints-test.sh` | **193/0** | not run |
| `tests/windowing-test.sh` | **103/0** | not run |
| `tests/state-model-test.sh 104729 50` | **100/0** | not run |
| `tests/state-model-test.sh 271828 50` | compositor segfault right after a marked reload | the same segfault (pre-existing) |
| `tests/state-regressions-test.sh` | stops at its fullscreen-focus assertion | stops earlier, at the late-widget assertion (pre-existing) |
| `tests/widget-peek-options-test.sh` | **4/4** | not run |
| `tests/widget-icon-center-test.sh` (new, WG10) | **16/16**; before the card fix every collapsed case was 3 px off on both axes | the same 3 px offset (card unchanged on main) |

Screenshots (hidden, held, Alt in hidden and collapsed, attention coming in and then peeking at
the edge) were inspected: the strip shows the card's inner end with its goo breathing amber.
This is headless validation; no physical screen (D2) was used, so WG16 is not "verified".

## What a widget can learn about its app (standard sources)

Widgets need no Scottland API for these; Scottland's part is passing the identity (WG8).

| What | Standard source |
|---|---|
| Process name, executable, command line, working directory, child processes | `/proc/<pid>/` (for terminals: what's running, and where) |
| App name and icon | freedesktop Desktop Entry (`.desktop`) and icon-theme specs, from the app-id |
| Window title (often the document, page or command) | Passed by Scottland, with changes (WG9) |
| Media: track, artist, artwork, state | MPRIS (D-Bus) |
| Badge counts and progress | Unity Launcher API (`com.canonical.Unity.LauncherEntry`, D-Bus); Chromium/Electron apps, Telegram, Thunderbird and others publish it |
| Tray icon and status | StatusNotifierItem (D-Bus) |
| Recent documents | XBEL recently-used files (`~/.local/share/recently-used.xbel`) |
| What's in the app's interface (document text, a browser's URL bar, a selected item) | AT-SPI accessibility (D-Bus), for GTK, Qt, Chromium and Firefox when enabled |

No standard says "this window's open document" or "this tab's URL" cleanly; the title,
accessibility, or the app talking to its own widget (WG11, or its own service) cover it.

## Notes

- **Trust:** a widget is code running as the user, the same trust as installing an app. Widgets
  shipped with apps come with apps the user chose; downloaded widgets deserve the same care as
  downloaded programs. (macOS sandboxes widgets; KDE's Plasma widgets aren't. This follows KDE.)
- **Cost:** each widget is a process. If a full rail of QML widgets gets heavy, QML widgets can
  share one host process as an optimization; the contract stays the same.
- **Matching app-ids to `.desktop` entries** needs a heuristic for apps that don't declare their
  window class (Omarchy's web apps: window `chrome-app.element.io__-Default`, entry
  `Element X.desktop` with icon `element-x`).
- **Not needed now:** per-window screen capture for third-party widgets. Wayfire 0.11 offers
  whole-screen capture only; wlroots has the per-window protocol, which the Scottland plugin could
  implement (custom capture source rendering the window) if a widget ever needs another app's
  pixels.
- **Frame:** widgets keep Scottland's frame and halo (rounded, at 100%), which is also how they're
  dragged off the rail. A different look is still open.
- **One service per session bus:** `org.scottland.Widgets` is a bus name; a second Scottland
  session on the same user bus can't own it (tests use a private bus: `tests/headless.sh --widgets`).
  Runtime files are per session (`$XDG_RUNTIME_DIR/scottland/widgets/<display>/`): the widget
  service is the only writer of `<launch scope>.json` (one per launch, so an earlier launch's file
  is never read as this one's). A synchronous Prepare handshake creates it before exec; the
  resolved desktop identity is in the model, without a launch side file.
- **Palette:** `$XDG_RUNTIME_DIR/scottland/<display>.palette.json` (`scheme`, `background`,
  `foreground`, `muted`, `accent`, `alert`), kept current by `scottland-color-scheme` from the
  desktop's light/dark setting and accent, overlaid by an integration's palette (an `accent.d`
  provider run with `--palette`; the Omarchy adapter's reads the Omarchy theme). Core never reads
  an integration's files itself.
- **Not covered yet by tests:** halo, three-finger and touch drags onto a rail (the drop path is
  shared with Super+drag), dialogs and fullscreen windows, input inside a widget, several
  screens, X11 apps, `Focus()`, urgency, the palette changing live (the file is tested; the card
  watches it), an app unmapping and remapping the same window while widgetized (GTK 4 makes a new
  window when shown again, which is tested).
- **Processes:** each widget runs in a systemd user scope; its windows are recognized by that scope
  (or, without one, by descending from the launched process). Without systemd (no user manager),
  a widget runs in its own session instead, and only its first process is ended (through a pidfd
  taken at launch: SIGTERM, then SIGKILL after 2 s). The scope command gets the widget's arguments
  verbatim (no `$` expansion). Without systemd, the mailbox identifies a widget by process tree,
  checked fresh from the compositor at each call; a process id reused within that call's
  moment is the accepted residual risk.
- **Older GLib:** the widget service registers its D-Bus objects with GLib 2.84's API where
  present and the older one otherwise (Ubuntu 24.04 ships GLib 2.80). The older path passes the
  whole end-to-end suite here (`SCOTTLAND_DBUS_LEGACY=1 tests/widgets-test.sh`) but hasn't run on
  an actual Ubuntu 24.04 system yet.
- **Helpers across updates:** the widget service and color-scheme watcher record a fingerprint of
  their code; a reload replaces one whose fingerprint differs from the installed code.
- Supersedes the earlier core invariant L13 ("apps are told to render as widgets") and settles
  L16 (how widgets sit on a rail: free-floating, WG4).


Goo-default follow-up (2026-10-01): widgets passed 146 checks in each mode. Morph passed
86 checks with the default goo and 76 with the explicit fallback halo. Independent-band
pixel checks replace removed halo neighbor diagnostics; goo field/attention sampling remains.
See [goo-default validation](goo.md#goo-default-validation-2026-10-01).

## Shipped-default config regression (2026-10-02)

Isolating config generation from personal settings in `33303d9` exposed a harness bug on
osanwe: with no private base config, the builder selected `/usr/share/scottland/scottland.ini`
from an older installed package. That config loaded Wayfire's decoration plugin, adding
4-pixel borders and a title bar to the card's 96×96 client: its frame was 104×134. The earlier
personal-config path had also selected a different base config; the layout slider values
were not the cause. The current shipped base already disables decorations (core A1).

`tests/headless.sh` now copies this checkout's `core/config/scottland.ini` into each session's
private config directory before running the real config builder. Personal `layout.ini` and
`overrides.ini` remain excluded. WG16's 96-pixel assertions stay intact: the frame and client
must agree under the shipped defaults, including for running previews and newly collapsed
widgets.

Validation on osanwe: `make test-hooks` and `bash -n tests/headless.sh` passed;
`tests/widgets-test.sh` passed **146 checks**, including **43 input regressions**, with the
checkout's shipped defaults. The baseline reproduced all four WG16 assertion failures
(142 passes). Screenshots and client/frame geometry confirmed A1's undecorated cards and
WG16's 96×96 collapse. Each run used its own `SCOTTLAND_HEADLESS_DIR`, and the sessions were
stopped afterward. No physical screen, live session or other checkout was used.

## Card-corner badge verification (2026-10-02)

WG10 now anchors the count to the card's upper corner away from its screen edge, including
icon-only cards. GO16 makes the goo and fallback halo hug the alpha contour of body and badge, so that
reservation no longer leaves a gap between the liquid and the card. Six points of reserved
surface space let the badge overlap the body without
clipping at the top of a rail or changing the card's 96-point collapsed size. Tenet 2
(recognition, not recall) keeps the count visible in either presentation.

`tests/badges-fixedsize-test.py`, run through an isolated `tests/headless.sh start --widgets`
session, checks both rails and a return rail change with real Super-drags, real Super+M collapse,
Unity counts 7 and 123 (`99+`), and screenshot pixels for placement, overlap and readable count.
Its combined badge/resize suite passes **94/94**, including GO8/GO12/A6 with goo on and off.
Screenshots and JSON geometry are in `build/badges-fixedsize-evidence/`. No live session was
used; this is headless validation, not physical-screen verification.

Final regression runs also passed `widgets-test` (all checks), `widget-morph-test` (185/185),
`goo-test` (46/46), and `goo-overlap-hover-test` (27/27). The morph run initialized its private
session palette through `scottland-color-scheme ensure` before reading window-mode hints.

GO16 also verifies WG10/WG16/WG22/WG23 against the widget's visible alpha body,
including badges, in isolated sessions. Real-input shape/control checks pass
133/133 on both goo GPU paths on Xe and RX 580, plus 43/43 with forced fallback
on RX 580; the morph suite passes 270/270 with goo and 242/242 with fallback.
The widget lifecycle/input suite passes all checks. See
[the contour and cost report](goo.md#go16-widget-alpha-contours-2026-10-02) for
before/after crops, cache timings and the unchanged morph test thresholds.

The GO16+GO17 merge also checks an attentive round widget whose opaque body is
deeply inset from its transparent surface. Its visible contour breathes while
the field sleeps; the fallback halo and ordinary rectangular windows retain
their respective GO16 and analytic paths. The merged build passed all **192**
widget-suite checks and **270/270** morph checks in isolated plumbus sessions
after the October 2 osanwe reset; see the [combined validation](goo.md#go16--go17-integration-on-originmain-c99f116-2026-10-02).

## Elastic widget size (WG23)

`scottland/widget_bounce` is a double in 0–0.1, default **0.04**: 4% overshoot of
the size change. Zero preserves the old monotonic transitions. `scottland-ctl` exposes
the option, and the Widgets settings tab (S19) edits it alongside peek timing.

`widget-spring.hpp` evaluates an underdamped step up to its first peak at 60% of a
360 ms transition, then a cubic tail reaches the exact target with zero velocity.
This matches the one-peak curve used by WK29's keyboard cycle spring, giving one
overshoot, no subsequent wobble, and no timer work after settlement.
The same curve drives the snapshot presentation mixer and live drag morph, so
Super+M, menu/IPC presentation actions, hover/attention peeks, collapsed arrivals,
rail drops, and Esc returns share it. Very large contractions reduce the overshoot
so the card stays at least 75% of its target size, even at the maximum setting.

The content mix stays monotonic (180 ms for form changes, 200 ms for presentation),
keeping premultiplied opacity and the card icon's natural scale. Rail anchoring,
hit testing, goo and fallback halos follow the displayed size. A reversal freezes
the composition currently on screen before taking a new path; it does not restart
from the old client size. The option is sampled at transition/drag creation.
Tenet 2 (recognition) chooses a small bounce and bounds large contractions; tenet 5
keeps peek animation independent of focus and collapse intent.

### WG23 validation (2026-10-02, isolated headless)

`tests/widget-morph-test.sh` passed **246 checks with goo** and **222 with goo off**. This
includes real-input Super+M collapse/expand, IPC/menu actions, hover and attention peeks,
attention expiry, interrupted reversals, one overshoot without wobble, exact settlement,
and a live zero setting that preserves monotonic size changes. `tests/widgets-test.sh`
passed all checks, including **83 real-input regressions** and its widget lifecycle, drag,
service and reload cases. `tests/windowing-unit.sh` passed **95 checks**.

`tests/widget-elastic-frames.py` used Super+M to capture contraction and expansion directly
from the rendered headless session; both directions showed the overshoot and exact final
size. The six-frame-per-direction strip and sampled geometry are in
`build/elastic-final/frames-final/`. All sessions and artifacts stayed under `build/`;
no physical display or live session was used.

The small-jobs batch reran `tests/widget-morph-test.sh` with goo: **270 passed, 0 failed** in
`build/widget-morph-batch1-r3.log`. An earlier run passed 269 checks and missed the 12 pt
intermediate goo/frame sampling bound once (14.6 pt); the unchanged-code rerun passed.
`tests/widgets-test.sh` also passed all checks, including reload and widget-service behavior.

## WG27: conversion pacing (2026-10-03)

Window → widget used to synchronously fork twice through Wayfire's `core.run()`,
render new app/card snapshots, and compile the blend shader on first use. An
ordinary entry also held the captured app still until the new client mapped.
Widget → window already had a live app and used its running drag morph; it did
not have that startup dependency. Its teardown still forked for a scope stop.

Conversion now sends a nonblocking packet to a small subprocess broker created
at plugin startup. The broker inherits Wayfire's session environment, launches
through the unchanged widget launcher and transfers an open pidfd back through
its private socket. Cancellation before the reply, timeout, scope stop and
reload keep the existing ownership rules. The broker reaps its children and
exits when its compositor socket closes; it drains queued cancellations during
unload. A failed broker retains the synchronous scope-stop recovery path.

The mixer shader compiles at plugin startup. Simple surfaces retain their
applied texture; complex app surfaces transfer the last composed frame buffer
when one exists at the correct size. RGBX textures force alpha to one, preserving opaque client rendering even when their unused channel bits are zero. Transfer takes ownership rather than
sharing a mutable render cache. The presentation renderer uses those retained
pixels directly instead of composing the live app again. Uncached/unsupported
scenes still use the existing snapshot path. Live hidden content during a drag
continues to refresh, including client frame callbacks.

The source starts moving and shrinking on the next 8 ms animation tick, using
the same provisional card size as drag previews. On the card's applied mapping
transaction, the handoff starts from the source's current drawn rectangle and
corrects the estimate over 180 ms, with the full established 180 ms crossfade.
No new card design, color, placement rule or widget selection rule is involved.

Measurements used isolated plumbus sessions, the Radeon Pro 580X GLES renderer,
1280×720 at 60 Hz, shipped goo enabled, debugoptimized builds with asserts/frame
pointers, a 100×30-cell foot and the real built-in card. Baseline is **30514ff**.
Each entry path ran six times with real stipc input, followed by a real reverse
drag of that card. A separate process sent serial IPC pings with a 1 ms pause;
opt-in test instrumentation recorded conversion callbacks and output render-hook
timestamps. Frame intervals below are render-hook intervals, not physical screen
presentation timestamps. The keyboard measurement begins after Window mode is
active and includes the real hint-key double tap.

| Entry path | Frame interval p99 before → after | Reverse drag frame p99 after | Worst IPC reply before → after |
|---|---:|---:|---:|
| Drag onto rail | 21.953 → 18.469 ms | 20.804 ms | 25.581 → 9.256 ms |
| Fling into rail | 26.421 → 18.273 ms | 19.640 ms | 21.475 → 8.381 ms |
| Window-mode hint key | 26.410 → 19.322 ms | 20.004 ms | 26.371 → 3.823 ms |

The final entry paths had **two IPC replies over 8 ms**, versus 40 before. Entry frame maxima
were 23.325, 18.503 and 19.652 ms respectively, versus 41.888, 31.029 and 42.075 ms
before. Conversion capture callbacks were at most 0.015 ms; widgetize callbacks
at most 0.197 ms; drag/drop freeze at most 1.372 ms. Baseline widgetize reached
10.567 ms and the first blend freeze reached 18.607 ms. Full percentile/callback
summaries are [before](measurements/widget-conversion-before-2026-10-03.json) and
[after](measurements/widget-conversion-after-2026-10-03.json). Raw pings, input
windows, logs and screenshots are under this checkout's
`build/conversion-{before,rgbx-final}-results/` on plumbus and the working machine.

The first hint-text raster still took 8.839 ms when entering Window mode, outside
the conversion interval; warm hint updates during conversion topped out at
0.036 ms. Glyph rasterization and rare uncached scene captures remain candidates
for the separate main-loop worker effort. GL scene capture needs an appropriate
render context; it cannot simply run arbitrary Wayfire APIs on a worker thread.
Card-client startup still takes time, but now overlaps a moving source image.

Reproduce in a disposable test checkout: build its hooks, run
`tests/instrument-conversion.py CHECKOUT`, rebuild, and start a unique headless
`--widgets` session with `SCOTTLAND_CONVERSION_TRACE=1`. Run
`tests/widget-conversion-bench.py RESULTS --trials 6` through `headless.sh run`,
copy its `wayfire.log` into RESULTS before stopping, then run
`tests/widget-conversion-summary.py RESULTS`. Instrumentation changes only the
test copy and is absent from shipped code. Keep all directories under `build/`.

Validation on the plain build: **270 morph checks passed**, all widget lifecycle
checks passed (including **91 real-input regressions**, scoped/no-scope timeout,
closing, service updates and reload), and all five repeat/race suites passed
(avoidance, repeated transitions, foot race, Ghostty race, batch race). Peek
options passed four checks; keyboard spring/cycle input passed **79 checks**;
elastic frame capture passed contraction/expansion overshoot and settlement.
The deliberately delayed card passed **12 entry/restore checks** across drag,
fling and hint-key paths. A native XRGB client with zero unused alpha bits passed
opacity, client-resize rail anchoring and handoff checks. The broker passed live
pidfd/cancellation and disconnect queue-draining checks. Logs and screenshots
remain in this checkout's `build/` on plumbus; all owned sessions were stopped.
No shared session was installed or reloaded.
