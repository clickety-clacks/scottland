# Knocks (concept, in discussion with Mike, 2026-10-04)

Status: mental model being formalized; nothing designed or built. Supersedes the narrower
"agentd as an attention source" framing; agentd, Yoohoo's bell menu, notifications, bells and
Tightbeam decision requests are all expected to become cases of this.

## The model so far (Mike's words, organized)

- **Objects** exist whether or not they are on the desktop: an agent session on some host, the
  machine itself (CPU overheating), a remote SaaS, an app.
- **Windows and widgets are representations** of the objects that are forefront in the user's mind
  and visual field. An object with no representation still exists and has to *call into* the desktop.
- **A knock** is how an object asks for the user. It is ephemeral, and it is not a window or a
  widget. It carries a message (what it's about) and may carry a UI to answer with (e.g. a menu of
  choices from a remote agent needing a decision).
- **A knock has its own representation**, and it should feel organic to what it is, not "just another
  window": the attention goo is already part of it. (Example of the spirit: a lost network shown as a
  red vignette over the screen rather than a dialog box.) Rectangles aren't forbidden, just not the default.
- From a knock the user can:
  1. **Answer it directly**: call up the knock's own UI and act on it, without inviting the object in.
  2. **Invite the object in**: it becomes a member of the desktop. If it already has a window, Scottland
     reconciles which window that is, brings it to the center, and messages the object to focus its UI
     on that knock (e.g. scroll to the message). If it has none, Scottland opens a window with the
     appropriate UI to talk to it (e.g. Ghostty with the harness in it).
- **Mapping a knock to the window representing its sender must be dependable** (declared identity,
  exact match, never a guess).

## Open

- How knocks from objects with no representation appear, and how urgency/kind changes the look.
- The address scheme (e.g. "session on host" for terminal agents; pane granularity).
- The protocol: knock (address, message, answer UI, how to open the object), and the message back to
  the object ("focus on this knock").
- Lifetime: answered, dismissed, expired; many knocks from one object; gathering/overview.
- Core vs flavorings split (P9), and removing Yoohoo.
