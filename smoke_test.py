"""
Smoke test: explore this project with mongomock (no real MongoDB needed).
Dumps files, symbols, and edges collections as JSON.

Usage:
  python3 smoke_test.py [repo_path]   # default: current directory
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import mongomock

# --- patch get_db before any agent import ---
import tools.mongo_tools as _mt
_mt.get_db = lambda: mongomock.MongoClient()["codebase_graph"]

from agents.explorer import ExplorerAgent

repo_path = sys.argv[1] if len(sys.argv) > 1 else "."
repo_path = str(Path(repo_path).resolve())

db = _mt.get_db()
ExplorerAgent(db).explore(repo_path)

def dump(name: str) -> None:
    docs = list(db[name].find({}, {"_id": 0}))
    print(f"\n{'='*60}")
    print(f"  {name}  ({len(docs)} documents)")
    print('='*60)
    print(json.dumps(docs, indent=2, default=str))

dump("files")
dump("symbols")
dump("edges")
