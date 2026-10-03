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
- Nautilus's built-in file source into Chromium's file drop target.
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

The failing source app and item are still needed to exercise the reported path.
These are headless compositor sessions on plumbus; physical libinput touchpad
input and Mike's osanwe desktop have not been driven. No installed package or
live-session build was changed.
