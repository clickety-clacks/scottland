#!/usr/bin/env python3
import os
from pathlib import Path
import sys
root = Path(__file__).resolve().parents[2]
os.execv(sys.executable, [sys.executable, str(root/'dnd-app.py'), 'dnd-widget',
                       str(Path(os.environ['SCOTTLAND_TEST_STATE'])/'dnd-widget.jsonl')])
