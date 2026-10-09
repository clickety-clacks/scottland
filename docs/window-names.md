# Window names

The `scottland-window-name` tool computes a readable name from the selected session's desktop
snapshot. It is a core mechanism and does not ship app-specific recipes. The tool writes one JSON
object per window to stdout, in argument order; `--explain` adds the ordered recipe decisions. A
missing requested window is reported as `{"window":ID,"error":"no-such-window"}`. It writes all
results before returning a nonzero status if any requested window is missing. Without IDs, it
reports every window in the snapshot.

## Invariants

| ID | Invariant | Status |
|---|---|---|
| WN-1 | Emit one result per requested window in argument order; report missing IDs and exit nonzero after writing all results. | not yet validated |
| WN-2 | Read recipe changes on the next request without a restart or reload. | not yet validated |
| WN-3 | Earlier directories win by recipe ID; a disabled winner withdraws that ID. | not yet validated |
| WN-4 | Try matching recipes by descending priority then ascending ID; fall through on source failure or empty/unavailable fields. | not yet validated |
| WN-5 | Floor order is title, desktop `Name`, app-id, then `null` with basis `none`. | not yet validated |
| WN-6 | Strip the final recognized title segment only when it contains the desktop name or final app-id component, ignoring case. | not yet validated |
| WN-7 | Normalize output to one line, collapse whitespace, and never truncate. | not yet validated |
| WN-8 | Bound every source by its timeout and avoid repeating an identical command invocation within a request. | not yet validated |
| WN-9 | Keep the service and shipped recipes read-only; a user's own command recipe is the user's responsibility. | not yet validated |
| WN-10 | Associate MPRIS by sole-owner process tree or unique exact case-insensitive DesktopEntry/app-id match; resolve multiple players only with one `Playing` player, and expose no fields without a track title. | not yet validated |
| WN-11 | Keep app names and app-specific behavior out of Scottland service code. | not yet validated |
| WN-12 | Return the same results for the same windows, recipes, and observed content. | not yet validated |
| WN-13 | Resolve every field to exactly one window field, title capture, or source field; validate pointer mappings and reject prohibited collisions. | not yet validated |
| WN-14 | Warn on invalid winning recipe files and unknown keys; an invalid winner shadows later copies of the same ID. | not yet validated |

## Recipe lookup and templates

Recipes are individual `.toml` files. Lookup precedence is:

1. `$XDG_CONFIG_HOME/scottland/window-names.d` (or `~/.config/scottland/window-names.d`)
2. `/usr/share/scottland/window-names.d` for app and flavoring recipes
3. `window-names.d` in the active hooks directory, which is `/usr/lib/scottland` in a packaged
   session and the development snapshot when running a linked development install

The file basename is the recipe ID. A higher-precedence file shadows every later file with that
ID, including when it has `enabled = false` or is invalid. Distinct recipes are ordered by
descending `priority` (default `0`) and then ascending ID. Evaluation tries each matching recipe
until its source and template succeed.

Example:

```toml
name = "{title_content} · {app_name}"
priority = 20

[match]
app_id = "^org\\.example\\.reader$"
desktop_category = "Office"
```

Templates accept window fields, named groups in `match.title`, MPRIS fields, and fields returned
by the command provider. `{{` and `}}` insert literal braces. Rendered names have control
characters replaced by spaces and whitespace collapsed; they are never truncated.

## Sources

MPRIS recipes declare `provider = "mpris"`; the default timeout is 1000 ms. The built-in
`media-player` and `media-player-title` recipes use priorities 70 and 69, so a player with a track
and artist gets `track · artist`, while a player without an artist can still name the window by
track. An app-provided recipe can use the same fields without any app-specific behavior in core.

Command recipes set `provider = "command"` and `command = ["program", "arg", ...]`. Each argument
may substitute window fields. The command runs with the selected session's recorded environment
and receives a JSON object containing all window fields on stdin. JSON output may expose top-level string fields automatically or map selected fields with
`[source.fields]`; text output uses its first line as `text`. `source.basis` is a JSON Pointer to a
string whose value is `current`, `launch`, `title`, `app` or `none`. A source defaults to a 1000 ms
timeout. Commands are launched directly without a shell, and their stdout is capped at 1 MiB.

Core also ships `terminal-tmux` (priority 60) and `terminal-tmux-local` (priority 50) for desktop
entries in the `TerminalEmulator` category. They call `scottland-terminal-session`, which asks the
shared agent-window-resolver for the selected window's live tmux session. A remote session is named
`session (host)`; a local session is named `session`. If the session cannot be attributed, recipe
evaluation falls through to the title. App curation belongs in the XDG shared-data recipe directory.

## Verification

The exact candidate's runner packet names the repository-standard checks, the post-merge first-use
headless start/environment/run/IPC/stop smoke, and a real MPRIS player with observable metadata
and window-owner association. Status remains pending until that runner evidence is recorded.
