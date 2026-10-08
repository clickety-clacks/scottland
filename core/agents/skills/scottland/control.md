# Controlling Scottland

## `scottland-ctl`

Works from any shell: outside a session it runs itself inside one through `scottland-exec`.

```bash
scottland-ctl windows                    # open windows: id, app-id, zone, scale, title
scottland-ctl option scottland/sounds    # an option's live value (any Wayfire option, SECTION/KEY)
scottland-ctl get                        # the layout, goo, Window mode and widget settings as JSON
scottland-ctl set min_scale 0.3          # change one of those live (not saved; gone at the next session)
scottland-ctl present 42                 # show window 42 (or its widget's window) to the user now
```

`windows` columns: Scottland window id, app-id, zone (`center`, `continuous` for the periphery,
`widget` for a rail), applied scale, title. Per-app settings match the **app-id**. `get` lists settings
the running plugin doesn't know under `unsupported` (the session predates them).

To keep a setting, write it to `~/.config/scottland/overrides.ini` (or let the user save it in
Scottland Settings); `set` is only for trying values live.

**Presenting** a window ("I want to see this now"): `present` opens a widget as if clicked; a
window at the side flies to its remembered center spot (else the nearest least-covered full-size
spot in the center) at 100%; a window already in the center stays put; either way it's raised and
focused. Use it when the user picks a window (in a launcher, a list of agents). Don't present
windows the user didn't ask for: other programs may ask for attention, only the user grants it.

## Calling Scottland's IPC

Scottland's plugin answers Wayfire IPC on the session's `WAYFIRE_SOCKET`: a 4-byte little-endian
length, then JSON `{"method": ..., "data": {...}}`; the reply is framed the same way. Call it
through `scottland-exec`, which supplies the session's socket:

```bash
scottland-exec -- python3 -c '
import json, os, socket, struct, sys
s = socket.socket(socket.AF_UNIX); s.connect(os.environ["WAYFIRE_SOCKET"])
body = json.dumps({"method": sys.argv[1], "data": json.loads(sys.argv[2])}).encode()
s.sendall(struct.pack("<I", len(body)) + body)
def read(n):
    b = b""
    while len(b) < n: b += s.recv(n - len(b))
    return b
print(read(struct.unpack("<I", read(4))[0]).decode())
' scottland/hints '{}'
```

**Read-only** (safe to call any time):

| Method and data | Returns |
|---|---|
| `scottland/layout-state {}` | Every view: `id`, `app_id`, `title`, `zone`, `scale`, `applied_scale`, `widget` (is a widget's own window), `widgetized` (an app window shown as a widget), `hidden`, and more |
| `scottland/hints {}` | Window mode: `active` (hints showing), and per window `window`, `hint` (its letters), `visible`, `rendered`, where it's drawn |
| `scottland/widgets {}` | The widgets on the rails now, with their windows |
| `scottland/widget-mode {}` | `mode` (`expanded`, `collapsed`, `hidden`) and what's `shown` now |
| `scottland/desktop-model {"slice": "desktop" or "widgets" or "attention"}` | One complete, versioned snapshot of that part of the desktop model |
| `scottland/key-layer {"action": "list"}` | Surfaces and their temporary shortcut layers (below) |
| `scottland/center-switcher {}` | The Alt+Tab switcher's state |

**Watching**: `scottland/subscribe {"slice": "desktop"|"widgets"|"attention"}` returns the
complete current slice at once, then sends `scottland-model#`, `scottland-widgets#` or
`scottland-attention#` events on the same connection, each a complete replacement (never a delta).
Replace your copy with each newer `version`; a new `session` replaces everything. Don't poll.

**Actions** (they change what the user sees; only when the user asked):

| Method and data | Effect |
|---|---|
| `scottland/present {"window": 42}` | Same as `scottland-ctl present` |
| `scottland/widget-action {"id": "42", "action": "open"}` | `open` (the window to the center, as a click on the card), `close`, `focus`, `minimize` (collapse/expand that widget), or `restore` (the window shown where it's parked on the rail; prefer `open`) |
| `scottland/widget-mode {"mode": "collapsed"}` | `expanded`, `collapsed`, `hidden` or `next`: the same as tapping Super+M |
| `scottland/attention {"window": 42, "attention": true, "source": "name"}` | Turn attention on or off under your own source name ([`widgets.md`](widgets.md#attention-sources)) |
| `scottland/key-layer {...}` | Temporary shortcut layers (below) |

Other `scottland/*` methods (`test-input`, `audit-model`, `send-key`...) are for test harnesses and
integrations; don't call them on a user's session.

## Shortcuts

Shortcuts are Wayfire bindings in the config. Change or add them in `overrides.ini`:

```ini
[command]
binding_files = <super> KEY_E
command_files = nautilus

[scottland]
minimize_widget = <super> KEY_N
```

Scottland's own keys are in `[scottland]` (`minimize_widget`, `navigate_left`/`_right`/`_up`/
`_down`, `center_switcher_next`/`_previous`, `move`, `resize`...) and in `[command]`
(`binding_scottland_settings`, `binding_scottland_reload`). Before choosing a key, check it's free
in `$XDG_RUNTIME_DIR/scottland/wayfire.ini` (the effective config). If the key was doing something
else for the user, tell them what it was and what it does now.

**Key-release commands**: `release_key_<name> = <xkb keysym>` and `release_command_<name> = <cmd>`
in `[scottland]` run a command when a key is let go.

## Per-app key remaps

For apps whose app-id matches, turn one key combination into another before shortcuts and the app
see it; holding the key repeats the replacement. Entries in `[scottland]`, three keys per name:

```ini
[scottland]
# App-id regex, case-insensitive.
remap_apps_zed_save = ^dev\.zed\.Zed$
# Modifiers CTRL, SHIFT, ALT, SUPER joined with + to an xkb keysym name.
remap_from_zed_save = CTRL+S
remap_to_zed_save = CTRL+SHIFT+S
```

Shipped: in browsers, Ctrl+W deletes the previous word (it becomes Ctrl+BackSpace) and Ctrl+Alt+W
closes the tab (Ctrl+F4). To switch a shipped remap off, give its `remap_from_<name>` an empty
value, e.g. `remap_from_browser_word =`.

## Temporary shortcut layers for a surface

An app can claim its own shortcuts while one of its surfaces has keyboard focus. Everything it
doesn't claim falls through to the user's shortcuts, release commands and remaps. Claimed keys reach
the surface as ordinary key events with modifiers intact. Registration never changes focus;
toplevels and layer-shell popups work independently, even within one process.

Call `scottland/key-layer` from the app, on its session's socket:

```json
{"action":"list"}
{"action":"set","window":42,"keys":["0:Escape","4:comma","4:j"]}
{"action":"set","pid":1234,"namespace":"my-popup","keys":["0:Up","0:Down"]}
{"action":"clear","window":42}
```

Select a surface with a positive Scottland `window` id, or a client `pid` plus layer-shell
`namespace`; an ambiguous selector is an error. `set` replaces the surface's keys atomically; `clear`
or empty `keys` removes them; replies are `{"result":"ok","window":42}` or `{"error":"..."}`.

Keys are case-sensitive XKB **`MODMASK:keysym`** strings. Modifier bits: Shift=1, Ctrl=4, Alt=8,
Super=64; add them (`5:j` is Ctrl+Shift+J). Lock keys are ignored. Shift consumed to produce a
symbol may be omitted: `4:plus` claims Ctrl+Shift+= where that produces `plus`; `4:j` does not
claim Ctrl+Shift+J.

Register after the surface maps, and again after it remaps or after a Scottland reload. Losing focus
deactivates the layer; unmap, close or the client disconnecting removes it (a short-lived IPC
connection closing does not). Drags and the lock screen keep their input. A claimed key pressed
before Alt is held long enough cancels Window mode for that Alt press. Return on a focused widget
always opens its window, even if claimed.

## Touchscreen scrolling for apps that ignore touch

Many apps scroll with a finger on their own (browsers, most GTK and Qt apps). Some don't, notably
terminals. For apps listed here, Scottland takes the touch: a one-finger drag scrolls (with
momentum after a flick) and a quick tap clicks. A long press still lifts the window.

Entries are `touch_scroll_<name> = <regex>` in `[scottland]`, matched case-insensitively against
the app-id. Shipped:

```ini
touch_scroll_terminals = ^(com\.mitchellh\.ghostty|foot|footclient|Alacritty|kitty|org\.wezfurlong\.wezterm)$
touch_scroll_wheel = ^com\.mitchellh\.ghostty$
```

In `~/.config/scottland/overrides.ini`:

```ini
[scottland]
# Add an app (any name after touch_scroll_; one entry per app or group):
touch_scroll_editors = ^(dev\.zed\.Zed|org\.gnome\.TextEditor)$

# Change a shipped entry: give the same name a new value.
touch_scroll_terminals = ^(com\.mitchellh\.ghostty|foot)$

# Switch a shipped entry off: give the same name an empty value.
touch_scroll_terminals =
```

Don't add apps that already handle touch: they would scroll twice. Test by dragging a finger in
the app; if it already scrolls without an entry, leave it out. Apps that ignore smooth scrolling
get a high-resolution wheel instead: list them in `touch_scroll_wheel` too (only apps also in a
`touch_scroll_<name>` entry). Check the live value with `scottland-ctl option scottland/touch_scroll`.
