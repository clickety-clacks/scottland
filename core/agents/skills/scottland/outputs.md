# Screens: rotation, scale and position

Scottland's screens are Wayfire outputs, configured with an `[output:NAME]` section in
`~/.config/scottland/overrides.ini`. Like other settings, a change applies to the running session
within a second or two; no reload.

## Find the output names

```bash
scottland-exec -- wlr-randr        # if wlr-randr is installed
```

or call the IPC method `window-rules/list-outputs {}` ([`control.md`](control.md#calling-scottlands-ipc)):
each output's `name` (such as `eDP-1`, `DP-2`, `HDMI-A-1`), its geometry and its current settings.

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

An easier way to arrange displays is coming; until then, use these settings.
