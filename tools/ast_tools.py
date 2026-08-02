"""
AST-based tools for the Explorer agent.

Uses Python's `ast` module — no regex parsing of source.
Produces SymbolNode and Edge records ready for MongoDB insertion.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Optional

from models.schemas import Edge, EdgeKind, SymbolKind, SymbolNode


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef, source_lines: list[str]) -> str:
    """Reconstruct the def line from source (cleaner than ast.unparse for display)."""
    return source_lines[node.lineno - 1].rstrip()


def _docstring(node: ast.AST) -> Optional[str]:
    return ast.get_docstring(node, clean=True)


def _qualified(class_name: Optional[str], func_name: str) -> str:
    return f"{class_name}.{func_name}" if class_name else func_name


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_symbols(repo_root: str, rel_path: str) -> list[SymbolNode]:
    """
    Parse a Python file and return all top-level and class-level symbols.
    Uses AST — no regex.
    """
    full_path = Path(repo_root) / rel_path
    source = full_path.read_text(encoding="utf-8", errors="replace")
    source_lines = source.splitlines()

    try:
        tree = ast.parse(source, filename=rel_path)
    except SyntaxError:
        return []

    symbols: list[SymbolNode] = []

    def visit(node: ast.AST, class_name: Optional[str] = None) -> None:
        if isinstance(node, ast.ClassDef):
            symbols.append(SymbolNode(
                file_path=rel_path,
                name=node.name,
                qualified_name=node.name,
                kind=SymbolKind.CLASS,
                signature=source_lines[node.lineno - 1].rstrip(),
                line_start=node.lineno,
                line_end=node.end_lineno or node.lineno,
                docstring=_docstring(node),
            ))
            for child in ast.iter_child_nodes(node):
                visit(child, class_name=node.name)

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kind = (
                SymbolKind.ASYNC_FUNCTION if isinstance(node, ast.AsyncFunctionDef)
                else (SymbolKind.METHOD if class_name else SymbolKind.FUNCTION)
            )
            symbols.append(SymbolNode(
                file_path=rel_path,
                name=node.name,
                qualified_name=_qualified(class_name, node.name),
                kind=kind,
                signature=_signature(node, source_lines),
                line_start=node.lineno,
                line_end=node.end_lineno or node.lineno,
                docstring=_docstring(node),
            ))
            # don't descend into nested functions for top-level class context
            if not class_name:
                for child in ast.iter_child_nodes(node):
                    visit(child)

    for node in ast.iter_child_nodes(tree):
        visit(node)

    return symbols


def get_imports(repo_root: str, rel_path: str) -> list[Edge]:
    """
    Extract import edges from a Python file using AST.

    Returns Edge(kind=IMPORT, from_path=rel_path, to_path=<resolved or raw module>).
    to_path is a best-effort repo-relative path; falls back to the module string.
    """
    full_path = Path(repo_root) / rel_path
    source = full_path.read_text(encoding="utf-8", errors="replace")
    root_path = Path(repo_root)

    try:
        tree = ast.parse(source, filename=rel_path)
    except SyntaxError:
        return []

    edges: list[Edge] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                edges.append(_make_import_edge(rel_path, alias.name, root_path))

        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            level = node.level or 0
            if level:
                # relative import — resolve against current package
                module = _resolve_relative(rel_path, module, level)
            edges.append(_make_import_edge(rel_path, module, root_path))

    return edges


def _make_import_edge(from_path: str, module: str, root_path: Path) -> Edge:
    """Convert a module string to a repo-relative file path if possible."""
    candidate = Path(module.replace(".", "/") + ".py")
    if (root_path / candidate).exists():
        to_path = str(candidate)
    else:
        pkg_init = Path(module.replace(".", "/")) / "__init__.py"
        to_path = str(pkg_init) if (root_path / pkg_init).exists() else module
    return Edge(kind=EdgeKind.IMPORT, from_path=from_path, to_path=to_path)


def _resolve_relative(rel_path: str, module: str, level: int) -> str:
    """Resolve a relative import to an absolute module string."""
    parts = Path(rel_path).parts[:-level]  # walk up `level` dirs
    if module:
        parts = parts + tuple(module.split("."))
    return ".".join(parts)


def signatures_only(symbols: list[SymbolNode]) -> str:
    """
    Flatten symbols to a compact signature-only string for LLM context.
    Skips raw source — ponytail: keeps context window lean.
    """
    lines: list[str] = []
    for s in symbols:
        line = f"[{s.kind.value}] {s.qualified_name}  {s.signature}  (L{s.line_start}-{s.line_end})"
        if s.docstring:
            first_line = s.docstring.splitlines()[0]
            line += f"  # {first_line}"
        lines.append(line)
    return "\n".join(lines)
