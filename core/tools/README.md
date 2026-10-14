# Scottland's system tools

Scottland's command-line tools are Rust binaries, one per subsystem, in this Cargo workspace. Mike
asked to "group the scripts by subsystem and make a single binary for that subsystem"
([rulings](../../docs/rulings.md)); the shell and Python helpers in `core/libexec` move into them over
time. Like the rest of core, the tools run on any distro and never know about Omarchy.

## Layout

    Cargo.toml        the workspace: shared version, edition and release profile
    lib/              scottland-lib (`use scottland::...`): code every tool shares
    bin/scottland/    the front command
    bin/<tool>/       one crate per tool, building the binary scottland-<tool>

The library holds what the scripts already agree on, so a Rust tool finds the same files a script
does:

- `scottland::dirs`: the runtime, config, state and data directories (XDG, as the scripts resolve
  them) and the hooks directory a session uses (dev snapshot or package).
- `scottland::session`: the running sessions and the environments they recorded at startup, chosen
  the way `scottland-exec` chooses: a named display, else the caller's own session, else the only one.

`scottland-sender-window` reports every Scottland window that the sender's identity proves is
showing or linked to it. It uses the resolver-v1 Python package copied verbatim from
`core/tools/vendor/agent_window_resolver/` at the commit in `VENDORED.json`; the normal `tools`
build and Arch package place that package beside the binaries under `lib/`. The command asks
`scottland-ctl sender-window-snapshot` for mapped window identities and never changes focus or
window state. It uses `scottland-exec`'s bounded recorded-session selection rather than the Rust
session library's blocking liveness probe.

The front command only finds and runs tools, the way `git` runs `git-*`: `scottland` lists the tools
installed, `scottland display ...` runs `scottland-display ...`, and `scottland help display` runs
`scottland-display --help`. It looks beside itself first, then on `PATH`. Each tool works the same
when run directly.

## Adding a tool

Create `bin/<tool>/` with a `Cargo.toml` that names the package `scottland-<tool>`, takes the shared
fields from the workspace and depends on the library:

    [package]
    name = "scottland-<tool>"
    description = "..."
    version.workspace = true
    edition.workspace = true
    rust-version.workspace = true
    license.workspace = true
    publish.workspace = true

    [dependencies]
    scottland.workspace = true

Nothing else needs editing: the Makefile and the Arch package build, install and link every crate
under `bin/`. When a tool replaces a script, remove the script and its install lines in the same
change; until then the tool, installed after the scripts, takes the script's place in `libexec`.

## Build, test, install

    make tools          # build every tool into build/tools/bin
    make tools-test     # the workspace's tests
    make dev-install    # also snapshots the tools and links them into the dev session and ~/.local/bin
    make package        # the Arch package installs them in /usr/lib/scottland/libexec and /usr/bin

All of these need `cargo`. Builds go to `build/cargo`, so `make clean` removes them with the rest.
The sender-window tool also needs the distro's Python 3 runtime, already required by Scottland.

How the tools' sources ship on a machine, and how they are edited and rebuilt there, is a separate
design; nothing here assumes it.
