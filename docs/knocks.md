# Knocks (concept, in discussion with Mike, 2026-10-04)

Status: mental model being formalized; nothing designed or built. Supersedes the narrower
"agentd as an attention source" framing; agentd, Yoohoo's bell menu, notifications, bells and
Tightbeam decision requests are all expected to become cases of this.

## The bigger picture: attention regimes (Mike, 2026-10-04)

Scottland and Gooarchy are about attention. The desktop is a small window the user uses to pull in the
things that have their attention, and it is organized as **attention regimes**:

1. **Center**: forefront in the user's mind.
2. **Periphery**: less so.
3. **Widgets (rails)**: less still.
4. **The ether**: everything not represented on the desktop. Objects there still exist; at some point
   they want the user's attention and can be promoted to a higher regime.

The ether is a mental place, not a location on screen (Mike): a website the user closes isn't gone; it is
still there, can keep running, and can be opened again. Because a sender can be on the desktop in any
form, **a way to see and act on its knocks must exist in every form: a full-size window, a scaled
window, and a widget** (and for a sender with no form at all).

Objects move between regimes: the user promotes and demotes them; an object asks to be promoted with a
knock. This is the frame for everything below, and for how Scottland models and visualizes the bigger
picture of what is trying to get the user's attention.

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

## How a knock is reached on a sender (direction, Mike, 2026-10-04)

The breathing border (the goo/halo in the attention color) is the direct representation of a knock,
not a pointer to something else. The existing interaction to hang access on: hovering near a border
makes it swell (A7; the swell is a fixed on-screen size however small the window is, so it works for
full-size windows, scaled windows and widgets alike). A swollen border that carries a knock is where
its message and answer UI come from.

## Open

- How knocks from objects with no representation appear, and how urgency/kind changes the look.
- The address scheme (e.g. "session on host" for terminal agents; pane granularity).
- The protocol: knock (address, message, answer UI, how to open the object), and the message back to
  the object ("focus on this knock").
- Lifetime: answered, dismissed, expired; many knocks from one object; gathering/overview.
- Core vs flavorings split (P9), and removing Yoohoo.
