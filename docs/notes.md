# Window notes

Window notes are short, plain-text labels attached to a window. They help someone remember what
the window is for without asking for attention. Any local process can add, update, remove or list
them through Scottland's existing Rust control tool. Notes belong to the window and disappear
when it closes; they survive a marked plugin reload, but not a compositor restart.

The plugin owns each note's id, writer and text in the desktop model. The `notes` subscription is
a versioned, complete replacement of notes on every window that has them. Its changes do not
change the desktop, widget or attention slices. Notes do not create attention, focus or raise a
window.

## Invariants

| ID | Invariant |
|---|---|
| NT-1 | A valid add appends a note to the named mapped window and returns its new id. |
| NT-2 | Writer and text must be valid UTF-8 containing 1–64 and 1–1000 Unicode scalar values, respectively. Writers reject controls; text permits only line feed and tab controls. Invalid fields, windows and ids are rejected without changing state. |
| NT-3 | A valid update replaces only a note's text; its id, writer, window and position stay the same. |
| NT-4 | A valid remove deletes the named note. |
| NT-5 | Notes survive focus changes, widget form, drags, scale changes and a marked plugin reload, including the next-id counter. Removing a window removes its notes. |
| NT-6 | `scottland/subscribe` and `scottland/desktop-model` expose a complete `notes` slice with a `windows` array. A window without notes is omitted. Each accepted mutation and closing a noted window publishes a complete replacement with a newer version. Notes changes do not change desktop, widget or attention slice content. |
| NT-7 | Adding, updating, removing or showing a note creates no knock, goo, attention source, halo, focus change, raise or notification. |
| NT-8 | The existing Rust `scottland-ctl` supplies `notes add`, `update`, `remove` and `list`. `--window focused` names the focused window. List filters match window id and writer exactly and `--json` prints the filtered windows array. Add and update accept `-` to read text from stdin through EOF, removing one trailing newline. Errors are one line on stderr and exit nonzero. |
| NT-9 | A shown, unfocused window has one accent dot per note, 8 pt with a 1 pt background outline, in a 4 pt-spaced vertical column 4 pt outside its drawn top-right corner. At six or more notes, one accent count pill replaces the dots. Their size is independent of window scale, and each dot follows its window's stacking order. |
| NT-10 | Hovering a dot or coming within 4 pt shows that note's panel; doing so at a count pill shows a note stack. Leaving hides the panel or stack. |
| NT-11 | A focused, shown window shows its notes in order as a stack, 4 pt outside its top-right corner with 6 pt gaps. When space runs short, the first panel is shortened as needed and a final `+N more` panel reports hidden notes. Losing focus restores dots. |
| NT-12 | A panel uses the live hint theme palette: writer at 11 pt and 70% opacity, body at 13 pt, background fill, 1 pt accent border, 6 pt radius and 8 pt padding. It is at most 320 pt wide or the workarea width, whichever is smaller; body text wraps, breaks long words, maps tabs to spaces and shows at most 12 lines. A cut body ends with an ellipsis. A panel is wholly drawn or omitted, always includes its writer and at least one body line, and never takes pointer input. Full text remains available in `notes list`. |
| NT-13 | Dots, markers, panels and stacks stay wholly inside the anchor output's workarea. An element moves by the shortest distance needed to fit, and a stack moves as one piece. |
| NT-14 | Panels and stacks draw above windows and widgets. A hovered panel draws above a focused stack. Notes do not move windows or rearrange panels from different windows. |
| NT-15 | Window mode hides all note visuals on every output. Fullscreen hides all note visuals on its output. Notes return unchanged when those states end. |
| NT-16 | A window in widget form shows one noninteractive note dot on the lower corner away from the screen edge. It shows no note text; the widget card, state file and D-Bus properties stay unchanged. The dot hides when widgets are hidden. |
| NT-17 | The Rust launch verb accepts one or more `--note` values with a writer, attaches them to the pending launch, and execs the command only after every note is accepted. A rejected note prevents exec and discards the pending launch. |
| NT-18 | When a pending declaration binds to a window, its notes move to that window in the same model change as provenance. Notes that never bind expire after 60 seconds. Pending notes are absent from the notes slice. |
| NT-19 | The supported browser flow starts a new Chromium process with a user-data directory no running Chromium uses. When that process's page window binds, its launch note appears without another command. Reusing an already-running Chromium is outside this guarantee. |

The plugin adds no service, dependency, settings, general metadata field or separate state store.
The notes and launch verbs belong to the existing Rust `scottland-ctl`; the Python control script
does not gain notes commands. Provenance rules stay unchanged. NT-17–NT-19 depend on the existing
Rust launch path and provenance binding path; they are sequenced after those shared components.
