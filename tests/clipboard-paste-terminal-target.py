#!/usr/bin/env python3
"""Read one pasted line from a focused terminal and save the observed bytes."""

from pathlib import Path
import sys


ready_path, result_path = map(Path, sys.argv[1:3])
ready_path.write_text("ready\n", encoding="ascii")
value = sys.stdin.buffer.readline()
result_path.write_bytes(value)
