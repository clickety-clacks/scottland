#!/usr/bin/env python3
"""Scale auto: an output's natural scale, and the one place `scale = auto` in the assembled config
becomes a number before Wayfire reads it (Wayfire's scale is a number; it ignores "auto").

`scale = auto` may stand in any [output:NAME] section of the assembled config: an integration's
display settings, this session's (scottland-output set NAME --scale auto) or the user's
overrides.ini. scottland-build-config runs this file on the assembled config:

  scottland_output_scale.py FILE

It rewrites FILE in place. Wayfire reads repeated [output:NAME] sections as one, each option taking
its last value, so only the winning scale line of each output counts: where that is auto it becomes
the output's natural scale; an auto a later number overrides, and every number, stay as they are.

The natural scale comes from what the compositor reports for the output (scottland-output-heads):
its physical size and the pixels of its mode (the winning mode line when it names a size, else the
output's current mode, else its preferred one). Pixel density along the diagonal of 200 per inch
or more gives 2 (a HiDPI laptop panel), 140 or more gives 1.5, anything less 1 (an ordinary desktop
monitor). An output that reports no physical size (a projector, a virtual output), or one the
compositor does not report, gets 1. Each of these is an exact step of Wayfire's 1/120 scale.

It is worked out again at every config rebuild, so a mode changed in the config takes effect with
its own natural scale. Before the compositor runs (the build at session start) there are no outputs
to ask about: auto gets 1, marked with WAITING, and autostart.d/04-output-scale-auto rebuilds once
the compositor reports its outputs.
"""
import json
import math
import os
import re
import subprocess
import sys

WAITING = "scale auto: waiting for the compositor's outputs"
SECTION = re.compile(r"\s*\[(.*)\]\s*$")
OPTION = re.compile(r"\s*([^=\s]+)\s*=\s*(.*?)\s*$")


def mode_pixels(head, mode=None):
    """The pixel size auto scale is worked out for: MODE ("WIDTHxHEIGHT[@mHz]", as the config writes
    it) when it names a size, else the output's current mode, else its preferred one."""
    match = re.match(r"(\d+)x(\d+)", mode or "")
    if match:
        return int(match.group(1)), int(match.group(2))
    current = head.get("mode") or next(
        (m for m in head.get("modes") or [] if m.get("preferred")), None) or {}
    return current.get("width") or 0, current.get("height") or 0


def natural_scale(head, mode=None):
    width, height = mode_pixels(head, mode)
    physical_width, physical_height = head.get("physical_width") or 0, head.get("physical_height") or 0
    if min(width, height, physical_width, physical_height) <= 0:
        return 1
    density = math.hypot(width, height) / (math.hypot(physical_width, physical_height) / 25.4)
    return 2 if density >= 200 else 1.5 if density >= 140 else 1


def heads():
    """The compositor's outputs by name, or None when it doesn't answer (it isn't running yet).
    Without WAYLAND_DISPLAY (the build before the session's compositor starts), none is asked:
    libwayland would fall back to wayland-0, which may be another desktop's."""
    hooks = os.environ.get("SCOTTLAND_HOOKS", "/usr/lib/scottland")
    if not os.environ.get("WAYLAND_DISPLAY"):
        return None
    try:
        result = subprocess.run([os.path.join(hooks, "libexec/scottland-output-heads")],
                                capture_output=True, text=True, timeout=5, stdin=subprocess.DEVNULL)
        if result.returncode != 0:
            return None
        return {head["name"]: head for head in json.loads(result.stdout)}
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        return None


def uncommented(line):
    """LINE without its comment, as Wayfire reads it: from the first # not escaped with \\."""
    match = re.search(r"(?<!\\)#", line)
    return line[:match.start()] if match else line


def resolve(text, outputs):
    """TEXT, an assembled config, with each output's winning `scale = auto` made a number.
    OUTPUTS: the compositor's outputs by name (heads()), or None before it runs."""
    lines = text.split("\n")
    scale_line, mode = {}, {}
    section, continued = None, False
    for index, line in enumerate(lines):
        content = uncommented(line).rstrip()
        joined, continued = continued, content.endswith("\\") and not content.endswith("\\\\")
        if joined:
            continue  # the rest of the previous option's value
        header = SECTION.match(content)
        if header:
            name = header.group(1).strip()
            section = name[len("output:"):] if name.startswith("output:") and len(name) > 7 else None
            continue
        option = OPTION.match(content)
        if section is None or not option:
            continue
        if option.group(1) == "scale":
            scale_line[section] = index
        elif option.group(1) == "mode":
            mode[section] = option.group(2)
    for name, index in scale_line.items():
        if OPTION.match(uncommented(lines[index])).group(2) != "auto":
            continue
        indent = re.match(r"\s*", lines[index]).group(0)
        if outputs is None:
            lines[index] = f"{indent}scale = 1  # {WAITING}"
        else:
            head = outputs.get(name)
            scale = natural_scale(head, mode.get(name)) if head else 1
            lines[index] = f"{indent}scale = {scale}  # scale auto"
    return "\n".join(lines)


def main(path):
    with open(path) as handle:
        text = handle.read()
    resolved = resolve(text, heads())
    if resolved != text:
        with open(path, "w") as handle:
            handle.write(resolved)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: scottland_output_scale.py FILE")
    main(sys.argv[1])
