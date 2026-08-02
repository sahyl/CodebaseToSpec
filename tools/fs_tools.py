"""
Filesystem tools for the Explorer agent.

All paths are repo-relative strings. The repo_root is injected at construction
time so tools stay pure and testable.
"""

from __future__ import annotations

import os
import re
from pathlib import Path


SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", ".mypy_cache"}
PYTHON_EXTENSIONS = {".py"}


def list_files(repo_root: str, extensions: set[str] = PYTHON_EXTENSIONS) -> list[str]:
    """Return all matching files as repo-relative paths, sorted."""
    root = Path(repo_root)
    results: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # prune skip dirs in-place so os.walk doesn't descend
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fname in filenames:
            if Path(fname).suffix in extensions:
                abs_path = Path(dirpath) / fname
                results.append(str(abs_path.relative_to(root)))
    return sorted(results)


def read_file(repo_root: str, rel_path: str) -> str:
    """Read a file and return its text content."""
    full = Path(repo_root) / rel_path
    return full.read_text(encoding="utf-8", errors="replace")


def grep(repo_root: str, pattern: str, rel_path: str | None = None) -> list[dict]:
    """
    Search for a regex pattern.

    Returns list of {file, line_number, line} dicts.
    Searches rel_path if given, else whole repo.
    """
    root = Path(repo_root)
    compiled = re.compile(pattern)
    targets = [rel_path] if rel_path else list_files(repo_root)
    hits: list[dict] = []
    for rp in targets:
        full = root / rp
        try:
            for i, line in enumerate(full.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if compiled.search(line):
                    hits.append({"file": rp, "line_number": i, "line": line})
        except OSError:
            continue
    return hits
