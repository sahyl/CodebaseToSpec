"""
Verifier Agent: re-reads actual files cited in a plan, confirms they exist
and referenced symbols are real. Returns specific errors to the Planner.

No LLM needed — pure file + MongoDB lookup.
Max 3 retries (enforced by the caller in main.py).
"""

from __future__ import annotations

from pathlib import Path

from pymongo.database import Database

from models.schemas import ImplementationPlan, PlanStep, VerificationError, VerifierResult
from tools.mongo_tools import get_file, get_symbol


class VerifierAgent:
    def __init__(self, db: Database, repo_root: str | None = None) -> None:
        self.db = db
        if repo_root is None:
            effective_root = Path.cwd()
            print(
                f"[Verifier] WARNING: no repo_root provided, "
                f"checking file existence against cwd: {effective_root}"
            )
        else:
            effective_root = Path(repo_root).resolve()
        self.repo_root = effective_root


    def verify(self, plan: ImplementationPlan) -> VerifierResult:
        """
        Check every step in the plan:
          - file exists on disk (or is a 'create' action)
          - symbol exists in MongoDB graph (if specified and action != 'create')

        Returns a VerifierResult with passed=True or a list of errors.
        """
        errors: list[VerificationError] = []

        for step in plan.steps:
            errs = self._check_step(step)
            errors.extend(errs)

        return VerifierResult(plan=plan, passed=len(errors) == 0, errors=errors)

    def _check_step(self, step: PlanStep) -> list[VerificationError]:
        errors: list[VerificationError] = []

        file_on_disk = (self.repo_root / step.file_path).exists()

        if step.action == "create":
            # New file — should NOT exist yet (warn but don't fail)
            return errors

        if not file_on_disk:
            errors.append(VerificationError(
                step_order=step.order,
                file_path=step.file_path,
                symbol=step.symbol,
                reason="file not found on disk",
            ))
            return errors  # can't check symbol if file missing

        # Check MongoDB graph has the file indexed
        if get_file(self.db, step.file_path) is None:
            errors.append(VerificationError(
                step_order=step.order,
                file_path=step.file_path,
                symbol=step.symbol,
                reason="file not in codebase graph — run explore first",
            ))

        # Check symbol exists
        if step.symbol:
            sym = get_symbol(self.db, step.file_path, step.symbol)
            if sym is None:
                errors.append(VerificationError(
                    step_order=step.order,
                    file_path=step.file_path,
                    symbol=step.symbol,
                    reason=f"symbol '{step.symbol}' not found in graph for {step.file_path}",
                ))

        return errors
