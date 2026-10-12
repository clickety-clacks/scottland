# Launch commands

`scottland-launch` provides nine shared launch actions. Core resolves a running Scottland session
and uses its recorded environment when one is available; launches that do not need a desktop model
may use the caller environment when no Scottland session is running. Ambiguous or invalid session
selection is an error. Focus actions require a selected Scottland session and use its desktop model
and presentation API.

Application selection follows the user's XDG defaults. The launcher does not write MIME defaults,
change terminal preferences, or store another application preference. It passes executables and
arguments as separate argv values; the explicit shell-command form of `--focus` remains the only
shell-evaluated launch mode.

| ID | Behavior | Status |
|---|---|---|
| LC1 | `browser` uses the selected `default-web-browser` desktop entry, its `Exec` field codes or desktop activation, and presents a matching Scottland window after a URL handoff. `--private` is accepted only for a known supported browser invocation. | implemented (behavior tests) |
| LC2 | `editor` selects nonempty `VISUAL`, then `EDITOR`, from the effective session environment; otherwise it uses the XDG `text/plain` application. Configured commands are parsed without shell evaluation, and terminal desktop entries use the selected terminal unless `--inline` is supplied. | implemented (behavior tests) |
| LC3 | `floating-terminal-with-presentation COMMAND...` uses `xdg-terminal-exec` with a Scottland app ID/title and a minimal text heading plus command-status prompt. Command arguments remain separate; status 130 skips the prompt. | implemented (behavior tests) |
| LC4 | `--cwd files` opens the resolved directory through `xdg-open`, using the same terminal-CWD result as `--cwd terminal`. An unavailable or ambiguous local shell directory falls back to HOME. | implemented (behavior tests) |
| LC5 | `--focus PATTERN [LAUNCH-COMMAND]` presents the first case-insensitive app-ID/title match from the Scottland desktop model. Without a match it launches the pattern directly; an explicitly supplied command remains an explicit shell-command form. | implemented (behavior tests) |
| LC6 | `--focus --tui [--app-id=ID] COMMAND...` presents a matching Scottland window or starts the command through `xdg-terminal-exec`; explicit IDs are preserved and generated IDs use `org.scottland.*`. | implemented (behavior tests) |
| LC7 | `--cwd terminal [--pid=PID] [COMMAND...]` uses the explicit process ID or focused window PID to find a local shell directory; HOME is the documented fallback. The configured terminal receives `--dir` and the command argv. | implemented (behavior tests) |
| LC8 | `--tui [--app-id=ID] COMMAND...` starts the argv through `xdg-terminal-exec` with an `org.scottland.*` generated app ID unless the caller supplied one. | implemented (behavior tests) |
| LC9 | `--restart APP [ARGS...]` stops only current-user processes whose exact process name matches `APP`; if none exist, it still directly launches `APP ARGS...` in the selected environment. Stop and executable-start errors are returned to the caller. | implemented (behavior tests) |

Terminal invocation uses the documented `xdg-terminal-exec` app-ID, title, directory and command
options. D-Bus-activatable desktop entries use GIO's desktop-file launcher. No one terminal
emulator or file manager is required. Core does not contain desktop-adapter helpers or assumptions.

This document covers only these nine launch actions. Other entries in the binary checklist remain
separate and undecided.
