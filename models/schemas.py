"""
Pydantic models for the codebase graph, implementation plan, and verifier results.

MongoDB collections:
  - files    → FileNode
  - symbols  → SymbolNode
  - edges    → Edge
"""

from __future__ import annotations

from enum import Enum
from typing import Optional, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Codebase graph
# ---------------------------------------------------------------------------

class FileNode(BaseModel):
    """One source file in the target repo."""
    path: str                        # repo-relative path, e.g. "src/parser.py"
    language: str = "python"
    size_bytes: int = 0
    num_lines: int = 0
    entry_point: bool = False        # True if __main__ or CLI entry


class SymbolKind(str, Enum):
    FUNCTION = "function"
    CLASS = "class"
    METHOD = "method"
    ASYNC_FUNCTION = "async_function"


class SymbolNode(BaseModel):
    """A function, class, or method extracted via AST."""
    file_path: str                   # FK → FileNode.path
    name: str
    qualified_name: str              # e.g. "MyClass.my_method"
    kind: SymbolKind
    signature: str                   # full def line, e.g. "def foo(x: int) -> str"
    line_start: int
    line_end: int
    docstring: Optional[str] = None


class EdgeKind(str, Enum):
    IMPORT = "import"                # file A imports file B
    CALL = "call"                    # symbol A calls symbol B


class Edge(BaseModel):
    """Directed relationship between two nodes in the codebase graph."""
    kind: EdgeKind
    from_path: str                   # file path (both edge kinds)
    from_symbol: Optional[str] = None  # qualified_name; None for file-level imports
    to_path: str
    to_symbol: Optional[str] = None
    is_internal: Optional[bool] = None  # True = to_path is in the repo; set by Explorer


# ---------------------------------------------------------------------------
# Implementation plan
# ---------------------------------------------------------------------------

class PlanStep(BaseModel):
    """One concrete action in the implementation plan."""
    order: int
    file_path: str
    symbol: Optional[str] = None     # function/class to touch; None = new file
    action: str                      # "modify" | "create" | "delete"
    rationale: str                   # one-line explanation


class ReActStep(BaseModel):
    """One step in the ReAct loop process."""
    step_number: int
    step_type: Literal["thought", "action", "observe", "plan"]
    raw_content: str
    tool_name: Optional[str] = None
    tool_args: Optional[dict] = None
    tool_result: Optional[str] = None
    duration_seconds: float


class ImplementationPlan(BaseModel):
    """Full plan output from the Planner agent."""
    feature_request: str
    steps: list[PlanStep] = Field(default_factory=list)
    attempt: int = 1                 # which retry pass produced this plan
    trace: list[ReActStep] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Verifier results
# ---------------------------------------------------------------------------

class VerificationError(BaseModel):
    step_order: int
    file_path: str
    symbol: Optional[str]
    reason: str                      # e.g. "function not found", "file missing"


class VerifierResult(BaseModel):
    """Output from the Verifier agent."""
    plan: ImplementationPlan
    passed: bool
    errors: list[VerificationError] = Field(default_factory=list)
