# Scottland: agent guide

Scottland is a spatial desktop for Linux on Wayfire, after Scott Jenson's "working memory" concept.
Windows scale down as they move from a full-scale center zone toward the screen edges, and turn
into widgets on thin rails at the edges. See README.md for the idea and layout.

**The behaviors Mike has asked for are the spec and the test list:**

- [core/INVARIANTS.md](core/INVARIANTS.md): the desktop itself, on any distro.
- [omarchy/INVARIANTS.md](omarchy/INVARIANTS.md): the Omarchy adapter (switching, service
  handover, Hyprland shim, shortcut import, menu entries).

When Mike asks for a new behavior or changes one, add or update its entry in the right file in the
same change.

**Scottland's tenets are in [docs/tenets.md](docs/tenets.md).** Read them before designing a
feature: when a request leaves an edge unspecified, decide it the way the tenets point and say which
tenet decided it; when they conflict, ask Mike.

**Design docs live in [docs/](docs/)**, one file per subsystem, each with its own invariants table
(IDs prefixed per doc). When a subsystem's design grows past a few rows in the files above, give it
a doc there and leave a one-line pointer in the invariants file. Current docs:

- [docs/distro-notes.md](docs/distro-notes.md): defaults that belong to a future Scottland distro, not core.
- [docs/tenets.md](docs/tenets.md): what Scottland is for; how to decide unspecified edges.
- [docs/widgets.md](docs/widgets.md): rail widgets (WG1-WG22): what a widget is, how it's chosen,
  its launch context and D-Bus interface, the default card, the live morph while dragging, Esc,
  attention on widgets.
- [docs/windowing-keys.md](docs/windowing-keys.md): Window mode: hints, theme colors, cycles, keyboard inertia and the exterior widget hints and the declutter pause (WK1–WK27),
  remembered zones and contention-aware placement (WP1–WP7).
- [docs/goo.md](docs/goo.md): the goo (GO1-GO13): the halo as one liquid for the whole screen,
  dye for state colors, live tuning, overlap film, control highlight, antialiasing, GPU cost (implemented; on by default, with a per-window halo fallback).
- [docs/settings.md](docs/settings.md): the settings app, Scottland Settings (S1-S18): zone sliders and
  overlay with draggable borders, Goo tab, hint popouts, the planned Window mode tab with friction curves.
- [docs/desktop-model.md](docs/desktop-model.md): the single reactive desktop state model and its snapshots.
- [docs/key-layers.md](docs/key-layers.md): focused-surface shortcut layers (KL1–KL8), IPC and fall-through.
- [docs/attention.md](docs/attention.md): attention (AT1-AT6): sources, per-app configuration,
  pluggable and networked sources (mostly not built yet).

## Where does a request belong?

Every time Mike asks for something, decide which of these it is, **say which you assumed and
why**, and ask only if it's genuinely unclear:

1. **Core** (`core/`, package `scottland`): what any Scottland user on any distro would want from
   the desktop itself. Layout, scaling, the plugin's compositor abilities, the settings app,
   shipped defaults, acting as a graphical session.
2. **Adapter** (`omarchy/`, package `scottland-omarchy`): exists only because Scottland runs on
   Omarchy beside Hyprland and uwsm. The Hyprland shim, Lua host, shortcut import, switching
   between desktops, service handover, Omarchy menu entries.
3. **Mike's personal preference**: his taste, not a default others should get. Goes in his own
   files, never in the repo: `~/.config/scottland/overrides.ini` (appended last, wins over
   everything; example: natural scrolling), or `~/.config/scottland/layout.ini` (written by the
   settings app).

Hints: "turn X off in both installed and shipped" is core. Anything mentioning Hyprland,
Omarchy's menu, uwsm or Hyprland-config behavior is the adapter. "I like", "for me", or a tweak to
feel/appearance with no reason others would want it is personal. A shipped default that's just
a value (a slider position) is usually personal unless Mike says it should ship.

## Scope and project rules

| ID | Rule |
|---|---|
| C1 | Scottland implements Scott Jenson's spatial "working memory" desktop concept (KDE Akademy 2026 talk), with his go-ahead. The README credits him and says it is independent, not his. |
| C2 | No workspaces. Window navigation and layout are Scottland's own; Hyprland's workspace/tiling behavior is not reproduced. |
| C3 | Stock Wayfire, not a fork: Wayfire + the scottland plugin + config + integration. The plugin must load after `move`. |
| C4 | Core never references Omarchy, Hyprland or uwsm. Omarchy integration ships as `scottland-omarchy`. |
| C5 | Omarchy users install one package and keep their environment as far as possible: Scottland overrides Omarchy only where that's needed for Scottland to work consistently (its own feature shortcuts, window placement/scaling, no workspaces, and later notifications), and minimally. Other distros (e.g. Ubuntu) run the core alone. |
| C6 | Hyprland, uwsm and the stock Omarchy shell are not modified. |
| C7 | Scottland may tune common apps (e.g. scroll feel), but only additively: it owns its own files (under `~/.config/scottland/apps/`) and adds at most one clearly marked, optional include line to an app's config, the way Omarchy includes its theme files. It never rewrites the user's own settings, and removing that line undoes it completely. Apps Omarchy ships are the first target (adapter); cross-distro apps can be core. |
| C8 | Generic, not per-app: Hyprland IPC goes through the shim, Hyprland-config behavior through the Lua host, compositor abilities through the plugin. No code for one particular app. |
| D1 | Nothing is hand-edited on a machine: files come from a package, or from the repo in dev mode. |
| D2 | Nothing is reported as done until it has been exercised with real input on a real session (plumbus), and any already-running session was checked for predating the build. |

## Build and dev mode

    make plugin          # build core/plugin into ./build
    make dev-install     # install a snapshot of HEAD (committed work only) and point the session at it
    make package         # build and install the Arch packages (needs sudo)

Dev mode covers everything except the root helper (`omarchy/helper/`), its polkit policy and
system unit files; those need a package install. A running Scottland keeps the plugin it started
with until it's reloaded (below) or restarted.

## Operating a running session from outside

Agents and ssh shells are not inside Scottland: their environment belongs to wherever they run
(often Hyprland, whose `HYPRLAND_INSTANCE_SIGNATURE` would send shortcut keys to Hyprland).
**Never hand-set `WAYFIRE_SOCKET`, `WAYLAND_DISPLAY`, `SCOTTLAND_HOOKS` or similar to reach a
session.** Each session records its own environment at startup (`autostart.d/01-record-environment`),
and these tools use that, never the caller's:

    scottland-reload [--display wayland-N]     load the current build and config in place
    scottland-exec --list                      running sessions
    scottland-exec [--display wayland-N] -- CMD   run CMD inside a session (grim, foot, wfipc.py...)

`--display` is only needed when several sessions run on one machine. Helpers a reload restarts
also take the session's environment (e.g. `reload.d/30-lua-host` copies the Hyprland shim's).

## Testing

Before reporting anything as done:

1. Pick where to test:
   - **Headless** (`tests/headless.sh`): a full Scottland with no screen, running the real
     config and imported shortcuts (`start --omarchy` adds the Hyprland shim and Lua host).
     It touches nobody's display or services, so it's the default for logic, shortcuts and
     input handling. `run CMD` runs a command inside it; `ipc METHOD JSON` drives stipc.
   - **plumbus** (Omarchy test machine on the tailnet) for anything that must be seen on a
     real screen or needs a real login. `tests/deploy.sh plumbus [--reload]` syncs, builds and
     dev-installs there. plumbus is shared: other agents run tests on it. Check what's on its
     screen first (deploy prints it); don't start, stop or switch sessions while someone else
     is using it. sudo there needs Mike's password; stage commands in its `scottland-install`
     tmux session and open a terminal attached to it on Mike's screen.
2. Use real input: Wayfire's `stipc` plugin (`tests/wfipc.py`, `tests/drag-test.py`). IPC calls
   that bypass input (e.g. `configure-view`) don't count as verification for input behavior.
3. Look: screenshots with `grim` (through `scottland-exec`), and on plumbus the Lumina webcam
   facing its screen (`/dev/video2`) when screenshots might lie.
4. Check whether a running session predates the build (`scottland-reload` loads it in place).
   **Before reloading anyone's live session, rehearse that exact reload headless**: start
   `tests/headless.sh` on the build the live session runs now (a `git worktree` at its commit),
   open a few windows, reload into the new build, and check it survives and renders. A reload
   swaps code under open windows; a crash there loses the user's windows. (2026-09-29: an
   unrehearsed reload crashed Mike's session; the cause, statics shared across plugin copies,
   is now prevented by `-fno-gnu-unique` in core/plugin/meson.build.)
5. Re-check every invariant the change could affect, and update its status.
6. Test sessions never read personal config (`~/.config/scottland/layout.ini`, `overrides.ini`):
   `tests/headless.sh` builds from the checkout's shipped config only.
7. Keep test output out of `XDG_RUNTIME_DIR`: it is a small tmpfs (1.6 GB on osanwe) shared with the
   live session. Put screenshots and logs under the checkout's `build/`, stop and remove headless
   dirs when done. Don't point `XDG_RUNTIME_DIR` itself elsewhere either: a private runtime dir
   restarts display names at wayland-1 and its widget units then share names with the live
   session's. (2026-10-02: test leftovers and dead Quickshell logs filled it, and the live
   session's widget cards could not start, so widgetized windows vanished.)

Useful tools: `tests/headless.sh`, `tests/deploy.sh`, `tests/shell-probe.sh` (stock shell
against the shim, headless and sandboxed), `tests/nested.sh` (Scottland in a window on
Hyprland), `scottland-ctl`, `scottland-exec`, and the logs in `~/.local/state/scottland/`
(`wayfire.log`, `hyprshim.log`, `handover.log`, `luahost.log`, `watch-config.log`).
