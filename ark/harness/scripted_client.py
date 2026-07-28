"""
ScriptedAgentClient — Milestone 7's deterministic, offline reference
`AgentClient`.

Two uses: (1) the automated test suite needs an `AgentClient` that never
makes a network call and always returns the same thing for the same
input, and (2) worked examples (`examples/milestone7/`) need a stand-in
"agent" the same way `examples/milestone6/generate_analysis_example.py`
used a hand-parameterized mock agent rather than a real model. This class
generalizes that pattern into a proper, reusable, documented `AgentClient`
implementation instead of one-off inline logic per example script.

Two constructions are supported:
- `ScriptedAgentClient.fixed(findings_json)` -- always returns the same
  raw JSON string, ignoring the prompt entirely. The simplest possible
  double, for tests that just need *an* agent output shape.
- `ScriptedAgentClient(responder)` -- a callable `(prompt: str) -> str`
  the caller controls, for tests/examples that need the "agent's" answer
  to depend on what's actually in the prompt (e.g. a mock whose accuracy
  is parameterized by trajectory complexity, as in Milestone 6.5's
  worked example).
"""

from __future__ import annotations

import json
from collections.abc import Callable

from ark.harness.contract import AgentClient


class ScriptedAgentClient(AgentClient):
    """An `AgentClient` whose response is fully determined by a supplied
    callable -- no network call, no randomness of its own, fully
    reproducible."""

    def __init__(self, responder: Callable[[str], str]):
        self._responder = responder
        self.prompts_received: list[str] = []
        """Every prompt this client was asked to respond to, in order --
        for tests/audits that want to assert something about what the
        harness actually sent (e.g. "the prompt contains this artifact's
        rendered content" or "the prompt never contains this internal id"),
        without needing to intercept the call some other way."""

    def generate(self, prompt: str) -> str:
        self.prompts_received.append(prompt)
        return self._responder(prompt)

    @classmethod
    def fixed(cls, findings: dict) -> "ScriptedAgentClient":
        """Always responds with `json.dumps(findings)`, regardless of the
        prompt. `findings` should already match the raw agent-output
        shape (a dict with a "findings" key), e.g.
        `{"findings": [{...}, ...]}`."""
        raw_response = json.dumps(findings)
        return cls(lambda _prompt: raw_response)
