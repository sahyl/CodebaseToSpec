"""
Explorer Agent: walks a repo, builds the codebase graph, persists to MongoDB.

No LLM call needed here — pure static analysis.

Loop:
  1. list all .py files
  2. for each file: extract FileNode, SymbolNode list, import Edge list
  3. upsert everything to MongoDB
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

from pymongo.database import Database

from models.schemas import FileNode
from tools.ast_tools import get_imports, get_symbols
from tools.fs_tools import list_files, read_file
from tools.mongo_tools import ensure_indexes, upsert_edges, upsert_file, upsert_symbols


class ExplorerAgent:
    def __init__(self, db: Database) -> None:
        self.db = db
        ensure_indexes(db)

    def explore(self, repo_root: str) -> None:
        """
        Walk the repo and persist the full codebase graph to MongoDB.
        Prints progress to stdout.
        """
        repo_root = str(Path(repo_root).resolve())
        files = list_files(repo_root)
        known_paths: set[str] = set(files)
        print(f"[Explorer] Found {len(files)} Python files in {repo_root}")

        for rel_path in files:
            self._process_file(repo_root, rel_path, known_paths)

        print(f"[Explorer] Done. Graph persisted to MongoDB.")

    def _process_file(self, repo_root: str, rel_path: str, known_paths: set[str]) -> None:
        full = Path(repo_root) / rel_path
        stat = full.stat()
        source = read_file(repo_root, rel_path)
        num_lines = source.count("\n") + 1

        file_node = FileNode(
            path=rel_path,
            size_bytes=stat.st_size,
            num_lines=num_lines,
            entry_point=self._is_entry_point(source, rel_path),
        )
        upsert_file(self.db, file_node)

        symbols = get_symbols(repo_root, rel_path)
        upsert_symbols(self.db, symbols)

        edges = get_imports(repo_root, rel_path)
        for e in edges:
            e.is_internal = e.to_path in known_paths
        upsert_edges(self.db, edges)

        print(f"  [Explorer] {rel_path}: {len(symbols)} symbols, {len(edges)} imports")

    @staticmethod
    def _is_entry_point(source: str, rel_path: str) -> bool:
        if Path(rel_path).name in {"main.py", "cli.py", "__main__.py"}:
            return True
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return False
        # ponytail: check AST structure, not substring — avoids self-match on this file
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.If)
                and isinstance(node.test, ast.Compare)
                and isinstance(node.test.left, ast.Name)
                and node.test.left.id == "__name__"
                and any(
                    isinstance(c, ast.Constant) and c.value == "__main__"
                    for c in node.test.comparators
                )
            ):
                return True
        return False
