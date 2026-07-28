"""
Raw agent-response -> JSON dict extraction — Milestone 7.

Real LLM responses are not always a bare JSON object even when asked for
one: some wrap it in a markdown code fence, some add a sentence before or
after it. This module's only job is recovering the JSON object from
whatever text came back. It does NOT validate the object's shape against
the `AgentOutput` contract (missing fields, bad `issue_type`, out-of-range
confidence, ...) -- that's `ark.evaluator.schema.parse_agent_output()`'s
job, deliberately left untouched and un-duplicated here. This module
only answers "is there a JSON object in this text, and if so, what is
it," nothing more.
"""

from __future__ import annotations

import json
import re

_FENCE_PATTERN = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


class AgentResponseParsingError(Exception):
    """Raised when no JSON object could be recovered from the agent's raw
    text response at all. Distinct from AgentOutputValidationError
    (schema.py) -- this is "couldn't even find JSON," that is "found JSON,
    but it doesn't match the required contract.\""""

    def __init__(self, raw_response: str):
        self.raw_response = raw_response
        super().__init__(
            "Could not extract a JSON object from the agent's response. "
            f"Raw response (truncated): {raw_response[:500]!r}"
        )


def extract_json_object(raw_response: str) -> dict:
    """Recover a JSON object from an agent's raw text response.

    Tries, in order:
    1. The whole response is already valid JSON (the common case for a
       well-behaved agent that follows the "respond with only JSON"
       instruction).
    2. A ```json ... ``` or ``` ... ``` fenced code block containing a
       JSON object.
    3. The substring from the first '{' to the last '}' in the response
       (handles a stray sentence before/after the JSON with no fence).

    Raises AgentResponseParsingError if none of these produce valid JSON,
    or if the recovered value isn't a JSON object (dict) at all -- a bare
    JSON list or scalar is not a valid agent-output shape either way, so
    it's treated the same as "couldn't extract."

    One deliberate ordering rule: if the WHOLE response is already valid
    JSON, its shape is treated as definitive -- a bare top-level list or
    scalar raises immediately rather than falling through to the
    substring heuristic below, which could otherwise "recover" some
    unrelated nested object from inside it (e.g. pulling the first
    dict out of a bare `[{"...": "..."}]` list, silently reinterpreting a
    response that was never an object to begin with).
    """
    stripped = raw_response.strip()
    try:
        whole = json.loads(stripped)
    except json.JSONDecodeError:
        whole = None
    else:
        if isinstance(whole, dict):
            return whole
        raise AgentResponseParsingError(raw_response)

    fence_match = _FENCE_PATTERN.search(raw_response)
    if fence_match:
        try:
            parsed = json.loads(fence_match.group(1).strip())
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed

    first_brace = raw_response.find("{")
    last_brace = raw_response.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        try:
            parsed = json.loads(raw_response[first_brace : last_brace + 1].strip())
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed

    raise AgentResponseParsingError(raw_response)
