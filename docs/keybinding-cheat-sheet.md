# Keybinding cheat sheet

Status: source is present in the candidate; build and session evidence are pending.

scottland-keybindings reads the assembled Wayfire configuration for the active session from
the runtime directory. It reads it when opened, so reloads and earlier configuration changes
are reflected without a fixed shortcut catalog. Every configured key or key-and-button chord is
listed on one line. A chord has a plain label when Scottland knows the action; otherwise the
configured command or plugin action supplies the description.

The base labels are kept in the command's label map. Adapter-imported and user shortcuts remain
visible because they are in the assembled session configuration. Core adds no default chord for
the sheet. Repeated options use the last value in the assembled config, matching Wayfire's active
setting.

The command opens the generic list picker and narrows by chord or label. With --print it writes
the same ordered lines to standard output and does not open the picker. A pointer-button chord or
a hold is shown, then closes the picker without running an action. Config read failures stop
before the picker opens.

When an action is selected, configured commands launch directly. Keyboard compositor bindings
are invoked through the compositor's binding repository, bypassing shortcut layers and without
sending keys to the picker surface. Actions that need a window are checked against the window that
was focused when the sheet opened; if it has closed or lost focus, the command reports the
condition without acting on another window. Pointer chords and holds are displayed but do nothing.
If no registered compositor action can be invoked directly, choosing it also does nothing.

| ID | Invariant | Status |
|---|---|---|
| KB1–KB2 | Show every shortcut in the current assembled configuration at invocation time. | not built |
| KB3–KB4 | Give base shortcuts plain labels and display multi-chord bindings on one line. | not built |
| KB5–KB7 | Narrow in the generic picker, run runnable actions against their target, and leave holds/pointer chords as no-ops. | not built |
| KB8 | Fail with one reason and no partial list when configuration cannot be read. | not built |
