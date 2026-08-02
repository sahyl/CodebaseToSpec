"""
CLI entrypoint.

Usage:
  python main.py explore <repo_path>
  python main.py plan "<feature request>"

Env vars:
  GEMINI_API_KEY  — required for `plan`
  MONGO_URI       — optional, default mongodb://localhost:27017
  MONGO_DB        — optional, default codebase_graph
"""

from __future__ import annotations

import os
import sys
import json

from agents.explorer import ExplorerAgent
from agents.planner import PlannerAgent
from agents.verifier import VerifierAgent
from tools.mongo_tools import get_db

MAX_RETRIES = 3


def cmd_explore(repo_path: str) -> None:
    db = get_db()
    agent = ExplorerAgent(db)
    agent.explore(repo_path)


def cmd_plan(feature_request: str) -> None:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        sys.exit("ERROR: GEMINI_API_KEY env var not set")

    db = get_db()
    # Planner needs repo_root to resolve file paths for Verifier.
    # We infer it from the graph's stored paths (all relative) — pass cwd.
    repo_root = os.getcwd()

    planner = PlannerAgent(db, api_key)
    verifier = VerifierAgent(db, repo_root)

    prior_errors: list[str] = []

    for attempt in range(1, MAX_RETRIES + 1):
        print(f"\n[main] Planning attempt {attempt}/{MAX_RETRIES}...")
        plan = planner.plan(feature_request, attempt=attempt, prior_errors=prior_errors or None)

        print(f"[main] Verifying plan ({len(plan.steps)} steps)...")
        result = verifier.verify(plan)

        if result.passed:
            print("\n✅ Plan verified. Final plan:\n")
            print(json.dumps([s.model_dump() for s in plan.steps], indent=2))
            return

        prior_errors = [f"Step {e.step_order} ({e.file_path}:{e.symbol}): {e.reason}"
                        for e in result.errors]
        print(f"[main] Verification failed. Errors:\n" + "\n".join(f"  {e}" for e in prior_errors))

    # Hard fail after MAX_RETRIES
    print(f"\n❌ Plan failed verification after {MAX_RETRIES} attempts. Last errors:")
    for e in prior_errors:
        print(f"  {e}")
    sys.exit(1)


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: python main.py explore <repo_path>")
        print("       python main.py plan \"<feature request>\"")
        sys.exit(1)

    command = sys.argv[1]
    arg = sys.argv[2]

    if command == "explore":
        cmd_explore(arg)
    elif command == "plan":
        cmd_plan(arg)
    else:
        sys.exit(f"Unknown command: {command!r}. Use 'explore' or 'plan'.")


if __name__ == "__main__":
    main()
