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

## Invariants

| ID | Invariant | Status |
|---|---|---|
| WG1 | Moving a window onto a widget rail (any drag: Super+drag, the halo, three-finger or touch) turns it into a widget when the pointer (or finger) enters the rail, wherever the window was grabbed; the window's own position doesn't decide. It changes while you drag, not on the drop (WG13). A widget stays a widget while the pointer is between the screen edge and the widget's inner side (wider than the rail itself, so it can be grabbed anywhere and slide along the rail or switch rails); the pointer going past that turns it back into the window, centered where the widget was dropped, on the screen it was dropped on. Dialogs (windows with a parent) and fullscreen windows aren't widgetized. | verified (Super+drag, on plumbus 2026-10-01); halo/three-finger/touch drags: implemented |
| WG2 | A widget is any program: a Quickshell (QML) file, a GTK/Qt app, a web view, a TUI, anything (its window may come from a process it starts and leaves running). It runs with the user's privileges, like any app, and may use anything on the system to render itself (files, D-Bus, commands, the network). Scottland imposes no widget API. | implemented (headless) |
| WG3 | Widgets are fully interactive: their windows get keyboard, pointer and touch input like any window. | implemented (widgets are ordinary windows; input inside one not yet tested) |
| WG4 | Placement: a widget is free-floating on the rail, above all ordinary windows (always on top), centered where its window was dropped and kept wholly on screen with room for its halo at its widest; each drop on the rail places it again, gliding (~260 ms) from where it was let go to its place against the edge rather than jumping. Widgets are always at 100%: they never follow the zone scale, while dragged or when dropped. A widget's window is placed by Scottland as it maps (Wayfire's place plugin is told it's positioned), and keeps its screen-edge side when it changes size. Client identity is read from the toplevel’s Wayland surface resource before mapping, without unstable Wayfire headers, so rail gravity is present in the mapping transaction and changes atomically with rail placement. Resize placement uses pending geometry in that transaction, with no corrective move after a size notification. | implemented (plumbus headless 2026-10-01: 7 mapping/resize checks, including both rail changes; two extra-move checks failed before the fix; no real-screen run for this change) |
| WG5 | Lifecycle: the real window stays alive while widgetized, so restoring is instant. Its image remains visible through startup until the card can take it over (WG22); the real window is hidden after that handoff. The window and its widget are tied: closing the widget closes the window (shown again first, so an app's "save changes?" question is visible), and closing the window closes the widget. Dragging the widget off the rail restores the window and dismisses the widget (not a close). A widget whose window doesn't appear within 8 s is abandoned: it's ended and the app's window restored. A widget asked to close that's still running 3 s later is ended. Ending a widget ends every process it started (each widget runs in its own systemd scope: SIGTERM, then SIGKILL after 2 s), never an unrelated process. Unloading the plugin restores every app window and ends every widget; a reload (scottland-reload) keeps them: the outgoing plugin hands its widgets to the new one. On load, any window whose center is on a rail becomes a widget again (WG1), whatever left it there; a reload also replaces Scottland's helper services when their installed code is newer. | verified for drag-off restore (plumbus); the rest implemented (headless) |
| WG21 | A widget has one lifecycle: previewing, docked, restoring, closing or handed-over. One transition function applies visibility to Wayfire; renderer disable leases are resources, never independent logical flags, and are returned on unmap/unload (only handed-over app leases transfer). Collapsed intent and temporary peek presentation are independent of lifecycle. | implemented (headless) |
| WG22 | Every window → widget transition is a continuous compositor morph, like widget → window: the visible app image moves/shrinks into the card’s place and cross-fades into it; no hide/show cut. This includes every starting-zone hint cycle, double-tap to rail, collapsed-mode arrivals, rail drops (including release before the preview is ready), Esc returning an undocked app to its original widget, and rail recovery on plugin load. The existing snapshot mixer owns the handoff; shape uses WG13’s 240 ms circle easing and contents its 180 ms fade. A card’s ordinary Wayfire map animation is suppressed so it cannot zoom/fade the composition a second time. Goo (or the fallback halo) follows the visible rectangle and interpolated scale. The app stays visible during startup; a card disappearing during the handoff restores the app and never closes it (WG5). | implemented (headless); validation below |
| WG23 | Every widget expansion and contraction (Super+M, peeking on hover or attention, collapsed arrival, any other trigger) settles with an elastic bounce: a lightly underdamped spring that overshoots once and settles without wobble, on the card's size/shape (and its goo). Amount is a setting (0 = none) on a Widgets tab in Scottland Settings (S19). (Mike, 2026-10-02) | planned |
| WG6 | Choosing a widget, in order: the user's assignment (`~/.config/scottland/widgets.ini`, app-id → widget), else the app's own widget (named by its `.desktop` entry, `X-Scottland-Widget=`, or installed for its app-id), else a Scottland built-in for that kind of app, else the default card (WG10). Any widget can be assigned to any app. | implemented (unit test) |
| WG7 | A widget package is a directory with a `widget.toml` manifest (`id`, `name`, `apps` = app-id regexes it suits, `exec` = the command) and whatever the command needs. Packages are found in `$SCOTTLAND_WIDGET_PATH` (colon-separated, if set; relative entries are taken from the current directory), `~/.local/share/scottland/widgets/` (the user's), `/usr/share/scottland/widgets/` (installed with apps, removed with them) and Scottland's built-ins (`/usr/lib/scottland/widgets/`); the first package with an id wins. | implemented (unit test) |
| WG8 | Launch context: the environment carries the window and launch identity (`SCOTTLAND_WIDGET_ID`, `_APP_ID`, `_ICON`, `_NAME`, `_DESKTOP`, `_PID`, `_WINDOW`, `_STATE`) and the palette path (`SCOTTLAND_PALETTE`). Mutable title, rail, collapsed mode and badge come only from the complete state file, written before exec. `.desktop`-style placeholders still fill the manifest's command per argument (`%a` app-id, `%t` initial title, `%i` icon, `%p` pid, `%w` window id, `%r` initial rail, `%d` package directory, `%%`). The widget runs in its package directory. Resolved identity and traits are submitted to the plugin for that launch. See [desktop-model.md](desktop-model.md), DM4. | implemented (headless) |
| WG9 | Live updates and actions over D-Bus: Scottland's widget service (`org.scottland.Widgets`, one per session bus) publishes one `org.scottland.Widget` object per widget (`/org/scottland/widget/<id>`) with the window's properties (Id, Version, Revision, AppId, Title, Pid, Window, Rail, Focused, Urgent, Badge, Data, with PropertiesChanged signals) and methods `Restore()`, `Close()`, `Focus()`. Widgets that don't need it ignore it. Driven by complete versioned model snapshots, not polling; property signals carry the complete public property set, with the model version and the revision of the already-written presentation file. | implemented (headless: properties, live Title, Restore, Close; not Focus, Urgent) |
| WG10 | The default widget, for any app with none configured, is a card: the app's icon (from its `.desktop` entry via the icon theme; web apps are matched by their site; else the theme's generic app icon, else the app's initial), the window's title (bold: what's in it) over the app's name (regular), and an alert badge when the app publishes a count (Unity Launcher API; partial updates, e.g. progress only, keep the count). The icon is on the screen-edge side of the text (left of it on the left rail, right of it on the right rail), with the alert badge overlapping the **card's upper corner opposite the screen edge**: upper-right on the left rail, upper-left on the right rail, in both expanded and collapsed (icon-only) form. The badge follows rail changes, stays readable (counts above 99 read `99+`), and its overhang stays inside the client surface so rail placement keeps it wholly on screen. Space for that overhang is reserved even without a count; badge updates never shift the contents. The card is as wide as its text needs, up to 320 pt, and square around the icon when there's no text; as it changes size, its screen-edge side stays put. Title, badge and rail stay live through the state file named by `SCOTTLAND_WIDGET_STATE`. Its colors follow the session's palette (`SCOTTLAND_PALETTE`), live. Mike approved the look (2026-10-01); layout, typography and sizing per his review the same day. | implemented (headless); badge rail/collapse/count pixels checked 2026-10-02 |
| WG11 | Optional data mailbox between an app and its widget, for apps without a service of their own: the app calls `org.scottland.WidgetData.Publish(json)` on `/org/scottland/Widgets` and its widget's `Data` property (and state file) changes; a widget's `Send(json)` is broadcast as `Received(app_pid, window, json)` for its app. Callers are identified by their D-Bus credentials and process tree: only an app (or its helpers) can publish for its windows, only a widget can send for its window. (X11 apps are identified by the process they declare, `_NET_WM_PID`: X11 offers nothing stronger, and X11 apps can already see each other.) | implemented (headless: Publish, Send, refusals of both) |
| WG12 | Apps learn their state from Scottland: `org.scottland.Windows.GetState()` (called from the app's own process tree) returns whether it's widgetized and its window's scale (where it's going, never a step of an animation); `StateChanged(pid, widgetized, scale)` signals changes. Both give the same answer: an app with several windows is widgetized if any is, with that window's scale, else the scale of its first open window. Apps that don't listen are unaffected. | implemented (headless) |
| WG13 | Live morph: a window dragged onto a rail changes into its widget while it's dragged, and a widget dragged off its rail changes back into its window, both ways as often as the drag goes in and out. The change animates (~240 ms): the frame (and halo) reshapes between the window's size at its scale there and the widget's size, while the contents cross-fade from one form to the other, each scaled evenly to cover the frame. The widget is started (unseen) when the drag first reaches the rail; until it exists (its program takes a moment to start the first time), the frame reshapes toward the size the default card will have for that window (or the collapsed square) around the window's contents, and when the widget appears the frame eases onto its real size (~150 ms) and its contents fade in: no snap. The other form's contents are live during the morph: a widget's hidden window is asked to draw as it's dragged out, so peeking shows what the app shows now. The drop keeps the form shown, as shown: a window dropped from a widget drag is at its size at once (no animation); a widget dropped glides only to align with the screen edge (WG4). A widget previewed this way and dragged back off is ended. | implemented (headless) |
| WG14 | Esc cancels any window drag (pointer, touchpad or touch; Super+drag, the halo, three-finger or touch): the window goes back to where it was picked up, on the screen it was picked up on (picking the same window up again within 2.5 s of letting it go, e.g. to reset fingers on the touchpad, continues the same move: Esc goes back to where the move began), gliding there from where it was let go (~260 ms), and if the drag changed it into its other form (window/widget), it morphs back. A cancel only ever uses that window's own origin (a drag cancelled before it moved stays put). A re-grab of what a drop turned it into (the widget a window became, or the window a widget became) continues the move too: Esc brings back the first form where the move began. Cancellation restores the saved rail and drop anchor as well as the scene position, so a subsequent card resize retains the restored position. The origin, re-grab chain and morph are owned by the desktop model's drag session ([desktop-model.md](desktop-model.md), DM7). | implemented (headless) |
| WG15 | Attention: when an app needs the user, its halo (its widget's, when it's a widget) takes the attention color (a secondary highlight, from the desktop's palette: the Omarchy theme's yellow) and breathes (the halo's goo swells and ripples as when hovered), until the user goes to it (the window or its widget). An app needs the user when it asks to be focused (xdg-activation: a terminal's bell, a finished task), sets the urgency hint, or sends a desktop notification from the process that owns its window (one window only, so it's clear which). Other programs are sources too, by configuration ([attention.md](attention.md), AT1-AT3): each turns attention on and off under its own name, never clearing another's or the app's own bell. Scottland implements xdg-activation itself: a request made from input in the app the user is using (a link opened from it) takes focus; any other request becomes attention and takes no focus (Wayfire's plugin dropped those, so a bell never arrived). | implemented (headless: bell, notification, IPC, clearing) |
| WG16 | Super+M collapses all widgets to just their icons, or, if they all are, expands them back (`scottland/minimize_widget`). Widgets learn it from the `Minimized` property and the state file; the default card becomes a square around its icon, keeping its screen-edge side; rail gravity and pending placement preserve its screen-edge side without a follow-up corrective move (WG4). Each widget independently morphs from its old snapshot to its new client buffer over 200 ms with smoothstep easing. Capture precedes publication; a 300 ms response bound handles clients that keep their size or do not respond. The frame stays rail-anchored, content stays at natural scale with the card's icon inset interpolated, and premultiplied images mix into one result. Reversing freezes the currently displayed composition. Damage, hit testing and each widget's halo band follow the animated rectangle; when goo is enabled its outline and dye sources follow it too; snapshots and the transition timer end on settlement. See presentation rendering below. Collapsed is a mode: a window widgetized while widgets are collapsed starts collapsed (the first state-file snapshot), and the mode survives a reload. Running previews follow mode changes in `model.widgets` through versioned snapshots, and reconcile to the current mode before transitioning to docked; their apps are still windows until committed. The compatibility `scottland/widgets` read excludes previews; the model subscription includes their lifecycle. A widget docked while collapsed shows its title and app once widgets are expanded again, like any other widget. | implemented (plumbus headless, 2026-10-01: frame sampling, pixel checks and real input; no physical-screen verification) |
| WG17 | Clicking the default card opens its app's window at its remembered center position, otherwise the least-overlapping full-size spot in the center nearest the card (the shared placement routine, [windowing-keys.md](windowing-keys.md), WP1–WP5): the window flies out of the card and grows to its size there; the card goes. (A click on the card's halo is no move, WG13.) | implemented (headless) |
| WG18 | A widget whose manifest sets `touch_drag = true` moves with a single-finger drag anywhere on it, at once (no long press), while a tap is still the widget's. For widgets that drag nothing themselves; the default card sets it. Off by default, so a widget's own finger drags (sliders, drawing) stay its own, and it's lifted with a long press as any window. The launcher tells Scottland over IPC (`scottland/widget-traits`). | implemented (headless) |
| WG19 | Peeking at a collapsed widget: after 150 ms over its visible card or goo/handles it shows expanded (title and app); leaving for 100 ms collapses it again. Sweeps and brief departures do not flicker. A collapsed widget receiving attention shows expanded for 5 seconds; each renewed attention request restarts that interval, including the same source. A pointer over it at expiry keeps it expanded until leaving. Held drags preserve the current presentation until release. Collapsed intent and Super+M's mode never change: trigger state and temporary presentation belong to the desktop model. Super+M and reload end temporary peeks; fullscreen widgets never peek into view. | implemented (isolated headless); validation below |
| WG20 | The collapse binding activates once per held key. Duplicate downs, including overlapping devices, do not toggle it again; only release of that key on all held devices rearms it (device removal clears that device). Releasing a modifier does not rearm it. No time debounce discards rapid intentional presses. Edges are tracked and logged only while the binding modifiers are held or a tracked press is in progress (including its release after the modifier); plain typing emits no collapse diagnostics. Those edges record device, input/receipt time, key and latch/mode state; activations and ignored duplicate callbacks are distinct. | verified (plumbus headless 2026-10-01: 10 input/diagnostic checks, including quiet plain-M/Shift+M typing and the tracked release after Super; duplicate down failed before the fix; device overlap/removal not exercised) |

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

All non-drag rail requests funnel through `widgetize`: the window-mode cycles and double tap,
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

## Peek triggers (WG19)

Tenet 1 (managing attention) chooses a **150 ms enter delay** to ignore rail sweeps and a
**100 ms leave delay** to absorb small pointer excursions. Tenet 2 (recognition) includes the
revealed title area and the widget's actual goo/handle hit regions. Proximity lighting alone
does not count as hover. The animated visible frame and stacking determine the hit, for either
halo renderer. A held drag freezes presentation; release reconciles hover and attention expiry.

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
card's icon inset moves between 16 and 20 pixels with the blend. Center-based frame scaling
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
icon-only cards. Six points of reserved surface space let the badge overlap the body without
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
