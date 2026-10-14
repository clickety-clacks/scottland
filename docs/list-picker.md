# Generic list picker

Scottland core provides one list picker for any distro and any `scottland-*` tool.
It is invoked through the `scottland-list-picker` command, which is available from
the core installation and runs in Scottland's existing Quickshell layer.

## Command and input/output

```text
scottland-list-picker [--show-empty] [--prompt TEXT]
```

The command reads standard input through EOF. Each LF-delimited line is one entry;
CRLF is accepted, and entry text is otherwise preserved, including spaces and
blank lines. A final line ending closes the last entry and does not add another
entry. Empty input contains no entries and cancels without opening the picker,
unless `--show-empty` is supplied.

`--show-empty` is an opt-in presentation for zero input rows. It opens the same
picker with no selectable entries and the text `No entries`. Escape or clicking
outside dismisses it with empty stdout and a nonzero status; Enter, arrows and
clicking the empty-state text cannot produce a selection. The flag does not
change behavior for nonempty input.

When the user chooses an entry, the command writes that entry alone to standard
output, followed by one LF, and exits with status 0. It writes no labels,
diagnostics or other data to standard output. On cancellation, standard output is
empty and the exit status is nonzero. Errors also exit nonzero and report any
diagnostic on standard error.

`--prompt` supplies optional instruction text. It affects only the UI and never
appears in the selected output.

## Interaction and placement

- Typing narrows the displayed entries by case-insensitive substring match. It
  preserves the input order and does not rank or remember entries.
- Up and Down move the highlighted entry. Enter chooses it. Escape cancels.
- Clicking an entry chooses it. Clicking outside the picker cancels.
- If filtering leaves no matches, there is no selectable entry; the user can
  continue typing or cancel.
- For zero input rows with `--show-empty`, the empty-state label is not an entry
  and cannot be selected.
- The picker opens on the focused output and uses Scottland's core palette.

## Invariants

| ID | Invariant | Status |
|---|---|---|
| LP1 | `scottland-list-picker` reads line-delimited entries from stdin and returns exactly one chosen entry plus LF on stdout with status 0; cancel returns no stdout and a nonzero status. | source implemented; behavior verification pending |
| LP2 | The picker narrows by case-insensitive substring while preserving input order; keyboard and pointer can choose an entry, and Escape or clicking outside cancels. | source implemented; behavior verification pending |
| LP3 | The optional prompt argument affects presentation only; it never appears in the selected output. | source implemented; behavior verification pending |
| LP4 | The picker uses Scottland's core palette, opens on the focused output, and runs in the existing Quickshell layer. | source implemented; behavior verification pending |
| LP5 | The command is part of the core installation and can be called by any distro and any Scottland tool without a distro-specific adapter. | source implemented; behavior verification pending |
| LP6 | `--show-empty` opts zero input rows into the existing picker with nonselectable `No entries`; without the flag, empty input still cancels before launch. Nonempty selection is unchanged. | source implemented; focused and behavior verification pending |

This contract defines one single-choice list picker with an opt-in empty-state
presentation. It does not include icons, previews, multi-select, history,
ranking, other picker modes, a new dependency, a daemon or native code.
