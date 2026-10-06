# Troubleshooting

Start with read-only checks; they don't disturb the user. `IPC METHOD` below means calling that
method as in [`control.md`](control.md#calling-scottlands-ipc). Before anything else:

```bash
scottland-exec --list                              # is a session running, and which display?
tail -n 50 ~/.local/state/scottland/wayfire.log    # recent compositor and plugin errors
df -h "$XDG_RUNTIME_DIR"                           # a full runtime directory breaks many things
```

If the session predates the installed build (see [`operating.md`](operating.md)), it runs the
older code: say so before diagnosing further.

## Window mode hints don't appear

Hints appear after **Alt alone** is held for `alt_hold_delay` (300 ms), once each has settled.

1. Ask how the user pressed it. Hints don't show if Ctrl, Shift or Super was held first, another
   key was pressed before the delay, a drag started, or Super+Alt was used for resizing. A quick
   Alt+letter goes to the app on purpose.
2. Check the delay: `scottland-ctl option scottland/alt_hold_delay`. A very long value feels like
   "never".
3. Ask the user to hold Alt, and meanwhile call `IPC scottland/hints {}` (for example with a short
   `sleep` before it). `"active": true` means Window mode is on: then look at each entry's
   `visible` and `rendered`. `active` false while Alt is held means the hold never counted: go on.
4. The focused app may claim keys: `IPC scottland/key-layer {"action": "list"}`. A surface that is
   `active` with keys that include Alt (`0:Alt_L`, `0:Alt_R`), or the key pressed first, cancels
   Window mode for that press.
5. A keyboard remap or another program may be turning Alt into something else: check
   `remap_from_*` entries and the keyboard options (`[input] xkb_options`) in
   `$XDG_RUNTIME_DIR/scottland/wayfire.ini`.
6. Errors about hints or avoidance in `wayfire.log`.

## A widget is stuck

Symptoms: a window went onto the rail but no widget came, a card shows stale text, a widget won't
open or close, or a window is missing.

1. `scottland-ctl windows` and `IPC scottland/layout-state {}`: is the app's window there,
   `widgetized`, `hidden`? Is there a widget view for it (`widget: true`)?
2. `IPC scottland/widget-mode {}`: widgets may simply be `hidden` or `collapsed` (Super+M). Full
   screen also slides them away.
3. `tail -n 50 ~/.local/state/scottland/widgets.log`: which widget was chosen and why, and whether
   its program started (`couldn't run widget ...`). A widget that shows no window within 8 seconds
   is abandoned and its app comes back.
4. The widget service: `busctl --user status org.scottland.Widgets` (is it running?) and
   `busctl --user introspect org.scottland.Widgets /org/scottland/widget/<id>` (its live
   properties). A card that stopped updating usually means its state file couldn't be written:
   check the runtime directory isn't full.
5. Each widget runs in its own systemd scope: `systemctl --user list-units 'scottland-widget-*'`.

What to offer the user, with their go-ahead: drag the widget off the rail (brings the window back),
or open it (`IPC scottland/widget-action {"id": "<id>", "action": "open"}`). Don't reload the
session to fix a widget.

## Suspected leak or growing memory

A long session's compositor memory should stay roughly flat. Stock Wayfire plugins Scottland
loads still leak a little per animation, so slow growth over days can be expected; steady growth
by hundreds of megabytes is not.

1. Measure, don't guess: note the compositor's memory now and again after a while of ordinary use.

   ```bash
   ps -o pid,etime,rss,vsz,cmd -C wayfire
   grep -E 'VmRSS|VmSwap' /proc/$(pgrep -o -x wayfire)/status
   ```

2. Count widget processes and scopes: `systemctl --user list-units 'scottland-widget-*'` and
   `pgrep -c quickshell`. More than the widgets on the rails means leftovers.
3. Leftover sessions: `scottland-exec --list`. Test sessions left running on this machine use
   memory and runtime space; they are not the user's session.
4. The runtime directory: `du -sh "$XDG_RUNTIME_DIR"/scottland/*`; `plugins/` should hold one copy.
5. Paging on the compositor thread shows as the pointer freezing: `VmSwap` well above zero for
   wayfire is a sign.

Report what you measured, over how long, and what the user was doing. Don't kill or signal the
compositor to "capture" anything: that ends the user's session and closes their windows.
