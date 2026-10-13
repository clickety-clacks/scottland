# Key layers

A layer adds a surface's own shortcuts above the user's current shortcuts. It claims only the
keys it lists: everything else bleeds through to the bindings and remaps that exist **now**.
Scottland never snapshots, rewrites or replaces the underlying keymap. This is a core feature,
independent of any app or desktop adapter.

Tenet 4 (concede as little as possible) decides fall-through, including live binding changes.
Tenet 5 (only the user grants attention) decides focus-only activation: registering a layer never
focuses, raises or moves a surface. Compositor grabs retain their input; a focused surface cannot
use a layer to take Esc from a drag or input from a session lock.

## Invariants

Status **implemented** means built and tested with real stipc input headless on plumbus; real
screen exercise and the coordinating session's rehearsed live reload remain outstanding.

| ID | Rule | Status |
|---|---|---|
| KL1 | A layer claims only its listed chords. Unclaimed keys use the current user bindings, including changes made while the layer is active, imported function shortcuts, release bindings, remaps, and any pre-existing blanket shortcut inhibition. Return and keypad Enter on a focused widget are intercepted by WG25 even if claimed; claims work normally on windows. | implemented |
| KL2 | Each mapped surface may hold one layer. Only actual keyboard focus activates it; several surfaces, even in the same process, may register independently. Registration never changes focus. | implemented |
| KL3 | Claimed presses and releases follow Wayfire's ordinary delivery to the focused surface, with physical modifiers and client repeat intact; compositor bindings and Scottland remaps/release commands skip them. WG25 consumes Return and keypad Enter on a focused widget before surface delivery, even when a layer claims them. | implemented |
| KL4 | Both native toplevels and layer-shell surfaces work. A Scottland view ID selects exactly one surface; PID plus layer namespace is a convenience selector and rejects ambiguity. | implemented |
| KL5 | A surface's unmap, close or Wayland client disconnect removes its layer. Remapping does not resurrect it. Set atomically replaces its keys; clear or an empty set removes it. A failed request preserves the previous set. | implemented |
| KL6 | Losing focus immediately deactivates claims for new presses. A claimed held key's release finishes the existing pair even after focus loss, clear, replacement or unmap; it cannot unexpectedly fire a release shortcut below. A new claim cannot take over an already pressed unclaimed key's release. | implemented |
| KL7 | Compositor input grabs (drag, lock) take precedence over surface layers. Window mode is a raw-key mode: a focused surface retains exactly its claimed keys even while hints are visible; unclaimed keys continue hint navigation and entered-mode arrow movement/center resize (WK17–WK24). A claimed press before entry cancels the pending hold for the entire Alt chord, including a claimed Alt key. Unclaimed input and unrelated bindings are unaffected. | implemented (plumbus headless, including hints) |
| KL8 | IPC is session-local `scottland/key-layer`, documented below and in the shipped skill and IPC header. The first scope is a focused surface; the separate module leaves additional scopes and stacked fall-through for future work. | implemented |

## IPC

From inside Scottland, connect to **that session's** `WAYFIRE_SOCKET` using ordinary Wayfire IPC
(length-prefixed JSON; existing bridge clients can call this alongside `scottland/present`).

Method: **`scottland/key-layer`**. Actions:

```json
{"action":"list"}
{"action":"set","window":42,"keys":["0:Escape","4:comma","4:j"]}
{"action":"set","pid":1234,"namespace":"my-popup","keys":["0:Up","0:Down"]}
{"action":"clear","window":42}
{"action":"clear","pid":1234,"namespace":"my-popup"}
```

Choose **one** selector: `window` (positive integer Scottland view ID), or `pid` (positive integer
Wayland client PID) **and** `namespace` (string). PID alone does not identify a surface. A namespace
selector must match exactly one currently mapped layer-shell surface. When it matches several,
use `list` and choose a `window` ID. No selector is required for `list`.

`list` returns `{ "result":"ok", "surfaces":[...] }`. Each mapped native surface reports
`window`, `pid`, `title`, `app_id`, `registered`, `active` and `keys`; layer-shell surfaces also
report `namespace`. Use PID and title/app-id to find a toplevel among a client's windows.
`active` reports keyboard focus with a registered layer; input grabs still take precedence.
`set`/`clear` return `{ "result":"ok", "window":42 }`. Invalid input, an unknown/unmapped ID,
or an ambiguous selector returns `{ "error":"..." }` without changing any layer.

The application registers **after mapping** its surface, re-registers when its shortcut context
changes, and registers again after remapping. Registration belongs to the Wayland surface, so
closing a short-lived IPC connection does **not** clear it. The Wayland client's disconnect does.
This is runtime state: plugin unload/reload clears registrations, so clients must register again
after a plugin reload. Layers never persist across session restarts.

### Chords

`keys` is an array of strings **`MODMASK:keysym`**. Keysyms use case-sensitive XKB names (`j`,
`Escape`, `Return`, `Page_Up`, `comma`, `equal`, `plus`, `Super_L`). Masks use these bits:

| Modifier | Bit |
|---|---|
| Shift | 1 |
| Ctrl | 4 |
| Alt | 8 |
| Super | 64 |

Add bits for combinations (`5:j` is Ctrl+Shift+J). No other bits are accepted. Extra Mod3/Mod5 (such as AltGr) therefore falls through.
Lock modifiers (Caps Lock, Num Lock) do not alter matching, and no modifier state is changed for
delivery.
Names match the unshifted key in the **current** keyboard layout with the exact mask, or the
produced keysym. Shift consumed to produce a symbol can be omitted: `4:plus` also claims Ctrl
plus Shift+= on a layout that produces `plus` there. `4:j` does not claim Ctrl+Shift+J; `4:equal`
does not claim Ctrl+Shift+=. Use both `4:equal` and `4:plus` to claim both spellings.

### Input integration and future scopes

`core/plugin/src/key-layers.*` owns selection, matching, lifecycle and IPC. Before a claimed
event, it temporarily disables Wayfire's binding repository only if it is currently enabled;
its post-input hook balances only that suspension. A private sentinel binding with an impossible
modifier bit/keycode queries enabled state without touching hardware/configurable shortcuts.
This avoids Wayfire 0.11's overlapping-inhibitor counter crossing zero and accidentally enabling
bindings. Existing blanket inhibition remains intact. Ordinary core input processing still
manages held keys, modifier-only binding state, input methods, and press/release delivery. Unclaimed events never change the repository.

Raw-key consumers inside Scottland must connect **after** `key_layers.init()` and check
`key_layers.handles(ev)` before acting on claimed keys. Core shortcuts (E13, release and any-modifier
matching) and the remap handler do so, as does the collapse-key tracker. The integrated window mode’s `on_window_key` updates physical
held-key tracking but skips claimed events; a claimed press cancels the pending Alt timer (`alt_bypassed = true;
alt_hold.disconnect()`). This keeps a layer's Alt chord from navigating windows while allowing
unclaimed window mode navigation to work. While hints are active, exact claims reach the focused surface and
unclaimed keys continue navigating. Tenet 4 decides this limited concession: registering one chord
does not suppress the rest of window navigation. Drag and lock grabs still take precedence.
For a focused widget, WG25 intercepts Return and keypad Enter before surface delivery; a widget's
key-layer claims for those keys are ignored while it is a widget. Other claimed widget keys and all
window claims keep ordinary layer behavior. The Alt key tracking runs for claimed presses/releases
too, so neither side can leave hints stuck.

Future cross-window scopes or stacked layers should resolve a winner here before processing an
event. They should not copy or restore user bindings, change surface identities, or build app
names into core.

## Tests

On plumbus, in this checkout's own test session (never the real screen):

```bash
SCOTTLAND_DEPLOY_DIR=Projects/scottland-key-layers tests/deploy.sh plumbus --tests-only
```

`tests/key-layers-test.sh` starts and stops its own private headless session; it refuses to replace
a running one. `tests/key-layers-test.py` injects real stipc key and pointer events into real GTK
surfaces, including multiple toplevel/layer-shell surfaces sharing one client, and a sandboxed
imported Lua shortcut. Current runs keep their artifacts under the checkout's `build/` and
leave `XDG_RUNTIME_DIR` unchanged. Use a unique `SCOTTLAND_HEADLESS_DIR` under `build/`.

Integrated validation on plumbus, 2026-10-01, code commit `756b8d7` (later merge `56b0cc1`
adds only main's documentation):

| Check | Result |
|---|---|
| `make plugin` / checkout-local test helpers | passed |
| `tests/key-layers-test.sh` (real stipc input, GTK toplevels and layer-shell, sandboxed Lua host, combined hints) | 59 passed, 0 failed |
| `tests/windowing-test.sh` | 73 passed, 0 failed |
| `tests/widgets-test.sh` | 146 passed, 0 failed, including 43 collapse-input/preview checks; no runner errors |
| Native toplevel and layer-shell popup screenshot | inspected |

The combined tests exercise claimed left/right Alt holds, a claimed quick Alt chord, claimed
letters while hints remain active, clearing a held claim during hints, and subsequent unclaimed
hint navigation. The widget runner rounds geometry before shell arithmetic, so cancellation
sequences execute when IPC coordinates are JSON floats. Complete merge validation and isolation
are recorded in [windowing-keys.md](windowing-keys.md). Real-screen verification and a rehearsal
from the live session's build before its reload belong to the coordinating session after merge.
