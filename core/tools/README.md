# Scottland's system tools

Scottland's command-line tools are Rust binaries, one per subsystem, in this Cargo workspace. Mike
asked to "group the scripts by subsystem and make a single binary for that subsystem"
([rulings](../../docs/rulings.md)); the shell and Python helpers in `core/libexec` move into them over
time. Like the rest of core, the tools run on any distro and never know about Omarchy.

## Layout

    Cargo.toml        the workspace: shared version, edition and release profile
    lib/                    scottland-lib (`use scottland::...`): shared Scottland paths and session code
    bin/scottland/          the front command
    bin/scottland-source/   source mechanism library and thin Scottland command
    bin/<tool>/             one crate per subsystem tool, building scottland-<tool>

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
`scottland-display --help`. It also forwards `edit`, `rollback`, `update` and `status` to
`scottland-source`. It looks beside itself first, then on `PATH`. Each tool works the same when run
directly.

`scottland-source` provides the product-neutral source mechanism library and the thin command that
supplies the product name. It keeps the source history in a root-owned bare repository, builds the
whole workspace in the user's clone, and asks for root only to prepare and activate a completed
build. Product packages carry a base build and a full-history Git bundle; the mechanism adopts that
release after installation and owns the active switch and command links. A future product can use
the library as a pinned Cargo dependency and supply its own thin command.

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

The Makefile builds every crate under `bin/` for development. Product packages build the same
workspace at one commit and place its binaries in the mechanism's base-build directory; runtime
links follow the active build. When a tool replaces a script, remove the script and its install
lines in the same change.

## Build, test, install

    make tools          # build every tool into build/tools/bin
    make tools-test     # the workspace's tests
    make dev-install    # also snapshots the tools and links them into the dev session and ~/.local/bin
    make package        # the Arch package includes the base build and full-history source bundle

All of these need `cargo`. Builds go to `build/cargo`, so `make clean` removes them with the rest.
The sender-window tool also needs the distro's Python 3 runtime, already required by Scottland.
