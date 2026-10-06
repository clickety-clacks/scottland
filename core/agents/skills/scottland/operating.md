# Operating Scottland safely

The user's session holds their open windows. Anything that restarts or reloads it can lose them,
so most work needs neither: settings in `~/.config/scottland/` apply live.

## Reloading

`scottland-reload` (also Super+Ctrl+Alt+R) loads the current Scottland build and config into the
running session in place: windows stay open, widgets carry over, new settings take their saved
values, and helper services restart if their installed code is newer. Run it from anywhere; it goes
through the session's own environment:

```bash
scottland-reload                       # the only running session
scottland-reload --display wayland-1   # one of several
```

A reload is only for a new build (after a package update or a dev install). A session restart is
needed only for changes to the launcher or session hooks.

**Before reloading anyone's live session, rehearse that exact reload headless.** A reload swaps
the compositor's code under open windows, and a crash there loses every one of them. On a test
machine (never the user's daily machine), with two Scottland source checkouts there, both built
(`make plugin`, `make test-hooks`): OLD at the commit the live session runs, NEW at the build you
want to load.

```bash
cd NEW
SCOTTLAND_HEADLESS_DIR=OLD/build/hl-rehearsal tests/reload-rehearsal-test.sh OLD
```

It starts a headless session on OLD's build, opens windows and widgets with real input, reloads it
in place into NEW's plugin as `scottland-reload` does, checks that it survived, still renders and
still takes input, then stops the session and removes what it created. For a change that touches
something it doesn't exercise, do the same by hand with `tests/headless.sh` (`start`, `run`, `ipc`,
`stop`), screenshots and real input.

Only then reload the live session, and only with the user's go-ahead. Afterwards check
`~/.local/state/scottland/wayfire.log` for errors.

To check whether a running session predates the installed build: compare the compositor's start
time (`ps -o lstart= -C wayfire`) with when the build was installed (the package's install date, or
in dev mode the snapshot's time: `ls -l --time-style=full-iso ~/.local/share/scottland/releases/`).
A session that was reloaded since runs the copy named in `$XDG_RUNTIME_DIR/scottland/plugins/current`.

## Testing

- **Never test on the user's daily machine.** No headless sessions, extra compositors, test
  widget services or test harnesses there: they share the user's services and runtime directory
  with the live desktop, and have filled it and frozen it before. Use a separate test machine.
- Drive behavior with real input (Wayfire's `stipc` virtual keyboard, pointer and touch in a
  headless session), not IPC shortcuts that skip input, and look at the result (screenshots).
- Own your test session: give it its own `SCOTTLAND_HEADLESS_DIR`, stop it, and remove exactly
  what you created. Kill only processes you started.
- Don't start, stop or switch sessions on a shared test machine's screen while someone else is
  using it.

## Logs

All in `~/.local/state/scottland/` (`$XDG_STATE_HOME/scottland/`):

| File | What |
|---|---|
| `wayfire.log` | The compositor and the Scottland plugin. `wayfire.log.previous` is the session before (kept when a frozen session needed a new login). |
| `widgets.log` | Which widget each app got and why; widget launches and failures; the widget service. |
| `attention.log` | Configured attention sources. |
| `watch-config.log` | The config watcher (rebuilds when `~/.config/scottland/` changes). |
| `focus-mode.log` | Full-screen focus hooks (`focus.d`) and their failures. |

Integrations add their own logs in the same folder.

## The runtime directory

Scottland keeps per-session state in `$XDG_RUNTIME_DIR/scottland/` (usually
`/run/user/<uid>/scottland/`): the assembled `wayfire.ini`, each session's recorded environment
(`wayland-N.env`), reload copies of the plugin (`plugins/`), widget state files
(`widgets/<display>/`) and the palette (`<display>.palette.json`). `$XDG_RUNTIME_DIR` is a small
memory-backed filesystem shared by everything the user runs.

When it fills up, things fail quietly: widget cards stop updating, config rebuilds and reloads
fail, logs show `No space left on device`. Check:

```bash
df -h "$XDG_RUNTIME_DIR"
du -sh "$XDG_RUNTIME_DIR"/* 2>/dev/null | sort -h | tail
du -sh "$XDG_RUNTIME_DIR"/scottland/* 2>/dev/null | sort -h | tail
scottland-exec --list        # more sessions than the user knows about = leftover test sessions
```

`plugins/` should hold at most one copy (a reload deletes the older ones). Leftover test sessions
and their files are the usual cause. Don't delete what you didn't create: show the user what is
using the space and ask.
