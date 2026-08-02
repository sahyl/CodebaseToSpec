"""
MongoDB tools: upsert and query helpers for the codebase graph.

Collections:
  - files    → FileNode documents
  - symbols  → SymbolNode documents
  - edges    → Edge documents

Connection string is read from MONGO_URI env var (default: localhost).
"""

from __future__ import annotations

import os
from typing import Any

from pymongo import MongoClient, UpdateOne
from pymongo.collection import Collection
from pymongo.database import Database

from models.schemas import Edge, FileNode, SymbolNode

def _load_env() -> None:
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip() not in os.environ:
                        os.environ[k.strip()] = v.strip().strip("'\"")


_load_env()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.getenv("MONGO_DB", "codebase_graph")


def get_db() -> Database:
    uri = os.getenv("MONGO_URI", MONGO_URI)
    db_name = os.getenv("MONGO_DB", DB_NAME)
    client: MongoClient = MongoClient(uri)
    return client[db_name]


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------

def upsert_file(db: Database, node: FileNode) -> None:
    db.files.update_one(
        {"path": node.path},
        {"$set": node.model_dump()},
        upsert=True,
    )


def upsert_symbols(db: Database, symbols: list[SymbolNode]) -> None:
    if not symbols:
        return
    ops = [
        UpdateOne(
            {"file_path": s.file_path, "qualified_name": s.qualified_name},
            {"$set": s.model_dump()},
            upsert=True,
        )
        for s in symbols
    ]
    db.symbols.bulk_write(ops, ordered=False)


def upsert_edges(db: Database, edges: list[Edge]) -> None:
    if not edges:
        return
    ops = [
        UpdateOne(
            {"kind": e.kind, "from_path": e.from_path, "to_path": e.to_path,
             "from_symbol": e.from_symbol, "to_symbol": e.to_symbol},
            {"$set": e.model_dump()},
            upsert=True,
        )
        for e in edges
    ]
    db.edges.bulk_write(ops, ordered=False)


def clear_graph(db: Database) -> None:
    """Drop all graph data. Used before a fresh explore run."""
    db.files.drop()
    db.symbols.drop()
    db.edges.drop()


# ---------------------------------------------------------------------------
# Read helpers (used by Planner / Verifier)
# ---------------------------------------------------------------------------

def get_file(db: Database, rel_path: str) -> dict[str, Any] | None:
    return db.files.find_one({"path": rel_path}, {"_id": 0})


def get_symbols_for_file(db: Database, rel_path: str) -> list[dict[str, Any]]:
    return list(db.symbols.find({"file_path": rel_path}, {"_id": 0}))


def get_symbol(db: Database, rel_path: str, qualified_name: str) -> dict[str, Any] | None:
    return db.symbols.find_one(
        {"file_path": rel_path, "qualified_name": qualified_name}, {"_id": 0}
    )


def get_neighbors(db: Database, rel_path: str, include_external: bool = False) -> list[dict[str, Any]]:
    """Return all edges where from_path or to_path == rel_path.

    Args:
        include_external: If False (default), return only internal project edges
                          (is_internal=True). Pass True to include stdlib/third-party.
    """
    query: dict = {"$or": [{"from_path": rel_path}, {"to_path": rel_path}]}
    if not include_external:
        query["is_internal"] = True
    return list(db.edges.find(query, {"_id": 0}))


def search_symbols(db: Database, name_fragment: str) -> list[dict[str, Any]]:
    """Case-insensitive substring search on qualified_name."""
    return list(db.symbols.find(
        {"qualified_name": {"$regex": name_fragment, "$options": "i"}},
        {"_id": 0},
    ))


def ensure_indexes(db: Database) -> None:
    """Create indexes once at startup. Safe to call repeatedly."""
    db.files.create_index("path", unique=True)
    db.symbols.create_index([("file_path", 1), ("qualified_name", 1)], unique=True)
    db.edges.create_index([("from_path", 1), ("to_path", 1), ("kind", 1)])
