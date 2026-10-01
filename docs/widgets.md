# Widgets

When a window is moved onto a screen-edge widget rail, Scottland shows a **widget** in its
place: a compact, live stand-in for the app. Widgets don't depend on apps adding support.
Scottland decides what to show for each app, ships defaults, lets apps ship their own widget,
and lets the user swap in any widget for any app.

Status: designed with Mike (2026-09-30/10-01) and implemented (2026-10-01). Verified headless
with real input, real widget programs and real D-Bus: `tests/widgets-test.sh` (end to end) and
`tests/widget-launch-test.py` (widget choice). Pieces: the plugin (rail drops, hiding, adopting
the widget's window, placement, tied lifecycles; IPC `scottland/widgets`, `scottland/widget-action`,
events `scottland-widgets#`, `scottland-scale#`), `scottland-widget-launch` (choice, context,
exec), `scottland-widget-bus` (D-Bus, badges, mailbox, state files), the card
(`core/widgets/card/`).

## Invariants

| ID | Invariant | Status |
|---|---|---|
| WG1 | Moving a window onto a widget rail (any drag: Super+drag, the halo, three-finger or touch) turns it into a widget: its center lands in the rail. Moving the widget off the rail turns it back into the window, where it's dropped. Dialogs (windows with a parent) and fullscreen windows aren't widgetized. | verified (headless) |
| WG2 | A widget is any program: a Quickshell (QML) file, a GTK/Qt app, a web view, a TUI, anything. It runs with the user's privileges, like any app, and may use anything on the system to render itself (files, D-Bus, commands, the network). Scottland imposes no widget API. | verified (headless) |
| WG3 | Widgets are fully interactive: their windows get keyboard, pointer and touch input like any window. | verified (headless) |
| WG4 | Placement: a widget is free-floating on the rail, centered where its window was dropped and kept wholly on screen (its halo too). Widgets are always at 100%: they never follow the zone scale, wherever they're dragged. | verified (headless) |
| WG5 | Lifecycle: the real window stays alive (hidden) while widgetized, so restoring is instant. The window and its widget are tied: closing the widget closes the window (shown again first, so an app's "save changes?" question is visible), and closing the window closes the widget. Dragging the widget off the rail restores the window and dismisses the widget (not a close). A widget whose window doesn't appear within 8 s is abandoned and the app's window restored. Unloading or reloading the plugin restores every app window. | verified (headless) |
| WG6 | Choosing a widget, in order: the user's assignment (`~/.config/scottland/widgets.ini`, app-id → widget), else the app's own widget (named by its `.desktop` entry, `X-Scottland-Widget=`, or installed for its app-id), else a Scottland built-in for that kind of app, else the default card (WG10). Any widget can be assigned to any app. | verified (unit test) |
| WG7 | A widget package is a directory with a `widget.toml` manifest (`id`, `name`, `apps` = app-id regexes it suits, `exec` = the command) and whatever the command needs. Packages are found in `~/.local/share/scottland/widgets/` (the user's), `/usr/share/scottland/widgets/` (installed with apps, removed with them) and Scottland's built-ins (`/usr/lib/scottland/widgets/`); the first package with an id wins. | verified (unit test) |
| WG8 | Launch context: Scottland starts the widget with the window's identity in its environment (`SCOTTLAND_WIDGET_ID`, `_APP_ID`, `_TITLE`, `_ICON`, `_NAME`, `_DESKTOP`, `_PID`, `_WINDOW`, `_RAIL`, `_BADGE`, `_STATE`), and `.desktop`-style placeholders in the manifest's command, filled per argument (`%a` app-id, `%t` title, `%i` icon, `%p` pid, `%w` window id, `%r` rail, `%d` the package directory, `%%`). The widget runs in its package directory. | verified (unit test) |
| WG9 | Live updates and actions over D-Bus: Scottland's widget service (`org.scottland.Widgets`, one per session bus) publishes one `org.scottland.Widget` object per widget (`/org/scottland/widget/<id>`) with the window's properties (Id, AppId, Title, Pid, Window, Rail, Focused, Urgent, Badge, Data, with PropertiesChanged signals) and methods `Restore()`, `Close()`, `Focus()`. Widgets that don't need it ignore it. Driven by compositor events, not polling. | verified (headless) |
| WG10 | The default widget, for any app with none configured, is a card: the app's icon (from its `.desktop` entry via the icon theme; web apps are matched by their site), the window title, and an alert badge when the app publishes a count (Unity Launcher API). Title and badge stay live through the state file named by `SCOTTLAND_WIDGET_STATE`. It follows the theme's colors (Omarchy's when present). Mike approved the look (2026-10-01). | verified (headless) |
| WG11 | Optional data mailbox between an app and its widget, for apps without a service of their own: the app calls `org.scottland.WidgetData.Publish(json)` on `/org/scottland/Widgets` and its widget's `Data` property (and state file) changes; a widget's `Send(json)` is broadcast as `Received(app_pid, window, json)` for its app. Callers are identified by their D-Bus credentials and process tree: only an app (or its helpers) can publish for its windows, only a widget can send for its window. | verified (headless: Publish) |
| WG12 | Apps learn their state from Scottland: `org.scottland.Windows.GetState()` (called from the app's own process tree) returns whether it's widgetized and its window's scale; `StateChanged(pid, widgetized, scale)` signals changes. Apps that don't listen are unaffected. | verified (headless: GetState) |

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
- Supersedes the earlier core invariant L13 ("apps are told to render as widgets") and settles
  L16 (how widgets sit on a rail: free-floating, WG4).
