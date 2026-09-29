# Scottland: agent guide

Scottland is a spatial desktop for Linux on Wayfire, after Scott Jenson's "working memory" concept.
Windows scale down as they move from a full-scale center zone toward the screen edges, and turn
into widgets on thin rails at the edges. See README.md for the idea and layout.

**Every behavior Mike has asked for is in [INVARIANTS.md](INVARIANTS.md).** Treat it as the
spec and the test list. When Mike asks for a new behavior or changes one, add or update its entry
there in the same change.

## Rules

- **Core vs adapter.** `core/` works on any distro and never mentions Omarchy, Hyprland or uwsm.
  Everything Omarchy-specific (the Hyprland shim, Lua host, shortcut import, session switching,
  service handover) lives in `omarchy/` and ships as `scottland-omarchy`.
- **Generic, not per-app.** Never add code for one app. Hyprland IPC goes through the shim,
  Hyprland-config behavior through the Lua host, compositor abilities through the plugin.
- **Leave Hyprland, uwsm and the Omarchy shell alone**, and never edit users' app configs.
- **Stock Wayfire.** Extend it with the plugin; don't fork it. The plugin must load after `move`.
- **No hand-edits on machines.** Install packages, or use dev mode.

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
   there needs Mike's password; stage commands in its `scottland-install` tmux session.
2. Use real input: Wayfire's `stipc` plugin (`tests/drag-test.py`, `tests/wfipc.py`). IPC calls
   that bypass input (e.g. `configure-view`) don't count as verification for input behavior.
3. Look: screenshots with `grim`, and the Lumina webcam facing plumbus's screen
   (`/dev/video2`) when screenshots might lie.
4. Check whether a running session predates the build.
5. Re-check every invariant in INVARIANTS.md the change could affect, and update statuses.

Useful tools: `tests/shell-probe.sh` (stock shell against the shim, headless and sandboxed),
`tests/nested.sh` (Scottland nested in a window), `scottland-ctl`, and the logs in
`~/.local/state/scottland/` (`wayfire.log`, `hyprshim.log`, `handover.log`, `luahost.log`,
`watch-config.log`).
