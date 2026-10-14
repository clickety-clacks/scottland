# Keybinding cheat sheet

Status: candidate source; seven focused helper behavior scenarios pass; compositor build/session evidence is pending.

`scottland-keybindings` reads the assembled Wayfire configuration for the active session from
the runtime directory when opened. Reloaded configuration and user or adapter imports therefore
appear without a fixed shortcut catalog. Every configured key or key-and-button chord is listed
on one line with a plain label. Core binds no default chord; flavorings or the user may choose one.

The command opens the generic list picker and narrows by chord or label. With `--print` it writes
the same ordered lines to standard output without opening the picker. Pointer-button chords and
holds are listed but close the picker without running an action. A configuration read failure
stops before the picker opens, so no partial list is shown.

Configured commands launch directly. Runnable keyboard compositor bindings are invoked through
the compositor's binding repository, bypassing shortcut layers and without sending keys to the
picker surface. For actions that need a window, the command captures the focused window before
opening the picker; if that window is closed or loses focus, the action does not run and the
command exits nonzero with one line explaining why.

| ID | Invariant | Status |
|---|---|---|
| KB1–KB2 | Show every shortcut from the current assembled configuration when opened. | focused helper scenarios pass; live session update unverified |
| KB3–KB4 | Give base shortcuts plain labels and display alternate chords on one line. | focused output scenario passes |
| KB5–KB7 | Narrow through the generic picker, run runnable actions for their original target, and leave holds/pointer chords as no-ops. | helper dispatch scenarios pass; picker/compositor session behavior unverified |
| KB8 | Fail with one reason and no partial list when configuration cannot be read. | focused config-failure scenario passes |
