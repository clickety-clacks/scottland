---
name: scottland
description: Operate and configure Scottland, the Wayfire spatial desktop. Use for tagged installation or upgrades, session selection and reload, window/layout/input settings, app IDs, touch scrolling, writing or choosing rail widgets, attention, display configuration, and troubleshooting.
---

# Scottland

Scottland is a spatial desktop for Linux: stock Wayfire plus the `scottland` plugin, its config
and helpers. Each screen has a full-size **center zone**; windows to either side (the
**periphery**) shrink the farther they are from the center; on thin **rails** at the left and right
edges, windows turn into **widgets**. There are no workspaces: everything open stays on one screen
in some form. The window manager's job is the user's attention, so a window that needs the user
breathes a halo in the attention color, and nothing an agent does should move or cover what the
user is looking at.

This skill covers Scottland itself, on any system. Integrations and distros that ship Scottland may
install their own skill next to this one, named `scottland-<something>`. If one is installed, read
it too: it says what changes on that system.

## Topic guides

Read the matching guide before starting:

- [`concepts.md`](concepts.md): zones and scaling, moving and resizing, rail widgets and their
  modes, Window mode (hints, cycling, solo, pairing), attention, full screen.
- [`widgets.md`](widgets.md): choosing a widget for an app, writing one (with a worked example),
  its launch context, state file, palette, D-Bus interface and actions, attention sources.
- [`control.md`](control.md): `scottland-ctl`, calling Scottland's IPC (including read-only state
  such as `scottland/hints`), shortcuts, temporary shortcut layers, key remaps, touch scrolling.
- [`outputs.md`](outputs.md): screen rotation, scale and position.
- [`operating.md`](operating.md): installation, upgrades, session selection, reloading safely,
  testing, logs, the runtime directory.
- [`troubleshooting.md`](troubleshooting.md): hints missing, a widget stuck, a suspected leak.

## Which environment are you in?

Check before acting; the answers change what is safe.

| Question | How to tell |
|---|---|
| Is Scottland running on this machine? | `scottland-exec --list` prints one line per running session (`wayland-1 ...`). Nothing printed: no session is running. |
| Is your shell inside it? | `XDG_CURRENT_DESKTOP` starts with `Scottland`. Agents and ssh shells usually aren't, even when the user's screen shows Scottland. Either way, reach the session with `scottland-exec` (below), never by setting its variables yourself. |
| Packaged or dev mode? | Inspect `~/.local/share/scottland/dev/plugins/libscottland.so`: a valid development plugin takes precedence over the packaged plugin at startup/reload. A directory alone does not prove which build is running; see [`operating.md`](operating.md). |
| Is this the user's daily machine? | Ask if you don't know. Never run test sessions there (see [`operating.md`](operating.md)). |

## Where settings live

Scottland assembles the session's config from layers; later layers win.

| Layer | Who writes it | Notes |
|---|---|---|
| `/usr/share/scottland/scottland.ini` | The package | Shipped defaults. Never edit it. |
| `~/.config/scottland/scottland.ini` | The user, rarely | A complete replacement for the shipped file if present. Avoid: it stops tracking shipped changes; prefer `overrides.ini`. In dev mode it is a link to the snapshot's shipped file: leave it. |
| Generated fragments | Integrations | Added by programs in Scottland's `config.d/` (for example shortcuts imported from another desktop). |
| `~/.config/scottland/layout.ini` | Scottland Settings (`scottland-settings`, Super+,) | Zones, scale curve, goo, Window mode, translucency and widget settings. Global sizes and per-screen zone overrides use exact reported make/model/serial identity. Prefer the app. |
| `~/.config/scottland/solar.ini` | Scottland Settings (Sunlight) | Sun following and its location sources. Standalone defaults enable sun following and network lookup; an integration may require opt-in. Saved explicit choices win. Geoclue comes first, then saved coordinates, then the network lookup. |
| `~/.config/scottland/overrides.ini` | The user, and you on their behalf | Wayfire ini format, appended last, so it wins over everything. Scottland never writes it. |

Other files in `~/.config/scottland/`: `widgets.ini` (which widget each app gets),
`attention.d/*.ini` (attention sources), `focus.d/` (the user's hooks run when full screen starts
and ends).

Settings owns pending edits: use its Save/Cancel behavior instead of editing `layout.ini`
underneath it. Screens with the same reported make/model/serial share a zone override; a screen
without that identity uses global sizes. “Reset to global” removes a screen's override.

The result is `$XDG_RUNTIME_DIR/scottland/wayfire.ini`; read it to see the effective config. A
watcher rebuilds it within a second or two when anything in `~/.config/scottland/` changes, and
Wayfire applies the result live: no reload or restart for settings. To edit `overrides.ini`, put
each key under its section (`[scottland]`, `[input]`, `[command]`, `[output:NAME]` ...), add only
the keys you change, and keep the user's existing lines. Check a live value with
`scottland-ctl option SECTION/KEY`.

Common `[scottland]` settings:

| Setting | What it does |
|---|---|
| `lift_delay = 350` | Milliseconds a finger must rest on a window before it lifts to move. |
| `sounds = true` | Interface sounds (the lift "bloop"). |
| `touchpad_gestures = true` | Three-finger drag moves a window; three-finger click-drag resizes it; a three-finger hold solos or pairs. |
| `alt_hold_delay = 300` | How long Alt alone must be held before Window mode shows hints. |
| `minimize_hold_delay = 300` | Super+M: shorter is a tap (cycle widget mode), longer is a momentary hold. |
| `goo = true` | One liquid surface around all windows; `false` gives each window its own halo band. |
| `window_avoidance_always = true` | Keep covered windows peeking out even outside Window mode. |
| `touch_scroll_<name>`, `touch_scroll_wheel` | Touchscreen scrolling for apps that ignore touch ([`control.md`](control.md)). |
| `remap_apps_<name>`, `remap_from_<name>`, `remap_to_<name>` | Per-app key remaps ([`control.md`](control.md)). |

`[input]` holds Wayfire's input options: `natural_scroll`, `touchpad_scroll_speed` (shipped 0.2),
`click_method` (shipped `clickfinger`), `drag_lock`, `xkb_layout`, `xkb_options`, `cursor_size`.

## Reaching a running session

Use these; they run in the session's own recorded environment, from anywhere (another desktop's
terminal, ssh, an agent):

    scottland-exec --list                       running sessions
    scottland-exec [--display wayland-N] -- CMD run CMD inside a session (grim, wl-copy, a script)
    scottland-ctl ...                           settings, windows, present (see control.md)
    scottland-reload [--display wayland-N]      load the current build and config in place (see operating.md)

`--display` is only needed when several sessions run on one machine.

## Don't

- Don't edit shipped files: anything under `/usr/share/scottland` or `/usr/lib/scottland`, or a
  dev snapshot under `~/.local/share/scottland/`. Override in `~/.config/scottland/`.
- Don't hand-set `WAYFIRE_SOCKET`, `WAYLAND_DISPLAY`, `SCOTTLAND_HOOKS` or similar to reach a
  session; use `scottland-exec`.
- Don't restart or reload the user's session to apply settings; config changes apply live. Reload
  only for a new build, and only as [`operating.md`](operating.md) says.
- Don't run tests, extra compositors, headless sessions or test widgets on the user's daily
  machine.
- Don't move, focus, resize or cover the user's windows unasked. To show the user a window they
  asked for, use `scottland-ctl present` ([`control.md`](control.md)).
- Don't silently replace something the user had (a shortcut, a remap, a widget choice). If a change
  displaces it, tell the user what it was, what it is now and why.
- Don't delete files in the runtime or state directories you didn't create; ask the user.
