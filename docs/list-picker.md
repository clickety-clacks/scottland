# Generic list picker

Scottland core provides one list picker for any distro and any `scottland-*` tool.
It is invoked through the `scottland-list-picker` command, which is available from
the core installation and runs in Scottland's existing Quickshell layer.

## Command and input/output

```text
scottland-list-picker [--prompt TEXT]
```

The command reads standard input through EOF. Each LF-delimited line is one entry;
CRLF is accepted, and entry text is otherwise preserved, including spaces and
blank lines. A final line ending closes the last entry and does not add another
entry. An empty input contains no entries and cancels without opening the picker.

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
- The picker opens on the focused output and uses Scottland's core palette.

## Invariants

| ID | Invariant | Status |
|---|---|---|
| LP1 | `scottland-list-picker` reads line-delimited entries from stdin and returns exactly one chosen entry plus LF on stdout with status 0; cancel returns no stdout and a nonzero status. | source implemented; plumbus verification pending |
| LP2 | The picker narrows by case-insensitive substring while preserving input order; keyboard and pointer can choose an entry, and Escape or clicking outside cancels. | source implemented; plumbus verification pending |
| LP3 | The optional prompt argument affects presentation only; it never appears in the selected output. | source implemented; plumbus verification pending |
| LP4 | The picker uses Scottland's core palette, opens on the focused output, and runs in the existing Quickshell layer. | source implemented; plumbus verification pending |
| LP5 | The command is part of the core installation and can be called by any distro and any Scottland tool without a distro-specific adapter. | source implemented; plumbus verification pending |

This contract defines one single-choice list picker. It does not include icons,
previews, multi-select, history, ranking, other picker modes, a new dependency,
a daemon or native code.
