# Scottland concepts

What the user sees and does, so you can explain it and choose the right setting. The defaults
below are shipped values; the user may have changed them in Scottland Settings.

## Zones and scaling

Each screen has five vertical zones, left to right: **rail**, **periphery**, **center**,
**periphery**, **rail**.

- **Center zone**: a third of the width by default. Windows there are at 100%.
- **Periphery** (the side zones): a window's scale follows a curve from 100% next to the center
  down to 20% next to the rails. A blend band just outside the center (the "center edge
  softness") eases the change so there is no jump.
- **Rails**: thin strips at the far left and right (2% of the width). A window moved onto a rail
  becomes a widget.
- A window's **center point** decides its zone and scale, and it scales around that point. Moving
  a window is how you change how much it matters right now.
- Scaling is a transform: the app keeps its real size and stays fully usable (click, type, scroll)
  at any scale. Scottland never resizes a window unless the user does.
- Windows have no title bars or borders. Each has a **halo**: a translucent rounded band around
  it, in the theme's accent color when focused. By default the halos are **goo**: one liquid
  surface for the whole screen that pools between nearby windows. Dragging the halo moves the
  window, its corners resize it (when the window can resize), and a dot at the middle of its bottom
  edge closes it. There is no minimize.

## Moving and resizing

| Action | Pointer | Touchpad | Touchscreen |
|---|---|---|---|
| Move | Super + left-drag, or drag the halo | Three-finger drag | Hold a finger still on the window (~350 ms) until it lifts, then drag; or drag the halo |
| Resize (around the center) | Super + right-drag, Super+Alt + left-drag, or a halo corner | Three-finger click-drag | Drag a halo corner |
| Keep the current scale while moving | Hold Shift during the drag | | |
| Cancel a move | Esc: the window glides back where the move began, in its original form | | |

Windows dragged across the zones rescale live; dropping causes no jump. A flick coasts and slows
down. A window dragged across a side edge onto a rail turns into its widget while still being
dragged; dragged off the rail it turns back into its window.

## Rail widgets

A widget stands in for a window on a rail: the window stays alive but hidden, and a widget program
shows what it is. The default widget is a **card**: the app's icon, the window's title over the
app's name, and a badge with the app's unread count when it publishes one.

- Widgets are always at 100%, above ordinary windows, free-floating where they were dropped. When a
  widget is dropped among others, the others move aside; the dropped one stays exactly where it
  was put.
- Clicking the card (or Return on a focused widget) opens its window in the center zone, at its
  remembered spot or the least-covered spot near the card.
- Closing either the widget or its window closes both.
- **Super+M** sets the widget mode. A tap cycles **expanded** → **collapsed** (each widget shrinks
  to its icon) → **hidden** (widgets slide off the screen edges) → expanded. Holding it is
  momentary: from expanded it hides them, otherwise it shows them expanded, until released.
  Hovering a collapsed widget shows it expanded until the pointer leaves.
- Which program a widget runs, and how to write one: [`widgets.md`](widgets.md).

## Window mode (Alt)

Holding **Alt alone** for a moment (300 ms) enters Window mode: every window and widget gets a
large lettered **hint** (a colored circle). Covered windows shift a little so each
shows a strip of itself ("window avoidance", or peeking); this is visual only and goes back when
Alt is released. Hints appear only once they have settled.

While Alt is held:

| Keys | Effect |
|---|---|
| A window's hint letters | First press selects and focuses it; further presses cycle it through the zones (to the center first, then the rail or periphery, then back). |
| The same hint twice quickly | Sends the window straight to its rail as a widget. |
| Hold the focused window's hint | **Solo**: it takes the center; the other center windows go to the periphery. Commits at once. |
| Hold an unfocused window's hint | **Pair**: that window and the focused one are placed side by side in the center at their own sizes (shrunk together only if they don't fit). Entering a pair doesn't change the windows' remembered periphery spots. |
| Arrows | Push the target window with inertia; Ctrl+arrows resize it around its center. |
| Tab / Shift+Tab | Select the next or previous window or widget. |
| F4 | Close the selected window. |
| Esc | Remove the hints and stop motion; keys stay captured until Alt is released. |

Releasing Alt ends Window mode. A quick Alt+letter still reaches the app; pressing Ctrl, Shift or
Super before Alt, or starting a drag, means no hints for that press. On a touchpad, a three-finger
hold (fingers still, no click or drag) on the focused window solos it, and on another window pairs
it with the focused one.

Each window remembers a spot per zone (center, each periphery side, each rail) and returns there
when cycled back.

## Switching and navigation

- **Alt+Tab** / **Alt+Shift+Tab**: switch among center-zone windows, most recently used first.
- **Super+arrows**: focus the neighboring window or widget in that direction.
- Presenting (`scottland-ctl present`, [`control.md`](control.md)) brings a chosen window to the
  center at 100%, raised and focused. A plain focus request moves nothing.

## Attention

A window (or its widget) whose app needs the user breathes a halo in the **attention color** until
the user goes to it. Built-in triggers: the app asks for focus (a terminal bell, a finished task),
sets its urgency hint, or sends a desktop notification from its own process. Other programs can be
attention sources by configuration ([`widgets.md`](widgets.md#attention-sources)). Apps can't take
focus by asking: only a request that comes from input in the app the user is using (a link opened
from it) takes focus; any other request becomes attention. While widgets are collapsed or hidden,
one that gets attention comes in expanded for a few seconds, and a hidden one then keeps a thin
breathing strip at the screen edge. The attention color follows the theme or is chosen in Settings
(Theme, Warm, Cool).

## Full screen

Full screen is for focus. While a fullscreen window is in front on a screen, that screen's widgets
slide off its edges and Scottland runs the `focus.d` hooks with `on` (integrations use them to hold
notifications; the user's own go in `~/.config/scottland/focus.d/`), and with `off` when full
screen ends. Attention still accumulates and shows afterwards. Alt still shows hints in full
screen.

## Keys at a glance

| Keys | Action |
|---|---|
| Super+Enter | Terminal |
| Super+, | Scottland Settings |
| Super+Q, Alt+F4 | Close the focused window |
| Super+M | Widget mode (tap / hold) |
| Super+arrows | Focus the neighbor in that direction |
| Alt (hold) | Window mode |
| Alt+Tab, Alt+Shift+Tab | Center-window switcher |
| Super+Ctrl+Alt+R | Reload Scottland in place |
| Super+Escape | Switch to another desktop, if an integration provides a switcher |
| Super+Shift+Escape, Ctrl+Alt+Backspace | Quit Scottland |
| Print, Shift+Print | Screenshot, of the screen or a selected area |

The user's own changes are in `~/.config/scottland/overrides.ini`, and an integration may import
more shortcuts; the effective list is in `$XDG_RUNTIME_DIR/scottland/wayfire.ini`.
