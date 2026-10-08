# Clipboard history

Status: source is present in the candidate; build and session evidence are pending.

The feature uses one session service and Scottland's generic list picker. The watcher observes
the regular clipboard selection through Wayland data-control; it does not read primary selection.
The service keeps entries in memory, newest first, and drops the oldest after 50 entries. Matching
text or image content moves to the top instead of creating a duplicate. Copies carrying
 x-kde-passwordManagerHint with the value secret are not added.

The service starts with the session through autostart.d/10-clipboard-history. Its control socket
is in a per-display directory under XDG_RUNTIME_DIR, with user-only directory and socket
permissions. Clipboard contents and snapshots used by emoji insertion stay in service memory.
Emoji snapshots retain every offered MIME format. If the service cannot read one of those formats,
it refuses the snapshot before opening the picker so restoring an incomplete clipboard cannot
silently discard data.
Opening an entry creates a mode-0600 temporary file under that same runtime directory so the
default application can read it; the service removes those files when it exits. No clipboard
history is written under the user's home directory.

The helper uses the compositor's wlr data-control protocol to observe and set the selection and
the wlr virtual-keyboard protocol to paste with Shift+Insert. It adds no package dependency.
The session build must confirm both protocols are available before this feature can be considered
usable.

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
- scottland-clipboard clear empties session history without changing the clipboard.

Before a paste-mode picker opens, Scottland requires a focused app window. If that target is
already gone when the selection is made, the command leaves the clipboard and history unchanged.
If the target disappears after the entry is set, the command reports the failed paste and leaves
the chosen entry on the clipboard.

The history list shows text on one line and shortens long lines. Images include their pixel size
when it can be read from the image header. Identical display lines get a small ordinal so the
picker still returns the exact entry the user chose. With no entries, the approved generic picker
contract cancels before opening; this conflicts with CH11's empty-list display and awaits an owner
ruling before the picker contract is changed.

| ID | Invariant | Status |
|---|---|---|
| CH1–CH4 | Add copies newest-first, deduplicate, cap history at 50, and honor the secret marker. | not built |
| CH5 | Keep history only in the session service's memory and restrict its socket to the user. | not built |
| CH6–CH9 | Search with the generic picker; select exact entries; copy, paste or open them. | not built |
| CH10, CH12–CH13 | Copy text/files and preserve the specified failure behavior. | not built |
| CH11 | Clear history and show the empty history list. | blocked: conflicts with LP empty-input contract; owner ruling pending |
| CH14 | Use compositor protocols without adding a package dependency or borrowing a clipboard tool. | not built |
