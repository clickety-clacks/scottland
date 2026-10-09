"""Hyprland monitor rules (`hl.monitor({...})`) in Scottland's terms: Wayfire output settings.

Shared by the shortcut importer (rules in the Hyprland config, config.d/50-omarchy-shortcuts) and
the Hyprland shim (rules sent with `hyprctl eval`), so a rule means the same thing either way.

Hyprland keys and what they become (Wayfire [output:NAME] options, as core's scottland-output
takes them):
  output      the connector name ("eDP-1"); "" is the rule for every output without its own;
              "desc:…" matches an output's make, model and serial
  disabled    true: the output is off (its other keys are ignored, as in Hyprland)
  mode        "preferred" -> auto, "highres", "highrr", "WxH" and "WxH@Hz" as they are
  position    "auto" (and Hyprland's directional autos, placed as plain auto) or "XxY" -> "X,Y"
  scale       a number or "auto"; core resolves "auto" from the output's physical size and mode
              before Wayfire reads the config, and a later numeric scale still wins
  transform   0-7 -> normal, 90, 180, 270, flipped, 90_flipped, 180_flipped, 270_flipped
  mirror      the output shows another one: mode "mirror NAME"
Anything else (bitdepth, vrr, colour management, reserved areas, ...) is not applied and is named
in the notes.
"""
import re

TRANSFORMS = ["normal", "90", "180", "270", "flipped", "90_flipped", "180_flipped", "270_flipped"]
NAME = re.compile(r"[A-Za-z0-9._-]+")
KNOWN = {"output", "disabled", "mode", "position", "scale", "transform", "mirror"}


def number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def translate(fields):
    """(output, settings, notes) for one rule's fields (Lua values as Python values).

    settings: {"enabled": bool, "mode": …, "position": …, "scale": float | "auto", "transform": …}
    with only the keys the rule settles; notes: what of the rule Scottland does not apply, in plain words."""
    output = str(fields.get("output", ""))
    settings, notes = {}, []
    for key in sorted(set(fields) - KNOWN):
        notes.append(f"{key} is not applied")
    disabled = fields.get("disabled")
    if disabled is True or str(disabled).lower() in ("true", "1"):
        settings["enabled"] = False
        return output, settings, notes
    settings["enabled"] = True

    mode = str(fields.get("mode", "preferred")).strip()
    match = re.fullmatch(r"(\d+)x(\d+)(?:@(\d+(?:\.\d+)?)(?:Hz)?)?", mode)
    if mode in ("preferred", ""):
        settings["mode"] = "auto"
    elif mode in ("highres", "highrr"):
        settings["mode"] = mode
    elif match:
        settings["mode"] = f"{match.group(1)}x{match.group(2)}" + (
            f"@{match.group(3)}" if match.group(3) else "")
    else:
        settings["mode"] = "auto"
        notes.append(f"mode {mode} is not applied (the preferred mode is used)")
    if fields.get("mirror"):
        mirror = str(fields["mirror"])
        if NAME.fullmatch(mirror):
            settings["mode"] = f"mirror {mirror}"
        else:
            notes.append(f"mirror {mirror} is not applied")

    position = str(fields.get("position", "auto")).strip()
    match = re.fullmatch(r"(-?\d+)x(-?\d+)", position)
    if match:
        settings["position"] = f"{int(match.group(1))},{int(match.group(2))}"
    else:
        settings["position"] = "auto"
        if position != "auto":
            notes.append(f"position {position} is placed automatically"
                         if position.startswith("auto") else f"position {position} is not applied")

    if "scale" in fields:
        scale = number(fields["scale"])
        if scale is not None and 0.1 <= scale <= 10:
            settings["scale"] = scale
        elif str(fields["scale"]).strip() == "auto":
            settings["scale"] = "auto"
        else:
            notes.append(f"scale {fields['scale']} is not applied")

    if "transform" in fields:
        transform = number(fields["transform"])
        if transform is not None and transform == int(transform) and 0 <= transform <= 7:
            settings["transform"] = TRANSFORMS[int(transform)]
        else:
            notes.append(f"transform {fields['transform']} is not applied")
    return output, settings, notes


def describe(head):
    """An output's make, model and serial, the text Hyprland's desc: rules match."""
    return " ".join(part for part in (head.get("make"), head.get("model"), head.get("serial")) if part)


def matches(output, head):
    """Whether a rule's output field names this output (catch-all aside)."""
    if output.startswith("desc:"):
        wanted = output[5:].strip()
        return bool(wanted) and describe(head).startswith(wanted)
    return output == head.get("name")


def ini_lines(settings):
    """The rule as Wayfire [output:NAME] lines."""
    if settings.get("enabled") is False:
        return ["mode = off"]
    lines = []
    for key in ("mode", "position", "scale", "transform"):
        if key in settings:
            value = settings[key]
            if key == "mode":
                match = re.fullmatch(r"(\d+x\d+)@(\d+(?:\.\d+)?)", value)
                if match:  # Wayfire reads the refresh as a whole number of mHz
                    value = f"{match.group(1)}@{round(float(match.group(2)) * 1000)}"
            lines.append(f"{key} = {value}")
    return lines


def set_arguments(settings):
    """The rule as core scottland-output set arguments (after the output name)."""
    if settings.get("enabled") is False:
        return ["--off"]
    args = ["--on"]
    for key in ("mode", "position", "scale", "transform"):
        if key in settings:
            args += [f"--{key}", str(settings[key])]
    return args
