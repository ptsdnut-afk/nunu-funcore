#!/usr/bin/env python3
"""Apply Filter+Game v41 onto an existing nunu-filter tree.

Usage (Chromebook Linux, after you copy nunu-panel-v41.zip somewhere):
  python3 apply_panel_v41.py /path/to/nunu-panel-v41.zip

Does NOT start the server. Restart yourself:
  python3 server.py
  open http://127.0.0.1:8765/?v=41

v41: remove Filter Paper-log modal; Game quote/buy accept ticker or CA.
"""
from __future__ import annotations
import shutil, sys, zipfile
from pathlib import Path

FILES = [
    "static/index.html",
    "server.py",
    "game.py",
    "tweets.py",
]

def main() -> int:
    if len(sys.argv) < 2:
        print("usage: apply_panel_v41.py /path/to/nunu-panel-v41.zip", file=sys.stderr)
        return 2
    zpath = Path(sys.argv[1]).expanduser().resolve()
    root = Path(__file__).resolve().parent
    if not zpath.exists():
        print(f"missing zip: {zpath}", file=sys.stderr)
        return 1
    with zipfile.ZipFile(zpath) as zf:
        names = zf.namelist()
        for rel in FILES:
            candidates = [rel, f"nunu-filter/{rel}"]
            hit = next((c for c in candidates if c in names), None)
            if not hit:
                print(f"zip missing {rel}", file=sys.stderr)
                return 1
            dest = root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(hit) as src, dest.open("wb") as out:
                shutil.copyfileobj(src, out)
            print(f"wrote {dest}")
    print("done — restart: python3 server.py then open ?v=41")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
