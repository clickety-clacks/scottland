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
events `scottland-widgets#`, `scottland-scale#`), `scottland-widget-launch` (choice, context,
exec), `scottland-widget-bus` (D-Bus, badges, mailbox, state files), the card
(`core/widgets/card/`).

## Invariants

| ID | Invariant | Status |
|---|---|---|
| WG1 | Moving a window onto a widget rail (any drag: Super+drag, the halo, three-finger or touch) turns it into a widget: its center lands in the rail. It changes while you drag, not on the drop (WG13). A widget stays a widget while any of it (its halo included) is on a rail, so it can slide along the rail or switch rails; moving it so none of it is on a rail turns it back into the window, centered where the widget was dropped, on the screen it was dropped on. Dialogs (windows with a parent) and fullscreen windows aren't widgetized. | verified (Super+drag, on plumbus 2026-10-01); halo/three-finger/touch drags: implemented |
| WG2 | A widget is any program: a Quickshell (QML) file, a GTK/Qt app, a web view, a TUI, anything (its window may come from a process it starts and leaves running). It runs with the user's privileges, like any app, and may use anything on the system to render itself (files, D-Bus, commands, the network). Scottland imposes no widget API. | implemented (headless) |
| WG3 | Widgets are fully interactive: their windows get keyboard, pointer and touch input like any window. | implemented (widgets are ordinary windows; input inside one not yet tested) |
| WG4 | Placement: a widget is free-floating on the rail, centered where its window was dropped and kept wholly on screen with room for its halo at its widest; each drop on the rail places it again. Widgets are always at 100%: they never follow the zone scale, while dragged or when dropped. | verified (plumbus 2026-10-01: 100% on a 4K screen, halo unclipped, rail slide) |
| WG5 | Lifecycle: the real window stays alive (hidden) while widgetized, so restoring is instant. The window and its widget are tied: closing the widget closes the window (shown again first, so an app's "save changes?" question is visible), and closing the window closes the widget. Dragging the widget off the rail restores the window and dismisses the widget (not a close). A widget whose window doesn't appear within 8 s is abandoned: it's ended and the app's window restored. A widget asked to close that's still running 3 s later is ended. Ending a widget ends every process it started (each widget runs in its own systemd scope: SIGTERM, then SIGKILL after 2 s), never an unrelated process. Unloading or reloading the plugin restores every app window and ends every widget; a reload also replaces Scottland's helper services when their installed code is newer. | verified for drag-off restore (plumbus); the rest implemented (headless) |
| WG6 | Choosing a widget, in order: the user's assignment (`~/.config/scottland/widgets.ini`, app-id → widget), else the app's own widget (named by its `.desktop` entry, `X-Scottland-Widget=`, or installed for its app-id), else a Scottland built-in for that kind of app, else the default card (WG10). Any widget can be assigned to any app. | implemented (unit test) |
| WG7 | A widget package is a directory with a `widget.toml` manifest (`id`, `name`, `apps` = app-id regexes it suits, `exec` = the command) and whatever the command needs. Packages are found in `$SCOTTLAND_WIDGET_PATH` (colon-separated, if set; relative entries are taken from the current directory), `~/.local/share/scottland/widgets/` (the user's), `/usr/share/scottland/widgets/` (installed with apps, removed with them) and Scottland's built-ins (`/usr/lib/scottland/widgets/`); the first package with an id wins. | implemented (unit test) |
| WG8 | Launch context: Scottland starts the widget with the window's identity in its environment (`SCOTTLAND_WIDGET_ID`, `_APP_ID`, `_TITLE`, `_ICON`, `_NAME`, `_DESKTOP`, `_PID`, `_WINDOW`, `_RAIL` (`left` or `right`: the screen edge it's on, also in the state file and the `Rail` property, live, so a widget can lay itself out toward its edge), `_BADGE`, `_STATE`) plus `SCOTTLAND_PALETTE` (WG10), and `.desktop`-style placeholders in the manifest's command, filled per argument (`%a` app-id, `%t` title, `%i` icon, `%p` pid, `%w` window id, `%r` rail, `%d` the package directory, `%%`). The widget runs in its package directory. | implemented (unit test, with a real exec) |
| WG9 | Live updates and actions over D-Bus: Scottland's widget service (`org.scottland.Widgets`, one per session bus) publishes one `org.scottland.Widget` object per widget (`/org/scottland/widget/<id>`) with the window's properties (Id, AppId, Title, Pid, Window, Rail, Focused, Urgent, Badge, Data, with PropertiesChanged signals) and methods `Restore()`, `Close()`, `Focus()`. Widgets that don't need it ignore it. Driven by compositor events, not polling. | implemented (headless: properties, live Title, Restore, Close; not Focus, Urgent) |
| WG10 | The default widget, for any app with none configured, is a card: the app's icon (from its `.desktop` entry via the icon theme; web apps are matched by their site; else the theme's generic app icon, else the app's initial), its name (bold) and the window's title (regular) on two lines, and an alert badge when the app publishes a count (Unity Launcher API; partial updates, e.g. progress only, keep the count). The icon is on the screen-edge side of the text (left of it on the left rail, right of it on the right rail), with the badge on its corner toward the middle of the screen. The card is as wide as its text needs, up to 320 pt, and square around the icon when there's no text; as it changes size, its screen-edge side stays put. Title, badge and rail stay live through the state file named by `SCOTTLAND_WIDGET_STATE`. Its colors follow the session's palette (`SCOTTLAND_PALETTE`), live. Mike approved the look (2026-10-01); layout, typography and sizing per his review the same day. | implemented (headless) |
| WG11 | Optional data mailbox between an app and its widget, for apps without a service of their own: the app calls `org.scottland.WidgetData.Publish(json)` on `/org/scottland/Widgets` and its widget's `Data` property (and state file) changes; a widget's `Send(json)` is broadcast as `Received(app_pid, window, json)` for its app. Callers are identified by their D-Bus credentials and process tree: only an app (or its helpers) can publish for its windows, only a widget can send for its window. (X11 apps are identified by the process they declare, `_NET_WM_PID`: X11 offers nothing stronger, and X11 apps can already see each other.) | implemented (headless: Publish, Send, refusals of both) |
| WG12 | Apps learn their state from Scottland: `org.scottland.Windows.GetState()` (called from the app's own process tree) returns whether it's widgetized and its window's scale (where it's going, never a step of an animation); `StateChanged(pid, widgetized, scale)` signals changes. Both give the same answer: an app with several windows is widgetized if any is, with that window's scale, else the scale of its first open window. Apps that don't listen are unaffected. | implemented (headless) |
| WG13 | Live morph: a window dragged onto a rail changes into its widget while it's dragged, and a widget dragged off its rail changes back into its window, both ways as often as the drag goes in and out. The change animates (~240 ms): the frame (and halo) reshapes between the window's size at its scale there and the widget's size, while the contents cross-fade from one form to the other, each scaled evenly to cover the frame. The widget is started (unseen) when the drag first reaches the rail; until its contents exist, the frame reshapes around the window's. The drop keeps the form shown. A widget previewed this way and dragged back off is ended. | implemented (headless) |
| WG14 | Esc cancels any window drag (pointer, touchpad or touch; Super+drag, the halo, three-finger or touch): the window goes back to where it was picked up, gliding there from where it was let go (~260 ms), and if the drag changed it into its other form (window/widget), it morphs back. | implemented (headless) |
| WG15 | Attention: when an app needs the user, its widget's halo takes the attention color (a secondary highlight, from the desktop's palette: the Omarchy theme's yellow) and breathes (the halo's goo swells and ripples as when hovered), until the user goes to the widget (or the app's window). An app needs the user when it asks to be focused (xdg-activation: a terminal's bell, a finished task), sets the urgency hint, or sends a desktop notification from the process that owns its window (one window only: the rule Yoohoo uses). Scottland implements xdg-activation itself: a request made from input in the app the user is using (a link opened from it) takes focus; any other request becomes attention and takes no focus (Wayfire's plugin dropped those, so a bell never arrived). | implemented (headless: bell, notification, IPC, clearing) |

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
  service is the only writer of `<launch scope>.json` (one per launch, so an earlier launch's file is never read as this one's); the launcher leaves the app's `.desktop` id in
  `<launch scope>.launch.json` (one per launch).
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
