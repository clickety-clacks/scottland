# Emoji picker

Status: source and generated data are present in the candidate; build and session evidence are
pending.

scottland-emoji sends the bundled Unicode list to Scottland's generic list picker. Each line
contains the fully-qualified emoji and its Unicode name, including skin-tone sequences. Typing
narrows the names through the picker's standard filter. Core adds no default shortcut.

Insert mode is the default. Before opening the picker, the command records the focused window and
takes an in-memory snapshot of the current clipboard. After a choice it temporarily offers the
emoji, pastes it to that original target, and restores the snapshot. The clipboard watcher
recognizes Scottland's own selection source, so the temporary emoji is not recorded in history.
Cancel releases the snapshot without changing the clipboard. If the target is no longer focused,
the command exits before changing the clipboard. With --copy, the chosen emoji goes to the
clipboard without pasting.

Before opening in insert mode, the session service snapshots all clipboard formats in memory. If
any format cannot be read from its current owner, it leaves the clipboard alone and refuses to
open the picker rather than restore an incomplete copy.

The generated list contains fully-qualified entries from Unicode Emoji 18.0's emoji-test.txt.
The source data and generated names ship with the package; runtime lookup does not use the
network. The source file and Unicode License v3 notice are included in the repository.

| ID | Invariant | Status |
|---|---|---|
| EM1–EM2 | Search the shipped fully-qualified emoji list by its Unicode name in the generic picker. | not built |
| EM3–EM4 | Insert into the original target and restore the earlier clipboard without adding the emoji to history. | not built |
| EM5–EM7 | Copy mode does not paste; cancel and a closed target do not alter the clipboard. | not built |
| EM8 | Generate and package the data locally with no new package dependency or runtime network use. | not built |

Unicode source data: https://www.unicode.org/Public/18.0.0/emoji/emoji-test.txt
