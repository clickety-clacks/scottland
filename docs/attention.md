# Attention

How a window says it needs the user, and how Scottland shows it. Today's behavior is in
[widgets.md](widgets.md) (WG15) and [../core/INVARIANTS.md](../core/INVARIANTS.md) (L28); the
Omarchy adapter's Yoohoo bridge is O16. This doc records where attention is going: Mike's asks
(2026-10-01) for Yoohoo-like capabilities built into Scottland, with a better architecture.
**Not built yet** unless a row says otherwise.

## What exists now

- Sources: an app asking for focus (xdg-activation: a terminal's bell, a finished task), the
  urgency hint, a desktop notification from the process that owns the window, and anything that
  calls IPC `scottland/attention {window, attention}` (the Yoohoo bridge does, for agents that
  Agentd Hub reports).
- Shown as a breathing halo in the palette's attention color, on the window or its widget;
  cleared when the user goes to it.
- Verified headless: a Ghostty bell (its default `bell-features` include `attention`) reaches a
  widgetized Ghostty window's halo with no Agentd involved. Agents running over mosh only ring it
  if they ring the terminal's bell (Claude Code: `preferredNotifChannel` set to `terminal_bell`).

## Invariants (direction)

| ID | Invariant | Status |
|---|---|---|
| AT1 | Any process can turn a window's attention on or off, with a reason (its source), through a documented interface (CLI and D-Bus), without polling: sources push. | partly (IPC `scottland/attention`, no source or reason yet) |
| AT2 | A window can have attention from several sources at once; each source turns only its own off; the halo shows while any source has it on. Going to the window answers all of them. | not built |
| AT3 | Sources plug in: Scottland's built-ins (bell/activation, urgency hint, notifications) plus installable sources (a directory of watchers started with the session, like the other hook directories), including ones fed over the network, e.g. an Agentd Hub watcher subscribed to the hub's event stream that maps agents to their local terminal windows. | not built (Yoohoo bridge is the interim) |
| AT4 | Per-app configuration, by the user or the user's agent: which sources count for an app (default when none is set: the built-ins, so a bell works out of the box), in a config file documented in the Scottland skill. | not built |
| AT5 | Sources address windows by what they know: a window id, a process (and its children), an app-id, a title, or richer identity (a terminal pane, an SSH/mosh host and session, an agent id), resolved to windows by Scottland. | not built |
| AT6 | Showing attention is separate from detecting it: the halo (window or widget), and later anything else (a list, a sound), read one attention state. | partly (halo only) |

## Notes

- An Agentd watcher fits AT3 as one source among others: the hub already pushes agent state as
  Server-Sent Events; the watcher subscribes, resolves each agent to its terminal window (Yoohoo's
  resolver does this today), and turns attention on and off. No polling.
- Yoohoo would become one more source (or its pieces would move into Scottland's sources); the
  bridge (O16) stays until then.
