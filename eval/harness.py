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
    plan = None
    result = None
    passed = False
    attempts_used = 0

    plans_history = []
    for attempt in range(1, MAX_RETRIES + 1):
        attempts_used = attempt
        plan = planner.plan(feature_request, attempt=attempt, prior_errors=prior_errors or None)
        result = verifier.verify(plan)

        attempt_record = {
            "attempt": attempt,
            "passed": result.passed,
            "plan": plan.model_dump(),
            "errors": [e.model_dump() for e in result.errors]
        }
        plans_history.append(attempt_record)

        if result.passed:
            passed = True
            break
        prior_errors = [f"Step {e.step_order}: {e.reason}" for e in result.errors]

    elapsed = time.perf_counter() - t0

    # 3. Optional assertions
    missing_files = []
    extra_files = []
    plan_steps_data = []
    errors_data = []

    if plan:
        plan_steps_data = [s.model_dump() for s in plan.steps]
        plan_files = {s.file_path for s in plan.steps}
        missing_files = sorted(list(set(expected_files) - plan_files))
        extra_files = sorted(list(plan_files - set(expected_files) if expected_files else set()))

    if result and result.errors:
        errors_data = [e.model_dump() for e in result.errors]

    # 4. Save results
    scenario_id = Path(scenario_path).stem
    results_dir = Path(__file__).parent / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    
    run_num = 1
    while (results_dir / f"{scenario_id}_run{run_num}.json").exists():
        run_num += 1
        
    result_file = results_dir / f"{scenario_id}_run{run_num}.json"
    
    import datetime
    run_data = {
        "scenario_id": scenario_id,
        "run_number": run_num,
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "elapsed_seconds": round(elapsed, 2),
        "passed": passed,
        "attempts_used": attempts_used,
        "plan": plan_steps_data,
        "errors": errors_data,
        "attempts": plans_history,
        "expected_files": expected_files,
        "missing_expected_files": missing_files,
        "extra_files": extra_files,
        "manual_score": None,
        "manual_notes": None
    }
    
    result_file.write_text(json.dumps(run_data, indent=2))
    print(f"\n[Harness] Saved run results to {result_file}")

    if passed:
        print(f"\n[Harness] PASS in {elapsed:.1f}s — {len(plan.steps)} steps, attempt {attempts_used}")
        if missing_files:
            print(f"  ⚠️  Expected files not in plan: {missing_files}")
        print(json.dumps(plan_steps_data, indent=2))
    else:
        print(f"[Harness] FAIL — plan did not pass after {MAX_RETRIES} attempts")
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python eval/harness.py <scenario.json>")
    run_scenario(sys.argv[1])

