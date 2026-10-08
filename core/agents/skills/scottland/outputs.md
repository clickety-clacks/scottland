# Screens: rotation, scale and position

Scottland's screens are Wayfire outputs. Use `scottland-output` through the selected session
for temporary changes; use an `[output:NAME]` section in `~/.config/scottland/overrides.ini`
for persistent settings. Config changes apply live; no plugin reload is needed.

## Find the output names

```bash
scottland-exec -- scottland-output list
```

or call the IPC method `window-rules/list-outputs {}` ([`control.md`](control.md#calling-scottlands-ipc)):
each output's `name` (such as `eDP-1`, `DP-2`, `HDMI-A-1`) and its logical geometry. Read a
setting's live value with `scottland-ctl option output:eDP-1/transform` (or `/scale`, ...).
The list command also reports enabled state, available modes, physical size and current scale.

## Temporary changes

After the user chooses the output and change:

```bash
scottland-exec -- scottland-output set DP-2 --scale 1.5 --position 1920,0
scottland-exec -- scottland-output reset DP-2
```

Changes survive config rebuilds and reconnects for this session; reset drops that session's
settings so the config applies again. The user's `overrides.ini` takes precedence. A set
command exits nonzero if the compositor does not apply the requested result; do not report
success merely because a settings file was written. `--mode WIDTHxHEIGHT@HZ` uses Hz;
the persistent Wayfire config below uses mHz. Use `--scale auto` for the output's natural scale.

## Settings

```ini
[output:eDP-1]
# Display scale: windows and text get bigger.
scale = 1.5
# Rotation: normal, 90, 180, 270, flipped, flipped-90, flipped-180, flipped-270.
transform = 90
# Top-left corner in the layout, in logical pixels; or auto.
position = 0,0
# Resolution and refresh rate (in mHz); or auto.
mode = 2560x1600@60000
```

Comments go on their own lines: text after a value is part of the value.

Only add the keys you change. With several screens, positions are in logical pixels (after scale
and rotation): a 2560-wide screen at scale 2 is 1280 logical pixels wide, so the next screen to its
right starts at `x = 1280`.

Confirm with the user before changing the screen they're using: a wrong mode can leave them
without a usable display until the line is removed (from another machine over ssh if need be).

To undo, remove the lines (or the section) from `overrides.ini`.
