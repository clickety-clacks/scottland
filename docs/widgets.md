# Widgets

When a window is moved onto a screen-edge widget rail, Scottland shows a **widget** in its
place: a compact, live stand-in for the app. Widgets don't depend on apps adding support.
Scottland decides what to show for each app, ships defaults, lets apps ship their own widget,
and lets the user swap in any widget for any app.

Status: designed with Mike (2026-09-30/10-01). Only the default card's look exists so far
(`core/widgets/card/shell.qml`, a sample run by hand). Everything else is **not built**.

## Invariants

| ID | Invariant | Status |
|---|---|---|
| WG1 | Moving a window onto a widget rail turns it into a widget. Moving the widget off the rail turns it back into the window, where it's dropped. | not built |
| WG2 | A widget is any program: a Quickshell (QML) file, a GTK/Qt app, a web view, a TUI, anything. It runs with the user's privileges, like any app, and may use anything on the system to render itself (files, D-Bus, commands, the network). Scottland imposes no widget API. | not built |
| WG3 | Widgets are fully interactive: their windows get keyboard, pointer and touch input like any window. | not built |
| WG4 | Placement: a widget is free-floating on the rail, exactly where its window was dropped, at the widget's own size. Rail widgets don't follow the zone scale. | not built |
| WG5 | Lifecycle: the real window stays alive (hidden) while widgetized, so restoring is instant. The window and its widget are tied: closing the widget closes the window, and closing the window closes the widget. Dragging the widget off the rail restores the window and dismisses the widget (not a close). | not built |
| WG6 | Choosing a widget, in order: the user's assignment (`~/.config/scottland/widgets.ini`, app-id → widget), else the app's own widget (named by its `.desktop` entry, `X-Scottland-Widget=`, or installed for its app-id), else a Scottland built-in for that kind of app, else the default card (WG10). Any widget can be assigned to any app. | not built |
| WG7 | A widget package is a directory with a `widget.toml` manifest (id, name, apps it suits, the command to run) and whatever the command needs. Packages are found in `~/.local/share/scottland/widgets/` (the user's), `/usr/share/scottland/widgets/` (installed with apps, removed with them) and Scottland's built-ins. | not built |
| WG8 | Launch context: Scottland starts the widget with the window's identity in its environment (`SCOTTLAND_WIDGET_APP_ID`, `_TITLE`, `_ICON`, `_PID`, `_WINDOW`, `_RAIL`, `_BADGE`), and `.desktop`-style placeholders in the manifest's command (`%a` app-id, `%t` title, `%i` icon, `%p` pid, `%w` window id, `%r` rail). | not built |
| WG9 | Live updates and actions over D-Bus: one `org.scottland.Widget` object per widget (`/org/scottland/widget/<id>`) with the window's properties (AppId, Title, Pid, Window, Rail, Focused, Urgent, Badge, with change signals) and methods `Restore()`, `Close()`, `Focus()`. Widgets that don't need it ignore it. | not built |
| WG10 | The default widget, for any app with none configured, is a card: the app's icon (from its `.desktop` entry via the icon theme), the window title, and an alert badge when the app publishes a count (Unity Launcher API). It follows the theme's colors (Omarchy's when present). Mike approved the look of the sample (2026-10-01). | implemented (sample look; not wired) |
| WG11 | Optional data mailbox between an app and its widget, for apps without a service of their own: the app calls `org.scottland.WidgetData.Publish(json)` and its widget sees a `Data` property change; the widget's `Send(json)` reaches the app as a signal. Scottland identifies the publishing app by its D-Bus credentials (process), so an app can only publish for its own windows. | not built |
| WG12 | Apps can learn they're widgetized (or their zone scale) from Scottland over D-Bus, to pause heavy rendering or adapt; apps that don't listen are unaffected. | not built |

Planned built-in widgets besides the card: **live miniature** (Scottland draws the real window
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
- **Open:** how a widget's frame looks on the rail (no halo, or a lighter one).
- Supersedes the earlier core invariant L13 ("apps are told to render as widgets") and settles
  L16 (how widgets sit on a rail: free-floating, WG4).
