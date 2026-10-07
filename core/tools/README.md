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

## Weather app

`bin/weather` installs `scottland-weather`, the launcher for the core Quickshell app in
`core/apps/weather`. It reads the current Scottland location through the existing location setup,
fetches current conditions, and retains the reading in the app. Each reading published to a
widget includes `status` and an RFC 3339 UTC `fetchedAt`; current and stale readings also include
their condition fields and the last successful `updated` time. Publication uses the existing
WG11 data method over the WG9 widget service after WG12 reports the app widgetized. Dev and test
hooks expose the app at `apps/weather`; the Arch package installs the app assets and
`scottland-weather.desktop`. The Weather widget package remains separate in Flavorings.

## System Stats app

`bin/system-stats` installs `scottland-system-stats`, the launcher for the core Quickshell app in
`core/system-stats`. It reads CPU, memory and load every two seconds; unavailable values and the
`updated` reading time are `null`. The app publishes widget readings through the existing WG11
method over the WG9 service when WG12 reports it widgetized. The Arch package installs
`scottland-system-stats.desktop`; the System Stats widget package remains separate in Flavorings.

## Build, test, install

    make tools          # build every tool into build/tools/bin
    make tools-test     # the workspace's tests
    make dev-install    # also snapshots the tools and links them into the dev session and ~/.local/bin
    make package        # the Arch package installs them in /usr/lib/scottland/libexec and /usr/bin

All of these need `cargo`. Builds go to `build/cargo`, so `make clean` removes them with the rest.

How the tools' sources ship on a machine, and how they are edited and rebuilt there, is a separate
design; nothing here assumes it.
