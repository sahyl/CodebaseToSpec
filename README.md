# Codebase-to-Spec

Given an OSS repo and a feature request, produce an implementation plan
that cites real files and functions, verified against the actual codebase.

---

## Architecture

Three agents, hand-rolled control flow — no LangGraph/CrewAI:

```
Explorer → MongoDB graph → Planner (ReAct loop) → Verifier → Plan (or retry)
```

### 1. Explorer Agent (`agents/explorer.py`)
- Walks the repo with `os.walk`, skips `.git`, `__pycache__`, `venv` etc.
- Parses every `.py` file with Python's `ast` module (no regex).
- Persists to MongoDB:
  - **`files`** — `FileNode`: path, size, num_lines, entry_point flag
  - **`symbols`** — `SymbolNode`: qualified_name, kind, signature, line range, docstring
  - **`edges`** — `Edge`: import relationships between files

### 2. Planner Agent (`agents/planner.py`)
- **ReAct loop** (max 12 steps): Reason → Act → Observe, repeat.
- Tools: `search_symbols`, `get_symbols_for_file`, `get_neighbors`
- Context uses **signatures only** (no raw source) to stay within the LLM window.
- Terminates when the model outputs `PLAN: <json>`.
- Accepts prior Verifier errors on retry and injects them into the prompt.

### 3. Verifier Agent (`agents/verifier.py`)
- No LLM — pure disk + MongoDB checks.
- Confirms every plan step: file exists on disk, symbol exists in graph.
- Returns typed `VerificationError` list. Caller retries up to 3 times, then hard-fails.

---

## MongoDB Schema

| Collection | Key fields | Purpose |
|---|---|---|
| `files` | `path` (unique) | One doc per source file |
| `symbols` | `file_path` + `qualified_name` (unique) | Functions, classes, methods |
| `edges` | `from_path`, `to_path`, `kind` | Import relationships |

---

## Quick Start

```bash
# 1. Install deps
pip install -r requirements.txt

# 2. Start MongoDB (or use Atlas — set MONGO_URI)
mongod --dbpath ./data

# 3. Set env vars
export GEMINI_API_KEY=your_key_here
export MONGO_URI=mongodb://localhost:27017   # optional
export MONGO_DB=codebase_graph              # optional

# 4. Explore a repo (builds the graph)
python main.py explore /path/to/some/repo

# 5. Plan a feature
python main.py plan "Add rate limiting to the API endpoints"
```

## Eval Harness

```bash
# Run an end-to-end scenario
python eval/harness.py eval/scenarios/example.json
```

Scenario JSON format:
```json
{
  "repo_root": "/path/to/repo",
  "feature_request": "Add rate limiting",
  "expected_files": ["src/middleware.py"]   // optional assertions
}
```

---

## Project Structure

```
.
├── main.py                  # CLI entrypoint
├── requirements.txt
├── agents/
│   ├── explorer.py          # Static analysis walk, no LLM
│   ├── planner.py           # ReAct loop over codebase graph
│   └── verifier.py          # Disk + MongoDB verification
├── tools/
│   ├── fs_tools.py          # list_files, read_file, grep
│   ├── ast_tools.py         # get_symbols, get_imports (AST-based)
│   └── mongo_tools.py       # upsert/query helpers
├── models/
│   └── schemas.py           # Pydantic: FileNode, SymbolNode, Edge, Plan, VerifierResult
└── eval/
    ├── harness.py           # End-to-end test runner
    └── scenarios/
        └── example.json
```

---

## Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| LLM | Gemini 2.5 Flash | Free tier handles hundreds of iterations/day; reliable structured JSON output |
| Vendor interface | `call_llm()` in `tools/llm_client.py` | Zero vendor imports in agents; swap to Claude/OpenAI = one file change |
| 429 retries | Exponential backoff in `llm_client.py` | Transport concern, separate from logical re-plan retries |
| Large files | Signatures + docstrings only | Keeps context lean; `read_file` called only on files the agent decides matter |
| Re-plan retries | Immediate, no delay | Verifier errors are logical, not transient — waiting doesn't fix wrong symbol names |
| Parsing | `ast` module | Reliable, no regex edge cases |
| Agent framework | None | Explicit control flow; every step is visible |
