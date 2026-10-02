# Scottland's tenets

What Scottland is for, in Mike's words (kept in sync with his notes, Drift doc 0024). Use them to
decide the edges a request doesn't specify: when a new feature leaves a choice open, pick what the
tenets point to and say which tenet decided it. When two pull different ways, ask. Specific
behaviors and their status live in the invariants files and docs/; the bullets here are examples.

## Things people do on a desktop

- peek at windows but then put them away
- put n things side-by-side
- focus on a window

## Tenets

1. The window manager's job is your attention, not your windows.
- Windows are the medium. Every tenet below is about who controls your attention and how the screen shows it.
- A window that needs you breathes a halo in the attention color, on the window or its widget; going to it answers it.
- Attention comes from many sources at once (bells, urgency, notifications, configured sources such as an agent watcher), plugged in by configuration, never by code that names an app.
2. Recognition, not recall: you may remember where things are, you never have to.
- A window manager that makes you remember where things are is failing at its job. Workspaces fail at this: out of sight becomes out of mind.
- No workspaces: everything open stays on one screen, in some form.
- Windows you aren't using shrink toward the edges instead of hiding; at the rails they become widgets that still say what they are (what's in the window first, the app second).
- Super+M collapses all widgets to their icons: smaller, never gone. Collapsed is a mode, so a window widgetized while collapsed arrives collapsed.
3. Position means priority, not address.
- The center of the screen is where you focus; the sides are the periphery. Distance from the center says how much something matters right now.
- The center is for looking, the periphery is for keeping. To look at something you bring it to the center; you don't enlarge it where it sits.
- A window's zone and scale come from its center, so moving it is how you change its priority.
- Clicking a widget brings its window to the center.
- Peek, then put away: Esc during a drag returns the window to where the move began, in its original form.
- Because position is temporary, it was never something to memorize (tenet 2).
4. Concede as little as possible, and nothing in the center.
- Every change to a window costs you something. Cheapest first: moving (costs the content nothing), scaling (keeps the framing, costs legibility), resizing (reframes the content: it reflows and rewraps).
- Scottland uses the cheapest change that does the job. Scaling is the concession for getting a window out of the way, so it belongs to the periphery only.
- The center is full scale, always: windows centered in the center zone are at 100%, and scaling starts outside it, easing in.
- Scaling is a transform: the app keeps its size, so its content is framed exactly as before.
- Resizing is yours: Scottland resizes only when you do (Super+right-drag, the halo's corners, Ctrl+arrows in window mode), and around the window's center, so it keeps its zone and scale.
- Side-by-side: when several windows brought to the center don't fit, Scottland has nothing left to concede there. It doesn't scale them (that's the periphery's concession) or resize them (that's yours); the ones that don't fit stay in the periphery, or you make room. (Direction, not built.)
- Tiling doesn't fit: it resizes windows to fit positions, the most expensive concession, made for you.
5. Others may ask for your attention; only you grant it.
- An agent or external process never moves or obscures what you're focusing on unasked. The windows in the center are sacred.
- A bell or an app asking for focus turns on its attention instead of taking focus (Scottland handles activation requests itself).
- When an agent opens a window for you, it arrives in the periphery, scaled, never in the center; you bring it in. (Not built yet.)
6. Full screen is for focus: nothing interrupts it.
- Like focus mode in a text editor: going full screen says "only this, now".
- The widgets slide off the screen's edges while a fullscreen window is in front, and come back when it isn't.
- Notifications are held for as long as it lasts (the desktop's do-not-disturb, through its integration); one the user had on already stays on.
- Attention still accumulates (halos, badges), and shows when you leave full screen: nothing is lost, only deferred.
