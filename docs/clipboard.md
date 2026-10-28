# Clipboard history

Status: source is present in the candidate; build and session evidence are pending.

The feature uses one session service and Scottland's generic list picker. The watcher observes
the regular clipboard selection through Wayland data-control; it does not read primary selection.
The service keeps entries in memory, newest first, and drops the oldest after 50 entries. Matching
text or image content moves to the top instead of creating a duplicate. Copies carrying
x-kde-passwordManagerHint with the value secret are not added.

The service starts with the session through autostart.d/10-clipboard-history. Its control socket
is in a per-display directory under XDG_RUNTIME_DIR, with user-only directory and socket
permissions.
Opening an entry creates a mode-0600 temporary file under that same runtime directory so the
default application can read it; the service removes those files when it exits. No clipboard
history is written under the user's home directory.

The helper uses the compositor's wlr data-control protocol to observe and set the regular clipboard
selection and the wlr virtual-keyboard protocol to paste with Ctrl+Shift+V. It adds no package
dependency.
The session build must confirm both protocols are available before this feature can be considered
usable.

The same service exposes private, session-memory snapshot/release/type-text operations for the
emoji feature. They preserve every readable clipboard format, temporarily set selected UTF-8 text,
use the same clipboard-paste path, restore the saved clipboard, and do not add the temporary text
to history. If any offered format cannot be read completely, snapshot refuses before the picker
opens.

## Commands

- scottland-clipboard history opens the newest-first picker. Choosing an entry puts it on the
  clipboard.
- scottland-clipboard history --paste puts the chosen entry on the clipboard and pastes it into
  the window that was focused when the command opened.
- scottland-clipboard history --open opens the chosen text, address or image with its default
  application.
- scottland-clipboard copy-text reads UTF-8 text from standard input, puts it on the clipboard
  and pastes it. Add --copy-only to skip pasting.
- scottland-clipboard copy-file PATH MIME copies the file using its MIME type and pastes it.
  Add --copy-only to skip pasting.
- scottland-clipboard clear empties session history immediately, then opens the shared picker
  with no entries and the prompt `Clipboard history`. Dismissing the empty view completes clear
  successfully; no entry is selected or sent to the clipboard.

Before a paste-mode picker opens, Scottland requires a focused app window. If that target is
already gone when the selection is made, the command leaves the clipboard and history unchanged.
If the target disappears after the entry is set, the command reports the failed paste and leaves
the chosen entry on the clipboard.

The history list shows text on one line and shortens long lines. Images include their pixel size
when it can be read from the image header. Identical display lines get an ordinal so the picker
still returns the exact entry the user chose. Opening `history` with no entries still follows the
generic picker default and cancels before opening. `clear` opts into the generic picker's
nonselectable empty view; it does not add a selectable sentinel or a second picker. If clearing
fails, the view is not opened. If clearing succeeds but the view cannot be presented, the command
reports that history is already cleared and exits unsuccessfully without restoring it.

| ID | Invariant | Status |
|---|---|---|
| CH1–CH4 | Add copies newest-first, deduplicate, cap history at 50, and honor the secret marker. | source candidate; build and session evidence pending |
| CH5 | Keep history only in the session service's memory and restrict its socket to the user. | source candidate; build and session evidence pending |
| CH6–CH9 | Search with the generic picker; select exact entries; copy, paste or open them. | source candidate; build and session evidence pending |
| CH10, CH12–CH13 | Copy text/files and preserve the specified failure behavior. | source candidate; focused helper checks pass; build and session evidence pending |
| CH11 | Clear history immediately and show the empty history list in the shared picker; preserve the current clipboard selection and provide no selectable empty row. | source implements the approved opt-in picker seam; private UI behavior verification pending |
| CH14 | Use compositor protocols without adding a package dependency or borrowing a clipboard tool. | source candidate; build and session evidence pending |
