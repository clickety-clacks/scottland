# Attention

How a window says it needs the user, and how Scottland shows it. Showing it is in
[widgets.md](widgets.md) (WG15) and [../core/INVARIANTS.md](../core/INVARIANTS.md) (L28). This doc
records the design Mike asked for (2026-10-01): attention from anywhere, many sources at once,
plugged in by configuration, never by code that names a particular app. Rows say what's built.

## What exists now

- Built-in sources: an app asking for focus (xdg-activation: a terminal's bell, a finished task),
  the urgency hint, a desktop notification from the process that owns the window.
- Configured sources: `attention.d/<name>.ini` files (the user's in ~/.config/scottland/attention.d/),
  each naming a command that lists the windows needing attention, how its entries name windows,
  how often to read it (or a file to watch), and a command to run when the user answers one.
  `scottland-attention-sources` runs them; a reload picks up new or changed ones. See the
  Scottland skill for the format. Any process can also call IPC `scottland/attention {window,
  attention, source}` directly.
- Shown as a breathing halo in the palette's attention color, on the window or its widget;
  cleared when the user goes to it.
- Verified headless: a Ghostty bell (its default `bell-features` include `attention`) reaches a
  widgetized Ghostty window's halo with no Agentd involved. Agents running over mosh only ring it
  if they ring the terminal's bell (Claude Code: `preferredNotifChannel` set to `terminal_bell`).

## Invariants (direction)

| ID | Invariant | Status |
|---|---|---|
| AT1 | Any process can turn a window's attention on or off, under its own source name, through a documented interface (CLI and D-Bus), without polling: sources push. | partly (IPC `scottland/attention` with a source name; no CLI or D-Bus yet) |
| AT2 | A window can have attention from several sources at once; each source turns only its own off; the halo shows while any source has it on. Going to the window answers all of them (and each configured source's `answered` command runs). | implemented (headless) |
| AT3 | Sources plug in by configuration: Scottland's built-ins plus configured ones (attention.d), including ones fed over the network, e.g. a watcher subscribed to an agent hub's event stream that maps agents to their terminal windows. No Scottland code names a particular app. | partly (list-command sources, polled or file-watched; push sources not yet) |
| AT4 | Per-app configuration, by the user or the user's agent: which sources count for an app (default when none is set: the built-ins, so a bell works out of the box), in a config file documented in the Scottland skill. | not built |
| AT5 | Sources address windows by what they know: a window id, a process (and its children), an app-id, a title, or richer identity (a terminal pane, an SSH/mosh host and session, an agent id), resolved to windows by Scottland. | not built |
| AT6 | Showing attention is separate from detecting it: the halo (window or widget), and later anything else (a list, a sound), read one plugin-owned attention source set in the desktop model. Subscribers receive complete versioned attention slices, including the current state immediately; configured sources read their marked state from that snapshot, never from a mirrored `marked` map or a reload drift check. See [desktop-model.md](desktop-model.md), DM1-DM3, DM5. | implemented (headless; halo display only) |

## Notes

- An agent-hub watcher fits AT3 as one source among others: a hub that pushes agent state as
  Server-Sent Events can be subscribed to, each agent resolved to its terminal window, and
  attention turned on and off, with no polling. That needs push sources (a long-running command
  writing on/off lines), the next step for AT3.
- Mike's setup: his attention app's list is a configured source in his own
  ~/.config/scottland/attention.d/ (personal configuration, not Scottland code).
