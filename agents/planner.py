"""
Planner Agent: given a feature request, traverses the codebase graph and
produces a structured ImplementationPlan via a hand-rolled ReAct loop.

ReAct loop (Reason → Act → Observe, repeat):
  - Reason: ask the LLM what to look up next
  - Act:    call one of the graph traversal tools
  - Observe: feed result back to the LLM
  - Terminate when the model outputs a plan (JSON) or hits MAX_STEPS

Tools available to the loop:
  - search_symbols(name_fragment)
  - get_symbols_for_file(rel_path)
  - get_neighbors(rel_path)

LLM: via tools/llm_client.py (currently Gemini 2.5 Flash).
Swap vendor: change llm_client.py only — this file has zero vendor imports.
Context: signatures only (no raw source) to stay within token limits.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pymongo.database import Database

from models.schemas import ImplementationPlan, PlanStep, ReActStep
from tools.llm_client import Message, call_llm
from tools.ast_tools import signatures_only
from tools.mongo_tools import (
    get_neighbors,
    get_symbols_for_file,
    search_symbols,
)

MAX_STEPS = 12   # ponytail: cap prevents runaway loops

SYSTEM_PROMPT = """\
You are a senior software architect. You have access to a codebase graph stored \
in MongoDB. You will receive a feature request and must produce a concrete \
implementation plan.

You operate in a ReAct loop. Each turn you output exactly one of:
  THOUGHT: <reasoning about what to look up>
  ACTION: <tool_name> <json_args>
  PLAN: <json>

Available tools:
  search_symbols {"name_fragment": "..."}
  get_symbols_for_file {"rel_path": "..."}
  get_neighbors {"rel_path": "..."}

When you have enough information, output PLAN with a JSON array of steps.
You MUST explicitly populate the "order" field for every step, starting from 1.

Example of a valid PLAN output:
PLAN:
[
  {
    "order": 1,
    "file_path": "src/utils.py",
    "symbol": "clean_input",
    "action": "modify",
    "rationale": "Add validation logic to the input helper."
  },
  {
    "order": 2,
    "file_path": "src/main.py",
    "symbol": "main",
    "action": "modify",
    "rationale": "Call the updated input helper and handle validation errors."
  }
]

Rules:
- Never invent file paths or function names. Only use what you've observed.
- The "order" field is required and must be an integer sequence (1, 2, 3, ...).
- The "symbol" field is the qualified_name from the graph, or null for new files.
- The "action" field is one of: modify, create, delete.
- The "rationale" field is one sentence max.
"""


class PlannerAgent:
    def __init__(self, db: Database, gemini_api_key: str) -> None:
        self.db = db
        self._api_key = gemini_api_key
        self._tools: dict[str, Any] = {
            "search_symbols": lambda args: search_symbols(db, args["name_fragment"]),
            "get_symbols_for_file": lambda args: get_symbols_for_file(db, args["rel_path"]),
            "get_neighbors": lambda args: get_neighbors(db, args["rel_path"]),
        }
        # ponytail: no vendor object stored — call_llm is stateless

    def plan(self, feature_request: str, attempt: int = 1,
             prior_errors: list[str] | None = None) -> ImplementationPlan:
        """
        Run the ReAct loop and return an ImplementationPlan.
        prior_errors (from Verifier) are injected into the initial prompt on retry.
        """
        import time
        trace: list[ReActStep] = []
        step_counter = 1

        messages: list[Message] = []

        user_text = f"Feature request: {feature_request}"
        if prior_errors:
            error_block = "\n".join(f"- {e}" for e in prior_errors)
            user_text += f"\n\nPrevious plan failed verification. Fix these errors:\n{error_block}"

        messages.append({"role": "user", "content": user_text})

        for step in range(MAX_STEPS):
            t0 = time.perf_counter()
            reply = call_llm(SYSTEM_PROMPT, messages, api_key=self._api_key)
            duration_llm = time.perf_counter() - t0
            messages.append({"role": "model", "content": reply})

            if reply.startswith("PLAN:"):
                raw_json = reply[len("PLAN:"):].strip()
                trace.append(ReActStep(
                    step_number=step_counter,
                    step_type="plan",
                    raw_content=reply,
                    duration_seconds=duration_llm
                ))
                # ponytail: minimum implementation, return trace directly in parse_plan
                return self._parse_plan(raw_json, feature_request, attempt, trace)

            if reply.startswith("ACTION:"):
                tool_name = None
                tool_args = None
                match = re.match(r"ACTION:\s*(\w+)\s*(.*)", reply, re.DOTALL)
                if match:
                    tool_name = match.group(1)
                    raw_args = match.group(2).strip()
                    try:
                        tool_args = json.loads(raw_args) if raw_args else {}
                    except Exception:
                        pass

                trace.append(ReActStep(
                    step_number=step_counter,
                    step_type="action",
                    raw_content=reply,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    duration_seconds=duration_llm
                ))
                step_counter += 1

                t1 = time.perf_counter()
                obs = self._dispatch_action(reply)
                duration_tool = time.perf_counter() - t1

                messages.append({"role": "user", "content": f"OBSERVATION:\n{obs}"})

                trace.append(ReActStep(
                    step_number=step_counter,
                    step_type="observe",
                    raw_content=f"OBSERVATION:\n{obs}",
                    tool_name=tool_name,
                    tool_args=tool_args,
                    tool_result=obs,
                    duration_seconds=duration_tool
                ))
                step_counter += 1
                continue

            # THOUGHT or unrecognised — just continue the loop
            trace.append(ReActStep(
                step_number=step_counter,
                step_type="thought",
                raw_content=reply,
                duration_seconds=duration_llm
            ))
            step_counter += 1
            messages.append({"role": "user", "content": "Continue. Output ACTION or PLAN next."})

        raise RuntimeError(f"[Planner] ReAct loop hit MAX_STEPS={MAX_STEPS} without producing a plan.")

    def _dispatch_action(self, reply: str) -> str:
        """Parse ACTION line, call the tool, return observation as string."""
        # FORMAT: ACTION: tool_name {"key": "value"}
        match = re.match(r"ACTION:\s*(\w+)\s*(.*)", reply, re.DOTALL)
        if not match:
            return "ERROR: malformed ACTION line"
        tool_name, raw_args = match.group(1), match.group(2).strip()
        try:
            args = json.loads(raw_args) if raw_args else {}
        except json.JSONDecodeError as e:
            return f"ERROR: bad JSON args: {e}"

        fn = self._tools.get(tool_name)
        if fn is None:
            return f"ERROR: unknown tool '{tool_name}'"

        try:
            result = fn(args)
            return json.dumps(result, default=str)[:8000]  # cap observation size
        except Exception as e:
            return f"ERROR: tool raised {type(e).__name__}: {e}"

    @staticmethod
    def _parse_plan(raw_json: str, feature_request: str, attempt: int, trace: list[ReActStep]) -> ImplementationPlan:
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError as e:
            raise ValueError(f"[Planner] Model output invalid JSON: {e}\nRaw:\n{raw_json}") from e

        # Defensively inject 1-based order from list position if the model omitted it.
        steps = []
        for i, s in enumerate(data, start=1):
            if "order" not in s:
                print(f"[Planner] WARNING: step {i} missing 'order' field — assigning {i} from position")
                s = {"order": i, **s}
            steps.append(PlanStep(**s))

        return ImplementationPlan(feature_request=feature_request, steps=steps, attempt=attempt, trace=trace)
