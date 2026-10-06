# Rail widgets

A window moved onto a rail becomes a **widget**: the window stays alive but hidden, and a widget
program stands in for it. A widget is any program (a Quickshell QML file, a GTK or Qt app, a web
view, a terminal UI...), fully interactive, running as the user with the user's normal access.
Scottland imposes no widget API; it gives the widget the window's identity and live state, and
draws the frame, halo, attention and morph around it.

## Choosing a widget for an app

First match wins:

1. The user's choice in `~/.config/scottland/widgets.ini` (create it if missing):

   ```ini
   [widgets]
   # exact app-id (or .desktop id, without .desktop), case-sensitive = widget id
   org.gnome.Nautilus = card
   foot = title
   ```

2. The app's own widget: its `.desktop` entry's `X-Scottland-Widget=<widget id>`, or an installed
   widget package whose `apps` list matches the app-id.
3. The default **card** (icon, title over app name, unread badge).

Any widget can be assigned to any app. The choice is made when the window goes onto the rail;
widgets already on a rail keep theirs until they're opened and put back. Find the app-id with
`scottland-ctl windows` (second column). `~/.local/state/scottland/widgets.log` says which widget
was chosen for each app and why.

## Widget packages

A widget package is a folder with a `widget.toml` manifest and whatever its command needs:

```toml
id = "my-widget"                     # what widgets.ini and X-Scottland-Widget name
name = "My widget"
apps = ['^org\.example\.App$']       # optional: app-id regexes it's the app's own widget for
exec = "quickshell -p %d/shell.qml"  # any command; runs in the package folder
touch_drag = true                    # optional: a one-finger drag anywhere moves it
```

`touch_drag` is only for widgets that drag nothing themselves (no sliders, no drawing); without it
a finger drag belongs to the widget and a long press lifts it, as with any window.

Packages are found, first one with an id wins, in:

| Folder | For |
|---|---|
| `$SCOTTLAND_WIDGET_PATH` (colon-separated, if set) | Development and tests |
| `~/.local/share/scottland/widgets/<name>/` | The user's own widgets |
| `/usr/share/scottland/widgets/<name>/` | Widgets installed with apps (removed with them) |
| `/usr/lib/scottland/widgets/` | Scottland's built-ins (the `card`) |

Placeholders in `exec`, filled per argument: `%a` app-id, `%t` title at launch, `%i` icon, `%p`
the app's pid, `%w` window id, `%r` rail at launch (`left`/`right`), `%d` the package folder, `%%`.

## Worked example: a title widget

This example ships with this skill in `examples/title-widget/`. It shows the window's title on the
desktop's colors, follows title and color changes live, and opens the window on click.

Install it for the user and choose it for an app:

```bash
mkdir -p ~/.local/share/scottland/widgets
cp -r <this skill's folder>/examples/title-widget ~/.local/share/scottland/widgets/title
```

and add `foot = title` under `[widgets]` in `~/.config/scottland/widgets.ini` (create the file with
that section if it's missing; one `[widgets]` section only).

`widget.toml`:

```toml
id = "title"
name = "Title"
exec = "quickshell -p %d/shell.qml"
touch_drag = true
```

`shell.qml`:

```qml
// A minimal rail widget: the window's title on the desktop's colors. Click opens the window.
import QtQuick
import Quickshell
import Quickshell.Io

FloatingWindow {
    id: root
    implicitWidth: 240
    implicitHeight: 64
    color: "transparent"

    // Live presentation (title, rail, collapsed, badge...) and the desktop's colors.
    property var state: ({})
    property var colors: ({})
    FileView {
        path: Quickshell.env("SCOTTLAND_WIDGET_STATE")
        watchChanges: true
        onFileChanged: reload()
        onLoaded: { try { root.state = JSON.parse(text()) } catch (e) {} }
    }
    FileView {
        path: Quickshell.env("SCOTTLAND_PALETTE")
        watchChanges: true
        onFileChanged: reload()
        onLoaded: { try { root.colors = JSON.parse(text()) } catch (e) {} }
    }

    // Open the window in the center, as a click on the default card does.
    Process {
        id: open
        command: ["busctl", "--user", "call", "org.scottland.Widgets",
            "/org/scottland/widget/" + Quickshell.env("SCOTTLAND_WIDGET_ID"),
            "org.scottland.Widget", "Open"]
    }

    Rectangle {
        anchors.fill: parent
        radius: 20
        color: root.colors.background || "#2e3440"
        Text {
            anchors.fill: parent
            anchors.margins: 16
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
            text: root.state.title || root.state.app_id || ""
            color: root.colors.foreground || "#d8dee9"
            font.bold: true
        }
        MouseArea {
            anchors.fill: parent
            onClicked: open.running = true
        }
    }
}
```

Then drag a `foot` window onto a rail: the widget replaces it. Nothing needs reloading; each new
widget launch reads the manifests. If it doesn't appear, read `~/.local/state/scottland/widgets.log`.
A widget that shows no window within 8 seconds is abandoned and the app's window comes back.

## What a widget gets

**Environment** (identity and paths; fixed for the launch):

| Variable | Value |
|---|---|
| `SCOTTLAND_WIDGET_ID` | The widget's id (its D-Bus object is `/org/scottland/widget/<id>`) |
| `SCOTTLAND_WIDGET_APP_ID`, `_NAME`, `_ICON`, `_DESKTOP` | The app's app-id, name, icon and `.desktop` id |
| `SCOTTLAND_WIDGET_PID`, `_WINDOW` | The app's process and the window's Scottland id |
| `SCOTTLAND_WIDGET_STATE` | The state file (below) |
| `SCOTTLAND_PALETTE` | The palette file (below) |

**State file** (`SCOTTLAND_WIDGET_STATE`): one JSON object with the complete current
presentation, written before the widget starts and atomically replaced on every change. Watch it
and replace your copy each time; never merge. Fields: `id`, `app_id`, `desktop`, `name`, `icon`,
`title`, `badge` (unread count, 0 for none), `rail` (`left`/`right`), `minimized` (true while
widgets are collapsed: draw a compact form; the card becomes a square around its icon), `focused`,
`urgent`, `data` (the app's mailbox payload, below), `version`, `revision`.

**Palette** (`SCOTTLAND_PALETTE`): JSON with `scheme` (`light`/`dark`), `background`,
`foreground`, `muted`, `accent`, `alert`, as `#rrggbb`. It follows the desktop's light/dark
setting and accent (and an integration's theme) live; watch the file.

Scottland draws around the widget: its frame and halo (at 100%), the attention breath, the morph
between window and widget, collapse and expand. A widget doesn't need to do any of that. Keep the
window's size fixed for a given form; Scottland keeps its screen-edge side in place when it
changes.

## D-Bus interface and actions

On the session bus, service `org.scottland.Widgets`:

- `/org/scottland/widget/<id>`, interface `org.scottland.Widget`: properties `Id`, `Version`,
  `Revision`, `AppId`, `Title`, `Pid`, `Window`, `Rail`, `Focused`, `Urgent`, `Badge`, `Data`,
  `Minimized` (with `PropertiesChanged`), and methods:

  | Method | Effect |
  |---|---|
  | `Open()` | Open the window in the center, as clicking the card does |
  | `Restore()` | Bring the window back and focus it; the widget goes |
  | `Close()` | Close the window and the widget (the window is shown first, so a "save changes?" question is visible) |
  | `Focus()` | Focus the widget |

  ```bash
  busctl --user call org.scottland.Widgets /org/scottland/widget/42 org.scottland.Widget Open
  ```

- `/org/scottland/Widgets`, interface `org.scottland.WidgetData`: an optional mailbox for apps
  without a service of their own. The app (or its helpers) calls `Publish(json)` and its widget's
  `Data` (and state file `data`) changes; a widget calls `Send(json)` and its app receives the
  `Received(app_pid, window, json)` signal. Callers are identified by their process tree: only an
  app can publish for its windows, only a widget can send for its window.
- `/org/scottland/Widgets`, interface `org.scottland.Windows`: an app calls `GetState()` from its
  own process to learn whether it's widgetized and its window's scale; `StateChanged(pid,
  widgetized, scale)` signals changes.

The same actions are on Scottland's IPC as `scottland/widget-action {"id":"42","action":
"open"|"restore"|"close"|"focus"|"minimize"}` ([`control.md`](control.md)).

Widgets can learn more from standard sources without Scottland: `/proc/<pid>/` for the app's
process (in a terminal: what's running, and where), MPRIS for media, the Unity Launcher API for
badge counts, StatusNotifierItem, recently-used files, and AT-SPI accessibility.

## The default card

The card is a Quickshell file, `/usr/lib/scottland/widgets/card/shell.qml` (in dev mode under
`~/.local/share/scottland/dev/widgets/card/`). Read it as a fuller example: it renders only from
the state file, follows the palette, draws the badge on the corner away from the screen edge, keeps
its screen-edge side when it grows, and calls `Open` on click. Don't edit it; copy it into your own
package with a new `id`.

## Attention sources

Built in: an app's bell or focus request, its urgency hint, a desktop notification from its own
process. Another program that knows which windows need the user can be a **source**, under its own
name; each source turns only its own attention off, and going to the window answers all of them.

**From a listing command**: a file `~/.config/scottland/attention.d/<name>.ini`, then
`scottland-reload` (or a new session) picks it up:

```ini
[source]
# A command printing JSON that lists the windows needing attention now.
list = some-command --json
# Where the list is in that JSON: a key, keys joined by "." when nested, empty for the top level.
windows = items
# The field of each entry that names its window (default: window).
window = id
# How that field names a window: id (a Scottland window id, the default), pid (the process
# owning the window, when it owns only one), or hex-offset:<base> (hexadecimal, window id + base).
format = id
# Seconds between listings (default 2).
interval = 2
# Optional: a file whose changes mean the list changed; it's read again at once.
watch = ~/.cache/x/state.json
# Optional: run when the user goes to a window the source listed; {field} is that entry's field.
answered = some-command dismiss {id}
```

Comments go on their own lines: text after a value is part of the value.

Log: `~/.local/state/scottland/attention.log`.

**Directly**: any process in the session can call IPC
`scottland/attention {"window": 42, "attention": true, "source": "my-source"}` (and `false` to
take its own back). Source names starting with `builtin:` are reserved. A window already in front
of the user is answered at once.
