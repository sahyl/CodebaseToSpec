"""
Thin LLM interface. Every agent calls call_llm() — never the vendor SDK directly.
Swap vendor: change this file only.

Transport concerns live here:
  - 429 / RESOURCE_EXHAUSTED retries with exponential backoff
  - API key resolution

Logical re-plan retries live in main.py. These are separate concerns:
  - Transport retries: "the network hiccuped, try again"
  - Re-plan retries:   "the plan was logically wrong, fix it"
"""

from __future__ import annotations

import os
import time

from google import genai
from google.genai import types

MODEL = "gemini-3.5-flash"

# Transport-level only — not to be confused with Verifier re-plan retries
_MAX_TRANSPORT_RETRIES = 4
_BACKOFF_BASE = 1.0  # seconds; doubles each attempt: 1, 2, 4, 8

# Plain dict so callers have zero vendor imports
Message = dict[str, str]  # {"role": "user"|"model", "content": "..."}


def call_llm(
    system: str,
    messages: list[Message],
    api_key: str | None = None,
) -> str:
    """
    Call the configured LLM and return the text response.

    Args:
        system:   System prompt / instruction.
        messages: Conversation history as plain dicts with "role" and "content".
        api_key:  Optional override; falls back to GEMINI_API_KEY env var.

    Returns:
        Stripped text response from the model.

    Raises:
        Anything except rate-limit errors (those are retried internally).
    """
    key = api_key or os.environ.get("GEMINI_API_KEY")
    if not key:
        raise EnvironmentError("GEMINI_API_KEY not set and no api_key argument provided")

    client = genai.Client(api_key=key)
    contents = [
        types.Content(role=m["role"], parts=[types.Part(text=m["content"])])
        for m in messages
    ]

    for attempt in range(_MAX_TRANSPORT_RETRIES):
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=contents,
                config=types.GenerateContentConfig(system_instruction=system),
            )
            return response.text.strip()
        except Exception as exc:
            if _is_rate_limit(exc) and attempt < _MAX_TRANSPORT_RETRIES - 1:
                wait = _BACKOFF_BASE * (2 ** attempt)
                print(f"[llm_client] Rate limit (429). Retrying in {wait:.0f}s "
                      f"(attempt {attempt + 1}/{_MAX_TRANSPORT_RETRIES})...")
                time.sleep(wait)
                continue
            raise


def _is_rate_limit(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "429" in msg or "resource_exhausted" in msg or "rate_limit" in msg
