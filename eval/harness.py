"""
Eval harness: run a scenario (repo + feature request) end-to-end and
report pass/fail with timing.

Usage:
  python eval/harness.py scenarios/my_scenario.json
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# allow imports from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.explorer import ExplorerAgent
from agents.planner import PlannerAgent
from agents.verifier import VerifierAgent
from tools.mongo_tools import clear_graph, get_db

MAX_RETRIES = 3


def run_scenario(scenario_path: str) -> None:
    scenario = json.loads(Path(scenario_path).read_text())
    repo_root: str = scenario["repo_root"]
    feature_request: str = scenario["feature_request"]
    expected_files: list[str] = scenario.get("expected_files", [])  # optional assertions

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        sys.exit("ERROR: GEMINI_API_KEY not set")

    db = get_db()
    clear_graph(db)

    t0 = time.perf_counter()

    # 1. Explore
    print(f"[Harness] Exploring {repo_root}...")
    ExplorerAgent(db).explore(repo_root)

    # 2. Plan + Verify loop
    planner = PlannerAgent(db, api_key)
    verifier = VerifierAgent(db, repo_root)
    prior_errors: list[str] = []

    for attempt in range(1, MAX_RETRIES + 1):
        plan = planner.plan(feature_request, attempt=attempt, prior_errors=prior_errors or None)
        result = verifier.verify(plan)
        if result.passed:
            break
        prior_errors = [f"Step {e.step_order}: {e.reason}" for e in result.errors]
    else:
        print(f"[Harness] FAIL — plan did not pass after {MAX_RETRIES} attempts")
        sys.exit(1)

    elapsed = time.perf_counter() - t0

    # 3. Optional assertions
    plan_files = {s.file_path for s in plan.steps}
    missing = set(expected_files) - plan_files
    extra = plan_files - set(expected_files) if expected_files else set()

    print(f"\n[Harness] PASS in {elapsed:.1f}s — {len(plan.steps)} steps, attempt {plan.attempt}")
    if missing:
        print(f"  ⚠️  Expected files not in plan: {missing}")
    print(json.dumps([s.model_dump() for s in plan.steps], indent=2))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python eval/harness.py <scenario.json>")
    run_scenario(sys.argv[1])
