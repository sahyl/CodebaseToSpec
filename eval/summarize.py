"""
Eval results summarizer.
Reads all runs in eval/results/ and outputs a report.
"""

from __future__ import annotations

import json
from pathlib import Path
from collections import defaultdict

def main() -> None:
    results_dir = Path(__file__).parent / "results"
    if not results_dir.exists():
        print("No eval results found. Run eval/harness.py first.")
        return

    run_files = sorted(results_dir.glob("*.json"))
    if not run_files:
        print("No eval results found in eval/results/")
        return

    scenarios = defaultdict(list)
    for rf in run_files:
        try:
            data = json.loads(rf.read_text())
            scenario_id = data.get("scenario_id", rf.stem.split("_run")[0])
            scenarios[scenario_id].append(data)
        except Exception as e:
            print(f"Error parsing {rf.name}: {e}")

    print("\n" + "=" * 80)
    print("                      CODEBASE TO SPEC EVALUATION SUMMARY")
    print("=" * 80)
    print(f"{'Scenario ID':<25} | {'Runs':<5} | {'Pass Rate':<9} | {'Avg Time (s)':<12} | {'Avg Attempts':<12} | {'Manual Score':<12}")
    print("-" * 87)

    for scenario_id, runs in sorted(scenarios.items()):
        total_runs = len(runs)
        passed_runs = sum(1 for r in runs if r.get("passed", False))
        pass_rate = f"{(passed_runs / total_runs) * 100:.1f}%"
        
        avg_time = sum(r.get("elapsed_seconds", 0.0) for r in runs) / total_runs
        avg_attempts = sum(r.get("attempts_used", 1) for r in runs) / total_runs
        
        manual_scores = [r.get("manual_score") for r in runs if r.get("manual_score") is not None]
        if manual_scores:
            avg_manual = sum(manual_scores) / len(manual_scores)
            manual_str = f"{avg_manual:.1f} ({len(manual_scores)} rated)"
        else:
            manual_str = "N/A"

        print(f"{scenario_id:<25} | {total_runs:<5} | {pass_rate:<9} | {avg_time:<12.2f} | {avg_attempts:<12.1f} | {manual_str:<12}")

    print("=" * 87 + "\n")

if __name__ == "__main__":
    main()
