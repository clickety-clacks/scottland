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
| C5 | Omarchy users install one package and keep their environment; only window placement/scaling and workspaces change. Other distros (e.g. Ubuntu) run the core alone. |
| C6 | Hyprland, uwsm and the stock Omarchy shell are not modified. |
| C7 | Scottland may tune common apps (e.g. scroll feel), but only additively: it owns its own files (under `~/.config/scottland/apps/`) and adds at most one clearly marked, optional include line to an app's config, the way Omarchy includes its theme files. It never rewrites the user's own settings, and removing that line undoes it completely. Apps Omarchy ships are the first target (adapter); cross-distro apps can be core. |
| C8 | Generic, not per-app: Hyprland IPC goes through the shim, Hyprland-config behavior through the Lua host, compositor abilities through the plugin. No code for one particular app. |
| D1 | Nothing is hand-edited on a machine: files come from a package, or from the repo in dev mode. |
| D2 | Nothing is reported as done until it has been exercised with real input on a real session (plumbus), and any already-running session was checked for predating the build. |

## Build and dev mode

    make plugin          # build core/plugin into ./build
    make dev-install     # symlink plugin, hooks, helpers and settings from the repo
    make package         # build and install the Arch packages (needs sudo)

Dev mode covers everything except the root helper (`omarchy/helper/`), its polkit policy and
system unit files; those need a package install. A running Scottland keeps the plugin it started
with: restart the session after rebuilding the plugin.

## Testing

Before reporting anything as done:

1. Test on **plumbus** (Omarchy test machine on the tailnet; installing there is fine). sudo
   there needs Mike's password; stage commands in its `scottland-install` tmux session and open a
   terminal attached to it on Mike's screen.
2. Use real input: Wayfire's `stipc` plugin (`tests/drag-test.py`, `tests/wfipc.py`). IPC calls
   that bypass input (e.g. `configure-view`) don't count as verification for input behavior.
3. Look: screenshots with `grim`, and the Lumina webcam facing plumbus's screen
   (`/dev/video2`) when screenshots might lie.
4. Check whether a running session predates the build.
5. Re-check every invariant the change could affect, and update its status.

Useful tools: `tests/shell-probe.sh` (stock shell against the shim, headless and sandboxed),
`tests/nested.sh` (Scottland nested in a window), `scottland-ctl`, and the logs in
`~/.local/state/scottland/` (`wayfire.log`, `hyprshim.log`, `handover.log`, `luahost.log`,
`watch-config.log`).
