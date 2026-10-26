#!/usr/bin/env python3
"""Local helper: panel already on disk at this tree. Prefer ~/APPLY_NUNU_V45.py on Chromebook."""
from pathlib import Path
print("Panel files live in", Path(__file__).resolve().parent)
print("On Chromebook run: python3 ~/APPLY_NUNU_V45.py")
print("Then restart server and open http://127.0.0.1:8765/?v=45")
