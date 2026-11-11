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

## Window-mode captions and asynchronous consumption

These requirements extend the existing service output into Window-mode hint presentation. They do
not change the service's recipe evaluation or add widget naming. The service's +2400 ms and +2600 ms
and the consumer's +2800 ms and +3000 ms boundaries are future policy targets, not measurements or
guarantees of the current source. A consumer slot is released only after CLI termination is
observed, its endpoints are closed, and consumer work for that invocation has settled. If the
consumer target is missed, the slot remains counted and the miss is logged; the target does not
guarantee slot release or progress for pending work by that time.

| ID | Invariant | Status |
|---|---|---|
| NC1 | Request names from `scottland-window-name` by numeric window ID, preserve complete returned strings, and do not evaluate recipes in the renderer. | not yet validated |
| NC2 | Refresh on Window-mode entry, eligibility, title/app-id/process identity and relevant MPRIS events; do not poll. | not yet validated |
| NC3 | Keep CLI work off Wayfire's main loop, cap active work, and coalesce to one latest pending request per window. Consumer Dk=T0+2800 ms is the direct-child kill target; D=T0+3000 ms is the settlement target. Neither guarantees release by a fixed time: retain/count/log until termination is observed, endpoints are closed, and consumer work is settled. | not yet validated |
| NC4 | Apply only when mode, live window object, eligibility and latest request generations still match in the same main-loop action. | not yet validated |
| NC5 | A usable string is preserved, explicit `null` suppresses the caption, and missing/error results clear stale service content and use current local title then app-id. | not yet validated |
| NC6 | While a refresh is pending, show the current local fallback rather than the previously resolved content name; update without restarting Window mode or delaying hint input. | not yet validated |
| NC7 | Center a secondary name plate below the dominant hint, at 15 logical px nominal and 12 px minimum at text scale 1, with both sizes following desktop text scale only. | not yet validated |
| NC8 | Fit by rendered text metrics; use one-line end ellipsis at the floor without splitting displayed graphemes, while retaining the full model value. | not yet validated |
| NC9 | Keep the caption within its owning output and preserve the hint's existing anchor/action; report concrete infeasible geometry rather than lowering the font floor or moving the window. | not yet validated |

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

Core ships only generic recipes: `media-player`, `media-player-title`, and `title-content`. App
curation belongs in the XDG shared-data recipe directory; no terminal recipe is shipped here.

## Verification

The exact candidate's runner packet names the repository-standard checks, the post-merge first-use
headless start/environment/run/IPC/stop smoke, and a real MPRIS player with observable metadata
and window-owner association. Status remains pending until that runner evidence is recorded.
