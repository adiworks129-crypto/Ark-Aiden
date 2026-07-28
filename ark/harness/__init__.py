"""
Ark's agent-harness contract — Milestone 7.

This package defines HOW an evaluated AI agent is shown Ark's rendered
artifacts and HOW its response is turned into the raw `agent_output` dict
`ark.evaluator.orchestrator.evaluate()` already expects. It introduces no
new scoring, matching, or metrics of any kind — everything downstream of
`run_agent_harness()`'s return value is still `ark.evaluator`'s job,
completely unchanged.

Isolation boundary (unchanged from every evaluator milestone, restated
here because this is the module that actually calls the agent): the
agent sees ONLY rendered artifact contents (`dict[str, str]`, exactly
`RenderedEstate.artifacts` from `ark.adapters.base` — never the
`RenderedEstate` object itself, and never its `.manifest`). Concretely,
this package never imports `ark.core.models`, `ark.mutation.ledger`,
`ark.mutation.engine`, or `ark.adapters` (generic or MuleSoft-specific) —
if it can't do its job with a plain `dict[str, str]` of artifact path ->
contents, it doesn't get a richer input. `run_agent_harness()`'s own
signature enforces this: its one required parameter is `artifacts:
dict[str, str]`, not a `RenderedEstate`.

The one exception, and it's deliberate: this package DOES import
`ark.evaluator.schema` for `ISSUE_TYPE_TAXONOMY` and
`AGENT_OUTPUT_SCHEMA_VERSION` — these are the agent-VISIBLE output
contract (the exact taxonomy and JSON shape a finding must use), not
ground truth. A real agent already needs to know this to comply
syntactically; sourcing it from schema.py (rather than hand-copying the
taxonomy into a second string here) guarantees the prompt can never drift
from what `parse_agent_output()` actually accepts.

Vendor independence: nothing in this package imports any specific LLM
SDK. `AgentClient` is a two-line abstract interface
(`generate(prompt) -> str`); concrete, vendor-specific implementations
(e.g. an Anthropic-SDK-backed client) live OUTSIDE the `ark` package
entirely, in the repo-root `integrations/` directory, so `ark`'s own
dependencies stay empty — see `integrations/anthropic_agent_client.py`
and its docstring for why. `ark/harness/` ships only `ScriptedAgentClient`
(scripted_client.py), a deterministic, offline reference/test double —
useful both for the automated test suite and for worked examples,
exactly like `ark.evaluator.analysis`'s worked example used a scripted,
not real, agent.
"""

from __future__ import annotations
