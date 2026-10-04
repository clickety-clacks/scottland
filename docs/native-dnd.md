# Native Wayland drag and drop

L35 covers app content transfer through `wl_data_device`; window movement remains
the separate Super/halo/touch/three-finger mechanism. Normal content input must
reach the app with its original serial and implicit grab intact. Wayfire and
wlroots own drag validation, icon placement, target offers and transfer.

Mike's 2026-10-03 report is **still open**: the source shows a preview which cannot
leave its own window. That can indicate a rejected or absent `start_drag`, but
it has not been reproduced in the following isolated plumbus fixtures. No
drag-validation bypass or speculative compositor workaround was added.

`tests/dnd-test.sh` runs two native GTK clients, native Wayland Chromium and
Nautilus. Input is real pointer press/motion/release through stipc, with no
modifier, halo move or gesture involved in the content transfer. Separate real
Super drags arrange peripheral and rail fixtures. Tests check:

- GTK text between full-size windows (goo and fallback halo), with Window mode
  active, and into a peripheral scaled window, with the actual received
  client-local coordinates and no movement/resizing of the source window.
- Text into a native GTK rail widget that accepts drops, at 100% scale.
- GTK text into Chromium and a Chromium link into GTK.
- Nautilus's built-in file source into Chromium's file drop target, including
  a peripheral scaled target, and into an interactive GTK rail widget's file
  drop target (with received filenames and client-local coordinates).
- `WAYLAND_DEBUG=1` source traces: every `start_drag` serial came from a real
  left-button press, and the compositor subsequently sent a DnD `enter`.

All pass on main, with the Omarchy adapter enabled, and with ship-merged5 plus
goo-wallpaper-wake combined. `tests/dnd-test.sh ARTIFACTS --stock` omits the
Scottland plugin while retaining the other stock Wayfire plugins and config;
GTK/Chromium/Nautilus transfer passes there too. Artifacts include source
protocol logs, accepted serials, GTK drop records and rendered screenshots.

An interactive widget is its own client surface and receives its own data offer.
The default card does not expose a drop target; this test does not claim that
dropping on a default card forwards data to the hidden represented app.

### Files through the imported Omarchy binding (2026-10-03)

Mike identified the source as Files, opened with Super+Shift+F through
`omarchy-launch-nautilus` (`setsid uwsm-app -- nautilus --new-window`). This is
an adapter launch path (O10); the content transfer itself is core behavior
(L35/C8). `tests/dnd-test.sh ARTIFACTS --nautilus-launcher` now exercises that
binding with real stipc key input, the installed launcher and uwsm-app, and an
actual systemd scope. A test-directory PATH wrapper only records the process
environment/cgroup and enables `WAYLAND_DEBUG=1` before execing the real binary
with the launcher's original arguments. Ctrl+L navigation uses real keyboard
input to select the isolated fixture folder. No global service environment is
imported or changed.

The launcher path passes on the branch, on the unmodified compositor code at
30514ff (the 43149b4 test checkout differs only in documentation), on stock
Wayfire, and on ship-merged5 plus goo-wallpaper-wake. The native client receives
`GDK_BACKEND=wayland,x11,*`, `XDG_CURRENT_DESKTOP=Scottland:Wayfire:wlroots`, and
the isolated session's display. Versions match osanwe: Nautilus 50.3.1, GTK
4.22.4, Wayfire 0.11.0 and uwsm 0.26.7.

For example, the branch's first launcher run records pointer press serial 318,
`start_drag(..., 318)`, compositor DnD `enter`, departure from the source,
`dnd_drop_performed` and `dnd_finished`. Chromium receives and accepts a
`text/uri-list` offer and receives the file drop. A later **discarded**
`cancelled` event follows completion, rather than rejecting that drag. The
pre-fix and stock controls likewise complete the transfer (serials 348 and
254). Compared with direct launch, the launcher starts at Home with only
`--new-window`, then navigates to the test folder, and runs in a scope;
both paths connect natively to Wayland and successfully transfer the file.
`nautilus-launch.json`, `nautilus.log`, `nautilus-drag-events.log`,
`chromium.log`, `protocol.json` and screenshots retain the evidence.

Mike's trapped preview remains **unreproduced and unfixed**. These are isolated
headless compositor sessions on plumbus with a private D-Bus session and fresh
Nautilus, rather than his existing desktop/application state. Physical
libinput touchpad input and osanwe's desktop have not been driven. No installed
package or live-session build was changed, and every test session was stopped.
