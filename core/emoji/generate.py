#!/usr/bin/env python3
"""Generate Scottland's fixed emoji list from Unicode's emoji-test.txt."""

from pathlib import Path
import re
import sys


HERE = Path(__file__).resolve().parent
SOURCE = HERE / "data" / "emoji-test.txt"
OUTPUT = HERE / "emoji.tsv"


def entries(source: Path):
    version = None
    rows = []
    for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        if line.startswith("# Version:"):
            version = line.partition(":")[2].strip()
            continue
        if line.startswith("#") or not line.strip():
            continue
        try:
            codepoints, description = line.split("#", 1)
            codepoint_list, status = codepoints.split(";", 1)
            symbols = "".join(chr(int(value, 16)) for value in codepoint_list.split())
            name = description.strip().split(" ", 1)[1]
            name = re.sub(r"^E\d+(?:\.\d+)?\s+", "", name)
        except (ValueError, IndexError) as error:
            raise ValueError(f"{source}:{line_number}: malformed Unicode emoji row") from error
        if status.strip() == "fully-qualified":
            if not symbols or "\t" in name or "\n" in name:
                raise ValueError(f"{source}:{line_number}: invalid emoji row")
            rows.append((symbols, name))
    if not version or not rows:
        raise ValueError(f"{source}: missing version or fully-qualified emoji rows")
    return version, rows


def main():
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else SOURCE
    output = Path(sys.argv[2]) if len(sys.argv) > 2 else OUTPUT
    version, rows = entries(source)
    output.write_text(
        f"# Unicode Emoji {version}; generated from emoji-test.txt.\n"
        "# Unicode License v3 applies; see data/LICENSE.txt.\n"
        + "".join(f"{emoji}\t{name}\n" for emoji, name in rows),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
